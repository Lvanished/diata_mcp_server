"""

CLI: PubMed search → metadata → PMC full text → keyword context → JSON + Markdown.



Single drug: ``--drug``

Batch: ``--input-xlsx`` (repo path or file under ``input/``). Default ``--max-drugs`` 10 (use ``0`` for all).

"""



from __future__ import annotations



import argparse

import asyncio

import logging

import sys

from pathlib import Path



import yaml

from dotenv import load_dotenv

from rich.console import Console

from rich.logging import RichHandler



from .article_filter import (

    filter_articles_with_pmcid,

    normalize_any_article,

)

from .context_extractor import extract_keyword_contexts

from .excel_input import build_drug_jobs, resolve_excel_input_path, resolve_sheet

from .fulltext_extractor import fetch_fulltext_for_articles

from .mcp_client import PubMedMCPClient

from .query_builder import iter_layered_pubmed_query_rounds, iter_pubmed_query_fallbacks

from .report_writer import write_excel_batch_markdown, write_json, write_markdown_report



console = Console()





def _project_root() -> Path:

    return Path(__file__).resolve().parent.parent





def _load_config(root: Path) -> dict:

    p = root / "config" / "qt_keywords.yaml"

    with p.open("r", encoding="utf-8") as f:

        return yaml.safe_load(f) or {}





