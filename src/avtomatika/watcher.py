# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio
from asyncio import CancelledError, sleep
from logging import getLogger
from socket import gethostname
from typing import Any

logger = getLogger(__name__)


class Watcher:
    """
    Background process that monitors job timeouts and handles expired tasks.
    It runs periodically and checks for jobs that have exceeded their
    deadlines (dispatch or execution).
    """

    def __init__(self, engine: Any):
        self.engine = engine
        self.storage = engine.storage
        self.config = engine.config
        self.watch_interval_seconds: int = self.config.WATCHER_INTERVAL_SECONDS
        self._instance_id: str = self.config.INSTANCE_ID or gethostname()
        self._running = False

    async def run(self):
        """The main loop of the watcher."""
        logger.info(f"Watcher started (Instance ID: {self._instance_id}).")
        self._running = True
        backoff_delay = self.watch_interval_seconds

        # Parallelize timeout processing
        semaphore = asyncio.Semaphore(50)

        while self._running:
            try:
                if await self.storage.acquire_lock("global_watcher_lock", self._instance_id, 60):
                    try:
                        logger.debug("Watcher running check for timed out jobs...")
                        limit = int(getattr(self.config, "WATCHER_LIMIT", 100))
                        timed_out_job_ids = await self.storage.get_timed_out_jobs(limit=limit)

                        if timed_out_job_ids:
                            logger.warning(f"Found {len(timed_out_job_ids)} timed out jobs. Processing...")

                            async def _process_single(job_id: str) -> None:
                                async with semaphore:
                                    try:
                                        job_state = await self.storage.get_job_state(job_id)
                                        if job_state:
                                            await self.engine.handle_job_timeout(job_state)
                                    except Exception:
                                        logger.exception(f"Failed to process timeout for job {job_id}")

                            tasks = [_process_single(jid) for jid in timed_out_job_ids]
                            await asyncio.gather(*tasks, return_exceptions=True)

                    finally:
                        await self.storage.release_lock("global_watcher_lock", self._instance_id)

                backoff_delay = self.watch_interval_seconds

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
