"""
ChEMBL molecule data models and helper functions for pipeline enrichment.

Models use extra="allow" so all ChEMBL API fields pass through without validation errors.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").casefold()


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ── Pydantic models ──────────────────────────────────────────────


class ChEMBLSynonym(BaseModel):
    model_config = ConfigDict(extra="allow")
    syn_type: str
    syn_name: str | None = None


class ChEMBLActivity(BaseModel):
    model_config = ConfigDict(extra="allow")
    standard_type: str | None = None
    standard_value: float | None = None
    standard_units: str | None = None
    pchembl_value: float | None = None
    target_chembl_id: str | None = None
    target_pref_name: str | None = None
    assay_chembl_id: str | None = None
    document_chembl_id: str | None = None
    action_type: Any = None

    @field_validator("standard_value", "pchembl_value", mode="before")
    @classmethod
    def coerce_numeric(cls, v: Any) -> float | None:
        return _to_float(v)


class ChEMBLDocument(BaseModel):
    model_config = ConfigDict(extra="allow")
    document_chembl_id: str | None = None
    pubmed_id: str | None = None
    journal: str | None = None
    year: int | None = None
    title: str | None = None


class ChEMBLMolecule(BaseModel):
    model_config = ConfigDict(extra="allow")
    molecule_chembl_id: str
    pref_name: str | None = None
    molecule_synonyms: list[ChEMBLSynonym] = []
    activities: list[ChEMBLActivity] = []
    documents: list[ChEMBLDocument] = []
    atc_classifications: list[str] = []
    max_phase: Any = None
    withdrawn: Any = None


# ── Name variant extraction ──────────────────────────────────────

_PRIORITY_ORDER = ["INN", "BAN", "USAN", "TRADE_NAME", "OTHER", "RESEARCH_CODE", "MERCK_INDEX"]

_HERG_TARGET_IDS = {"CHEMBL240", "CHEMBL2079"}
_HERG_KEYWORDS = {"herg", "kcnh2", "ether-a-go-go", "ikr", "rapid delayed rectifier"}
_IC50_KI_TYPES = {"IC50", "Ki", "Kd", "EC50", "AC50"}


def extract_name_variants(molecule: ChEMBLMolecule) -> list[str]:
    """Return deduplicated drug name variants for PubMed search.

    Priority: pref_name > INN > BAN > USAN > trade names > research codes > other.
    Each variant is salt-stripped and whitespace-normalized.
    Empty syn_name values are skipped.
    """
    from .query_builder import strip_salt_suffix

    seen: set[str] = set()
    out: list[str] = []

    # pref_name first
    if molecule.pref_name:
        v = _fold(strip_salt_suffix(molecule.pref_name))
        if v and len(v) >= 2 and v not in seen:
            seen.add(v)
            out.append(molecule.pref_name.strip())

    # synonyms sorted by priority
    by_type: dict[str, list[str]] = {}
    for syn in molecule.molecule_synonyms:
        name = (syn.syn_name or "").strip()
        if not name:
            continue
        t = syn.syn_type.upper()
        by_type.setdefault(t, []).append(name)

    for syn_type in _PRIORITY_ORDER:
        for raw_name in by_type.get(syn_type, []):
            v = _fold(strip_salt_suffix(raw_name))
            if v and len(v) >= 2 and v not in seen:
                seen.add(v)
                out.append(raw_name.strip())

    # remaining synonym types not in priority list
    for syn_type, names in by_type.items():
        if syn_type not in _PRIORITY_ORDER:
            for raw_name in names:
                v = _fold(strip_salt_suffix(raw_name))
                if v and len(v) >= 2 and v not in seen:
                    seen.add(v)
                    out.append(raw_name.strip())

    return out


def extract_known_pubmed_ids(molecule: ChEMBLMolecule) -> list[str]:
    """Collect all PubMed IDs from molecule.documents, deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for doc in molecule.documents:
        pid = str(doc.pubmed_id or "").strip()
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def extract_herg_activities(molecule: ChEMBLMolecule) -> list[dict[str, Any]]:
    """Filter activities to hERG/KCNH2 targets with IC50/Ki values."""
    out: list[dict[str, Any]] = []
    for act in molecule.activities:
        tid = (act.target_chembl_id or "").upper()
        tname = _fold(act.target_pref_name or "")
        stype = (act.standard_type or "").upper()

        if tid not in _HERG_TARGET_IDS and not any(k in tname for k in _HERG_KEYWORDS):
            continue
        if stype not in _IC50_KI_TYPES:
            continue

        out.append({
            "standard_type": act.standard_type,
            "standard_value": act.standard_value,
            "standard_units": act.standard_units,
            "pchembl_value": act.pchembl_value,
            "target_chembl_id": act.target_chembl_id,
            "target_pref_name": act.target_pref_name,
            "action_type": act.action_type,
            "assay_description": act.assay_description if hasattr(act, "assay_description") else None,
        })
    return out


def build_chembl_enrichment(molecule: ChEMBLMolecule) -> dict[str, Any]:
    """Build the enrichment dict consumed by run_pipeline_for_drug."""
    return {
        "chembl_id": molecule.molecule_chembl_id,
        "name_variants": extract_name_variants(molecule),
        "known_pubmed_ids": extract_known_pubmed_ids(molecule),
        "herg_activities": extract_herg_activities(molecule),
        "pref_name": molecule.pref_name,
        "max_phase": _to_float(molecule.max_phase),
        "withdrawn": molecule.withdrawn,
    }