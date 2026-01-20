import asyncio
import contextlib
from abc import ABC, abstractmethod
from logging import getLogger
from typing import Any

logger = getLogger(__name__)


class HistoryStorageBase(ABC):
    """Abstract base class for a history store.
    Implements buffered asynchronous logging to avoid blocking the main loop.
    """

    def __init__(self):
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=5000)
        self._worker_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Starts the background worker for writing logs."""
        if not self._worker_task:
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("HistoryStorage background worker started.")

    async def close(self) -> None:
        """Stops the background worker and closes resources."""
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
            logger.info("HistoryStorage background worker stopped.")

    @abstractmethod
    async def initialize(self) -> None:
        """Performs initialization, e.g., creating tables in the DB."""
        raise NotImplementedError

    async def log_job_event(self, event_data: dict[str, Any]) -> None:
        """Queues a job event for logging."""
        try:
            self._queue.put_nowait(("job", event_data))
        except asyncio.QueueFull:
            logger.warning("History queue full! Dropping job event.")

    async def log_worker_event(self, event_data: dict[str, Any]) -> None:
        """Queues a worker event for logging."""
        try:
            self._queue.put_nowait(("worker", event_data))
        except asyncio.QueueFull:
            logger.warning("History queue full! Dropping worker event.")

    async def _worker(self) -> None:
        while True:
            try:
                kind, data = await self._queue.get()
                try:
                    if kind == "job":
                        await self._persist_job_event(data)
                    elif kind == "worker":
                        await self._persist_worker_event(data)
                except Exception as e:
                    logger.error(f"Error persisting history event: {e}")
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break

    @abstractmethod
    async def _persist_job_event(self, event_data: dict[str, Any]) -> None:
        """Actual implementation of writing a job event to storage."""
        raise NotImplementedError

    @abstractmethod
    async def _persist_worker_event(self, event_data: dict[str, Any]) -> None:
        """Actual implementation of writing a worker event to storage."""
        raise NotImplementedError

    @abstractmethod
    async def get_job_history(self, job_id: str) -> list[dict[str, Any]]:
        """Gets the full history for the specified job."""
        raise NotImplementedError

    @abstractmethod
    async def get_jobs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Gets a paginated list of recent jobs.
        Primarily returns the last event for each job.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_job_summary(self) -> dict[str, int]:
        """Returns a summary of job statuses.
        Example: {'running': 10, 'completed': 50, 'failed': 5}
        """
        raise NotImplementedError

    @abstractmethod
    async def get_worker_history(
        self,
        worker_id: str,
        since_days: int,
    ) -> list[dict[str, Any]]:
        """Gets the event history for a specific worker for the last N days."""
        raise NotImplementedError
