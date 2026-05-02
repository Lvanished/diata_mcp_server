"""
Extract keyword windows from abstract and full text; assign evidence_type per hit.

Evidence type quotas ensure structural inference contexts are not squeezed out
by repetitive clinical/mechanistic terms.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .qt_vocabulary import CLASSIFIER_CLINICAL, CLASSIFIER_MECH, CLASSIFIER_PHENOTYPIC, CLASSIFIER_STRUCTURAL


# Priority order: longer / more specific phrases first (so "QT prolongation" wins over "QT" where both match).
def _sort_terms(terms: Iterable[str]) -> list[str]:
    return sorted({t for t in terms if t}, key=lambda s: (-len(s), s.lower()))


def _classify_evidence(matched_cfg_term: str) -> str:
    """
    Classify by the **configured** qt_keywords term that matched, not the raw regex span.

    Vocabulary is sourced from qt_vocabulary.py so PubMed query and classifier stay aligned.
    """
    t = (matched_cfg_term or "").strip()
    if t in CLASSIFIER_CLINICAL:
        return "clinical_or_direct_qt_evidence"
    if t in CLASSIFIER_MECH:
        return "mechanistic_herg_ikr_evidence"
    if t in CLASSIFIER_PHENOTYPIC:
        return "phenotypic_repolarization_evidence"
    if t in CLASSIFIER_STRUCTURAL:
        return "structural_inference_evidence"
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

    Per-type quotas prevent structural inference contexts from being squeezed out:
      - clinical: 20
      - mechanistic: 15
      - phenotypic: 15
      - structural: 20
      - max total: 80
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

    # Per-type quotas
    type_counts: dict[str, int] = {}
    max_per_type = {
        "clinical_or_direct_qt_evidence": 20,
        "mechanistic_herg_ikr_evidence": 15,
        "phenotypic_repolarization_evidence": 15,
        "structural_inference_evidence": 20,
    }
    max_abstract_total = 25
    max_total = 80
    abstract_count = 0

    def _add_context(source: str, section: str, display_term: str, snippet: str, ev_type: str) -> bool:
        """Try to add a context; return False if quota exceeded."""
        k = _dedup_key(source, section, display_term, snippet)
        if k in seen:
            return False
        seen.add(k)

        if len(contexts) >= max_total:
            return False

        # Check per-type quota
        type_counts[ev_type] = type_counts.get(ev_type, 0) + 1
        type_max = max_per_type.get(ev_type, 20)
        if type_counts[ev_type] > type_max:
            return False

        contexts.append({
            "source": source,
            "section": section,
            "matched_term": display_term,
            "context": snippet,
            "evidence_type": ev_type,
        })
        if display_term not in matched_terms:
            matched_terms.append(display_term)
        return True

    # Abstract
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
                ev_type = _classify_evidence(display_term)
                if _add_context("abstract", "Abstract", display_term, snippet, ev_type):
                    abstract_count += 1
                if abstract_count >= max_abstract_total:
                    break

    # Fulltext sections
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
                    ev_type = _classify_evidence(display_term)
                    _add_context("fulltext", stitle, display_term, snippet, ev_type)
                    if len(contexts) >= max_total:
                        return {"matched_terms": _sort_terms(matched_terms), "contexts": contexts}

    return {"matched_terms": _sort_terms(matched_terms), "contexts": contexts}