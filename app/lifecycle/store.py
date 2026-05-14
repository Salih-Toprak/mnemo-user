"""rag-wiki mağazalarını başlatır."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from rag_wiki.storage.global_store import GlobalDocStore
from rag_wiki.storage.sqlite import SQLiteStateStore

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


def init_stores(settings: Settings) -> tuple[SQLiteStateStore, GlobalDocStore]:
    """
    Bu kullanıcı konteyneri için rag-wiki mağazalarını başlatır.
    Veri dizini yoksa oluşturur. Her iki mağaza aynı SQLite dosyasına yazar.
    """
    db_dir = os.path.dirname(settings.db_path)
    os.makedirs(db_dir, exist_ok=True)

    state_store = SQLiteStateStore(settings.db_url)
    global_store = GlobalDocStore(settings.db_url)
    logger.info("stores_initialized db_path=%s", settings.db_path)
    return state_store, global_store
