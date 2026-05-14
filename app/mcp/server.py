"""MCP (Model Context Protocol) sunucusu."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from app.query.pipeline import QueryPipeline

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


def build_mcp_server(pipeline: QueryPipeline, settings: "Settings") -> FastMCP:
    """
    Tek araçlı MCP sunucusu: query_knowledge_base.
    FastAPI uygulamasında /mcp altına monte edilir.
    """

    mcp = FastMCP(settings.resolved_mcp_server_name)

    @mcp.tool()
    async def query_knowledge_base(query: str) -> str:
        """
        Query the Mnemo knowledge base.

        Search through your organization's ingested documents
        (Slack messages, Notion pages, uploaded files) and return
        a synthesized answer with source citations.

        Args:
            query: The question or topic to search for.

        Returns:
            A text answer with cited sources.
        """
        result = await pipeline.query(query)
        answer = result["answer"]
        sources = result["sources"]
        if sources:
            source_lines = "\n".join(
                f"- {s['doc_title']} ({s['source']})" for s in sources
            )
            answer += f"\n\nSources:\n{source_lines}"
        return answer

    logger.info("mcp_server_built name=%s", settings.resolved_mcp_server_name)
    return mcp
