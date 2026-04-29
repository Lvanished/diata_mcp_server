"""
Builds PubMed query strings for QT / hERG drug-safety lookups.

Strategy: drug-as-topic first, mention-only as fallback.

Tiers (run in this order; stop early once enough hits are collected):
    1. mesh_major  — drug must be a MeSH Major Topic OR Pharmacological Action.
                     Highest precision (indexed relevance).
    2. mesh        — drug appears in any MeSH term OR Pharmacological Action OR
                     Supplementary Concept OR Substance Name (covers substances not yet
                     full MeSH headings, including comparative indexing).
    3. title       — drug name in article Title.
                     Catches recent / not-yet-indexed papers where the drug is the
                     study subject (Title is a strong relevance signal).
    4. tiab        — drug name anywhere in Title or Abstract. Last-resort recall;
                     callers should treat hits at this tier as low-confidence.

Each tier is AND-combined with one of two mechanism/clinical OR-blocks:
    - hERG / IKr / KCNH2 + (block / inhibit / ...)   → mechanistic evidence
    - QT / long QT / TdP / repolarization (TIAB)     → clinical/phenotypic evidence

Salt suffixes ("HYDROCHLORIDE", "CITRATE", ...) are stripped from the drug name
before any query is built; the original is retained only as a final fallback for
rare cases where the salt form is itself the indexed name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .qt_vocabulary import (
    BLOCK_ACTION_TERMS_BROAD,
    BLOCK_ACTION_TERMS_STRICT,
    CLINICAL_QT_TERMS,
    MECH_HERG_TERMS,
    PHENOTYPIC_TERMS,
    quote_for_pubmed,
    or_join_bare,
    or_join_tiab,
)

__all__ = [
    "QueryRound",
    "Tier",
    "tier_from_strategy_label",
    "tier_strength",
    "article_passes_min_relevance_tier",
    "build_pubmed_query",
    "iter_pubmed_query_fallbacks",
    "build_herg_query",
    "build_qt_query",
    "build_layered_herg_kcnh2_block_query",
    "build_layered_qt_ta_query",
    "iter_layered_pubmed_query_rounds",
    "strip_salt_suffix",
    "TIER_MIN_HITS_TO_STOP",
]

Tier = Literal["mesh_major", "mesh", "title", "tiab"]

# Precision ordering for optional output filtering (mesh_major = strongest topic signal).
TIER_RANK_DESCENDING: tuple[Tier, ...] = ("mesh_major", "mesh", "title", "tiab")


def tier_strength(tier: str) -> int:
    """Higher value = higher-precision topic match (mesh_major highest, tiab lowest)."""
    return {t: 4 - i for i, t in enumerate(TIER_RANK_DESCENDING)}.get(tier, 0)


def tier_from_strategy_label(label: str) -> Tier:
    """Parse tier from labels like ``mesh_major__herg`` (fallback ``tiab``)."""
    head = (label.split("__", 1)[0].strip() if "__" in label else "").lower()
    if head in ("mesh_major", "mesh", "title", "tiab"):
        return head  # type: ignore[return-value]
    return "tiab"


def article_passes_min_relevance_tier(article_tier: str | None, minimum: str) -> bool:
    """Keep article if its PubMed tier meets ``minimum`` (same scale as ``tier_strength``)."""
    return tier_strength(article_tier or "tiab") >= tier_strength(minimum)


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
    """Strip a single trailing salt/formulation token. Idempotent."""
    s = _normalize_whitespace(drug_name)
    s = _SALT.sub("", s).strip()
    return _normalize_whitespace(s)


def _clean_drug_name(drug_name: str) -> str:
    """Canonical form used by all query builders: whitespace-normalized AND salt-stripped."""
    return strip_salt_suffix(drug_name)


# ---------------------------------------------------------------------------
# OR-block builders (drug-independent)
# ---------------------------------------------------------------------------


def _qt_or_block_full() -> str:
    """Full clinical+mechanistic+phenotypic OR-block, all TIAB-scoped. Used by build_pubmed_query."""
    parts: list[str] = []
    for t in CLINICAL_QT_TERMS:
        parts.append(f"{quote_for_pubmed(t)}[Title/Abstract]")
    for t in MECH_HERG_TERMS:
        parts.append(f"{quote_for_pubmed(t)}[Title/Abstract]")
    for t in PHENOTYPIC_TERMS:
        parts.append(f"{quote_for_pubmed(t)}[Title/Abstract]")
    return " OR ".join(parts)


# ---------------------------------------------------------------------------
# drug-as-topic clause: the heart of the new strategy
# ---------------------------------------------------------------------------


def _drug_topic_clause(drug_name: str, tier: Tier) -> str:
    """
    Build the drug part of a PubMed query at the requested tier.

    Returned string is already parenthesized and ready to be AND-ed with an OR-block.
    """
    drug = _clean_drug_name(drug_name)
    if not drug:
        raise ValueError("drug_name is empty after cleaning")
    q = quote_for_pubmed(drug)

    # mesh_major: primary-topic / pharmacologic role only — tightest tier.
    # Supplementary Concept / Substance Name live in ``mesh`` tier (broader substance indexing).
    if tier == "mesh_major":
        return f"({q}[MeSH Major Topic] OR {q}[Pharmacological Action])"
    if tier == "mesh":
        return (
            f"({q}[MeSH Terms]"
            f" OR {q}[Pharmacological Action]"
            f" OR {q}[Supplementary Concept]"
            f" OR {q}[Substance Name])"
        )
    if tier == "title":
        return f"{q}[Title]"
    if tier == "tiab":
        return f"{q}[Title/Abstract]"
    raise ValueError(f"unknown tier: {tier!r}")


# ---------------------------------------------------------------------------
# composite query builders
# ---------------------------------------------------------------------------


def build_herg_query(drug_name: str, *, broad: bool = False, tier: Tier = "tiab") -> str:
    """Drug-topic AND (hERG/KCNH2/IKr/...) AND (block/inhibit/...)."""
    actions = BLOCK_ACTION_TERMS_BROAD if broad else BLOCK_ACTION_TERMS_STRICT
    drug_clause = _drug_topic_clause(drug_name, tier)
    return f"{drug_clause} AND {or_join_bare(MECH_HERG_TERMS)} AND {or_join_bare(actions)}"


def build_qt_query(drug_name: str, *, broad: bool = False, tier: Tier = "tiab") -> str:
    """Drug-topic AND (QT / long QT / TdP / ...) — clinical OR-block is TIAB-scoped."""
    _ = broad  # reserved
    drug_clause = _drug_topic_clause(drug_name, tier)
    return f"{drug_clause} AND {or_join_tiab(CLINICAL_QT_TERMS)}"


def build_pubmed_query(
    drug_name: str,
    *,
    drug_field: str = "Title/Abstract",
    quote_drug: bool = True,
) -> str:
    """
    Backwards-compatible single-string builder.

    `drug_field` may be 'Title/Abstract', 'Text Word', 'Title', 'MeSH Terms',
    'MeSH Major Topic'. The drug name is salt-stripped first.
    """
    del quote_drug  # we always quote
    drug = _clean_drug_name(drug_name)
    if not drug:
        raise ValueError("drug_name is empty after cleaning")
    field = drug_field.strip()
    if not field.startswith("["):
        field = f"[{field}]"
    return f"({quote_for_pubmed(drug)}{field}) AND ({_qt_or_block_full()})"


# Keep these names importable; they now route through the tier system.
def build_layered_herg_kcnh2_block_query(drug_name: str, *, quote_drug: bool = True) -> str:
    del quote_drug
    return build_herg_query(drug_name, broad=False, tier="mesh_major")


def build_layered_qt_ta_query(drug_name: str, *, quote_drug: bool = True) -> str:
    del quote_drug
    return build_qt_query(drug_name, broad=False, tier="mesh_major")


# ---------------------------------------------------------------------------
# layered rounds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryRound:
    """One layered tier: union (label, query) pairs client-side, then optional short-circuit."""

    name: str
    queries: list[tuple[str, str]]
    min_hits_to_stop: int = 1


# Short-circuit thresholds. Tighter at high-precision tiers, loose at the recall tier.
TIER_MIN_HITS_TO_STOP: dict[str, int] = {
    "mesh_major": 3,
    "mesh": 3,
    "title": 3,
    "tiab": 1,
}


def _round_for_tier(drug: str, tier: Tier) -> QueryRound:
    return QueryRound(
        name=tier,
        queries=[
            (f"{tier}__herg", build_herg_query(drug, broad=False, tier=tier)),
            (f"{tier}__qt", build_qt_query(drug, broad=False, tier=tier)),
        ],
        min_hits_to_stop=TIER_MIN_HITS_TO_STOP[tier],
    )


def iter_layered_pubmed_query_rounds(
    drug_name: str,
    *,
    enable_broad: bool = True,  # kept for API stability; unused
    enable_salt_fallback: bool = True,  # kept for API stability; unused (salt-strip is now default)
) -> list[QueryRound]:
    """
    Topic-first tier ladder:
        mesh_major → mesh → title → tiab

    Caller runs each round, takes the union of PMIDs across queries within the
    round, and stops once `len(unique_pmids) >= round.min_hits_to_stop`.
    """
    del enable_broad, enable_salt_fallback
    drug = _clean_drug_name(drug_name)
    if not drug:
        return []
    return [_round_for_tier(drug, t) for t in ("mesh_major", "mesh", "title", "tiab")]


def iter_pubmed_query_fallbacks(drug_name: str) -> list[tuple[str, str]]:
    """
    Flat (label, query) ladder, kept for legacy callers that don't use rounds.

    Order matches the new tier ladder: mesh_major → mesh → title → tiab,
    mechanistic and clinical sub-queries interleaved.
    """
    drug = _clean_drug_name(drug_name)
    if not drug:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for tier in ("mesh_major", "mesh", "title", "tiab"):
        for label_suffix, q in (
            (f"{tier}__herg", build_herg_query(drug, broad=False, tier=tier)),
            (f"{tier}__qt", build_qt_query(drug, broad=False, tier=tier)),
        ):
            if q not in seen:
                seen.add(q)
                out.append((label_suffix, q))
    return out
