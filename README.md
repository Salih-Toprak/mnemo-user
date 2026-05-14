# mnemo-user

Headless **per-user knowledge lifecycle container** for the [Mnemo](https://github.com/sstprk) platform (`sstprk` org). It is **not** a browser application: it is a FastAPI service that sits between the **master** stack (shared vector database, ingestion) and end-user tools (chatbots, MCP clients, custom integrations).

Each deployment runs **one** `USER_ID` (chatbot, employee, agent, …). All **rag-wiki** lifecycle state is stored under `/app/data/{USER_ID}/` on disk (SQLite + JSON overrides) and survives restarts.

---

## Two API surfaces

| Surface | Base path | Auth | Purpose |
|--------|-----------|------|---------|
| **Inward** (master / dashboard) | `/internal/*` | `X-Master-Key` | Health, stats, document CRUD, runtime config |
| **Outward** (users / bots / MCP) | `POST /query`, MCP | `X-Api-Key` on HTTP query (`/query/health` has no auth) | RAG answers with citations |

OpenAPI: **`/docs`**, **`/redoc`**.

---

## Quick start

1. Create a Docker network (once), e.g. from **mnemo-master**: `docker network create mnemo-net`
2. `cp .env.example .env` and set at least **`USER_ID`**
3. `docker compose up --build`
4. Call **`POST /query`** with a JSON body and optional `X-Api-Key` if `USER_API_KEY` is set

Example:

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-user-key" \
  -d '{"query":"What is our vacation policy?","include_provenance":true}'
```

---

## Claude Desktop (MCP over SSE)

Set `MCP_ENABLED=true` (default). The MCP app is mounted at **`/mcp`** using FastMCP’s **SSE** transport. The SSE entry is typically:

`http://<container-host>:8000/mcp/sse`

Add to **Claude Desktop** config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mnemo": {
      "url": "http://your-container-host:8000/mcp/sse"
    }
  }
}
```

MCP shares the **same** `QueryPipeline` instance as the HTTP API (no duplicate pipeline).

---

## API key example (`/query`)

```bash
curl -sS -X POST "http://localhost:8000/query" \
  -H "X-Api-Key: $USER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"Summarize onboarding docs"}'
```

If `USER_API_KEY` is empty, outward query auth is **open** (dev only).

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `USER_ID` | **Yes** | Unique instance id (directory name + rag-wiki scope) |
| `DISPLAY_NAME` | No | Dashboard label |
| `CONTAINER_TYPE` | No | `user` \| `chatbot` \| `agent` (default `user`) |
| `DATA_DIR` | No | Root data dir (default `/app/data`) |
| `VECTORDB_BACKEND` | No | `qdrant` (default) or `pinecone` |
| `QDRANT_URL` / `VECTORDB_URL` | No* | Qdrant HTTP URL (*required in practice for Qdrant) |
| `QDRANT_API_KEY` | No | Qdrant API key if enabled |
| `QDRANT_COLLECTION` | No | Collection / logical index name |
| `PINECONE_*` | If Pinecone | See `.env.example` |
| `EMBEDDING_BACKEND` | No | `ollama` or `openai` |
| `OLLAMA_*` / `OPENAI_*` | If used | Embedding + optional LLM keys |
| `LLM_BACKEND` | No | `ollama` or `openai` |
| `RAG_WIKI_*` | No | Fetch threshold, decay interval, top-k |
| `MASTER_API_KEY` | No | Master `X-Master-Key` (empty = open) |
| `USER_API_KEY` | No | User `X-Api-Key` (empty = open) |
| `MCP_ENABLED` | No | Mount MCP sub-app |
| `MCP_SERVER_NAME` | No | Defaults to `mnemo-{USER_ID}` |
| `APP_PORT` | No | Host port in compose mapping (container listens on **8000**) |
| `LOG_LEVEL` | No | Python logging level |

---

## Data persistence

- **Volume**: mount host dir to **`/app/data`**
- **Files**:
  - `{DATA_DIR}/{USER_ID}/mnemo.db` — rag-wiki `SQLiteStateStore` + `GlobalDocStore`
  - `{DATA_DIR}/{USER_ID}/runtime_config.json` — PATCHable runtime overrides

If the volume is **lost**, per-user lifecycle + registry rows are gone; **vectors in Qdrant/Pinecone** remain until explicitly deleted.

---

## Multiple containers (example)

`docker-compose.yml` maps host `${APP_PORT:-8000}` → container `8000`. Run several stacks with different `USER_ID` and host ports:

```yaml
services:
  user001:
    build: .
    env_file: .env.user001
    ports: ["8100:8000"]
    volumes: ["./data-user001:/app/data"]
    networks: [mnemo-net]
  user002:
    build: .
    env_file: .env.user002
    ports: ["8200:8000"]
    volumes: ["./data-user002:/app/data"]
    networks: [mnemo-net]
networks:
  mnemo-net: { external: true, name: mnemo-net }
```

---

## Registering with mnemo-master

Point the master registry at this container’s **base URL** (scheme + host + port):

```bash
curl -sS -X POST "http://<master-host>:<master-port>/master/registry/containers" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "base_url": "http://mnemo-container-user-001:8000",
    "display_name": "User 001 Assistant"
  }'
```

(Exact master path and body may match your mnemo-master API.)

---

## Runtime vs restart configuration

**Adjustable at runtime** (via `PATCH /internal/config` and persisted in `runtime_config.json`):

- `display_name`, `rag_wiki_fetch_threshold`, `rag_wiki_top_k`, `llm_temperature`, `llm_max_tokens`, `mcp_enabled`

**Requires container restart** (documented in PATCH response `patch_notes`):

- Vector DB backend/URL, embedding backend/model, LLM backend/model, `app_port` / image CMD port

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export USER_ID=local-dev
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Repository layout

- `app/vectordb/` and `app/embeddings/` — duplicated from **mnemo-master** adapters (see file headers); keep in sync manually.
- `app/lifecycle/` — rag-wiki stores, retriever, APScheduler decay job
- `app/query/` — RAG + LLM pipeline
- `app/mcp/` — FastMCP SSE server
- `app/api/` — inward + outward routers

Master repo path (local): `../mnemo-master`.
