"""
Builds PubMed query strings: drug in Title/Abstract AND (QT ECG / repolarization OR-terms).
"""

from __future__ import annotations

import re


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
