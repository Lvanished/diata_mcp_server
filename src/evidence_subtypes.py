"""Subtype classification for articles that would otherwise fall into uncertain_relevance."""

from __future__ import annotations

from .qt_vocabulary import EVIDENCE_SUBTYPES


def classify_evidence_subtypes(
    title: str,
    abstract: str,
    full_text: str | None = None,
) -> list[str]:
    """
    Return all subtype names whose patterns match the combined text.

    Uses title+abstract by default; adds full_text when provided.
    Multiple subtypes may match — return them all in declaration order.
    """
    text_parts = [title or "", abstract or ""]
    if full_text:
        text_parts.append(full_text)
    text = "\n".join(text_parts)
    return [s.name for s in EVIDENCE_SUBTYPES if s.pattern.search(text)]
