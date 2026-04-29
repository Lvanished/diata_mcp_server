"""
Single source of truth for QT / hERG keyword vocabulary.

This module is consumed by:
  - PubMed query builders (build_herg_query, build_qt_query)
  - Full-text classifier (_classify_evidence and successors)
  - Inference feature extractor (extract_inference_features)

Any keyword change happens here, in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# ─────────────────────────────────────────────────────────────────────────────
# Job A: shared term groups for PubMed query AND full-text classification
# ─────────────────────────────────────────────────────────────────────────────

# Clinical / direct QT terms — strongest "this is about QT" signal.
CLINICAL_QT_TERMS: tuple[str, ...] = (
    "QT prolongation",
    "QTc prolongation",
    "long QT",
    "prolonged QT",
    "torsades de pointes",
    "torsade de pointes",
    "TdP",
)

# hERG / IKr channel mechanism terms.
# NOTE: do NOT add a bare "ERG" — collides with fungal ERG1 (ITRACONAZOLE PMID 15215098).
MECH_HERG_TERMS: tuple[str, ...] = (
    "hERG",
    "KCNH2",
    "IKr",
    "ether-a-go-go",
    "ether a go go",
)

# Block / inhibition action verbs and nouns for the hERG branch (query-only).
BLOCK_ACTION_TERMS_STRICT: tuple[str, ...] = (
    "block",
    "blocker",
    "inhibit",
    "inhibitor",
    "inhibition",
)
BLOCK_ACTION_TERMS_BROAD: tuple[str, ...] = BLOCK_ACTION_TERMS_STRICT + (
    "blockade",
    "current",
    "channel",
)

# Phenotypic / cellular repolarization terms (classifier phenotypic bucket).
PHENOTYPIC_TERMS: tuple[str, ...] = (
    "APD",
    "APD90",
    "FPD",
    "FPDc",
    "action potential duration",
    "field potential duration",
    "repolarization",
)


# ─────────────────────────────────────────────────────────────────────────────
# Job B: evidence subtype patterns (split uncertain_relevance-rich articles)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceSubtype:
    name: str
    pattern: re.Pattern[str]
    description: str = ""


def _ci(p: str) -> re.Pattern[str]:
    return re.compile(p, re.IGNORECASE)


EVIDENCE_SUBTYPES: tuple[EvidenceSubtype, ...] = (
    EvidenceSubtype(
        "human_rct_tqt",
        _ci(r"\b(thorough\s+qt|tqt\s+study|randomi[sz]ed[\w\s,-]{0,40}placebo)\b"),
        "Thorough QT or randomized placebo-controlled QT study.",
    ),
    EvidenceSubtype(
        "human_case_report",
        _ci(r"\bcase\s+report\b|\bwe\s+report\s+(?:a\s+)?(?:case|patient)\b"),
        "Single-patient case report.",
    ),
    EvidenceSubtype(
        "human_pharmacovigilance",
        _ci(
            r"\b(faers|vigibase|pharmacovigilance|reporting\s+odds\s+ratio|"
            r"disproportionality|spontaneous\s+report)\b"
        ),
        "Pharmacovigilance / FAERS / disproportionality analysis.",
    ),
    EvidenceSubtype(
        "human_observational",
        _ci(
            r"\b(retrospective|prospective|cohort|registry|chart\s+review|"
            r"observational\s+study)\b"
        ),
        "Observational human study.",
    ),
    EvidenceSubtype(
        "electrophys_patch_clamp",
        _ci(r"\b(patch[-\s]?clamp|whole[-\s]?cell|voltage[-\s]?clamp)\b"),
        "Patch-clamp electrophysiology.",
    ),
    EvidenceSubtype(
        "electrophys_ic50",
        _ci(r"\bic[\s_]?50\b|ic₅₀"),
        "Reports an IC50 / IC₅₀ value.",
    ),
    EvidenceSubtype(
        "electrophys_cell_model",
        _ci(
            r"\b(hek[-\s]?293|cho\s+cell|xenopus\s+oocyte|"
            r"hipsc[-\s]?cardiomyocyte|ipsc[-\s]?cardiomyocyte)\b"
        ),
        "Heterologous expression or stem-cell cardiomyocyte assay.",
    ),
    EvidenceSubtype(
        "electrophys_animal",
        _ci(r"\b(rabbit|guinea[-\s]?pig|canine|wedge\s+preparation|langendorff)\b"),
        "Whole-animal or isolated-heart preparation.",
    ),
    EvidenceSubtype(
        "in_silico",
        _ci(
            r"\b(in\s+silico|cipa|qsar|molecular\s+docking|rosetta|"
            r"machine\s+learning|deep\s+learning)\b"
        ),
        "Computational / modeling study.",
    ),
    EvidenceSubtype(
        "review",
        _ci(r"\b(review|meta[-\s]?analysis|systematic\s+review|narrative\s+review)\b"),
        "Review or meta-analysis.",
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Job C: inference feature patterns (structured signals for downstream model)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InferenceFeature:
    name: str
    pattern: re.Pattern[str]
    kind: str
    description: str = ""


INFERENCE_FEATURES: tuple[InferenceFeature, ...] = (
    InferenceFeature(
        "direction_positive",
        _ci(r"\b(prolong\w*|increase\w*|widen\w*)\s+(?:the\s+)?qtc?\b"),
        "bool",
        "Article asserts the drug prolongs QT/QTc.",
    ),
    InferenceFeature(
        "direction_negative",
        _ci(
            r"\b(no\s+(?:significant\s+)?(?:qt\s+)?prolong\w*|"
            r"did\s+not\s+prolong|not\s+associated\s+with\s+(?:qt|qtc|tdp)|"
            r"no\s+significant\s+(?:qt|qtc)\s+(?:change|effect|prolongation)|"
            r"no\s+clinically\s+significant)\b"
        ),
        "bool",
        "Article claims absence of QT effect.",
    ),
    InferenceFeature(
        "direction_equivocal",
        _ci(r"\b(may|might|could|potentially|possibly)\s+(?:cause\s+)?prolong\w*\s+qt"),
        "bool",
        "Hedged claim.",
    ),
    InferenceFeature(
        "outcome_tdp",
        _ci(r"\btorsade[s]?\s+de\s+pointes?\b|\btdp\b"),
        "bool",
        "Mentions TdP.",
    ),
    InferenceFeature(
        "outcome_sudden_death",
        _ci(r"\bsudden\s+(?:cardiac\s+)?death|cardiac\s+arrest|fatal\s+arrhythmi"),
        "bool",
        "Sudden cardiac death / arrest / fatal arrhythmia.",
    ),
    InferenceFeature(
        "outcome_syncope",
        _ci(r"\bsyncope\b"),
        "bool",
        "Syncope.",
    ),
    InferenceFeature(
        "outcome_arrhythmia",
        _ci(r"\b(ventricular\s+(?:tachycardia|fibrillation|arrhythmi)|polymorphic\s+vt)\b"),
        "bool",
        "Ventricular arrhythmia.",
    ),
    InferenceFeature(
        "qt_delta_ms",
        _ci(
            # "QTc prolonged by 18 ms" / "QT increase by 10 ms"
            r"(?:qtc?|Δ\s*qtc?|delta\s*qtc?)\s+"
            r"(?:prolong\w*|increase\w*|increment\w*)\s+"
            r"(?:by\s+|of\s+)?(\d+(?:\.\d+)?)\s*ms"
            r"|"
            # "QTc was prolonged by 18 ms"
            r"qtc?\s+(?:was\s+)?prolong\w*\s+by\s+(\d+(?:\.\d+)?)\s*ms"
            r"|"
            # "12 ms prolongation of QTc" / "a 12 ms increase in QTc"
            r"(\d+(?:\.\d+)?)\s*ms\s+"
            r"(?:prolongation|increase|increment|change)\s+"
            r"(?:in\s+|of\s+)?(?:qtc?|delta\s*qtc?)"
            r"|"
            # "QTc prolongation of 22.7 ms"
            r"qtc?\s+prolongation\s+of\s+(\d+(?:\.\d+)?)\s*ms"
            r"|"
            # "ΔQTc of 8.3 ms" / "ΔQTc = 8.3 ms"
            r"(?:Δ\s*qtc?|delta\s*qtc?)\s+(?:of\s+|=\s*)(\d+(?:\.\d+)?)\s*ms"
        ),
        "numeric",
        "Reported QT prolongation magnitude in ms (multiple phrasings).",
    ),
    InferenceFeature(
        "ic50_value",
        _ci(
            r"(?:ic[\s_]?50|ic₅₀)[^.]{0,40}?"
            r"(\d+(?:\.\d+)?)\s*(nm|µm|μm|um|nanomolar|micromolar)"
        ),
        "numeric",
        "Reported hERG IC50 with unit (handles both 'IC50' and 'IC₅₀').",
    ),
    InferenceFeature(
        "cmax_value",
        _ci(r"\bc[\s_]?max[^.]{0,40}?(\d+(?:\.\d+)?)\s*(ng/ml|µg/ml|μg/ml|nm|µm|μm)"),
        "numeric",
        "Reported plasma Cmax with unit.",
    ),
    InferenceFeature(
        "cofactor_hypokalemia",
        _ci(r"\bhypokal[ae]mia|low\s+(?:serum\s+)?potassium\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_hypomagnesemia",
        _ci(r"\bhypomagnes[ae]mia\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_cyp3a4",
        _ci(r"\bcyp\s*3a4?\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_cyp2d6",
        _ci(r"\bcyp\s*2d6\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_ddi",
        _ci(
            r"\b(drug[-\s]?drug\s+interaction|\bddi\b|comedication|"
            r"concomitant\s+(?:use|administration|medication))\b"
        ),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_renal",
        _ci(r"\brenal\s+(?:impairment|insufficiency|failure)|" r"reduced\s+creatinine\s+clearance\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_hepatic",
        _ci(r"\bhepatic\s+(?:impairment|insufficiency|failure)\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_female",
        _ci(r"\b(female\s+sex|women)\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "cofactor_elderly",
        _ci(r"\b(elderly|aged?\s+>?\s*65|geriatric)\b"),
        "bool",
        "",
    ),
    InferenceFeature(
        "reversibility",
        _ci(
            r"\b(reversibl[ey]|resolved\s+after|normaliz\w+\s+after|"
            r"upon\s+discontinuation|after\s+(?:drug\s+)?withdrawal)\b"
        ),
        "bool",
        "",
    ),
    InferenceFeature(
        "dose_dependent",
        _ci(r"\b(dose|concentration)[-\s]?(?:dependent|response|related)\b"),
        "bool",
        "",
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers consumed by query builders and classifier
# ─────────────────────────────────────────────────────────────────────────────


def quote_for_pubmed(term: str) -> str:
    """Quote a term for PubMed if it contains spaces; otherwise leave bare."""
    return f'"{term}"' if " " in term else term


def or_join_tiab(terms: Iterable[str]) -> str:
    """Build an OR-joined PubMed clause restricted to [Title/Abstract]."""
    parts = [f"{quote_for_pubmed(t)}[Title/Abstract]" for t in terms]
    return "(" + " OR ".join(parts) + ")"


def or_join_bare(terms: Iterable[str]) -> str:
    """Build an OR-joined PubMed clause without field restriction (e.g. hERG / KCNH2)."""
    return "(" + " OR ".join(quote_for_pubmed(t) for t in terms) + ")"


CLASSIFIER_CLINICAL: frozenset[str] = frozenset(list(CLINICAL_QT_TERMS) + ["QT"])
CLASSIFIER_MECH: frozenset[str] = frozenset(MECH_HERG_TERMS)
CLASSIFIER_PHENOTYPIC: frozenset[str] = frozenset(PHENOTYPIC_TERMS)


def classifier_qt_terms_ordered() -> list[str]:
    """
    Ordered keyword list for context extraction (matches qt_keywords runtime).

    Order: unique insertion order — extract_keyword_contexts re-sorts by length for matching.
    """
    out: list[str] = []
    for t in ("QT", *CLINICAL_QT_TERMS, *MECH_HERG_TERMS, *PHENOTYPIC_TERMS):
        if t not in out:
            out.append(t)
    return out
