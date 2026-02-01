from asyncio import CancelledError, sleep
from logging import getLogger
from typing import TYPE_CHECKING
from uuid import uuid4

from .constants import JOB_STATUS_FAILED, JOB_STATUS_WAITING_FOR_WORKER

if TYPE_CHECKING:
    from .engine import OrchestratorEngine

logger = getLogger(__name__)


class Watcher:
    """A background process that monitors for "stuck" jobs."""

    def __init__(self, engine: "OrchestratorEngine"):
        self.engine = engine
        self.storage = engine.storage
        self.config = engine.config
        self._running = False
        self.watch_interval_seconds = self.config.WATCHER_INTERVAL_SECONDS
        self._instance_id = str(uuid4())

    async def run(self):
        """The main loop of the watcher."""
        logger.info(f"Watcher started (Instance ID: {self._instance_id}).")
        self._running = True
        while self._running:
            try:
                # Attempt to acquire distributed lock
                # We set TTL slightly longer than the expected execution time (60s)
                if await self.storage.acquire_lock("global_watcher_lock", self._instance_id, 60):
                    try:
                        logger.debug("Watcher running check for timed out jobs...")
                        timed_out_job_ids = await self.storage.get_timed_out_jobs(limit=100)

                        for job_id in timed_out_job_ids:
                            logger.warning(f"Job {job_id} timed out. Moving to failed state.")
                            try:
                                # Get the latest version to avoid overwriting
                                job_state = await self.storage.get_job_state(job_id)
                                if job_state and job_state["status"] == JOB_STATUS_WAITING_FOR_WORKER:
                                    job_state["status"] = JOB_STATUS_FAILED
                                    job_state["error_message"] = "Worker task timed out."
                                    await self.storage.save_job_state(job_id, job_state)

                                    # Increment the metric
                                    from . import metrics

                                    metrics.jobs_failed_total.inc(
                                        {
                                            metrics.LABEL_BLUEPRINT: job_state.get(
                                                "blueprint_name",
                                                "unknown",
                                            ),
                                        },
                                    )
                            except Exception:
                                logger.exception(
                                    f"Failed to update state for timed out job {job_id}",
                                )
                    finally:
                        # Always release the lock so we (or others) can run next time
                        await self.storage.release_lock("global_watcher_lock", self._instance_id)
                else:
                    logger.debug("Watcher lock held by another instance. Skipping check.")

            except CancelledError:
                logger.info("Watcher received cancellation request.")
                break
            except Exception:
                logger.exception("Error in Watcher main loop.")

            # Sleep at the end of iteration
            await sleep(self.watch_interval_seconds)

    def stop(self):
        """Stops the watcher."""
        self._running = False
