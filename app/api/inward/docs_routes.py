"""İç API: belge yönetimi."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import app.config as app_config
from app.api.deps import get_global_store, get_state_store, get_vectordb, require_master

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/docs",
    tags=["Internal — Documents"],
    dependencies=[Depends(require_master)],
)


def _serialize_record(rec: Any) -> dict[str, Any]:
    d = asdict(rec)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat() if v else None
    return d


@router.get("/departments")
async def list_departments(request: Request) -> dict[str, list[str]]:
    gs = get_global_store(request)

    def _run() -> list[str]:
        return gs.list_departments()

    depts = await asyncio.to_thread(_run)
    return {"departments": depts}


@router.get("/sources")
async def list_sources(request: Request) -> dict[str, list[str]]:
    gs = get_global_store(request)

    def _run() -> list[str]:
        return gs.list_sources()

    srcs = await asyncio.to_thread(_run)
    return {"sources": srcs}


@router.get("/flagged")
async def list_flagged_docs(request: Request) -> dict[str, Any]:
    gs = get_global_store(request)

    def _run() -> list:
        return gs.list_flagged()

    docs = await asyncio.to_thread(_run)
    return {
        "count": len(docs),
        "documents": [_serialize_record(d) for d in docs],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("")
async def list_documents(
    request: Request,
    status: str | None = Query(default=None),
    department: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=5000),
) -> dict[str, Any]:
    gs = get_global_store(request)
    s = app_config.settings

    def _run() -> list:
        rows = gs.list_all(limit=limit)
        out = []
        for r in rows:
            d = _serialize_record(r)
            if department and d.get("department") != department:
                continue
            if source and d.get("source") != source:
                continue
            if status == "flagged" and not d.get("is_flagged"):
                continue
            if status == "healthy" and d.get("is_flagged"):
                continue
            out.append(d)
        return out

    documents = await asyncio.to_thread(_run)
    return {"count": len(documents), "documents": documents}


@router.get("/{doc_id}")
async def get_document(request: Request, doc_id: str) -> dict[str, Any]:
    gs = get_global_store(request)

    def _run():
        return gs.get(doc_id)

    rec = await asyncio.to_thread(_run)
    if rec is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize_record(rec)


from pydantic import BaseModel, Field


class FlagRequest(BaseModel):
    reason: str = Field(default="", min_length=0)


@router.post("/{doc_id}/flag")
async def flag_document(
    request: Request,
    doc_id: str,
    body: FlagRequest,
) -> dict[str, Any]:
    gs = get_global_store(request)
    s = app_config.settings

    def _flag():
        gs.flag(doc_id, body.reason)
        return gs.get(doc_id)

    rec = await asyncio.to_thread(_flag)
    if rec is None:
        raise HTTPException(status_code=404, detail="Document not found")
    logger.info("dashboard_flagged doc_id=%s user=%s reason=%s", doc_id, s.user_id, body.reason)
    return _serialize_record(rec)


@router.post("/{doc_id}/unflag")
async def unflag_document(request: Request, doc_id: str) -> dict[str, Any]:
    gs = get_global_store(request)

    def _run():
        gs.unflag(doc_id)
        return gs.get(doc_id)

    rec = await asyncio.to_thread(_run)
    if rec is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize_record(rec)


@router.delete("/{doc_id}")
async def delete_document(request: Request, doc_id: str) -> dict[str, Any]:
    s = app_config.settings
    gs = get_global_store(request)
    st = get_state_store(request)
    vd = get_vectordb(request)

    g_ok = False
    try:
        await asyncio.to_thread(gs.delete, doc_id)
        g_ok = True
    except Exception:
        logger.warning("global_store_delete_failed doc_id=%s", doc_id, exc_info=True)

    st_ok = False
    try:
        await asyncio.to_thread(st.delete, s.user_id, doc_id)
        st_ok = True
    except Exception:
        logger.warning("state_store_delete_failed doc_id=%s", doc_id, exc_info=True)

    v_ok = False
    removed = 0
    if vd is not None:
        try:
            coll = (
                s.pinecone_index_name.strip()
                if s.vectordb_backend.strip().lower() == "pinecone"
                and (s.pinecone_index_name or "").strip()
                else s.qdrant_collection
            )
            removed = int(await vd.delete_by_doc_id(coll, doc_id))
            v_ok = True
        except Exception:
            logger.warning("vectordb_delete_failed doc_id=%s", doc_id, exc_info=True)

    return {
        "doc_id": doc_id,
        "global_store_deleted": g_ok,
        "state_store_deleted": st_ok,
        "vectordb_deleted": v_ok,
        "vectordb_points_removed": removed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/{doc_id}/reset")
async def reset_document(request: Request, doc_id: str) -> dict[str, Any]:
    gs = get_global_store(request)

    def _run():
        gs.unflag(doc_id)
        gs.update_decay_score(doc_id, 1.0)
        return gs.get(doc_id)

    rec = await asyncio.to_thread(_run)
    if rec is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize_record(rec)
