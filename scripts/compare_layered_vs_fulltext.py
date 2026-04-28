"""
Compare layered PubMed query *intent* vs what the pipeline actually surfaced as evidence.

Input: a JSON file from ``python -m src.main`` (single-drug ``*_results.json`` or batch ``*_batch_results.json``).

Each exported article has no raw ``sections[]`` (stripped on write). This script uses:
  title + abstract + extracted ``contexts[].context`` snippets as the searchable blob.
That is a lower bound on full text; treat gaps as "needs re-export with sections" if required.

Usage (from project root)::

    python scripts/compare_layered_vs_fulltext.py outputs/your_batch_results.json
    python scripts/compare_layered_vs_fulltext.py outputs/your_batch_results.json -o outputs/layered_audit.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any


# Mirrors layered branch A (hERG / IKr family + block terms) — loose regex vs PubMed query syntax.
_HERG_FAMILY = re.compile(r"hERG|KCNH2|\bIKr\b|ether[\s-]?a[\s-]?go[\s-]?go", re.I)
_BLOCK_STRICT = re.compile(
    r"\b(?:block|blocker|blockade|inhibit|inhibitor|inhibition)\w*\b",
    re.I,
)
_BLOCK_BROAD = re.compile(
    r"\b(?:block|blocker|blockade|inhibit|inhibitor|inhibition|current|channel)\w*\b",
    re.I,
)

# Mirrors layered branch B scoped to title/abstract in PubMed (validate on TA only).
_QT_STRICT_TA = re.compile(
    r"QT\s*c?\s*prolongation|long\s+QT|prolonged\s+QT",
    re.I,
)
_QT_BROAD_TA = re.compile(
    r"QT\s*c?\s*prolongation|long\s+QT|prolonged\s+QT|"
    r"torsades?\s+de\s+pointes|\bTdP\b|proarrhythm",
    re.I,
)


def _ta_blob(art: dict[str, Any]) -> str:
    return f"{art.get('title') or ''}\n{art.get('abstract') or ''}"


def _evidence_blob(art: dict[str, Any], *, include_contexts: bool) -> str:
    parts = [art.get("title") or "", art.get("abstract") or ""]
    if include_contexts:
        for c in art.get("contexts") or []:
            parts.append(str(c.get("context") or ""))
    return "\n".join(p for p in parts if p)


def _herg_axis(text: str, *, broad_block: bool) -> bool:
    if not _HERG_FAMILY.search(text):
        return False
    blk = _BLOCK_BROAD if broad_block else _BLOCK_STRICT
    return bool(blk.search(text))


def _qt_in_ta(title_abstract: str, *, broad: bool) -> bool:
    pat = _QT_BROAD_TA if broad else _QT_STRICT_TA
    return bool(pat.search(title_abstract))


def _drug_in_ta(drug: str, title_abstract: str) -> bool:
    drug = (drug or "").strip()
    if not drug:
        return False
    return bool(re.search(re.escape(drug), title_abstract, flags=re.I))


def _pipeline_evidence_flags(art: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    clinical = False
    mech = False
    types: list[str] = []
    for c in art.get("contexts") or []:
        ev = str(c.get("evidence_type") or "")
        if ev:
            types.append(ev)
        if ev == "clinical_or_direct_qt_evidence":
            clinical = True
        if ev == "mechanistic_herg_ikr_evidence":
            mech = True
    return clinical, mech, sorted(set(types))


def _correspondence(*, intent_or_broad: bool, has_context: bool) -> str:
    if intent_or_broad and has_context:
        return "ok_intent_and_context"
    if intent_or_broad and not has_context:
        return "gap_no_keyword_context"
    if not intent_or_broad and has_context:
        return "gap_context_not_matching_layered_or"
    return "weak_no_intent_no_context"


def _iter_jobs(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(drug_name, result_dict) pairs."""
    out: list[tuple[str, dict[str, Any]]] = []
    if "results" in payload:
        for item in payload.get("results") or []:
            if not item.get("ok") or not item.get("result"):
                continue
            r = item["result"]
            drug = str(item.get("drug_name") or r.get("drug_name") or "")
            out.append((drug, r))
        return out
    if "articles" in payload:
        drug = str(payload.get("drug_name") or "")
        out.append((drug, payload))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit layered PubMed intent vs pipeline keyword evidence.")
    ap.add_argument("json_path", type=Path, help="*_results.json or *_batch_results.json")
    ap.add_argument(
        "-o",
        "--output-csv",
        type=Path,
        default=None,
        help="Write per-article CSV (default: alongside input with _layered_audit suffix)",
    )
    args = ap.parse_args()
    path = args.json_path.resolve()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    with path.open(encoding="utf-8") as f:
        payload = json.load(f)

    jobs = _iter_jobs(payload)
    if not jobs:
        print("No results to audit (empty batch or wrong JSON shape).", file=sys.stderr)
        return 1

    out_csv = args.output_csv
    if out_csv is None:
        out_csv = path.with_name(path.stem + "_layered_audit.csv")

    rows: list[dict[str, Any]] = []
    summary: dict[str, int] = {}

    for drug, result in jobs:
        strat = str(result.get("search_strategy") or "")
        if strat != "layered":
            print(
                f"[warn] drug={drug!r} search_strategy={strat!r} (expected 'layered'); "
                "intent flags are still computed but PubMed strategy differs.",
                file=sys.stderr,
            )

        ta = _ta_blob
        for art in result.get("articles") or []:
            title_abstract = ta(art)
            blob_loose = _evidence_blob(art, include_contexts=True)

            drug_ok_ta = _drug_in_ta(drug, title_abstract)
            herg_s = _herg_axis(blob_loose, broad_block=False)
            herg_b = _herg_axis(blob_loose, broad_block=True)
            qt_ta_s = _qt_in_ta(title_abstract, broad=False)
            qt_ta_b = _qt_in_ta(title_abstract, broad=True)

            # Layered PubMed returns PMID if either branch matches; approximate OR on our blob.
            intent_strict = (herg_s or qt_ta_s) and drug_ok_ta
            intent_broad = (herg_b or qt_ta_b) and drug_ok_ta

            has_ctx = bool(art.get("contexts"))
            p_clin, p_mech, p_types = _pipeline_evidence_flags(art)
            corr = _correspondence(intent_or_broad=intent_broad, has_context=has_ctx)
            summary[corr] = summary.get(corr, 0) + 1

            rows.append(
                {
                    "drug_name": drug,
                    "pmid": art.get("pmid", ""),
                    "drug_in_title_abstract": drug_ok_ta,
                    "layered_herg_axis_strict": herg_s,
                    "layered_herg_axis_broad_block": herg_b,
                    "layered_qt_in_ta_strict": qt_ta_s,
                    "layered_qt_in_ta_broad": qt_ta_b,
                    "intent_or_strict": intent_strict,
                    "intent_or_broad": intent_broad,
                    "has_keyword_context": has_ctx,
                    "pipeline_clinical_qt_evidence": p_clin,
                    "pipeline_mechanistic_evidence": p_mech,
                    "pipeline_evidence_types": ";".join(p_types),
                    "correspondence": corr,
                    "fulltext_available": bool(art.get("fulltext_available")),
                }
            )

    fieldnames = list(rows[0].keys()) if rows else []
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_csv}")
    print("correspondence counts:")
    for k in sorted(summary.keys()):
        print(f"  {k}: {summary[k]}")
    print()
    print(
        "Legend: ok_intent_and_context ≈ layered OR + drug visible in TA, and pipeline found "
        "keyword windows; gap_no_keyword_context = text roughly matches intent but no qt_terms hit "
        "in exported snippets; gap_context_not_matching_layered_or = pipeline hit terms but "
        "strict/broad intent OR failed on title/abstract+blob (check regex vs MeSH-only hits)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
