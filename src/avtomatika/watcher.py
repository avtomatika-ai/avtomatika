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
        backoff_delay = self.watch_interval_seconds

        while self._running:
            try:
                # Attempt to acquire distributed lock
                if await self.storage.acquire_lock("global_watcher_lock", self._instance_id, 60):
                    try:
                        logger.debug("Watcher running check for timed out jobs...")
                        timed_out_job_ids = await self.storage.get_timed_out_jobs(limit=100)

                        for job_id in timed_out_job_ids:
                            logger.warning(f"Job {job_id} timed out. Processing timeout...")
                            try:
                                job_state = await self.storage.get_job_state(job_id)
                                if not job_state:
                                    continue
                                await self.engine.handle_job_timeout(job_state)
                            except Exception:
                                logger.exception(f"Failed to process timeout for job {job_id}")
                    finally:
                        await self.storage.release_lock("global_watcher_lock", self._instance_id)

                backoff_delay = self.watch_interval_seconds  # Reset on success

            except CancelledError:
                logger.info("Watcher received cancellation request.")
                break
            except Exception:
                logger.exception("Error in Watcher main loop.")
                await sleep(backoff_delay)
                backoff_delay = min(backoff_delay * 2, 600.0)
                continue

            await sleep(self.watch_interval_seconds)

    def stop(self):
        """Stops the watcher."""
        self._running = False
