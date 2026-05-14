"""Kullanıcı yaşam döngüsü sayımları (SQLiteStateStore)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from rag_wiki.storage.base import DocumentState

logger = logging.getLogger(__name__)


def _count_state(state_store: Any, user_id: str, state: DocumentState) -> int:
    try:
        t = state_store._table
        eng = state_store._engine
        with eng.connect() as conn:
            return int(
                conn.execute(
                    select(func.count())
                    .select_from(t)
                    .where(
                        t.c.user_id == user_id,
                        t.c.user_state == state.value,
                    ),
                ).scalar_one()
            )
    except Exception:
        logger.debug("lifecycle_count_state_failed", exc_info=True)
        return 0


def _sum_fetch_counts(state_store: Any, user_id: str) -> int:
    try:
        t = state_store._table
        eng = state_store._engine
        with eng.connect() as conn:
            raw = conn.execute(
                select(func.coalesce(func.sum(t.c.fetch_count), 0)).where(
                    t.c.user_id == user_id,
                ),
            ).scalar_one()
            return int(raw or 0)
    except Exception:
        logger.debug("lifecycle_sum_fetch_failed", exc_info=True)
        return 0


def lifecycle_aggregates(state_store: Any, user_id: str) -> dict[str, int]:
    return {
        "claimed_docs": _count_state(state_store, user_id, DocumentState.CLAIMED),
        "pinned_docs": _count_state(state_store, user_id, DocumentState.PINNED),
        "surfaced_docs": _count_state(state_store, user_id, DocumentState.SURFACED),
        "demoted_docs": _count_state(state_store, user_id, DocumentState.DEMOTED),
        "total_fetch_count": _sum_fetch_counts(state_store, user_id),
    }
