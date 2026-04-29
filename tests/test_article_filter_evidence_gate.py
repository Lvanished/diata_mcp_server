"""Evidence gate behaviour by PubMed tier."""

from __future__ import annotations

from src.article_filter import enrich_article_evidence_metadata, filter_articles


def test_mesh_major_kept_when_evidence_filter_off_despite_no_context() -> None:
    a = {
        "tier": "mesh_major",
        "title": "Study title",
        "abstract": "Irrelevant abstract body.",
        "contexts": [],
        "pipeline_clinical_qt_evidence": False,
        "pipeline_mechanistic_evidence": False,
        "pipeline_phenotypic_evidence": False,
    }
    enrich_article_evidence_metadata(a, "dummy")
    out = filter_articles([a], "dummy", evidence_filter_on_mesh_tiers=False)
    assert len(out) == 1


def test_title_tier_dropped_without_keyword_signals() -> None:
    a = {
        "tier": "tiab",
        "title": "Noise",
        "abstract": "No qt vocabulary.",
        "contexts": [],
        "pipeline_clinical_qt_evidence": False,
        "pipeline_mechanistic_evidence": False,
        "pipeline_phenotypic_evidence": False,
    }
    enrich_article_evidence_metadata(a, "dummy")
    out = filter_articles([a], "dummy", evidence_filter_on_mesh_tiers=False)
    assert len(out) == 0
