from asyncio import Lock, PriorityQueue, Queue, QueueEmpty, wait_for
from asyncio import TimeoutError as AsyncTimeoutError
from time import monotonic
from typing import Any, Callable

from .base import StorageBackend


class MemoryStorage(StorageBackend):
    """In-memory implementation of StorageBackend.
    Intended for local execution and testing without Redis.
    Not persistent.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._workers: dict[str, dict[str, Any]] = {}
        self._worker_ttls: dict[str, float] = {}
        self._worker_task_queues: dict[str, PriorityQueue[Any]] = {}
        self._job_queue: Queue[str] = Queue()
        self._quarantine_queue: list[str] = []
        self._watched_jobs: dict[str, float] = {}
        self._client_configs: dict[str, dict[str, Any]] = {}
        self._quotas: dict[str, int] = {}
        self._worker_tokens: dict[str, str] = {}
        self._generic_keys: dict[str, Any] = {}
        self._generic_key_ttls: dict[str, float] = {}
        self._locks: dict[str, tuple[str, float]] = {}

        self._lock = Lock()

    async def get_job_state(self, job_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def _clean_expired(self) -> None:
        """Helper to remove expired keys."""
        now = monotonic()

        expired_generic = [k for k, t in self._generic_key_ttls.items() if t < now]
        for k in expired_generic:
            self._generic_key_ttls.pop(k, None)
            self._generic_keys.pop(k, None)

        expired_workers = [k for k, t in self._worker_ttls.items() if t < now]
        for k in expired_workers:
            self._worker_ttls.pop(k, None)
            self._workers.pop(k, None)

    async def save_job_state(self, job_id: str, state: dict[str, Any]) -> None:
        async with self._lock:
            self._jobs[job_id] = state

    async def update_job_state(
        self,
        job_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._lock:
            if job_id not in self._jobs:
                self._jobs[job_id] = {}
            self._jobs[job_id].update(update_data)
            return self._jobs[job_id]

    async def update_job_state_atomic(
        self,
        job_id: str,
        update_callback: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        async with self._lock:
            current_state = self._jobs.get(job_id, {})
            updated_state = update_callback(current_state)
            self._jobs[job_id] = updated_state
            return updated_state

    async def register_worker(
        self,
        worker_id: str,
        worker_info: dict[str, Any],
        ttl: int,
    ) -> None:
        """Registers a worker and creates a task queue for it."""
        async with self._lock:
            # Set default reputation for new workers
            worker_info.setdefault("reputation", 1.0)
            self._workers[worker_id] = worker_info
            self._worker_ttls[worker_id] = monotonic() + ttl
            if worker_id not in self._worker_task_queues:
                self._worker_task_queues[worker_id] = PriorityQueue()

    async def enqueue_task_for_worker(
        self,
        worker_id: str,
        task_payload: dict[str, Any],
        priority: float,
    ) -> None:
        """Puts a task on the priority queue for a worker."""
        async with self._lock:
            if worker_id not in self._worker_task_queues:
                self._worker_task_queues[worker_id] = PriorityQueue()
        await self._worker_task_queues[worker_id].put((-priority, task_payload))

    async def dequeue_task_for_worker(
        self,
        worker_id: str,
        timeout: int,
    ) -> dict[str, Any] | None:
        """Retrieves a task from the worker's priority queue with a timeout."""
        queue = None
        async with self._lock:
            if worker_id not in self._worker_task_queues:
                self._worker_task_queues[worker_id] = PriorityQueue()
            queue = self._worker_task_queues[worker_id]

        try:
            # Type ignore because PriorityQueue.get() return type is generic
            item = await wait_for(queue.get(), timeout=timeout)
            _, task_payload = item
            # Explicit cast for mypy
            if isinstance(task_payload, dict):
                return task_payload
            return None  # Should not happen if data integrity is kept
        except AsyncTimeoutError:
            return None

    async def refresh_worker_ttl(self, worker_id: str, ttl: int) -> bool:
        async with self._lock:
            if worker_id in self._workers:
                self._worker_ttls[worker_id] = monotonic() + ttl
                return True
            return False

    async def update_worker_status(
        self,
        worker_id: str,
        status_update: dict[str, Any],
        ttl: int,
    ) -> dict[str, Any] | None:
        async with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].update(status_update)
                self._worker_ttls[worker_id] = monotonic() + ttl
                return self._workers[worker_id]
            return None

    async def update_worker_data(
        self,
        worker_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].update(update_data)
                return self._workers[worker_id]
            return None

    async def get_available_workers(self) -> list[dict[str, Any]]:
        async with self._lock:
            now = monotonic()
            active_workers: list[dict[str, Any]] = []
            active_workers.extend(
                worker_info
                for worker_id, worker_info in self._workers.items()
                if self._worker_ttls.get(worker_id, 0) > now
            )
            return active_workers

    async def get_active_worker_ids(self) -> list[str]:
        async with self._lock:
            now = monotonic()
            return [worker_id for worker_id, ttl in self._worker_ttls.items() if ttl > now]

    async def cleanup_expired_workers(self) -> None:
        async with self._lock:
            await self._clean_expired()

    async def get_workers(self, worker_ids: list[str]) -> list[dict[str, Any]]:
        async with self._lock:
            return [self._workers[wid] for wid in worker_ids if wid in self._workers]

    async def find_workers_for_skill(self, skill_name: str) -> list[str]:
        """Finds idle workers supporting the skill (O(N) for memory storage)."""
        async with self._lock:
            now = monotonic()
            candidates = []
            for worker_id, info in self._workers.items():
                if self._worker_ttls.get(worker_id, 0) <= now:
                    continue
                if info.get("status", "idle") != "idle":
                    continue
                if skill_name in info.get("supported_skills", []):
                    candidates.append(worker_id)
            return candidates

    async def find_hot_workers(self, skill_name: str, model_name: str) -> list[str]:
        """Finds idle workers that have the specific model in hot cache."""
        async with self._lock:
            now = monotonic()
            candidates = []
            for worker_id, info in self._workers.items():
                if self._worker_ttls.get(worker_id, 0) <= now:
                    continue
                if info.get("status", "idle") != "idle":
                    continue
                if skill_name in info.get("supported_skills", []) and model_name in info.get("hot_cache", []):
                    candidates.append(worker_id)
            return candidates

    async def find_workers_by_hot_skill(self, skill_name: str) -> list[str]:
        """Finds idle workers that have the specific skill marked as 'hot'."""
        async with self._lock:
            now = monotonic()
            candidates = []
            for worker_id, info in self._workers.items():
                if self._worker_ttls.get(worker_id, 0) <= now:
                    continue
                if info.get("status", "idle") != "idle":
                    continue
                if skill_name in info.get("hot_skills", []):
                    candidates.append(worker_id)
            return candidates

    async def add_job_to_watch(self, job_id: str, timeout_at: float) -> None:
        async with self._lock:
            self._watched_jobs[job_id] = timeout_at

    async def remove_job_from_watch(self, job_id: str) -> None:
        async with self._lock:
            self._watched_jobs.pop(job_id, None)

    async def get_timed_out_jobs(self, limit: int = 100) -> list[str]:
        async with self._lock:
            now = monotonic()
            timed_out_ids = [job_id for job_id, timeout_at in self._watched_jobs.items() if timeout_at <= now]
            timed_out_ids = timed_out_ids[:limit]
            for job_id in timed_out_ids:
                self._watched_jobs.pop(job_id, None)
            return timed_out_ids

    async def enqueue_job(self, job_id: str) -> None:
        await self._job_queue.put(job_id)

    async def dequeue_job(self, block: int | None = None) -> tuple[str, str] | None:
        """Waits for a job ID from the queue.
        If block is None, waits indefinitely.
        If block is int, waits for that many milliseconds.
        """
        try:
            if block is None:
                job_id = await self._job_queue.get()
            else:
                job_id = await wait_for(self._job_queue.get(), timeout=block / 1000.0)

            self._job_queue.task_done()
            return job_id, "memory-msg-id"
        except AsyncTimeoutError:
            return None

    async def ack_job(self, message_id: str) -> None:
        """No-op for MemoryStorage as it doesn't support persistent streams."""
        pass

    async def quarantine_job(self, job_id: str) -> None:
        async with self._lock:
            self._quarantine_queue.append(job_id)

    async def get_quarantined_jobs(self) -> list[str]:
        async with self._lock:
            return list(self._quarantine_queue)

    async def deregister_worker(self, worker_id: str) -> None:
        async with self._lock:
            self._workers.pop(worker_id, None)
            self._worker_ttls.pop(worker_id, None)
            self._worker_task_queues.pop(worker_id, None)

    async def increment_key_with_ttl(self, key: str, ttl: int) -> int:
        async with self._lock:
            now = monotonic()
            if key not in self._generic_keys or self._generic_key_ttls.get(key, 0) < now:
                self._generic_keys[key] = 0

            self._generic_keys[key] += 1
            self._generic_key_ttls[key] = now + ttl
            return int(self._generic_keys[key])

    async def save_client_config(self, token: str, config: dict[str, Any]) -> None:
        async with self._lock:
            self._client_configs[token] = config

    async def get_client_config(self, token: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._client_configs.get(token)

    async def initialize_client_quota(self, token: str, quota: int) -> None:
        async with self._lock:
            self._quotas[token] = quota

    async def check_and_decrement_quota(self, token: str) -> bool:
        async with self._lock:
            if self._quotas.get(token, 0) > 0:
                self._quotas[token] -= 1
                return True
            return False

    async def flush_all(self) -> None:
        """
        Resets all in-memory storage containers to their initial empty state.
        This is a destructive operation intended for use in tests to ensure
        a clean state between test runs.
        """
        async with self._lock:
            self._jobs.clear()
            self._workers.clear()
            self._worker_ttls.clear()
            self._worker_task_queues.clear()
            while not self._job_queue.empty():
                try:
                    self._job_queue.get_nowait()
                except QueueEmpty:
                    break
            self._quarantine_queue.clear()
            self._watched_jobs.clear()
            self._client_configs.clear()
            self._quotas.clear()
            self._generic_keys.clear()
            self._generic_key_ttls.clear()
            self._locks.clear()

    async def get_job_queue_length(self) -> int:
        return self._job_queue.qsize()

    async def get_active_worker_count(self) -> int:
        async with self._lock:
            await self._clean_expired()
            return len(self._workers)

    async def set_nx_ttl(self, key: str, value: str, ttl: int) -> bool:
        async with self._lock:
            await self._clean_expired()
            if key in self._generic_keys:
                return False

            self._generic_keys[key] = value
            self._generic_key_ttls[key] = monotonic() + ttl
            return True

    async def get_str(self, key: str) -> str | None:
        async with self._lock:
            await self._clean_expired()
            val = self._generic_keys.get(key)
            return str(val) if val is not None else None

    async def set_str(self, key: str, value: str, ttl: int | None = None) -> None:
        async with self._lock:
            self._generic_keys[key] = value
            if ttl:
                self._generic_key_ttls[key] = monotonic() + ttl
            else:
                self._generic_key_ttls.pop(key, None)

    async def increment_worker_load(self, worker_id: str) -> None:
        async with self._lock:
            if worker_id in self._workers:
                worker = self._workers[worker_id]
                worker["load"] = worker.get("load", 0) + 1

    async def get_worker_info(self, worker_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._workers.get(worker_id)

    async def set_worker_token(self, worker_id: str, token: str) -> None:
        async with self._lock:
            self._worker_tokens[worker_id] = token

    async def get_worker_token(self, worker_id: str) -> str | None:
        async with self._lock:
            return self._worker_tokens.get(worker_id)

    async def save_worker_access_token(self, worker_id: str, token: str, ttl: int) -> None:
        async with self._lock:
            self._generic_keys[f"sts:{token}"] = worker_id
            self._generic_key_ttls[f"sts:{token}"] = monotonic() + ttl

    async def verify_worker_access_token(self, token: str) -> str | None:
        async with self._lock:
            await self._clean_expired()
            return self._generic_keys.get(f"sts:{token}")

    async def set_task_cancellation_flag(self, task_id: str) -> None:
        key = f"task_cancel:{task_id}"
        await self.increment_key_with_ttl(key, 3600)

    async def get_priority_queue_stats(self, task_type: str) -> dict[str, Any]:
        """
        Returns empty data, as `asyncio.PriorityQueue` does not
        support introspection to get statistics.
        """
        worker_type = task_type
        queue = self._worker_task_queues.get(worker_type)
        return {
            "queue_name": f"in-memory:{worker_type}",
            "task_count": queue.qsize() if queue else 0,
            "highest_bids": [],
            "lowest_bids": [],
            "average_bid": 0,
            "error": "Statistics are not supported for MemoryStorage backend.",
        }

    async def acquire_lock(self, key: str, holder_id: str, ttl: int) -> bool:
        async with self._lock:
            now = monotonic()
            current_lock = self._locks.get(key)
            if current_lock and current_lock[1] > now:
                return False
            self._locks[key] = (holder_id, now + ttl)
            return True

    async def release_lock(self, key: str, holder_id: str) -> bool:
        async with self._lock:
            current_lock = self._locks.get(key)
            if current_lock:
                owner, expiry = current_lock
                if owner == holder_id:
                    del self._locks[key]
                    return True
            return False

    async def ping(self) -> bool:
        return True
