from typing import Any

from .base import HistoryStorageBase


class NoOpHistoryStorage(HistoryStorageBase):
    """A "null" implementation of the history store that performs no actions.
    Used when history storage is not configured.
    """

    def __init__(self):
        super().__init__()

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def initialize(self) -> None:
        pass

    async def log_job_event(self, event_data: dict[str, Any]) -> None:
        pass

    async def log_worker_event(self, event_data: dict[str, Any]) -> None:
        pass

    async def _persist_job_event(self, event_data: dict[str, Any]) -> None:
        pass

    async def _persist_worker_event(self, event_data: dict[str, Any]) -> None:
        pass

    async def get_job_history(self, job_id: str) -> list[dict[str, Any]]:
        return []

    async def get_jobs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return []

    async def get_job_summary(self) -> dict[str, int]:
        return {}

    async def get_worker_history(
        self,
        worker_id: str,
        since_days: int,
    ) -> list[dict[str, Any]]:
        return []
