"""
Builds PubMed query strings for QT / hERG drug-safety lookups.

Simplified strategy: drug-name AND search-fragment, no tiering.

Drug name is searched in All Fields (covers MeSH, Supplementary Concept,
Substance Name, Title, Abstract). Salt suffixes are stripped by default.

Search-fragment structure — every branch is AND-constrained to avoid noise:

  1. herg_mechanistic:  (hERG/KCNH2/IKr) AND (block/assay/affinity/IC50/...)
  2. qt_clinical:       QT prolongation / QTc / long QT / drug-induced / TdP / proarrhythmic
  3. qt_ecg:            (QT OR QTc) AND (ECG OR electrocardiogram)
  4. safety_qt:         (withdrawn / adverse event / safety concern / ...) AND (QT OR hERG OR proarrhythmic)
  5. preclinical_qt:    (preclinical / in vitro / patch clamp) AND (hERG OR QT OR KCNH2)
  6. regulatory:        CiPA / ICH S7B / E14  (inherently QT-specific, stand-alone)
  7. phenotypic:        repolarization / APD / FPD

Fallbacks if 0 hits: try salt-stripped name, then just the drug name alone.
"""

from __future__ import annotations

import re

from .qt_vocabulary import (
    CLINICAL_QT_TERMS,
    MECH_HERG_TERMS,
    PHENOTYPIC_TERMS,
    quote_for_pubmed,
    or_join_bare,
)

__all__ = [
    "strip_salt_suffix",
    "build_simple_query",
    "iter_simple_fallbacks",
]

# ---------------------------------------------------------------------------
# name normalization
# ---------------------------------------------------------------------------

_SALT = re.compile(
    r"""(?ix)
    \s*(
        hydrochloride|HCl|hcl|mesylate|mesilate|maleate|sodium|sulfate|sulphate|
        bitartrate|acetate|phosphate|tartrate|fumarate|besylate|bromide|iodide|nitrate|
        citrate|lactate|oxalate|succinate|hydrate|monohydrate|dihydrate|anhydrous
    )\s*$
    """
)


def _normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def strip_salt_suffix(drug_name: str) -> str:
    """Strip the trailing salt suffix from a drug name."""
    s = _normalize_whitespace(drug_name)
    s = _SALT.sub("", s).strip()
    return _normalize_whitespace(s)


def _clean_drug_name(drug_name: str) -> str:
    """Canonical form: whitespace-normalized AND salt-stripped."""
    return strip_salt_suffix(drug_name)


# ---------------------------------------------------------------------------
# search fragment: AND-constrained branches OR'd together
# ---------------------------------------------------------------------------

# hERG action terms — covers both "blocks hERG" (clinical) and "hERG assay/IC50" (preclinical)
HERG_ACTION_TERMS: list[str] = [
    "block", "blocker", "inhibit", "inhibitor", "inhibition",
    "assay", "affinity", "IC50", "binding",
]

# QT clinical terms — inherently QT-specific, stand-alone
QT_CLINICAL_TERMS: list[str] = list(CLINICAL_QT_TERMS) + [
    "QTc", "drug-induced", "proarrhythmic", "proarrhythmia",
    "cardiac safety",
]

# ECG terms — must be AND-combined with QT terms to avoid noise
ECG_TERMS: list[str] = ["ECG", "electrocardiogram"]

# Safety/withdrawal terms — must be AND-combined with QT/hERG to avoid noise
SAFETY_TERMS: list[str] = [
    "withdrawn", "withdrawal", "discontinued", "suspended",
    "safety concern", "adverse event", "adverse reaction",
]

# Preclinical/method terms — must be AND-combined with hERG/QT to avoid noise
PRECLINICAL_TERMS: list[str] = [
    "preclinical", "in vitro", "patch clamp",
]

# Regulatory terms — inherently QT-specific, can stand alone
REGULATORY_TERMS: list[str] = ["CiPA", "ICH S7B", "E14"]

# QT anchoring terms — used to AND-constrain safety/preclinical branches
QT_ANCHOR_TERMS: list[str] = ["QT", "QTc", "hERG", "KCNH2", "proarrhythmic"]


def _build_search_fragment() -> str:
    # 1. hERG mechanistic: channel AND action
    herg_mechanistic = f"{or_join_bare(MECH_HERG_TERMS)} AND {or_join_bare(HERG_ACTION_TERMS)}"

    # 2. QT clinical: inherently QT-specific, stand-alone
    qt_clinical = or_join_bare(QT_CLINICAL_TERMS)

    # 3. QT + ECG: (QT OR QTc) AND (ECG OR electrocardiogram)
    qt_ecg = f"(QT OR QTc) AND {or_join_bare(ECG_TERMS)}"

    # 4. safety + QT: (withdrawn / adverse event / ...) AND (QT / hERG / proarrhythmic)
    safety_qt = f"{or_join_bare(SAFETY_TERMS)} AND {or_join_bare(QT_ANCHOR_TERMS)}"

    # 5. preclinical + QT: (preclinical / in vitro / patch clamp) AND (hERG / QT / KCNH2)
    preclinical_qt = f"{or_join_bare(PRECLINICAL_TERMS)} AND {or_join_bare(QT_ANCHOR_TERMS)}"

    # 6. regulatory: inherently QT-specific, stand-alone
    regulatory = or_join_bare(REGULATORY_TERMS)

    # 7. phenotypic: repolarization/APD/FPD
    phenotypic = or_join_bare(PHENOTYPIC_TERMS)

    return f"({herg_mechanistic} OR {qt_clinical} OR {qt_ecg} OR {safety_qt} OR {preclinical_qt} OR {regulatory} OR {phenotypic})"


# ---------------------------------------------------------------------------
# simple query builder
# ---------------------------------------------------------------------------

def build_simple_query(drug_name: str) -> str:
    """drug-name[tiab] AND search-fragment. [tiab] ensures the molecule appears in title or abstract."""
    drug = _clean_drug_name(drug_name)
    if not drug:
        raise ValueError("drug_name is empty after cleaning")
    return f"{quote_for_pubmed(drug)}[tiab] AND {_build_search_fragment()}"


def iter_simple_fallbacks(drug_name: str) -> list[tuple[str, str]]:
    """
    Fallback queries when the main query returns 0 hits:
      1. full query with salt-stripped name (if different)
      2. drug name alone (no QT/hERG filter — widest recall)
    """
    drug = _clean_drug_name(drug_name)
    if not drug:
        return []

    raw = _normalize_whitespace(drug_name)
    base = strip_salt_suffix(drug_name)

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, q: str) -> None:
        if q not in seen:
            seen.add(q)
            out.append((label, q))

    if base and base.lower() != raw.lower():
        add("salt_stripped", f"{quote_for_pubmed(base)} AND {_build_search_fragment()}")

    add("drug_only", quote_for_pubmed(drug))

    return out