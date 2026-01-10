from abc import ABC, abstractmethod
from typing import Any


class HistoryStorageBase(ABC):
    """Abstract base class for a history store.
    Defines the interface for logging job and worker events.
    """

    @abstractmethod
    async def initialize(self):
        """Performs initialization, e.g., creating tables in the DB."""
        raise NotImplementedError

    @abstractmethod
    async def log_job_event(self, event_data: dict[str, Any]):
        """Logs an event related to the job lifecycle."""
        raise NotImplementedError

    @abstractmethod
    async def log_worker_event(self, event_data: dict[str, Any]):
        """Logs an event related to the worker lifecycle."""
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
