"""PubMed query construction and tier helpers."""

from __future__ import annotations

from src.query_builder import (
    article_passes_min_relevance_tier,
    build_herg_query,
    tier_from_strategy_label,
    tier_strength,
)


def test_tier_strength_ordering() -> None:
    assert tier_strength("mesh_major") > tier_strength("mesh") > tier_strength("title") > tier_strength(
        "tiab"
    )


def test_min_relevance_title_drops_tiab_only() -> None:
    assert article_passes_min_relevance_tier("title", "title")
    assert article_passes_min_relevance_tier("mesh", "title")
    assert article_passes_min_relevance_tier(None, "title") is False
    assert article_passes_min_relevance_tier("tiab", "title") is False


def test_tier_from_strategy_label() -> None:
    assert tier_from_strategy_label("mesh_major__herg") == "mesh_major"
    assert tier_from_strategy_label("mesh__qt") == "mesh"
    assert tier_from_strategy_label("nonsense") == "tiab"


def test_mesh_major_strict_major_mesh_has_supplementary() -> None:
    qmaj = build_herg_query("dofetilide", tier="mesh_major")
    assert "[Supplementary Concept]" not in qmaj
    assert "[Substance Name]" not in qmaj
    qmesh = build_herg_query("dofetilide", tier="mesh")
    assert "[Supplementary Concept]" in qmesh
    assert "[Substance Name]" in qmesh
