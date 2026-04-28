"""
Builds PubMed query strings: drug in Title/Abstract AND (QT ECG / repolarization OR-terms).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


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
    return " OR ".join(
        [
            '"QT prolongation"[Title/Abstract]',
            '"long QT"[Title/Abstract]',
            '"torsades de pointes"[Title/Abstract]',
            "hERG[Title/Abstract]",
            "IKr[Title/Abstract]",
            "repolarization[Title/Abstract]",
            "APD[Title/Abstract]",
            "FPD[Title/Abstract]",
        ]
    )


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
        # Single-token drugs sometimes appear unquoted in PubMed; avoid breaking multiword.
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

    minimal = (
        f'("{drug}"[Title/Abstract]) AND ('
        '"QT prolongation"[Title/Abstract] OR "long QT"[Title/Abstract] OR '
        '"torsades de pointes"[Title/Abstract] OR hERG[Title/Abstract] OR IKr[Title/Abstract])'
    )
    add("ta_minimal_qt_block", minimal)
    if base and base.lower() != drug.lower():
        minimal_b = (
            f'("{base}"[Title/Abstract]) AND ('
            '"QT prolongation"[Title/Abstract] OR "long QT"[Title/Abstract] OR '
            '"torsades de pointes"[Title/Abstract] OR hERG[Title/Abstract] OR IKr[Title/Abstract])'
        )
        add("ta_minimal_qt_block_salt_stripped", minimal_b)

    return out


# ---------- Title/Abstract helpers (layered queries) ----------


def _ta(term: str) -> str:
    """Wrap a term/phrase in [Title/Abstract]. Quote if it contains spaces."""
    t = term.strip()
    if not t:
        raise ValueError("empty term")
    if " " in t and not (t.startswith('"') and t.endswith('"')):
        t = f'"{t}"'
    return f"{t}[Title/Abstract]"


def _drug_ta(drug_name: str) -> str:
    drug = _clean_drug_name(drug_name)
    if not drug:
        raise ValueError("drug_name is empty after cleaning")
    return f'"{drug}"[Title/Abstract]'


# ---------- Keyword groups (tune here) ----------

# hERG / channel — not field-restricted so MeSH / other fields can match.
_HERG_TERMS = '(hERG OR KCNH2 OR "ether-a-go-go" OR "IKr")'

_BLOCK_TERMS_BROAD = (
    "(block OR blocker OR blockade OR "
    "inhibit OR inhibitor OR inhibition OR "
    "current OR channel)"
)
_BLOCK_TERMS_STRICT = "(block OR blocker OR inhibit OR inhibitor OR inhibition)"

_QT_TA_CORE = (
    '"QT prolongation"[Title/Abstract] '
    'OR "QTc prolongation"[Title/Abstract] '
    'OR "long QT"[Title/Abstract] '
    'OR "prolonged QT"[Title/Abstract]'
)
_QT_TA_STRICT = f"({_QT_TA_CORE})"
_QT_TA_BROAD = (
    f"({_QT_TA_CORE} "
    'OR "torsades de pointes"[Title/Abstract] '
    'OR "torsade de pointes"[Title/Abstract] '
    "OR TdP[Title/Abstract] "
    "OR proarrhythmi*[Title/Abstract])"
)


def build_herg_query(drug_name: str, *, broad: bool = False) -> str:
    """Branch A: drug[TA] AND hERG-family terms AND block/channel terms."""
    block = _BLOCK_TERMS_BROAD if broad else _BLOCK_TERMS_STRICT
    return f"{_drug_ta(drug_name)} AND {_HERG_TERMS} AND {block}"


def build_qt_query(drug_name: str, *, broad: bool = False) -> str:
    """Branch B: drug[TA] AND QT / TdP phrases (Title/Abstract only)."""
    qt = _QT_TA_BROAD if broad else _QT_TA_STRICT
    return f"{_drug_ta(drug_name)} AND {qt}"


@dataclass(frozen=True)
class QueryRound:
    """One layered tier: union (label, query) pairs client-side, then optional short-circuit."""

    name: str
    queries: list[tuple[str, str]]
    min_hits_to_stop: int = 1


def iter_layered_pubmed_query_rounds(
    drug_name: str,
    *,
    enable_broad: bool = True,
    enable_salt_fallback: bool = True,
) -> list[QueryRound]:
    """
    Ordered tiers for layered search. Caller runs each round in order and may stop
    once ``len(unique_pmids) >= min_hits_to_stop`` (if ``min_hits_to_stop > 0``).

    1. **strict** — original name, narrow hERG/block and QT phrases.
    2. **broad** — same name, wider synonyms (TdP, channel/current, IKr, …) if enabled.
    3. **salt_stripped_strict** — de-salted name, strict branches only, if suffix strip changed
       the string and ``enable_salt_fallback``.
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
                )
            )

    return rounds
