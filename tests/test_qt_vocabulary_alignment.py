"""
Regression tests pinning vocabulary alignment between PubMed queries and full-text classification.
"""

from __future__ import annotations

import pytest

from src.article_filter import loose_match_strength
from src.context_extractor import _classify_evidence
from src.evidence_subtypes import classify_evidence_subtypes
from src.inference_features import extract_inference_features
from src.qt_vocabulary import (
    CLASSIFIER_CLINICAL,
    CLASSIFIER_MECH,
    CLASSIFIER_PHENOTYPIC,
    CLINICAL_QT_TERMS,
    EVIDENCE_SUBTYPES,
    INFERENCE_FEATURES,
    MECH_HERG_TERMS,
    PHENOTYPIC_TERMS,
    classifier_qt_terms_ordered,
)


def test_clinical_terms_round_trip() -> None:
    for t in CLINICAL_QT_TERMS:
        assert _classify_evidence(t) == "clinical_or_direct_qt_evidence", t


def test_mech_terms_round_trip() -> None:
    for t in MECH_HERG_TERMS:
        assert _classify_evidence(t) == "mechanistic_herg_ikr_evidence", t


def test_phenotypic_terms_round_trip() -> None:
    for t in PHENOTYPIC_TERMS:
        assert _classify_evidence(t) == "phenotypic_repolarization_evidence", t


def test_legacy_bare_qt_still_clinical() -> None:
    assert _classify_evidence("QT") == "clinical_or_direct_qt_evidence"


def test_classifier_qt_terms_exactly_vocab_axes() -> None:
    expected = {"QT", *CLINICAL_QT_TERMS, *MECH_HERG_TERMS, *PHENOTYPIC_TERMS}
    got = classifier_qt_terms_ordered()
    assert set(got) == expected
    assert len(got) == len(expected)
    assert CLASSIFIER_CLINICAL == frozenset({"QT", *CLINICAL_QT_TERMS})
    assert CLASSIFIER_MECH == frozenset(MECH_HERG_TERMS)
    assert CLASSIFIER_PHENOTYPIC == frozenset(PHENOTYPIC_TERMS)


def test_subtype_review_detection() -> None:
    title = "A systematic review of hERG channel blockers"
    abstract = "We performed a meta-analysis ..."
    subs = classify_evidence_subtypes(title, abstract)
    assert "review" in subs


def test_subtype_case_report_detection() -> None:
    abstract = "We report a case of QT prolongation following drug X administration."
    subs = classify_evidence_subtypes("", abstract)
    assert "human_case_report" in subs


def test_feature_direction_negative() -> None:
    abstract = "Drug X did not prolong the QT interval at therapeutic doses."
    feats = extract_inference_features("", abstract)
    assert feats.get("direction_negative") is True


def test_feature_ic50_numeric() -> None:
    abstract = "The hERG IC50 was 0.45 μM in HEK293 cells."
    feats = extract_inference_features("", abstract)
    assert "ic50_value" in feats
    value, unit = feats["ic50_value"][0]
    assert value == pytest.approx(0.45)
    assert unit is not None
    assert "m" in unit.lower()


def test_ic50_subscript_extraction() -> None:
    abstract = "Our compound exhibited a hERG IC₅₀ of 12.1 μM."
    feats = extract_inference_features("", abstract)
    assert "ic50_value" in feats
    assert feats["ic50_value"][0][0] == pytest.approx(12.1)


def test_qt_delta_ms_phrasings() -> None:
    cases = [
        ("QTc was prolonged by 18 ms", 18.0),
        ("a 12 ms increase in QTc", 12.0),
        ("QTc prolongation of 22.7 ms", 22.7),
        ("ΔQTc of 8.3 ms", 8.3),
        ("QTc prolongation by 18 ms compared to baseline.", 18.0),
    ]
    for text, expected in cases:
        feats = extract_inference_features("", text)
        assert "qt_delta_ms" in feats, f"missed: {text}"
        assert feats["qt_delta_ms"][0][0] == pytest.approx(expected), text


def test_feature_outcome_tdp() -> None:
    abstract = "Two patients developed torsades de pointes."
    feats = extract_inference_features("", abstract)
    assert feats.get("outcome_tdp") is True


def test_feature_cofactor_cyp3a4() -> None:
    abstract = "Co-administration with strong CYP3A4 inhibitors increased exposure."
    feats = extract_inference_features("", abstract)
    assert feats.get("cofactor_cyp3a4") is True


def test_no_bare_erg_in_mech_terms() -> None:
    for t in MECH_HERG_TERMS:
        assert t.lower() != "erg", "Bare ERG would collide with fungal ERG1"


def test_evidence_subtypes_declared() -> None:
    assert len(EVIDENCE_SUBTYPES) >= 1


def test_loose_match_strength_title_vs_abstract() -> None:
    assert (
        loose_match_strength(
            {"title": "METHADONE prolonged QT", "abstract": "Discussion."},
            "METHADONE",
            None,
        )
        == "title"
    )
    assert (
        loose_match_strength(
            {"title": "TdP case report", "abstract": "Patient on METHADONE developed TdP."},
            "METHADONE",
            None,
        )
        == "abstract"
    )
    assert (
        loose_match_strength(
            {"title": "Drug-induced arrhythmia", "abstract": "TdP noted."},
            "METHADONE",
            None,
        )
        == "fulltext_only"
    )


def test_inference_features_declared() -> None:
    assert len(INFERENCE_FEATURES) >= 1
