from contextlib import suppress
from datetime import datetime
from logging import getLogger
from time import time
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiosqlite import Connection, Error, Row, connect
from orjson import dumps, loads

from .base import HistoryStorageBase

logger = getLogger(__name__)

CREATE_JOB_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS job_history (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    state TEXT,
    event_type TEXT NOT NULL,
    duration_ms INTEGER,
    previous_state TEXT,
    next_state TEXT,
    worker_id TEXT,
    attempt_number INTEGER,
    context_snapshot TEXT
);
"""

CREATE_WORKER_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS worker_history (
    event_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    worker_info_snapshot TEXT
);
"""

CREATE_JOB_ID_INDEX = "CREATE INDEX IF NOT EXISTS idx_job_id ON job_history(job_id);"


class SQLiteHistoryStorage(HistoryStorageBase):
    """Implementation of the history store based on aiosqlite.
    Stores timestamps as Unix time (UTC) for correct sorting,
    and converts them to the configured timezone upon retrieval.
    """

    def __init__(self, db_path: str, tz_name: str = "UTC") -> None:
        super().__init__()
        self._db_path = db_path
        self._conn: Connection | None = None
        self.tz = ZoneInfo(tz_name)

    async def initialize(self) -> None:
        """Initializes the database connection and creates tables if they don't exist."""
        try:
            self._conn = await connect(self._db_path)
            # Enable WAL mode for better concurrency performance
            await self._conn.execute("PRAGMA journal_mode=WAL;")
            await self._conn.execute(CREATE_JOB_HISTORY_TABLE)
            await self._conn.execute(CREATE_WORKER_HISTORY_TABLE)
            await self._conn.execute(CREATE_JOB_ID_INDEX)
            await self._conn.commit()
            logger.info(f"SQLite history storage initialized at {self._db_path}")
        except Error as e:
            logger.error(f"Failed to initialize SQLite history storage: {e}")
            raise

    async def close(self) -> None:
        """Closes the database connection and background worker."""
        await super().close()
        if self._conn:
            await self._conn.close()
            logger.info("SQLite history storage connection closed.")

    def _format_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Helper to format a row from DB: decode JSON and convert timestamp."""
        item = dict(row)

        if item.get("context_snapshot"):
            with suppress(Exception):
                item["context_snapshot"] = loads(item["context_snapshot"])

        if item.get("worker_info_snapshot"):
            with suppress(Exception):
                item["worker_info_snapshot"] = loads(item["worker_info_snapshot"])

        if "timestamp" in item and isinstance(item["timestamp"], (int, float)):
            item["timestamp"] = datetime.fromtimestamp(item["timestamp"], self.tz)

        return item

    async def _persist_job_event(self, event_data: dict[str, Any]) -> None:
        """Logs a job lifecycle event to the job_history table."""
        if not self._conn:
            raise RuntimeError("History storage is not initialized.")

        query = """
            INSERT INTO job_history (
                event_id, job_id, timestamp, state, event_type, duration_ms,
                previous_state, next_state, worker_id, attempt_number,
                context_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        now_ts = time()

        context_snapshot = event_data.get("context_snapshot")
        context_snapshot_json = dumps(context_snapshot).decode("utf-8") if context_snapshot else None

        params = (
            str(uuid4()),
            event_data.get("job_id"),
            now_ts,
            event_data.get("state"),
            event_data.get("event_type"),
            event_data.get("duration_ms"),
            event_data.get("previous_state"),
            event_data.get("next_state"),
            event_data.get("worker_id"),
            event_data.get("attempt_number"),
            context_snapshot_json,
        )

        try:
            await self._conn.execute(query, params)
            await self._conn.commit()
        except Error as e:
            logger.error(f"Failed to log job event: {e}")

    async def _persist_worker_event(self, event_data: dict[str, Any]) -> None:
        """Logs a worker lifecycle event to the worker_history table."""
        if not self._conn:
            raise RuntimeError("History storage is not initialized.")

        query = """
            INSERT INTO worker_history (
                event_id, worker_id, timestamp, event_type, worker_info_snapshot
            ) VALUES (?, ?, ?, ?, ?)
        """
        now_ts = time()

        worker_info = event_data.get("worker_info_snapshot")
        worker_info_json = dumps(worker_info).decode("utf-8") if worker_info else None

        params = (
            str(uuid4()),
            event_data.get("worker_id"),
            now_ts,
            event_data.get("event_type"),
            worker_info_json,
        )

        try:
            await self._conn.execute(query, params)
            await self._conn.commit()
        except Error as e:
            logger.error(f"Failed to log worker event: {e}")

    async def get_job_history(self, job_id: str) -> list[dict[str, Any]]:
        """Gets the full history for the specified job, sorted by time."""
        if not self._conn:
            raise RuntimeError("History storage is not initialized.")

        query = "SELECT * FROM job_history WHERE job_id = ? ORDER BY timestamp ASC"
        try:
            self._conn.row_factory = Row
            async with self._conn.execute(query, (job_id,)) as cursor:
                rows = await cursor.fetchall()
                return [self._format_row(row) for row in rows]
        except Error as e:
            logger.error(f"Failed to get job history for job_id {job_id}: {e}")
            return []

    async def get_jobs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Gets a list of the latest unique jobs with pagination."""
        if not self._conn:
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
            LIMIT ? OFFSET ?;
        """
        try:
            self._conn.row_factory = Row
            async with self._conn.execute(query, (limit, offset)) as cursor:
                rows = await cursor.fetchall()
                return [self._format_row(row) for row in rows]
        except Error as e:
            logger.error(f"Failed to get jobs list: {e}")
            return []

    async def get_job_summary(self) -> dict[str, int]:
        """Returns a summary of job statuses."""
        if not self._conn:
            raise RuntimeError("History storage is not initialized.")

        query = """
            WITH latest_events AS (
                SELECT
                    json_extract(context_snapshot, '$.status') as status,
                    ROW_NUMBER() OVER(PARTITION BY job_id ORDER BY timestamp DESC) as rn
                FROM job_history
                WHERE json_valid(context_snapshot) AND json_extract(context_snapshot, '$.status') IS NOT NULL
            )
            SELECT
                status,
                COUNT(*) as count
            FROM latest_events
            WHERE rn = 1
            GROUP BY status;
        """
        summary = {}
        try:
            self._conn.row_factory = Row
            async with self._conn.execute(query) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    summary[row["status"]] = row["count"]
                return summary
        except Error as e:
            logger.error(f"Failed to get job summary: {e}")
            return {}

    async def get_worker_history(
        self,
        worker_id: str,
        since_days: int,
    ) -> list[dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("History storage is not initialized.")

        threshold_ts = time() - (since_days * 86400)

        query = """
            SELECT * FROM job_history
            WHERE worker_id = ?
            AND timestamp >= ?
            ORDER BY timestamp DESC
        """
        try:
            self._conn.row_factory = Row
            async with self._conn.execute(query, (worker_id, threshold_ts)) as cursor:
                rows = await cursor.fetchall()
                return [self._format_row(row) for row in rows]
        except Error as e:
            logger.error(f"Failed to get worker history for worker_id {worker_id}: {e}")
            return []
