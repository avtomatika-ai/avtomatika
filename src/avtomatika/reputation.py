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

                for event in task_finished_events:
                    # Extract the result from the snapshot
                    snapshot = event.get("context_snapshot", {})
                    result = snapshot.get("result", {})
                    status = result.get("status")

                    if status == "success":
                        successful_tasks += 1
                    elif status == "failure":
                        failed_tasks += 1
                    # "cancelled" is ignored - it's not the worker's fault

                total_relevant = successful_tasks + failed_tasks
                if total_relevant > 0:
                    new_reputation = successful_tasks / total_relevant
                    new_reputation = round(new_reputation, 4)

                    await self.storage.update_worker_data(
                        worker_id,
                        {"reputation": new_reputation},
                    )

                # Throttling: Small sleep to prevent DB spikes
                await sleep(0.1)

            except Exception as e:
                logger.error(f"Failed to calculate reputation for worker {worker_id}: {e}")

        logger.info("Reputation calculation finished.")
