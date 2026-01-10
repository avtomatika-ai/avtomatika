from abc import ABC
from logging import getLogger
from typing import Any
from uuid import uuid4

from asyncpg import Pool, PostgresError, create_pool  # type: ignore[import-untyped]

from .base import HistoryStorageBase

logger = getLogger(__name__)

CREATE_JOB_HISTORY_TABLE_PG = """
CREATE TABLE IF NOT EXISTS job_history (
    event_id UUID PRIMARY KEY,
    job_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    state TEXT,
    event_type TEXT NOT NULL,
    duration_ms INTEGER,
    previous_state TEXT,
    next_state TEXT,
    worker_id TEXT,
    attempt_number INTEGER,
    context_snapshot JSONB
);
"""

CREATE_WORKER_HISTORY_TABLE_PG = """
CREATE TABLE IF NOT EXISTS worker_history (
    event_id UUID PRIMARY KEY,
    worker_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    event_type TEXT NOT NULL,
    worker_info_snapshot JSONB
);
"""

CREATE_JOB_ID_INDEX_PG = "CREATE INDEX IF NOT EXISTS idx_job_id ON job_history(job_id);"


class PostgresHistoryStorage(HistoryStorageBase, ABC):
    """Implementation of the history store based on asyncpg for PostgreSQL."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Pool | None = None

    async def initialize(self):
        """Initializes the connection pool to PostgreSQL and creates tables."""
        try:
            self._pool = await create_pool(dsn=self._dsn)
            if not self._pool:
                raise RuntimeError("Failed to create a connection pool.")

            async with self._pool.acquire() as conn:
                await conn.execute(CREATE_JOB_HISTORY_TABLE_PG)
                await conn.execute(CREATE_WORKER_HISTORY_TABLE_PG)
                await conn.execute(CREATE_JOB_ID_INDEX_PG)
            logger.info("PostgreSQL history storage initialized.")
        except (PostgresError, OSError) as e:
            logger.error(f"Failed to initialize PostgreSQL history storage: {e}")
            raise

    async def close(self):
        """Closes the connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("PostgreSQL history storage connection pool closed.")

    async def log_job_event(self, event_data: dict[str, Any]):
        """Logs a job lifecycle event to PostgreSQL."""
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = """
            INSERT INTO job_history (
                event_id, job_id, state, event_type, duration_ms,
                previous_state, next_state, worker_id, attempt_number,
                context_snapshot
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """
        params = (
            uuid4(),
            event_data.get("job_id"),
            event_data.get("state"),
            event_data.get("event_type"),
            event_data.get("duration_ms"),
            event_data.get("previous_state"),
            event_data.get("next_state"),
            event_data.get("worker_id"),
            event_data.get("attempt_number"),
            event_data.get("context_snapshot"),
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(query, *params)
        except PostgresError as e:
            logger.error(f"Failed to log job event to PostgreSQL: {e}")

    async def log_worker_event(self, event_data: dict[str, Any]):
        """Logs a worker lifecycle event to PostgreSQL."""
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = """
            INSERT INTO worker_history (
                event_id, worker_id, event_type, worker_info_snapshot
            ) VALUES ($1, $2, $3, $4)
        """
        params = (
            uuid4(),
            event_data.get("worker_id"),
            event_data.get("event_type"),
            event_data.get("worker_info_snapshot"),
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(query, *params)
        except PostgresError as e:
            logger.error(f"Failed to log worker event to PostgreSQL: {e}")

    async def get_job_history(self, job_id: str) -> list[dict[str, Any]]:
        """Gets the full history for the specified job from PostgreSQL."""
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = "SELECT * FROM job_history WHERE job_id = $1 ORDER BY timestamp ASC"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, job_id)
                return [dict(row) for row in rows]
        except PostgresError as e:
            logger.error(
                f"Failed to get job history for job_id {job_id} from PostgreSQL: {e}",
            )
            return []

    async def get_jobs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = """
            WITH latest_events AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER(PARTITION BY job_id ORDER BY timestamp DESC) as rn
                FROM job_history
            )
            SELECT * FROM latest_events
            WHERE rn = 1
            ORDER BY timestamp DESC
            LIMIT $1 OFFSET $2;
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, limit, offset)
                return [dict(row) for row in rows]
        except PostgresError as e:
            logger.error(f"Failed to get jobs list from PostgreSQL: {e}")
            return []

    async def get_job_summary(self) -> dict[str, int]:
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = """
            WITH latest_events AS (
                SELECT
                    context_snapshot->>'status' as status,
                    ROW_NUMBER() OVER(PARTITION BY job_id ORDER BY timestamp DESC) as rn
                FROM job_history
                WHERE context_snapshot->>'status' IS NOT NULL
            )
            SELECT
                status,
                COUNT(*)::int as count
            FROM latest_events
            WHERE rn = 1
            GROUP BY status;
        """
        summary = {}
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query)
                for row in rows:
                    summary[row["status"]] = row["count"]
                return summary
        except PostgresError as e:
            logger.error(f"Failed to get job summary from PostgreSQL: {e}")
            return {}

    async def get_worker_history(
        self,
        worker_id: str,
        since_days: int,
    ) -> list[dict[str, Any]]:
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = """
            SELECT * FROM job_history
            WHERE worker_id = $1
            AND timestamp >= NOW() - ($2 * INTERVAL '1 day')
            ORDER BY timestamp DESC
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, worker_id, since_days)
                return [dict(row) for row in rows]
        except PostgresError as e:
            logger.error(f"Failed to get worker history for worker_id {worker_id} from PostgreSQL: {e}")
            return []
