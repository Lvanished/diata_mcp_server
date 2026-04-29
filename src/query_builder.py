"""
Builds PubMed query strings: drug in Title/Abstract AND (QT ECG / repolarization OR-terms).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


def _clean_drug_name(drug_name: str) -> str:
    s = (drug_name or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


# Common salt / formulation suffixes in compound inventory names (not always in article titles).
_SALT = re.compile(
    r"""(?ix)
    \s*(
        hydrochloride|HCl|hcl|mesylate|mesilate|maleate|sodium|sulfate|sulphate|
        bitartrate|acetate|phosphate|tartrate|fumarate|besylate|bromide|iodide|nitrate|
        citrate|lactate|oxalate|succinate|hydrate|monohydrate|dihydrate|anhydrous
    )\s*$
    """
)


def strip_salt_suffix(drug_name: str) -> str:
    s = _clean_drug_name(drug_name)
    s = _SALT.sub("", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _or_block() -> str:
    """PubMed OR clause aligned with classifier vocabulary (TIAB)."""
    parts: list[str] = []
    for t in CLINICAL_QT_TERMS:
        parts.append(f"{quote_for_pubmed(t)}[Title/Abstract]")
    for t in MECH_HERG_TERMS:
        parts.append(f"{quote_for_pubmed(t)}[Title/Abstract]")
    for t in PHENOTYPIC_TERMS:
        parts.append(f"{quote_for_pubmed(t)}[Title/Abstract]")
    return " OR ".join(parts)


def build_pubmed_query(
    drug_name: str,
    *,
    drug_field: str = "Title/Abstract",
    quote_drug: bool = True,
) -> str:
    """
    Return a PubMed query: (drug field) AND (QT/repolarization OR block).

    drug_field: e.g. "Title/Abstract" (default) or "Text Word" for a broader match.
    """
    drug = _clean_drug_name(drug_name)
    if not drug:
        raise ValueError("drug_name is empty after cleaning")

    field = drug_field.strip()
    if not field.startswith("["):
        field = f"[{field}]"
    tag = field
    if quote_drug:
        drug_quoted = f'"{drug}"'
    else:
        drug_quoted = f'"{drug}"' if " " in drug else drug
    or_block = _or_block()
    return f"({drug_quoted}{tag}) AND ({or_block})"


def iter_pubmed_query_fallbacks(drug_name: str) -> list[tuple[str, str]]:
    """
    Ordered (label, query) strategies when the first strict query returns 0 hits.

    1) Title/Abstract + quoted (default spec)
    2) After stripping common salt / formulation suffixes (e.g. ... HYDROCHLORIDE)
    3) [Text Word] for drug (broader than Title/Abstract)
    4) (2) + [Text Word]
    5) Minimal QT OR block + Title/Abstract (fewer ECG terms) — last resort
    """
    drug = _clean_drug_name(drug_name)
    if not drug:
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, q: str) -> None:
        if q not in seen:
            seen.add(q)
            out.append((label, q))

    add("ta_quoted", build_pubmed_query(drug, drug_field="Title/Abstract", quote_drug=True))
    base = strip_salt_suffix(drug)
    if base and base.lower() != drug.lower():
        add("ta_quoted_salt_stripped", build_pubmed_query(base, drug_field="Title/Abstract", quote_drug=True))

    add("tw_quoted", build_pubmed_query(drug, drug_field="Text Word", quote_drug=True))
    if base and base.lower() != drug.lower():
        add("tw_quoted_salt_stripped", build_pubmed_query(base, drug_field="Text Word", quote_drug=True))

    minimal_terms = ("QT prolongation", "long QT", "torsades de pointes", "hERG", "IKr")
    minimal = f'("{drug}"[Title/Abstract]) AND {or_join_tiab(minimal_terms)}'
    add("ta_minimal_qt_block", minimal)
    if base and base.lower() != drug.lower():
        minimal_b = f'("{base}"[Title/Abstract]) AND {or_join_tiab(minimal_terms)}'
        add("ta_minimal_qt_block_salt_stripped", minimal_b)

    return out


def _drug_ta(drug_name: str) -> str:
    drug = _clean_drug_name(drug_name)
    if not drug:
        raise ValueError("drug_name is empty after cleaning")
    return f'"{drug}"[Title/Abstract]'


def build_herg_query(drug_name: str, *, broad: bool = False) -> str:
    """Drug AND (hERG/KCNH2/IKr/ether…) AND (block/inhibit/…)."""
    actions = BLOCK_ACTION_TERMS_BROAD if broad else BLOCK_ACTION_TERMS_STRICT
    return (
        f"{_drug_ta(drug_name)} AND {or_join_bare(MECH_HERG_TERMS)} AND {or_join_bare(actions)}"
    )


def build_qt_query(drug_name: str, *, broad: bool = False) -> str:
    """Drug AND (QT/long QT/TdP terms) — all TIAB-scoped."""
    _ = broad  # reserved for broader TIAB expansions
    return f"{_drug_ta(drug_name)} AND {or_join_tiab(CLINICAL_QT_TERMS)}"


def build_layered_herg_kcnh2_block_query(drug_name: str, *, quote_drug: bool = True) -> str:
    del quote_drug
    return build_herg_query(drug_name, broad=False)


def build_layered_qt_ta_query(drug_name: str, *, quote_drug: bool = True) -> str:
    del quote_drug
    return build_qt_query(drug_name, broad=False)


@dataclass(frozen=True)
class QueryRound:
    """One layered tier: union (label, query) pairs client-side, then optional short-circuit."""

    name: str
    queries: list[tuple[str, str]]
    min_hits_to_stop: int = 1


LAYERED_STRICT_MIN_UNION_TO_SKIP_BROAD = 5
LAYERED_BROAD_MIN_UNION_TO_STOP = 3


def iter_layered_pubmed_query_rounds(
    drug_name: str,
    *,
    enable_broad: bool = True,
    enable_salt_fallback: bool = True,
) -> list[QueryRound]:
    """
    Ordered tiers: strict (inventory) → optional broad → optional salt-stripped strict.

    Caller runs each round in order and may stop once ``len(unique_pmids) >= min_hits_to_stop``.
    """
    drug = _clean_drug_name(drug_name)
    if not drug:
        return []

    rounds: list[QueryRound] = [
        QueryRound(
            name="strict",
            queries=[
                ("herg_strict", build_herg_query(drug, broad=False)),
                ("qt_strict", build_qt_query(drug, broad=False)),
            ],
            min_hits_to_stop=LAYERED_STRICT_MIN_UNION_TO_SKIP_BROAD,
        ),
    ]

    if enable_broad:
        rounds.append(
            QueryRound(
                name="broad",
                queries=[
                    ("herg_broad", build_herg_query(drug, broad=True)),
                    ("qt_broad", build_qt_query(drug, broad=True)),
                ],
                min_hits_to_stop=LAYERED_BROAD_MIN_UNION_TO_STOP,
            )
        )

    if enable_salt_fallback:
        base = strip_salt_suffix(drug)
        if base and base.lower() != drug.lower():
            rounds.append(
                QueryRound(
                    name="salt_stripped_strict",
                    queries=[
                        ("herg_salt", build_herg_query(base, broad=False)),
                        ("qt_salt", build_qt_query(base, broad=False)),
                    ],
                    min_hits_to_stop=1,
                )
            )

    return rounds
