# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from asyncio import CancelledError, Queue, QueueFull, Task, create_task, gather, sleep
from dataclasses import asdict, dataclass
from logging import getLogger
from typing import Any

from aiohttp import ClientSession, ClientTimeout

logger = getLogger(__name__)


@dataclass
class WebhookPayload:
    event: str  # "job_finished", "job_failed", "job_quarantined"
    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    security: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class WebhookSender:
    def __init__(self, session: ClientSession, worker_count: int = 5):
        self.session = session
        self.timeout = ClientTimeout(total=10)
        self.max_retries = 3
        self._queue: Queue[tuple[str, WebhookPayload]] = Queue(maxsize=1000)
        self._worker_tasks: list[Task[None]] = []
        self._worker_count = worker_count

    def start(self) -> None:
        if not self._worker_tasks:
            for i in range(self._worker_count):
                task = create_task(self._worker(i))
                self._worker_tasks.append(task)
            logger.info(f"WebhookSender started with {self._worker_count} concurrent workers.")

    async def stop(self) -> None:
        if self._worker_tasks:
            # Wait for the queue to be processed before stopping
            if not self._queue.empty():
                logger.info(f"WebhookSender stopping, waiting for {self._queue.qsize()} webhooks to be sent...")
                await self._queue.join()

            for task in self._worker_tasks:
                task.cancel()

            await gather(*self._worker_tasks, return_exceptions=True)
            self._worker_tasks = []
            logger.info("WebhookSender background workers stopped.")

    async def send(self, url: str, payload: WebhookPayload) -> None:
        """
        Queues a webhook to be sent. Non-blocking.
        Drops the message if the queue is full to prevent backpressure.
        """
        try:
            self._queue.put_nowait((url, payload))
        except QueueFull:
            logger.error(
                f"Webhook queue is full! Dropping webhook for job {payload.job_id} to {url}. "
                "Consider increasing queue size or checking external service latency."
            )

    async def _worker(self, worker_id: int) -> None:
        while True:
            try:
                url, payload = await self._queue.get()
                try:
                    await self._send_single(url, payload)
                except Exception as e:
                    logger.exception(f"Unexpected error in webhook worker {worker_id}: {e}")
                finally:
                    self._queue.task_done()
            except CancelledError:
                break

    async def _send_single(self, url: str, payload: WebhookPayload) -> bool:
        """
        Sends a webhook payload to the specified URL with retries.
        Returns True if successful, False otherwise.
        """
        data = asdict(payload)
        for attempt in range(1, self.max_retries + 1):
            try:
                async with self.session.post(url, json=data, timeout=self.timeout) as response:
                    if 200 <= response.status < 300:
                        logger.info(f"Webhook sent successfully to {url} for job {payload.job_id}")
                        return True
                    else:
                        logger.warning(
                            f"Webhook failed for job {payload.job_id} to {url}. "
                            f"Status: {response.status}. Attempt {attempt}/{self.max_retries}"
                        )
            except Exception as e:
                logger.warning(
                    f"Error sending webhook for job {payload.job_id} to {url}: {e}. "
                    f"Attempt {attempt}/{self.max_retries}"
                )

            # Exponential backoff
            if attempt < self.max_retries:
                await sleep(2**attempt)

        logger.error(f"Failed to send webhook for job {payload.job_id} to {url} after {self.max_retries} attempts.")
        return False
