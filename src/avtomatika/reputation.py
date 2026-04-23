# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio
from asyncio import CancelledError, sleep
from logging import getLogger
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .engine import OrchestratorEngine

logger = getLogger(__name__)

# Number of days of history collected to calculate reputation
REPUTATION_HISTORY_DAYS = 30


class ReputationCalculator:
    """A background process for periodically recalculating worker reputations."""

    def __init__(self, engine: "OrchestratorEngine", interval_seconds: int = 3600):
        self.engine = engine
        self.storage = engine.storage
        self.history_storage = engine.history_storage
        self.interval_seconds = interval_seconds
        self._running = False
        self._instance_id = str(uuid4())

    async def run(self):
        """The main loop that periodically triggers reputation recalculation."""
        logger.info(f"ReputationCalculator started (Instance ID: {self._instance_id}).")
        self._running = True
        while self._running:
            try:
                if await self.storage.acquire_lock("global_reputation_lock", self._instance_id, 300):
                    try:
                        await self.calculate_all_reputations()
                    finally:
                        await self.storage.release_lock("global_reputation_lock", self._instance_id)
                else:
                    logger.debug("ReputationCalculator lock held by another instance. Skipping.")
            except CancelledError:
                break
            except Exception:
                logger.exception("Error in ReputationCalculator main loop.")

            await sleep(self.interval_seconds)

        logger.info("ReputationCalculator stopped.")

    def stop(self):
        self._running = False

    async def calculate_all_reputations(self):
        """Calculates and updates the reputation for all active workers."""
        logger.info("Starting reputation calculation for all workers...")

        # Get only IDs of active workers to avoid O(N) scan of all data
        worker_ids = await self.storage.get_active_worker_ids()

        if not worker_ids:
            logger.info("No active workers found for reputation calculation.")
            return

        logger.info(f"Recalculating reputation for {len(worker_ids)} workers.")

        chunk_size = 20

        for i in range(0, len(worker_ids), chunk_size):
            if not self._running:
                break

            chunk = worker_ids[i : i + chunk_size]
            tasks = [self._calculate_single_worker_reputation(wid) for wid in chunk]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Small throttle between chunks
            await asyncio.sleep(0.1)

        logger.info("Reputation calculation finished.")

    async def _calculate_single_worker_reputation(self, worker_id: str) -> None:
        """Helper to calculate and update reputation for a single worker."""
        try:
            history = await self.history_storage.get_worker_history(
                worker_id,
                since_days=REPUTATION_HISTORY_DAYS,
            )

            task_finished_events = [event for event in history if event.get("event_type") == "task_finished"]

            if not task_finished_events:
                return

            worker_info = await self.storage.get_worker_info(worker_id)
            if not worker_info:
                return

            current_reputation = worker_info.get("reputation", 1.0)

            total_tasks = len(task_finished_events)
            successful_tasks = sum(
                1
                for event in task_finished_events
                if event.get("context_snapshot", {}).get("result", {}).get("status") == "success"
            )

            if total_tasks > 0:
                historical_success_rate = successful_tasks / total_tasks
                # Formula: 70% history, 30% current (weighted average)
                new_reputation = (historical_success_rate * 0.7) + (current_reputation * 0.3)

                # Clamp to [0.1, 1.0] to avoid complete exclusion
                new_reputation = max(0.1, min(1.0, new_reputation))

                await self.storage.update_worker_data(worker_id, {"reputation": round(new_reputation, 4)})
                logger.debug(
                    f"Updated reputation for {worker_id}: {new_reputation:.4f} ({successful_tasks}/{total_tasks})"
                )

        except Exception:
            logger.exception(f"Error calculating reputation for worker {worker_id}.")
