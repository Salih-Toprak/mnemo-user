"""Dış API: kullanıcı sorguları."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import app.config as app_config
from app import state as app_state
from app.api.deps import get_pipeline, require_user
from app.query.pipeline import QueryPipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["Query — User Facing"])


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    system_prompt: str | None = None
    top_k: int | None = Field(default=None, ge=1)
    include_provenance: bool = True

    @field_validator("query")
    @classmethod
    def strip_query(cls, v: str) -> str:
        t = (v or "").strip()
        if not t:
            raise ValueError("Query must not be empty")
        return t


@router.get("/health")
async def query_health() -> dict[str, str]:
    s = app_config.settings
    model = (
        s.openai_llm_model
        if s.llm_backend.strip().lower() == "openai"
        else s.ollama_llm_model
    )
    return {"status": "ok", "user_id": s.user_id, "model": model}


@router.post("", dependencies=[Depends(require_user)])
async def post_query(
    body: QueryRequest,
    pipeline: QueryPipeline = Depends(get_pipeline),
) -> dict[str, Any]:
    if app_state.get_vectordb() is None:
        raise HTTPException(
            status_code=503,
            detail="Vector database is not available. Check VECTORDB_* settings and connectivity.",
        )
    if app_state.get_embedder() is None:
        raise HTTPException(
            status_code=503,
            detail="Embedding service is not available. Check EMBEDDING_* settings and connectivity.",
        )
    t0 = time.perf_counter()
    qid = str(uuid.uuid4())
    try:
        result = await pipeline.query(
            body.query.strip(),
            system_prompt=body.system_prompt,
            top_k=body.top_k,
        )
    except RuntimeError as e:
        msg = str(e).lower()
        if "ollama" in msg or "openai" in msg or "llm" in msg:
            raise HTTPException(status_code=503, detail=str(e)) from e
        raise
    except Exception as e:
        msg = str(e).lower()
        if "vector" in msg or "qdrant" in msg or "pinecone" in msg:
            raise HTTPException(
                status_code=503,
                detail="Vector database query failed.",
            ) from e
        raise
    latency_ms = int((time.perf_counter() - t0) * 1000)
    out: dict[str, Any] = {
        "answer": result["answer"],
        "sources": result["sources"],
        "model": result["model"],
        "user_id": result["user_id"],
        "query_id": qid,
        "latency_ms": latency_ms,
    }
    if body.include_provenance:
        out["provenance"] = result.get("provenance") or {}
    return out
