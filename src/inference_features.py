"""Structured inference-feature extraction for QT/hERG articles."""

from __future__ import annotations

from typing import Any

from .qt_vocabulary import INFERENCE_FEATURES


def _looks_like_numeric_token(s: str) -> bool:
    try:
        float(s.replace(",", "").strip())
        return True
    except ValueError:
        return False


def _numeric_tuple_from_groups(groups: tuple[str | None, ...]) -> tuple[float, str | None] | None:
    """Pick value + optional unit from alternation patterns (only one branch typically fires)."""
    nonempty = [g for g in groups if g is not None]
    if not nonempty:
        return None
    value: float | None = None
    value_idx = -1
    for i, raw in enumerate(nonempty):
        token = raw.strip().replace(",", "")
        try:
            value = float(token)
            value_idx = i
            break
        except ValueError:
            continue
    if value is None:
        return None
    unit: str | None = None
    for raw in nonempty[value_idx + 1 :]:
        tok = raw.strip()
        if tok and not _looks_like_numeric_token(tok):
            unit = tok
            break
    return (value, unit)


def extract_inference_features(
    title: str,
    abstract: str,
    full_text: str | None = None,
) -> dict[str, Any]:
    """
    Run all INFERENCE_FEATURES against title+abstract (and full_text if provided).

    Returns a flat dict:
      - bool features → True iff the pattern matched at least once.
      - numeric features → a list of (value: float, unit: str | None) tuples.
    Missing features are absent from the dict.
    """
    text_parts = [title or "", abstract or ""]
    if full_text:
        text_parts.append(full_text)
    text = "\n".join(text_parts)

    out: dict[str, Any] = {}
    for feat in INFERENCE_FEATURES:
        matches = list(feat.pattern.finditer(text))
        if not matches:
            continue
        if feat.kind == "bool":
            out[feat.name] = True
        elif feat.kind == "numeric":
            extracted: list[tuple[float, str | None]] = []
            for m in matches:
                parsed = _numeric_tuple_from_groups(m.groups())
                if parsed is not None:
                    extracted.append(parsed)
            if extracted:
                out[feat.name] = extracted
    return out


def concat_sections_text(sections: list[object]) -> str:
    """Join PMC section bodies for inference / subtype scans."""
    parts: list[str] = []
    for sec in sections or []:
        if isinstance(sec, dict):
            t = sec.get("text") or ""
            if isinstance(t, str) and t.strip():
                parts.append(t)
    return "\n".join(parts)
