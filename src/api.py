"""
FastAPI application for DIATA MCP Pipeline.
Provides endpoints for drug-name-only and ChEMBL-enriched pipeline runs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.api_session import MCPSessionManager
from src.chembl_data import ChEMBLMolecule, build_chembl_enrichment
from src.qt_vocabulary import classifier_qt_terms_ordered
from src.main import run_pipeline_for_drug

logger = logging.getLogger(__name__)

CHEMBL_API_BASE = "https://www.ebi.ac.uk/chembl/api/data"


# ── Lifespan ────────────────────────────────────────────────────

session_mgr = MCPSessionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await session_mgr.start()
    yield
    await session_mgr.stop()


app = FastAPI(
    title="DIATA MCP Pipeline",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Request models ──────────────────────────────────────────────


class DrugNameRequest(BaseModel):
    drug_name: str
    top_n: int = 100
    window: int = 500


class ChEMBLRequest(BaseModel):
    model_config = {"extra": "allow"}
    molecule_chembl_id: str
    pref_name: str | None = None
    molecule_synonyms: list[dict] | None = None
    activities: list[dict] | None = None
    documents: list[dict] | None = None
    top_n: int = 100
    window: int = 500


# ── ChEMBL document → PubMed ID resolver ────────────────────────


async def resolve_chembl_doc_pubmed_ids(doc_ids: list[str]) -> list[str]:
    """Call ChEMBL document API to resolve document_chembl_ids to PubMed IDs."""
    pmids: list[str] = []
    for did in doc_ids:
        try:
            resp = await httpx.AsyncClient().get(
                f"{CHEMBL_API_BASE}/document/{did}.json",
                headers={"Accept": "application/json"},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                pid = str(data.get("pubmed_id") or "").strip()
                if pid:
                    pmids.append(pid)
        except Exception as e:
            logger.warning(f"Failed to resolve ChEMBL doc {did}: {e}")
    return pmids


def extract_doc_ids_from_activities(activities: list[dict] | None) -> list[str]:
    """Extract unique document_chembl_ids from activity records."""
    if not activities:
        return []
    ids: set[str] = set()
    for act in activities:
        did = act.get("document_chembl_id")
        if did and isinstance(did, str):
            ids.add(did)
    return list(ids)


# ── Endpoints ───────────────────────────────────────────────────


@app.get("/health")
async def health():
    ok = await session_mgr.health_check()
    if not ok:
        raise HTTPException(503, "MCP client session unavailable")
    return {"status": "ok", "mcp_client": "connected"}


@app.post("/pipeline/drug")
async def pipeline_by_name(request: DrugNameRequest):
    client = session_mgr.get_client()
    keywords = classifier_qt_terms_ordered()
    try:
        result = await run_pipeline_for_drug(
            client, request.drug_name, request.top_n, request.window, keywords,
        )
    except Exception as e:
        logger.exception("Pipeline error")
        raise HTTPException(500, str(e))
    return result


@app.post("/pipeline/chembl")
async def pipeline_by_chembl(request: ChEMBLRequest):
    client = session_mgr.get_client()
    keywords = classifier_qt_terms_ordered()

    # Build ChEMBLMolecule from request data
    mol_data = {
        "molecule_chembl_id": request.molecule_chembl_id,
        "pref_name": request.pref_name,
        "molecule_synonyms": request.molecule_synonyms or [],
        "activities": request.activities or [],
        "documents": request.documents or [],
    }
    molecule = ChEMBLMolecule.model_validate(mol_data)
    enrichment = build_chembl_enrichment(molecule)

    # Resolve document IDs from activities → PubMed IDs
    doc_ids = extract_doc_ids_from_activities(request.activities)
    if doc_ids:
        logger.info(f"Resolving {len(doc_ids)} ChEMBL document IDs to PubMed IDs")
        doc_pmids = await resolve_chembl_doc_pubmed_ids(doc_ids)
        # Merge into known_pubmed_ids (deduplicate)
        existing = set(enrichment.get("known_pubmed_ids") or [])
        for pid in doc_pmids:
            if pid not in existing:
                existing.add(pid)
                enrichment["known_pubmed_ids"].append(pid)

    drug_name = enrichment.get("pref_name") or request.molecule_chembl_id
    try:
        result = await run_pipeline_for_drug(
            client, drug_name, request.top_n, request.window, keywords,
            chembl_enrichment=enrichment,
        )
    except Exception as e:
        logger.exception("Pipeline error")
        raise HTTPException(500, str(e))
    return result