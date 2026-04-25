"""
Filters articles to those with a PMCID and normalizes fields for downstream steps.
"""

from __future__ import annotations

import re
from typing import Any


def _as_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, (int, float)):
        return str(x)
    return str(x).strip()


def _find_pmcid(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str) and re.search(r"PMC\d+", obj, re.I):
        m = re.search(r"(PMC\d+)", obj, re.I)
        return m.group(1) if m else obj.strip()
    if isinstance(obj, dict):
        for k in (
            "pmcid",
            "pmcId",
            "pmc_id",
            "PMCID",
        ):
            v = obj.get(k)
            if v:
                return _normalize_pmcid(_as_str(v))
        ids = obj.get("articleids") or obj.get("ids") or obj.get("identifiers")
        if isinstance(ids, list):
            for it in ids:
                if isinstance(it, dict):
                    t = _as_str(it.get("idtype") or it.get("type") or it.get("IdType"))
                    val = it.get("value") or it.get("id") or it.get("Value")
                    if val and "pmc" in t.lower():
                        return _normalize_pmcid(_as_str(val))
                elif isinstance(it, str) and "pmc" in it.lower():
                    return _normalize_pmcid(it)
    return ""


def _normalize_pmcid(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"PMC?(\d+)", s, re.I)
    if m:
        return f"PMC{m.group(1)}"
    if re.fullmatch(r"\d+", s):
        return f"PMC{s}"
    return s


def _mesh_descriptor_list(mesh: Any) -> list[str]:
    out: list[str] = []
    if not mesh:
        return out
    if isinstance(mesh, list):
        for m in mesh:
            if isinstance(m, dict) and m.get("descriptorName"):
                out.append(_as_str(m["descriptorName"]))
            elif isinstance(m, str):
                out.append(m)
    return out


def _year_from_article(a: dict[str, Any]) -> str:
    ji = a.get("journalInfo") or {}
    pd = ji.get("publicationDate") or {}
    y = pd.get("year")
    if y:
        return _as_str(y)
    ads = a.get("articleDates") or []
    if isinstance(ads, list) and ads:
        y2 = ads[0].get("year") if isinstance(ads[0], dict) else None
        if y2:
            return _as_str(y2)
    return ""


def _normalize_fetched_article(a: dict[str, Any]) -> dict[str, Any]:
    pmid = _as_str(a.get("pmid"))
    pmc = _find_pmcid(a)
    if not pmc and a.get("pmcId"):
        pmc = _normalize_pmcid(_as_str(a.get("pmcId")))
    if not pmc and a.get("pmcid"):
        pmc = _normalize_pmcid(_as_str(a.get("pmcid")))

    ji = a.get("journalInfo") or {}
    title = _as_str(a.get("title"))
    abstract = _as_str(a.get("abstractText") or a.get("abstract"))
    journal = _as_str(ji.get("title") or a.get("journal") or a.get("source"))
    mesh = _mesh_descriptor_list(a.get("meshTerms"))

    return {
        "pmid": pmid,
        "pmcid": pmc,
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "year": _year_from_article(a) if a else "",
        "mesh_terms": mesh,
    }


def filter_articles_with_pmcid(articles: list) -> list[dict[str, Any]]:
    """
    Keep only records that resolve to a PMCID. `articles` may be raw MCP `articles` dicts.
    Output uses unified keys: pmid, pmcid, title, abstract, journal, year, mesh_terms.
    """
    out: list[dict[str, Any]] = []
    for raw in articles:
        if not isinstance(raw, dict):
            continue
        norm = _normalize_fetched_article(raw)
        if norm["pmcid"]:
            out.append(norm)
    return out


def normalize_any_article(articles: list) -> list[dict[str, Any]]:
    """Normalize metadata for all articles (used when no PMCID rows exist)."""
    return [_normalize_fetched_article(a) for a in articles if isinstance(a, dict)]
