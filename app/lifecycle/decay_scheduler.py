"""Zamanlanmış decay döngüsü."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from rag_wiki.lifecycle.decay_engine import DecayConfig, DecayEngine
from rag_wiki.lifecycle.state_machine import StateMachine

logger = logging.getLogger(__name__)


class DecayScheduler:
    def __init__(
        self,
        user_id: str,
        state_store: object,
        interval_hours: int = 24,
    ) -> None:
        self._user_id = user_id
        self._engine = DecayEngine(
            store=state_store,
            state_machine=StateMachine(),
            config=DecayConfig(),
        )
        self._interval = interval_hours
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._run,
            trigger="interval",
            hours=self._interval,
            id="decay_cycle",
            name=f"Decay cycle for {self._user_id}",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "decay_scheduler_started user=%s interval_hours=%d",
            self._user_id,
            self._interval,
        )

    async def _run(self) -> None:
        try:
            results = self._engine.run_for_user(self._user_id)
            transitions = [r for r in results if r.transitioned]
            logger.info(
                "decay_cycle_complete user=%s docs=%d transitions=%d",
                self._user_id,
                len(results),
                len(transitions),
            )
        except Exception:
            logger.error("decay_cycle_failed user=%s", self._user_id, exc_info=True)

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
