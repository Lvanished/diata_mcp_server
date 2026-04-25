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
    return [
        f"- Total PubMed articles: {s.get('total_pubmed_articles', 0)}",
        f"- Articles with PMCID: {s.get('articles_with_pmcid', 0)}",
        f"- Articles with fulltext: {s.get('articles_with_fulltext', 0)}",
        f"- Articles with QT-related context: {s.get('articles_with_context', 0)}",
    ]


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

    lines.append("## Query")
    lines.append("```")
    lines.append(str(data.get("query", "")))
    lines.append("```")
    if data.get("note"):
        lines.append("")
        lines.append(f"> {data['note']}")
    lines.append("")

    lines.append("## Summary")
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
        lines.append(f"- PMCID: {a.get('pmcid', '')}")
        lines.append(f"- Journal: {a.get('journal', '')}")
        lines.append(f"- Year: {a.get('year', '')}")
        lines.append(f"- Matched terms: {', '.join(a.get('matched_terms') or [])}")
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
                f"| {i} | {j} | {name} | {pid} | — | — | — | — | {str(item.get('error'))[:80]} |"
            )
            continue
        r = item.get("result") or {}
        s = r.get("summary") or {}
        err = "—"
        lines.append(
            f"| {i} | {j} | {name} | {pid} | "
            f"{s.get('total_pubmed_articles', 0)} | {s.get('articles_with_pmcid', 0)} | "
            f"{s.get('articles_with_fulltext', 0)} | {s.get('articles_with_context', 0)} | {err} |"
        )
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
