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
        workers = await self.storage.get_available_workers()
        if not workers:
            logger.info("No active workers found for reputation calculation.")
            return

        for worker in workers:
            worker_id = worker.get("worker_id")
            if not worker_id:
                continue

            history = await self.history_storage.get_worker_history(
                worker_id,
                since_days=REPUTATION_HISTORY_DAYS,
            )

            # Count only task completion events
            task_finished_events = [event for event in history if event.get("event_type") == "task_finished"]

            if not task_finished_events:
                # If there is no history, the reputation does not change (remains 1.0 by default)
                continue

            successful_tasks = 0
            for event in task_finished_events:
                # Extract the result from the snapshot
                snapshot = event.get("context_snapshot", {})
                result = snapshot.get("result", {})
                if result.get("status") == "success":
                    successful_tasks += 1

            total_tasks = len(task_finished_events)
            new_reputation = successful_tasks / total_tasks if total_tasks > 0 else 1.0

            # Round for cleanliness
            new_reputation = round(new_reputation, 4)

            logger.info(
                f"Updating reputation for worker {worker_id}: {worker.get('reputation')} -> {new_reputation}",
            )
            await self.storage.update_worker_data(
                worker_id,
                {"reputation": new_reputation},
            )

        logger.info("Reputation calculation finished.")