async def run_pipeline_for_drug(

    client: PubMedMCPClient,

    drug: str,

    top_n: int,

    window: int,

    terms: list[str],

    *,

    search_strategy: str = "default",

) -> dict:

    """One full pipeline using an existing MCP session (stdio or HTTP)."""

    log = logging.getLogger(__name__)

    attempts_meta: list[dict] = []
    search: dict = {}
    q_used = ""
    strategy_used = ""
    layered_round_used: str | None = None

    if search_strategy == "layered":
        note_extra_layered = (
            "Layered search: strict → optional broad → optional salt-stripped strict; "
            "two branches per tier (hERG/channel + QT/TdP TA) merged; "
            "stops early once min hits reached. PMC open full text is fetched when PMCID is present (same as default)."
        )
        query_rounds = iter_layered_pubmed_query_rounds(drug)
        pmids_merged: list[str] = []
        seen_pmids: set[str] = set()
        effective_parts: list[str] = []

        for qr in query_rounds:
            layered_round_used = qr.name
            seen_round: set[str] = set()
            order_round: list[str] = []
            for label, q in qr.queries:
                log.info("PubMed layered [%s/%s]: %s", qr.name, label, q)
                s = await client.search_articles(q, top_n)
                if not isinstance(s, dict):
                    s = {}
                tf = int(s.get("totalFound") or 0)
                pmids_try = [str(p) for p in (s.get("pmids") or []) if p]
                attempts_meta.append(
                    {
                        "strategy": label,
                        "query": q,
                        "total_found": tf,
                        "returned": len(pmids_try),
                        "round": qr.name,
                    }
                )
                eff = str(s.get("effectiveQuery") or q)
                if eff and eff not in effective_parts:
                    effective_parts.append(eff)
                for p in pmids_try:
                    if p not in seen_round:
                        seen_round.add(p)
                        order_round.append(p)
            for p in order_round:
                if p not in seen_pmids:
                    seen_pmids.add(p)
                    pmids_merged.append(p)
            strategy_used = f"layered:{qr.name}"
            if qr.min_hits_to_stop > 0 and len(pmids_merged) >= qr.min_hits_to_stop:
                break

        unique_union = len(pmids_merged)
        q_used = " | ".join(a["query"] for a in attempts_meta if a.get("query"))
        pmids = pmids_merged[:top_n]
        total_found = unique_union
        search = {
            "totalFound": total_found,
            "pmids": pmids,
            "notice": None,
            "effectiveQuery": " ; ".join(effective_parts) if effective_parts else q_used,
        }
        log.info(
            "Layered search: last_round=%s, union pmids=%s (after cap top_n=%s: %s)",
            layered_round_used,
            unique_union,
            top_n,
            len(pmids),
        )
    else:
        for label, q in iter_pubmed_query_fallbacks(drug):
            log.info("PubMed try [%s]: %s", label, q)
            search = await client.search_articles(q, top_n)
            if not isinstance(search, dict):
                search = {}
            tf = int(search.get("totalFound") or 0)
            pmids_try = [str(p) for p in (search.get("pmids") or []) if p]
            attempts_meta.append(
                {
                    "strategy": label,
                    "query": q,
                    "total_found": tf,
                    "returned": len(pmids_try),
                }
            )
            q_used, strategy_used = q, label
            if tf > 0 and pmids_try:
                if label != "ta_quoted":
                    log.info("PubMed: hits after broader strategy %s (total=%s)", label, tf)
                break

    note: str | None = None
    if search.get("notice"):
        note = str(search.get("notice"))
        log.warning("Search notice: %s", note)
        console.print(f"[yellow]Search notice: {note}[/yellow]")
    if search_strategy != "layered" and len(attempts_meta) > 1 and int(search.get("totalFound") or 0) > 0:
        extra = (
            f" Recovered with broader query strategy: {strategy_used!r} "
            f"({len(attempts_meta) - 1} prior strateg(ies) returned 0 hits)."
        )
        note = f"{note}{extra}" if note else extra.strip()
        log.info(note)
    if search_strategy == "layered":
        note = f"{note_extra_layered}\n{note}" if note else note_extra_layered

    effective = str(search.get("effectiveQuery") or q_used)
    q = q_used

    if search_strategy != "layered":
        pmids = [str(p) for p in (search.get("pmids") or []) if p]
        total_found = int(search.get("totalFound") or 0)
    log.info("Found total=%s, retrieved pmids=%s (strategy=%s)", total_found, len(pmids), strategy_used)



    fetch = await client.fetch_articles(pmids)

    raw_arts = list(fetch.get("articles") or [])



    with_pmc = filter_articles_with_pmcid(raw_arts)

    log.info("Articles fetched: %s; with PMCID: %s", len(raw_arts), len(with_pmc))



    if with_pmc:

        merged = await fetch_fulltext_for_articles(client, with_pmc)

    else:

        merged = [dict(a) for a in normalize_any_article(raw_arts)]

        for a in merged:

            a["fulltext_available"] = False

            a["fulltext_error"] = (

                "No articles with PMCID in this result set; abstract-level evidence only."

            )

            a["sections"] = []

        if not note:

            note = (

                "No PMCID in the PubMed hit list — PubMed is not the same as PMC. "

                "Open-access full text is only available for a subset of articles. "

                "This export uses abstracts only; PMC open fulltext is not available for these records."

            )

        log.warning("No PMCID records; using abstract-level results only.")



    final_arts: list[dict] = []

    for a in merged:

        sections = a.get("sections") or []

        ex = extract_keyword_contexts(

            {

                "abstract": a.get("abstract") or "",

                "sections": sections,

            },

            terms,

            window=window,

        )

        row = {k: v for k, v in a.items() if k != "sections"}

        row["matched_terms"] = ex.get("matched_terms") or []

        row["contexts"] = ex.get("contexts") or []

        if "error" in row and row.get("error"):

            row["fulltext_error"] = row.get("fulltext_error") or str(row.get("error"))

        row.pop("error", None)

        final_arts.append(row)



    with_ft = sum(1 for a in final_arts if a.get("fulltext_available"))

    with_ctx = sum(1 for a in final_arts if a.get("contexts"))



    return {

        "drug_name": drug,

        "search_strategy": search_strategy,

        "query": q,

        "query_strategy": strategy_used,

        "query_attempts": attempts_meta,

        "effective_query": effective,

        "search_total_found": total_found,

        "layered_round": layered_round_used,

        "summary": {

            "total_pubmed_articles": len(pmids),

            "articles_with_pmcid": len(with_pmc),

            "articles_with_fulltext": with_ft,

            "articles_with_context": with_ctx,

        },

        "note": note,

        "articles": final_arts,

    }





async def _run_one_drug(drug: str, top_n: int, window: int, *, search_strategy: str) -> dict:

    root = _project_root()

    load_dotenv(root / ".env", override=False)

    cfg = _load_config(root)

    terms = [str(x) for x in (cfg.get("qt_terms") or [])]

    async with PubMedMCPClient.from_env(root) as client:

        return await run_pipeline_for_drug(
            client, drug, top_n, window, terms, search_strategy=search_strategy
        )





