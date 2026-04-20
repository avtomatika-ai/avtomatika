# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio
from abc import ABC
from contextlib import suppress
from datetime import datetime
from logging import getLogger
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from asyncpg import Connection, Pool, PostgresError, create_pool
from orjson import dumps, loads

from .base import HistoryStorageBase

logger = getLogger(__name__)

CREATE_JOB_HISTORY_TABLE_PG = """
CREATE TABLE IF NOT EXISTS job_history (
    event_id UUID PRIMARY KEY,
    job_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    state TEXT,
    event_type TEXT NOT NULL,
    duration_ms BIGINT,
    previous_state TEXT,
    next_state TEXT,
    worker_id TEXT,
    origin_task_id TEXT,
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
CREATE_WORKER_ID_INDEX_PG = "CREATE INDEX IF NOT EXISTS idx_worker_id_ts ON job_history(worker_id, timestamp);"


class PostgresHistoryStorage(HistoryStorageBase, ABC):
    """Implementation of the history store based on asyncpg for PostgreSQL."""

    def __init__(self, dsn: str, tz_name: str = "UTC") -> None:
        super().__init__()
        self._dsn = dsn
        self._pool: Pool | None = None
        self.tz_name = tz_name
        self.tz = ZoneInfo(tz_name)

    async def _setup_connection(self, conn: Connection) -> None:
        """Configures the connection session with the correct timezone."""
        try:
            await conn.execute(f"SET TIME ZONE '{self.tz_name}'")
        except PostgresError as e:
            logger.error(f"Failed to set timezone '{self.tz_name}' for PG connection: {e}")

    async def initialize(self) -> None:
        """Initializes the connection pool to PostgreSQL and creates tables with retries."""
        max_retries = 10
        retry_delay = 1.0
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                # We use init parameter to configure each new connection in the pool
                self._pool = await create_pool(dsn=self._dsn, init=self._setup_connection)
                if not self._pool:
                    raise RuntimeError("Failed to create a connection pool.")

                async with self._pool.acquire() as conn:
                    await conn.execute(CREATE_JOB_HISTORY_TABLE_PG)
                    await conn.execute(CREATE_WORKER_HISTORY_TABLE_PG)
                    await conn.execute(CREATE_JOB_ID_INDEX_PG)
                    await conn.execute(CREATE_WORKER_ID_INDEX_PG)
                logger.info(f"PostgreSQL history storage initialized (TZ={self.tz_name}) after {attempt} attempts.")
                return
            except (PostgresError, OSError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"PostgreSQL history storage initialization attempt {attempt}/{max_retries} failed: {e}. "
                        f"Retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10.0)
                else:
                    logger.error(f"Failed to initialize PostgreSQL history storage after {max_retries} attempts: {e}")
                    raise last_error from e

    async def close(self) -> None:
        """Closes the connection pool and background worker."""
        await super().close()
        if self._pool:
            await self._pool.close()
            logger.info("PostgreSQL history storage connection pool closed.")

    async def _persist_job_event(self, event_data: dict[str, Any]) -> None:
        """Logs a job lifecycle event to PostgreSQL."""
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = """
            INSERT INTO job_history (
                event_id, job_id, timestamp, state, event_type, duration_ms,
                previous_state, next_state, worker_id, origin_task_id, attempt_number,
                context_snapshot
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """
        now = datetime.now(self.tz)

        context_snapshot = event_data.get("context_snapshot")
        context_snapshot_json = dumps(context_snapshot).decode("utf-8") if context_snapshot else None

        duration_ms = event_data.get("duration_ms")
        if duration_ms is not None:
            # Ensure it is a valid integer and not negative (avoids int32 overflow/underflow issues)
            try:
                duration_ms = max(0, int(duration_ms))
            except (ValueError, TypeError):
                duration_ms = None

        params = (
            uuid4(),
            event_data.get("job_id"),
            now,
            event_data.get("state"),
            event_data.get("event_type"),
            duration_ms,
            event_data.get("previous_state"),
            event_data.get("next_state"),
            event_data.get("worker_id"),
            event_data.get("origin_task_id"),
            event_data.get("attempt_number"),
            context_snapshot_json,
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(query, *params)
        except PostgresError as e:
            logger.error(f"Failed to log job event to PostgreSQL: {e}")

    async def _persist_worker_event(self, event_data: dict[str, Any]) -> None:
        """Logs a worker lifecycle event to PostgreSQL."""
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = """
            INSERT INTO worker_history (
                event_id, worker_id, timestamp, event_type, worker_info_snapshot
            ) VALUES ($1, $2, $3, $4, $5)
        """
        now = datetime.now(self.tz)

        worker_info = event_data.get("worker_info_snapshot")
        worker_info_json = dumps(worker_info).decode("utf-8") if worker_info else None

        params = (
            uuid4(),
            event_data.get("worker_id"),
            now,
            event_data.get("event_type"),
            worker_info_json,
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(query, *params)
        except PostgresError as e:
            logger.error(f"Failed to log worker event to PostgreSQL: {e}")

    def _format_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Helper to format a row from DB: convert timestamp to local TZ and decode JSON."""
        item = dict(row)

        if isinstance(item.get("context_snapshot"), str):
            with suppress(Exception):
                item["context_snapshot"] = loads(item["context_snapshot"])

        if isinstance(item.get("worker_info_snapshot"), str):
            with suppress(Exception):
                item["worker_info_snapshot"] = loads(item["worker_info_snapshot"])

        if "timestamp" in item and isinstance(item["timestamp"], datetime):
            item["timestamp"] = item["timestamp"].astimezone(self.tz)

        return item

    async def get_job_history(self, job_id: str) -> list[dict[str, Any]]:
        """Gets the full history for the specified job from PostgreSQL."""
        if not self._pool:
            raise RuntimeError("History storage is not initialized.")

        query = "SELECT * FROM job_history WHERE job_id = $1 ORDER BY timestamp ASC"
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, job_id)
                return [self._format_row(row) for row in rows]
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
                return [self._format_row(row) for row in rows]
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
                return [self._format_row(row) for row in rows]
        except PostgresError as e:
            logger.error(f"Failed to get worker history for worker_id {worker_id} from PostgreSQL: {e}")
            return []
