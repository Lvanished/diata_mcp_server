"""
Extract keyword windows from abstract and full text; assign evidence_type per hit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .qt_vocabulary import CLASSIFIER_CLINICAL, CLASSIFIER_MECH, CLASSIFIER_PHENOTYPIC


# Priority order: longer / more specific phrases first (so "QT prolongation" wins over "QT" where both match).
def _sort_terms(terms: Iterable[str]) -> list[str]:
    return sorted({t for t in terms if t}, key=lambda s: (-len(s), s.lower()))


def _classify_evidence(matched_cfg_term: str) -> str:
    """
    Classify by the **configured** qt_keywords term that matched, not the raw regex span.

    Vocabulary is sourced from qt_vocabulary.py so PubMed query and classifier stay aligned.
    Backwards-compatible: emits the same 4 labels as before.
    """
    t = (matched_cfg_term or "").strip()
    if t in CLASSIFIER_CLINICAL:
        return "clinical_or_direct_qt_evidence"
    if t in CLASSIFIER_MECH:
        return "mechanistic_herg_ikr_evidence"
    if t in CLASSIFIER_PHENOTYPIC:
        return "phenotypic_repolarization_evidence"
    return "uncertain_relevance"


def _build_pattern(term: str) -> re.Pattern[str]:
    """
    Case-insensitive search. For short tokens (e.g. QT, APD) use word boundaries when safe.
    """
    esc = re.escape(term.strip())
    if len(term.strip()) <= 3 and re.match(r"^[A-Za-z0-9]+$", term.strip()):
        return re.compile(rf"(?<!\w){esc}(?!\w)", re.IGNORECASE)
    return re.compile(esc, re.IGNORECASE)


def _dedup_key(
    source: str,
    section: str,
    matched: str,
    ctx: str,
) -> tuple[str, str, str, str]:
    c = re.sub(r"\s+", " ", ctx).strip()[:2000]
    return (source, section, matched, c)


def extract_keyword_contexts(
    article: dict[str, Any],
    keywords: list[str],
    window: int = 500,
) -> dict[str, Any]:
    """
    Returns dict with:
      matched_terms: unique matched keywords (as in config, best-effort)
      contexts: list of {source, section, matched_term, context, evidence_type}
    Max 20 contexts per article, de-duplicated.
    """
    terms = _sort_terms(keywords)
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for t in terms:
        try:
            patterns.append((t, _build_pattern(t)))
        except re.error:
            continue

    contexts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    matched_terms: list[str] = []
    abstract_count = 0
    max_abstract = 20
    max_total = 40

    abstract = article.get("abstract") or ""
    if isinstance(abstract, str) and abstract:
        for display_term, pat in patterns:
            for m in pat.finditer(abstract):
                start, end = m.span()
                lo = max(0, start - window)
                hi = min(len(abstract), end + window)
                snippet = abstract[lo:hi]
                if len(snippet) < 10:
                    continue
                k = _dedup_key("abstract", "Abstract", display_term, snippet)
                if k in seen:
                    continue
                seen.add(k)
                mt = m.group(0)
                if display_term not in matched_terms and mt:
                    matched_terms.append(display_term)
                contexts.append(
                    {
                        "source": "abstract",
                        "section": "Abstract",
                        "matched_term": display_term,
                        "context": snippet,
                        "evidence_type": _classify_evidence(display_term),
                    }
                )
                abstract_count += 1
                if abstract_count >= max_abstract:
                    break

    sections = article.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            stitle = (sec.get("section_title") or sec.get("title") or "Body").strip() or "Body"
            text = sec.get("text") or ""
            if not isinstance(text, str) or not text:
                continue
            for display_term, pat in patterns:
                for m in pat.finditer(text):
                    start, end = m.span()
                    lo = max(0, start - window)
                    hi = min(len(text), end + window)
                    snippet = text[lo:hi]
                    if len(snippet) < 10:
                        continue
                    k = _dedup_key("fulltext", stitle, display_term, snippet)
                    if k in seen:
                        continue
                    seen.add(k)
                    mt = m.group(0)
                    if display_term not in matched_terms and mt:
                        matched_terms.append(display_term)
                    contexts.append(
                        {
                            "source": "fulltext",
                            "section": stitle,
                            "matched_term": display_term,
                            "context": snippet,
                            "evidence_type": _classify_evidence(display_term),
                        }
                    )
                    if len(contexts) >= max_total:
                        return {
                            "matched_terms": _sort_terms(matched_terms),
                            "contexts": contexts,
                        }

    return {"matched_terms": _sort_terms(matched_terms), "contexts": contexts}
