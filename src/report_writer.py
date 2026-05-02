"""Write JSON and Markdown report files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def write_json(data: Any, output_path: str | Path) -> None:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


def _summary_lines(payload: dict[str, Any]) -> list[str]:
    s = payload.get("summary") or {}
    before_f = s.get("articles_before_evidence_filter")
    before_s = str(before_f) if before_f is not None else "—"
    af_ev = s.get("articles_after_evidence_filter")
    af_ev_s = str(af_ev) if af_ev is not None else "—"
    lines = [
        f"- Total PubMed articles (retrieved): {s.get('total_pubmed_articles', 0)}",
        f"- After relevance filter (before → after): {before_s} → {af_ev_s}",
    ]
    am = s.get("articles_after_min_relevance_tier")
    below = s.get("articles_below_min_relevance_tier")
    if am is not None:
        mrt = payload.get("min_relevance_tier") or ""
        suf = f" (`min_relevance_tier={mrt}`)" if mrt else ""
        extra = ""
        if below:
            extra = f" — below-tier excluded from export: {below}"
        lines.append(f"- After min relevance tier{suf}: exported {am}{extra}")
    lines.extend(
        [
            f"- Articles with PMCID: {s.get('articles_with_pmcid', 0)}",
            f"- Articles with fulltext: {s.get('articles_with_fulltext', 0)}",
            f"- Articles with QT-related context: {s.get('articles_with_context', 0)}",
        ]
    )
    return lines


def write_markdown_report(data: dict[str, Any], output_path: str | Path) -> None:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    lines.append("# PubMed / PMC Evidence Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(tz=timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## Drug")
    lines.append(str(data.get("drug_name", "")))
    lines.append("")

    # ChEMBL enrichment section
    chembl = data.get("chembl_enrichment")
    if chembl:
        lines.append("## ChEMBL Data")
        if chembl.get("chembl_id"):
            lines.append(f"- ChEMBL ID: {chembl['chembl_id']}")
        if chembl.get("pref_name"):
            lines.append(f"- Preferred name: {chembl['pref_name']}")
        if chembl.get("max_phase") is not None:
            lines.append(f"- Max phase: {chembl['max_phase']}")
        if chembl.get("withdrawn") is not None:
            lines.append(f"- Withdrawn: {chembl['withdrawn']}")
        variants = chembl.get("name_variants") or []
        if variants:
            lines.append(f"- Name variants used for search: {', '.join(variants)}")
        known_ids = chembl.get("known_pubmed_ids") or []
        if known_ids:
            lines.append(f"- Known PubMed IDs from ChEMBL: {', '.join(known_ids)}")
        herg = chembl.get("herg_activities") or []
        if herg:
            lines.append("")
            lines.append("### hERG Activities from ChEMBL")
            lines.append("| Type | Value | Units | pChEMBL | Target |")
            lines.append("|------|-------|-------|---------|--------|")
            for h in herg:
                lines.append(
                    f"| {h.get('standard_type','')} | {h.get('standard_value','')} | "
                    f"{h.get('standard_units','')} | {h.get('pchembl_value','')} | "
                    f"{h.get('target_pref_name','')} |"
                )
        lines.append("")

    lines.append("## Query")
    ss = str(data.get("search_strategy", "default"))
    lines.append(f"- Search strategy: `{ss}`")
    attempts = data.get("query_attempts") or []
    if ss == "layered" and attempts:
        lr = data.get("layered_round")
        if lr is not None:
            lines.append(f"- Layered round used: {lr}")
        lines.append("")
        lines.append("### Layered branches")
        for a in attempts:
            strat = a.get("strategy", "")
            rnd = a.get("round", "")
            tf = a.get("total_found", "")
            ret = a.get("returned", "")
            lines.append(f"- **{strat}** (round {rnd}): total_found={tf}, returned={ret}")
            qn = str(a.get("query", ""))
            if qn:
                lines.append("  ```")
                lines.extend("  " + ln for ln in qn.splitlines() or [qn])
                lines.append("  ```")
        lines.append("")
        lines.append("Merged query string (join of branches):")
        lines.append("```")
        lines.append(str(data.get("query", "")))
        lines.append("```")
    else:
        lines.append("```")
        lines.append(str(data.get("query", "")))
        lines.append("```")
    if data.get("note"):
        lines.append("")
        lines.append(f"> {data['note']}")
    lines.append("")

    lines.append("## Summary")
    if data.get("min_relevance_tier"):
        lines.append(f"- Min relevance tier (PubMed): `{data['min_relevance_tier']}`")
        lines.append("")
    for row in _summary_lines(data):
        lines.append(row)
    lines.append("")

    lines.append("## Articles")
    articles = data.get("articles") or []
    for i, a in enumerate(articles, 1):
        title = a.get("title") or "Untitled"
        lines.append("")
        lines.append(f"### {i}. {title}")
        lines.append(f"- PMID: {a.get('pmid', '')}")
        if a.get("tier"):
            lines.append(f"- PubMed retrieval tier: {a.get('tier')}")
        lines.append(f"- PMCID: {a.get('pmcid', '')}")
        lines.append(
            f"- PMC body available (是否拿到 PMC 正文): "
            f"{a.get('pmc_body_available', a.get('fulltext_available', False))}"
        )
        lines.append(f"- Journal: {a.get('journal', '')}")
        lines.append(f"- Year: {a.get('year', '')}")
        lines.append(f"- Matched terms: {', '.join(a.get('matched_terms') or [])}")
        if "drug_in_title_abstract" in a:
            lines.append(
                f"- Audit: drug_in_title_abstract={a.get('drug_in_title_abstract')}, "
                f"loose_match={a.get('loose_match')}, "
                f"clinical_QT_evidence={a.get('pipeline_clinical_qt_evidence')}, "
                f"mechanistic_evidence={a.get('pipeline_mechanistic_evidence')}, "
                f"phenotypic_evidence={a.get('pipeline_phenotypic_evidence')}"
            )
        if a.get("fulltext_error"):
            lines.append(f"- Fulltext: {a.get('fulltext_error')}")
        else:
            lines.append(f"- Fulltext available: {a.get('fulltext_available', False)}")

        abs_txt = a.get("abstract") or ""
        if abs_txt:
            lines.append("")
            lines.append("#### Abstract")
            lines.append(abs_txt)
        lines.append("")
        lines.append("#### Evidence Context")
        ctxs = a.get("contexts") or []
        if not ctxs:
            lines.append("_No matching context windows in abstract/full text._")
        else:
            for j, c in enumerate(ctxs, 1):
                sec = c.get("section", "")
                mt = c.get("matched_term", "")
                ev = c.get("evidence_type", "")
                body = c.get("context", "")
                lines.append(
                    f"{j}. [{sec}] term: {mt}  ·  {ev}\n   \n   {body}\n"
                )

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_excel_batch_markdown(payload: dict[str, Any], output_path: str | Path) -> None:
    """Compact Markdown table for a multi-drug run loaded from an Excel file."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# PubMed / PMC batch report (from spreadsheet)",
        "",
        f"**Source:** `{payload.get('source_file', '')}`",
        f"**Sheet:** `{payload.get('sheet', '')}` · **name column:** `{payload.get('name_column', '')}`",
        f"**Search strategy:** `{payload.get('search_strategy', 'default')}` · "
        f"**min_relevance_tier:** `{payload.get('min_relevance_tier', 'title')}`",
        f"**Drugs in file / run:** {payload.get('row_count', 0)} / {payload.get('drugs_run', 0)}",
        "",
        "| # | row | drug | PubChem | PubMed n | PMCID n | fulltext n | context art. | error |",
        "|---|-----|------|---------|------------|---------|------------|--------------|-------|",
    ]
    for i, item in enumerate(payload.get("results") or [], 1):
        j = item.get("row_index", "")
        name = (item.get("drug_name") or "").replace("|", " ")
        pid = str(item.get("pubchem_id", "")) if item.get("pubchem_id") is not None else ""
        if item.get("error"):
            lines.append(
                f"| {i} | {j} | {name} | {pid} | — | — | — | — | "
                f"{str(item.get('error'))[:80]} |"
            )
            continue
        r = item.get("result") or {}
        s = r.get("summary") or {}
        err = "—"
        lines.append(
            f"| {i} | {j} | {name} | {pid} | "
            f"{s.get('total_pubmed_articles', 0)} | {s.get('articles_with_pmcid', 0)} | "
            f"{s.get('articles_with_fulltext', 0)} | {s.get('articles_with_context', 0)} | "
            f"{err} |"
        )
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
