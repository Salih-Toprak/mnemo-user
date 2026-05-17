"""FastAPI giriş noktası ve uygulama ömrü."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import app.config as app_config
import app.state as state
from app.api.inward.config_routes import router as internal_config_router
from app.api.inward.docs_routes import router as internal_docs_router
from app.api.inward.health_routes import router as internal_health_router
from app.api.outward.query_routes import router as query_router
from app.config import settings
from app.embeddings.factory import get_embedding_adapter
from app.lifecycle.decay_scheduler import DecayScheduler
from app.lifecycle.retriever import LifecycleRetriever
from app.lifecycle.store import init_stores
from app.mcp.server import build_mcp_server
from app.query.pipeline import QueryPipeline
from app.vectordb.factory import get_vector_db_adapter
from rag_wiki import RagWikiRetrieverConfig

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    state._startup_time = time.monotonic()
    logger.info(
        "Starting Belleq Container: user_id=%s type=%s",
        settings.user_id,
        settings.container_type,
    )

    state_store, global_store = init_stores(settings)
    state._state_store = state_store
    state._global_store = global_store
    logger.info("Stores initialized: %s", settings.db_path)

    try:
        vectordb = get_vector_db_adapter(settings)
        health = await vectordb.health()
        state._vectordb = vectordb
        if health.get("status") == "ok":
            logger.info("Vector DB connected: backend=%s", settings.vectordb_backend)
        else:
            logger.warning("Vector DB unhealthy: %s", health)
    except Exception as e:
        logger.warning("Vector DB init failed: %s", e)
        state._vectordb = None

    try:
        embedder = get_embedding_adapter(settings)
        eh = await embedder.health()
        state._embedder = embedder
        logger.info(
            "Embedder ready: backend=%s model=%s",
            settings.embedding_backend,
            eh.get("model"),
        )
    except Exception as e:
        logger.warning("Embedder init failed: %s", e)
        state._embedder = None

    cfg = RagWikiRetrieverConfig(
        fetch_threshold=settings.rag_wiki_fetch_threshold,
    )
    retriever = LifecycleRetriever(
        user_id=settings.user_id,
        vectordb=state._vectordb,
        embedder=state._embedder,
        state_store=state_store,
        config=cfg,
        collection_name=(
            settings.pinecone_index_name.strip()
            if settings.vectordb_backend.strip().lower() == "pinecone"
            and (settings.pinecone_index_name or "").strip()
            else settings.qdrant_collection
        ),
        top_k=settings.rag_wiki_top_k,
    )
    retriever.build()
    state._lifecycle_retriever = retriever

    pipeline = QueryPipeline(
        user_id=settings.user_id,
        lifecycle_retriever=retriever,
        global_store=global_store,
        settings=settings,
    )
    state._pipeline = pipeline
    app.state.pipeline = pipeline
    app.state.state_store = state_store
    app.state.global_store = global_store
    app.state.lifecycle_retriever = retriever
    app.state.vectordb = state._vectordb
    app.state.embedder = state._embedder

    decay = DecayScheduler(
        user_id=settings.user_id,
        state_store=state_store,
        interval_hours=settings.rag_wiki_decay_interval_hours,
    )
    decay.start()
    app.state.decay_scheduler = decay

    if settings.mcp_enabled:
        mcp_server = build_mcp_server(pipeline, settings)
        app.mount("/mcp", mcp_server.http_app(transport="sse"))
        logger.info(
            "MCP server mounted at /mcp (SSE) name=%s",
            settings.resolved_mcp_server_name,
        )

    logger.info(
        "Belleq Container ready: user_id=%s port=%d",
        settings.user_id,
        settings.app_port,
    )
    yield

    app.state.decay_scheduler.stop()
    await pipeline.close()
    emb = state._embedder
    if emb is not None and hasattr(emb, "aclose"):
        try:
            await emb.aclose()
        except Exception:
            logger.debug("embedder_aclose_failed", exc_info=True)
    logger.info("Belleq Container shut down: user_id=%s", settings.user_id)


app = FastAPI(
    title="Belleq Container",
    description="""
    Per-user knowledge lifecycle container for the Belleq platform.

    Two API surfaces:
    - `/internal/*` — called by the Belleq master API (dashboard control)
    - `/query` — called by end users via API key or MCP

    Authentication:
    - `/internal/*` requires `X-Master-Key` header
    - `POST /query` requires `X-Api-Key` header
    - Both keys can be left empty for dev mode
    """,
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    logger.error("unhandled_exception path=%s", request.url.path, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
async def public_health() -> dict:
    s = app_config.settings
    return {
        "status": "ok",
        "user_id": s.user_id,
        "container_type": s.container_type,
        "mcp_enabled": s.mcp_enabled,
    }


app.include_router(internal_health_router)
app.include_router(internal_docs_router)
app.include_router(internal_config_router)
app.include_router(query_router)
