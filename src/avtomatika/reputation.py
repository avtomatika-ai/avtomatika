# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


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
                # Attempt to acquire lock
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

        for worker_id in worker_ids:
            if not self._running:
                break

            try:
                history = await self.history_storage.get_worker_history(
                    worker_id,
                    since_days=REPUTATION_HISTORY_DAYS,
                )

                # Count only task completion events
                task_finished_events = [event for event in history if event.get("event_type") == "task_finished"]

                if not task_finished_events:
                    # If there is no history, skip to next worker
                    continue

                successful_tasks = 0
                failed_tasks = 0
                contract_violations = 0

                for event in task_finished_events:
                    # Extract the result from the snapshot
                    snapshot = event.get("context_snapshot", {})
                    result = snapshot.get("result", {})
                    status = result.get("status")
                    error = result.get("error", {})
                    error_code = error.get("code") if isinstance(error, dict) else None

                    if status == "success":
                        successful_tasks += 1
                    elif status == "failure":
                        if error_code == "CONTRACT_VIOLATION_ERROR":
                            contract_violations += 1
                        else:
                            failed_tasks += 1

                # HLN REPUTATION FORMULA: Violations are much more costly than simple failures
                # Penalty weight: 10x
                total_weight = successful_tasks + failed_tasks + (contract_violations * 10)

                if total_weight > 0:
                    # Statistical score from history
                    statistical_score = successful_tasks / total_weight

                    # Get current operative reputation from Redis
                    worker_info = await self.storage.get_worker_info(worker_id)
                    try:
                        current_reputation = float(worker_info.get("reputation", 1.0)) if worker_info else 1.0
                    except (TypeError, ValueError):
                        current_reputation = 1.0

                    # HLN SMOOTHING: 70% history, 30% current operative state
                    # This ensures penalties aren't instantly forgotten
                    new_reputation = (statistical_score * 0.7) + (current_reputation * 0.3)
                    new_reputation = round(min(1.0, new_reputation), 4)

                    await self.storage.update_worker_data(
                        worker_id,
                        {"reputation": new_reputation},
                    )

                # Throttling: Small sleep to prevent DB spikes
                await sleep(0.1)

            except Exception as e:
                logger.error(f"Failed to calculate reputation for worker {worker_id}: {e}")

        logger.info("Reputation calculation finished.")