async def _run_excel_batch(

    xlsx: Path,

    *,

    top_n: int,

    window: int,

    sheet: str | int | None,

    name_column: str,

    max_drugs: int | None,

    dedupe_by_name: bool,

    search_strategy: str,

) -> dict:

    root = _project_root()

    load_dotenv(root / ".env", override=False)

    cfg = _load_config(root)

    terms = [str(x) for x in (cfg.get("qt_terms") or [])]



    resolve = (root / xlsx).resolve() if not xlsx.is_absolute() else xlsx

    sheet_name, df = resolve_sheet(resolve, sheet)

    jobs = build_drug_jobs(

        df,

        name_column=name_column,

        max_drugs=max_drugs,

        dedupe_by_name=dedupe_by_name,

    )

    n_rows = int(len(df))



    results: list[dict] = []

    log = logging.getLogger(__name__)

    log.info("Excel rows: %s, jobs to run: %s (dedupe=%s)", n_rows, len(jobs), dedupe_by_name)



    async with PubMedMCPClient.from_env(root) as client:

        for j in jobs:

            name = j["drug_name"]

            try:

                r = await run_pipeline_for_drug(
                    client, name, top_n, window, terms, search_strategy=search_strategy
                )

                results.append({**j, "ok": True, "error": None, "result": r})

            except Exception as e:  # noqa: BLE001

                log.exception("Drug %s failed: %s", name, e)

                results.append({**j, "ok": False, "error": str(e), "result": None})



    return {

        "source_file": str(xlsx).replace("\\", "/"),

        "source_file_resolved": str(resolve),

        "sheet": sheet_name,

        "name_column": name_column,

        "search_strategy": search_strategy,

        "row_count": n_rows,

        "drugs_run": len(jobs),

        "top_n": top_n,

        "context_window": window,

        "dedupe_by_name": dedupe_by_name,

        "results": results,

    }





def _configure_logging() -> None:

    logging.basicConfig(

        level=logging.INFO,

        format="%(message)s",

        datefmt="[%X]",

        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],

    )





def _parse_args() -> argparse.Namespace:

    p = argparse.ArgumentParser(

        description="PubMed/PMC drug + QT context harvester (via pubmed-mcp-server).",

    )

    src = p.add_mutually_exclusive_group(required=True)

    src.add_argument(

        "--drug",

        type=str,

        default=None,

        help='Single drug name, e.g. thioridazine',

    )

    src.add_argument(

        "--input-xlsx",

        type=Path,

        default=None,

        help=(
            "Excel path (repo-relative, absolute, or filename under input/). "
            "Needs a name column (default: name)."
        ),

    )

    p.add_argument("--top-n", type=int, default=20, dest="top_n", help="Max PubMed results per drug (default 20)")

    p.add_argument("--window", type=int, default=500, help="Context character window (default 500)")

    p.add_argument(

        "--out-dir",

        type=str,

        default="outputs",

        help="Output directory (default: outputs/ under project root)",

    )

    p.add_argument(

        "--sheet",

        type=str,

        default=None,

        help="Worksheet name or 0-based index (default: first sheet)",

    )

    p.add_argument(

        "--name-column",

        type=str,

        default="name",

        dest="name_column",

        help="Column for drug / compound name (default: name)",

    )

    p.add_argument(

        "--max-drugs",

        type=int,

        default=10,

        dest="max_drugs",

        help=(
            "Batch: max unique names after dedupe (order preserved). Default 10. Use 0 for no limit."
        ),

    )

    p.add_argument(

        "--no-dedupe",

        action="store_true",

        help="Run every data row (same name may be queried more than once)",

    )

    p.add_argument(

        "--search-strategy",

        type=str,

        choices=["default", "layered"],

        default="default",

        dest="search_strategy",

        help=(
            "PubMed query mode: default (single QT_OR + fallbacks) "
            "or layered (strict→broad→salt tiers, two branches each, early stop). "
            "Both fetch PMC open full text when PMCID is available."
        ),

    )

    return p.parse_args()





