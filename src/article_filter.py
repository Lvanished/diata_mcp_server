"""
Filters articles to those with a PMCID and normalizes fields for downstream steps.
"""

from __future__ import annotations

import re
from typing import Any


def _pipeline_evidence_flags(article: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Clinical QT, mechanistic hERG/IKr, phenotypic hits from ``contexts[].evidence_type``."""
    clinical = False
    mech = False
    pheno = False
    for c in article.get("contexts") or []:
        ev = str(c.get("evidence_type") or "")
        if ev == "clinical_or_direct_qt_evidence":
            clinical = True
        elif ev == "mechanistic_herg_ikr_evidence":
            mech = True
        elif ev == "phenotypic_repolarization_evidence":
            pheno = True
    return clinical, mech, pheno


def loose_match_strength(
    article: dict[str, Any],
    drug_name: str,
    base_name: str | None,
) -> str:
    """Categorize how tightly the inventory/base name appears in TIAB (for salt-rescued hits)."""
    title = (article.get("title") or "").lower()
    abstract = (article.get("abstract") or "").lower()
    dn = (drug_name or "").strip().lower()
    names = [dn]
    bn = (base_name or "").strip().lower()
    if bn and bn != dn:
        names.append(bn)
    seen: set[str] = set()
    uniq: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    if not uniq:
        return "fulltext_only"
    in_title = any(n in title for n in uniq)
    in_abstract = any(n in abstract for n in uniq)
    if in_title:
        return "title"
    if in_abstract:
        return "abstract"
    return "fulltext_only"


def drug_in_title_abstract(drug_name: str, article: dict[str, Any]) -> bool:
    drug = (drug_name or "").strip()
    if not drug:
        return False
    ta = f"{article.get('title') or ''}\n{article.get('abstract') or ''}"
    return bool(re.search(re.escape(drug), ta, flags=re.I))


def enrich_article_evidence_metadata(
    article: dict[str, Any],
    drug_name: str,
    *,
    loose_match: bool = False,
    base_name: str | None = None,
) -> None:
    """Add pipeline audit fields expected by exports and evidence filtering."""
    article["drug_in_title_abstract"] = drug_in_title_abstract(drug_name, article)
    article["has_keyword_context"] = bool(article.get("contexts"))
    p_clin, p_mech, p_pheno = _pipeline_evidence_flags(article)
    article["pipeline_clinical_qt_evidence"] = p_clin
    article["pipeline_mechanistic_evidence"] = p_mech
    article["pipeline_phenotypic_evidence"] = p_pheno
    article["pipeline_evidence_types"] = sorted(
        {
            str(c.get("evidence_type"))
            for c in article.get("contexts") or []
            if c.get("evidence_type")
        }
    )
    article["loose_match"] = loose_match
    if loose_match:
        article["loose_match_strength"] = loose_match_strength(article, drug_name, base_name)
    else:
        article.pop("loose_match_strength", None)


def _is_likely_relevant(article: dict[str, Any]) -> bool:
    """
    Drop articles with neither keyword context nor any pipeline evidence label.

    Filters junk PubMed hits (e.g. fungal ERG1 matching ``hERG``) that surface no QT keyword windows.
    """
    if article.get("has_keyword_context"):
        return True
    if (
        article.get("pipeline_clinical_qt_evidence")
        or article.get("pipeline_mechanistic_evidence")
        or article.get("pipeline_phenotypic_evidence")
    ):
        return True
    return False


def filter_articles(articles: list[dict[str, Any]], drug_name: str) -> list[dict[str, Any]]:
    """
    Post-filter using ``_is_likely_relevant``. Requires ``enrich_article_evidence_metadata`` first.
    ``drug_name`` is accepted for API symmetry.
    """
    del drug_name
    return [a for a in articles if _is_likely_relevant(a)]


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
