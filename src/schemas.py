"""Pydantic models for report payloads (optional validation / typing aid)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceContext(BaseModel):
    source: Literal["abstract", "fulltext"]
    section: str
    matched_term: str
    context: str
    evidence_type: str


class ArticleReport(BaseModel):
    pmid: str
    pmcid: str = ""
    title: str = ""
    journal: str = ""
    year: str = ""
    abstract: str = ""
    fulltext_available: bool = False
    fulltext_error: str | None = None
    matched_terms: list[str] = Field(default_factory=list)
    contexts: list[dict[str, Any]] = Field(default_factory=list)
    raw_mesh_terms: list[str] = Field(default_factory=list)
