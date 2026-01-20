"""This module previously contained an active HealthChecker.
In the new architecture with heartbeat messages from workers,
the orchestrator no longer needs to actively poll workers.

Redis automatically deletes worker keys when their TTL expires,
and `storage.get_available_workers()` only retrieves active keys.

This file is left as a placeholder in case passive health-check
logic is needed in the future (e.g., for logging expired workers).
"""

from asyncio import CancelledError, sleep
from logging import getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import OrchestratorEngine

logger = getLogger(__name__)


class HealthChecker:
    def __init__(self, engine: "OrchestratorEngine", interval_seconds: int = 600):
        self.engine = engine
        self.storage = engine.storage
        self.interval_seconds = interval_seconds
        self._running = False
        from uuid import uuid4

        self._instance_id = str(uuid4())

    async def run(self):
        logger.info(f"HealthChecker started (Active Index Cleanup, Instance ID: {self._instance_id}).")
        self._running = True
        while self._running:
            try:
                # Use distributed lock to ensure only one instance cleans up
                if await self.storage.acquire_lock(
                    "global_health_check_lock", self._instance_id, self.interval_seconds - 5
                ):
                    try:
                        await self.storage.cleanup_expired_workers()
                    finally:
                        # We don't release the lock immediately to prevent other instances from
                        # running the same task if the interval is small.
                        pass

                await sleep(self.interval_seconds)
            except CancelledError:
                break
            except Exception:
                logger.exception("Error in HealthChecker main loop.")
                await sleep(60)
        logger.info("HealthChecker stopped.")

    def stop(self):
        self._running = False