def re_safe_filename(name: str) -> str:

    s = "".join(c if c.isalnum() or c in "._-" else "_" for c in name.strip().lower())

    return s or "result"


def _effective_max_drugs(n: int) -> int | None:
    """``0`` or negative means no cap (process all qualifying rows)."""
    if n <= 0:
        return None
    return n





def _sheet_arg(s: str | None) -> str | int | None:

    if s is None:

        return 0

    s = s.strip()

    if s.isdigit():

        return int(s)

    return s





def main() -> int:

    _configure_logging()

    log = logging.getLogger(__name__)

    args = _parse_args()

    root = _project_root()

    out_dir = (root / args.out_dir) if not Path(args.out_dir).is_absolute() else Path(args.out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)



    if args.drug is not None:

        drug = args.drug.strip()

        if not drug:

            console.print("[red]--drug must be non-empty[/red]")

            return 2

        base = re_safe_filename(drug)

        json_path = out_dir / f"{base}_results.json"

        md_path = out_dir / f"{base}_report.md"

        try:

            data = asyncio.run(
                _run_one_drug(
                    drug, int(args.top_n), int(args.window), search_strategy=args.search_strategy
                )
            )

        except Exception as e:  # noqa: BLE001

            log.exception("Pipeline failed: %s", e)

            console.print(f"[red]Error:[/red] {e}")

            return 1

        try:

            write_json(data, json_path)

            write_markdown_report(data, md_path)

        except OSError as e:

            log.exception("Write failed: %s", e)

            console.print(f"[red]Output write error:[/red] {e}")

            return 1

        s = data.get("summary") or {}

        console.print(

            f"[green]Wrote JSON:[/green] {json_path}\n"

            f"[green]Wrote report:[/green] {md_path}\n"

            f"PubMed row count: {s.get('total_pubmed_articles', 0)}  |  "

            f"PMCID: {s.get('articles_with_pmcid', 0)}  |  "

            f"Full text OK: {s.get('articles_with_fulltext', 0)}  |  "

            f"Context hits: {s.get('articles_with_context', 0)}"

        )

        return 0



    # Excel batch

    xlsx: Path = args.input_xlsx  # type: ignore[assignment]

    if xlsx is None:

        console.print("[red]No input specified[/red]")

        return 2

    xlsx_path = resolve_excel_input_path(root, xlsx)

    sheet = _sheet_arg(args.sheet)

    if not xlsx_path.is_file():

        console.print(f"[red]File not found:[/red] {xlsx_path}")

        return 2

    try:

        rel_for_meta = str(xlsx_path.relative_to(root)).replace("\\", "/")

    except ValueError:

        rel_for_meta = str(xlsx_path).replace("\\", "/")



    try:

        batch = asyncio.run(

            _run_excel_batch(

                xlsx_path,

                top_n=int(args.top_n),

                window=int(args.window),

                sheet=sheet,

                name_column=args.name_column,

                max_drugs=_effective_max_drugs(int(args.max_drugs)),

                dedupe_by_name=not args.no_dedupe,

                search_strategy=args.search_strategy,

            )

        )

    except Exception as e:  # noqa: BLE001

        log.exception("Batch failed: %s", e)

        console.print(f"[red]Error:[/red] {e}")

        return 1



    batch["source_file"] = rel_for_meta

    base = re_safe_filename(xlsx_path.stem)

    json_path = out_dir / f"{base}_batch_results.json"

    md_path = out_dir / f"{base}_batch_report.md"

    try:

        write_json(batch, json_path)

        write_excel_batch_markdown(batch, md_path)

    except OSError as e:

        log.exception("Write failed: %s", e)

        console.print(f"[red]Output write error:[/red] {e}")

        return 1



    ok = sum(1 for r in (batch.get("results") or []) if r.get("ok"))

    console.print(

        f"[green]Wrote JSON:[/green] {json_path}\n"

        f"[green]Wrote batch report:[/green] {md_path}\n"

        f"Drugs run: {batch.get('drugs_run', 0)}  |  OK: {ok}  |  failed: {len(batch.get('results') or []) - ok}"

    )

    return 0





if __name__ == "__main__":

    sys.exit(main())


