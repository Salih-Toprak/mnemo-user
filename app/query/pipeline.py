"""Sorgu hattı: yaşam döngülü getirme + LLM yanıtı."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from openai import AsyncOpenAI

from app.lifecycle.retriever import LifecycleRetriever

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """You are a helpful internal assistant.
Answer questions using only the provided context from the knowledge base.
If the context does not contain enough information, say so honestly.
Always cite the source (channel name or page title) when referencing information.
Be concise and professional."""


class QueryPipeline:
    def __init__(
        self,
        user_id: str,
        lifecycle_retriever: LifecycleRetriever,
        global_store: Any,
        settings: "Settings",
    ) -> None:
        self._user_id = user_id
        self._lifecycle = lifecycle_retriever
        self._global_store = global_store
        self._settings = settings
        self._http_client = httpx.AsyncClient(timeout=settings.llm_timeout)
        self._openai_llm: AsyncOpenAI | None = None
        if (settings.openai_llm_api_key or "").strip():
            self._openai_llm = AsyncOpenAI(
                api_key=settings.openai_llm_api_key.strip(),
                timeout=settings.llm_timeout,
            )

    def bind_settings(self, settings: "Settings") -> None:
        """runtime_config güncellemelerinde ayar referansını yeniler."""
        self._settings = settings
        if (settings.openai_llm_api_key or "").strip():
            self._openai_llm = AsyncOpenAI(
                api_key=settings.openai_llm_api_key.strip(),
                timeout=settings.llm_timeout,
            )

    async def query(
        self,
        user_message: str,
        system_prompt: str | None = None,
        top_k: int | None = None,
    ) -> dict:
        from app import config

        eff = config.settings
        docs, provenance = await self._lifecycle.retrieve(
            user_message,
            top_k=top_k if top_k is not None else eff.rag_wiki_top_k,
        )
        for d in docs:
            doc_id = None
            try:
                doc_id = (d.metadata or {}).get("doc_id")
                if doc_id:
                    self._global_store.increment_fetch(str(doc_id), self._user_id)
            except Exception:
                logger.debug("increment_fetch_failed doc_id=%s", doc_id, exc_info=True)
        context = self._build_context(docs)
        sys_p = system_prompt or DEFAULT_SYSTEM_PROMPT
        prompt = f"{sys_p}\n\nContext:\n{context}\n\nQuestion:\n{user_message}\n\nAnswer:"
        eff2 = config.settings
        backend = eff2.llm_backend.strip().lower()
        if backend == "openai":
            answer = await self._call_openai(prompt, eff2)
            model = eff2.openai_llm_model
        else:
            answer = await self._call_ollama(prompt, eff2)
            model = eff2.ollama_llm_model
        sources = self._sources_from_docs_and_provenance(docs, provenance)
        prov_block = self._provenance_dict(provenance, len(docs))
        return {
            "answer": answer,
            "sources": sources,
            "provenance": prov_block,
            "model": model,
            "user_id": self._user_id,
        }

    def _provenance_dict(self, provenance: Any, total_docs: int) -> dict:
        cache_hits = 0
        if provenance is not None and getattr(provenance, "sources", None):
            cache_hits = sum(1 for s in provenance.sources if getattr(s, "from_cache", False))
        return {
            "cache_hits": cache_hits,
            "global_hits": max(0, total_docs - cache_hits),
            "total_retrieved": total_docs,
        }

    def _sources_from_docs_and_provenance(
        self,
        docs: list,
        provenance: Any,
    ) -> list[dict]:
        prov_by_id: dict[str, Any] = {}
        if provenance is not None and getattr(provenance, "sources", None):
            for s in provenance.sources:
                prov_by_id[s.doc_id] = s
        out: list[dict] = []
        for doc in docs:
            meta = dict(doc.metadata or {})
            doc_id = str(meta.get("doc_id") or meta.get("id") or "")
            title = str(meta.get("doc_title") or meta.get("title") or doc_id or "document")
            source = str(meta.get("source") or meta.get("ac_source_id") or "")
            channel = meta.get("channel")
            if channel is not None:
                channel = str(channel)
            score = float(meta.get("score") or 0.0)
            state = "GLOBAL"
            p = prov_by_id.get(doc_id)
            if p is not None:
                st = getattr(p, "user_state", None)
                state = st.value if hasattr(st, "value") else str(st or "GLOBAL")
            elif meta.get("user_state"):
                state = str(meta["user_state"])
            out.append(
                {
                    "doc_id": doc_id,
                    "doc_title": title,
                    "source": source,
                    "channel": channel,
                    "score": score,
                    "state": state,
                },
            )
        return out

    def _build_context(self, docs: list) -> str:
        lines: list[str] = []
        for i, doc in enumerate(docs, start=1):
            meta = doc.metadata or {}
            title = meta.get("doc_title") or meta.get("title") or f"Document {i}"
            src = meta.get("source") or meta.get("channel") or ""
            header = f"[{i}] {title}" + (f" — {src}" if src else "")
            lines.append(header)
            lines.append(doc.page_content or "")
            lines.append("")
        if not lines:
            return "(No relevant documents found in the knowledge base.)"
        return "\n".join(lines).strip()

    async def _call_ollama(self, prompt: str, eff: "Settings") -> str:
        url = f"{eff.ollama_llm_url.rstrip('/')}/api/generate"
        body = {
            "model": eff.ollama_llm_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": float(eff.llm_temperature),
                "num_predict": int(eff.llm_max_tokens),
            },
        }
        try:
            r = await self._http_client.post(url, json=body)
            r.raise_for_status()
            data = r.json()
            return str(data.get("response", "")).strip()
        except Exception as e:
            raise RuntimeError(f"Ollama LLM error: {e!s}") from e

    async def _call_openai(self, prompt: str, eff: "Settings") -> str:
        client = self._openai_llm
        if client is None:
            raise RuntimeError("OpenAI LLM client is not configured (missing API key).")
        try:
            resp = await client.chat.completions.create(
                model=eff.openai_llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=float(eff.llm_temperature),
                max_tokens=int(eff.llm_max_tokens),
            )
            choice = resp.choices[0]
            content = choice.message.content or ""
            return str(content).strip()
        except Exception as e:
            raise RuntimeError(f"OpenAI LLM error: {e!s}") from e

    async def close(self) -> None:
        await self._http_client.aclose()
        if self._openai_llm is not None:
            await self._openai_llm.close()
