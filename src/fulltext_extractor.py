"""
Fetch PMC open full text per article and normalize sections for context extraction.
"""

from __future__ import annotations

import re
import traceback
from typing import Any

from .mcp_client import PubMedMCPClient, parse_pmcid_numeric


def _flatten_section(sec: dict[str, Any], parent_title: str = "") -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    title = (sec.get("title") or sec.get("label") or "").strip()
    if parent_title and title:
        st = f"{parent_title} / {title}"
    elif title:
        st = title
    else:
        st = parent_title or "Body"

    text = sec.get("text") or ""
    if isinstance(text, str) and text.strip():
        out.append({"section_title": st, "text": text})

    for sub in sec.get("subsections") or []:
        if isinstance(sub, dict):
            out.extend(_flatten_section(sub, st))
    return out


def _merge_article_sections(sections: list) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for sec in sections or []:
        if isinstance(sec, dict):
            rows.extend(_flatten_section(sec))
    return rows


def _normalize_pmc_id_for_request(pmcid: str) -> str:
    s = pmcid.strip()
    s = re.sub(r"^PMC", "", s, flags=re.I)
    return s


async def fetch_fulltext_for_articles(
    client: PubMedMCPClient,
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    For each input article (with pmcid), call pubmed_fetch_fulltext in batches of 10.
    Enrich with sections[] for context_extractor; on failure, fulltext_available=False.
    """
    if not articles:
        return []

    pmcids = [_normalize_pmc_id_for_request(a.get("pmcid") or "") for a in articles if a.get("pmcid")]
    pmcids = [p for p in pmcids if p]

    fetched: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pmcids), 10):
        batch = pmcids[i : i + 10]
        pmcid_args = [f"PMC{b}" if not str(b).upper().startswith("PMC") else b for b in batch]
        try:
            res = await client.fetch_fulltext_pmc(pmcid_args)
        except Exception as e:
            for pid in batch:
                fetched[parse_pmcid_numeric(pid)] = {
                    "error": str(e),
                    "trace": traceback.format_exc()[-2000:],
                }
            continue

        if not isinstance(res, dict):
            continue
        arts = res.get("articles")
        if not isinstance(arts, list):
            arts = []

        for fa in arts:
            if not isinstance(fa, dict):
                continue
            pid = fa.get("pmcId") or fa.get("pmcid") or ""
            key = parse_pmcid_numeric(str(pid))
            fetched[key] = fa

    out: list[dict[str, Any]] = []
    for base in articles:
        p = base.get("pmcid") or ""
        key = parse_pmcid_numeric(p)
        item = {**base}
        if not p:
            item["fulltext_available"] = False
            item["error"] = "No PMCID"
            item["sections"] = []
            out.append(item)
            continue

        fa = fetched.get(key)
        if not fa:
            item["fulltext_available"] = False
            item["error"] = "PMC open fulltext not available or fetch returned no data"
            item["sections"] = []
            out.append(item)
            continue

        if fa.get("error"):
            item["fulltext_available"] = False
            item["error"] = str(fa.get("error"))
            item["sections"] = []
            out.append(item)
            continue

        secs = _merge_article_sections(fa.get("sections"))
        item["fulltext_available"] = bool(secs)
        if not item["fulltext_available"]:
            item["error"] = "PMC open fulltext not available: empty body sections (paywalled or not in PMC OA subset)"
        else:
            item["error"] = None
        item["sections"] = [
            {"section_title": s["section_title"], "text": s["text"]} for s in secs
        ]
        out.append(item)

    return out
