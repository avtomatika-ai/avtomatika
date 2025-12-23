from asyncio import Lock, PriorityQueue, Queue, QueueEmpty, wait_for
from asyncio import TimeoutError as AsyncTimeoutError
from time import monotonic
from typing import Any, Dict, List, Optional

from .base import StorageBackend


class MemoryStorage(StorageBackend):
    """In-memory implementation of StorageBackend.
    Intended for local execution and testing without Redis.
    Not persistent.
    """

    def __init__(self):
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._workers: Dict[str, Dict[str, Any]] = {}
        self._worker_ttls: Dict[str, float] = {}
        self._worker_task_queues: Dict[str, PriorityQueue] = {}
        self._job_queue = Queue()
        self._quarantine_queue: List[str] = []
        self._watched_jobs: Dict[str, float] = {}
        self._client_configs: Dict[str, Dict[str, Any]] = {}
        self._quotas: Dict[str, int] = {}
        self._worker_tokens: Dict[str, str] = {}
        self._generic_keys: Dict[str, Any] = {}
        self._generic_key_ttls: Dict[str, float] = {}
        self._locks: Dict[str, tuple[str, float]] = {}  # key -> (holder_id, expiry_time)

        self._lock = Lock()

    async def get_job_state(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def save_job_state(self, job_id: str, state: Dict[str, Any]) -> None:
        async with self._lock:
            self._jobs[job_id] = state

    async def update_job_state(
        self,
        job_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        async with self._lock:
            if job_id not in self._jobs:
                self._jobs[job_id] = {}
            self._jobs[job_id].update(update_data)
            return self._jobs[job_id]

    async def register_worker(
        self,
        worker_id: str,
        worker_info: Dict[str, Any],
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
        task_payload: Dict[str, Any],
        priority: float,
    ) -> None:
        """Puts a task on the priority queue for a worker."""
        async with self._lock:
            if worker_id not in self._worker_task_queues:
                self._worker_task_queues[worker_id] = PriorityQueue()
        # asyncio.PriorityQueue is a min-heap, so we invert the priority
        await self._worker_task_queues[worker_id].put((-priority, task_payload))

    async def dequeue_task_for_worker(
        self,
        worker_id: str,
        timeout: int,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves a task from the worker's priority queue with a timeout."""
        queue = None
        async with self._lock:
            if worker_id not in self._worker_task_queues:
                self._worker_task_queues[worker_id] = PriorityQueue()
            queue = self._worker_task_queues[worker_id]

        try:
            _, task_payload = await wait_for(queue.get(), timeout=timeout)
            return task_payload
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
        status_update: Dict[str, Any],
        ttl: int,
    ) -> Optional[Dict[str, Any]]:
        async with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].update(status_update)
                self._worker_ttls[worker_id] = monotonic() + ttl
                return self._workers[worker_id]
            return None

    async def update_worker_data(
        self,
        worker_id: str,
        update_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        async with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].update(update_data)
                return self._workers[worker_id]
            return None

    async def get_available_workers(self) -> list[dict[str, Any]]:
        async with self._lock:
            now = monotonic()
            active_workers = []
            active_workers.extend(
                worker_info
                for worker_id, worker_info in self._workers.items()
                if self._worker_ttls.get(worker_id, 0) > now
            )
            return active_workers

    async def add_job_to_watch(self, job_id: str, timeout_at: float) -> None:
        async with self._lock:
            self._watched_jobs[job_id] = timeout_at

    async def remove_job_from_watch(self, job_id: str) -> None:
        async with self._lock:
            self._watched_jobs.pop(job_id, None)

    async def get_timed_out_jobs(self) -> list[str]:
        async with self._lock:
            now = monotonic()
            timed_out_ids = [job_id for job_id, timeout_at in self._watched_jobs.items() if timeout_at <= now]
            for job_id in timed_out_ids:
                self._watched_jobs.pop(job_id, None)
            return timed_out_ids

    async def enqueue_job(self, job_id: str) -> None:
        await self._job_queue.put(job_id)

    async def dequeue_job(self) -> str | None:
        """Waits indefinitely for a job ID from the queue and returns it.
        This simulates the blocking behavior of Redis's BLPOP.
        """
        job_id = await self._job_queue.get()
        self._job_queue.task_done()
        return job_id

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
            return self._generic_keys[key]

    async def save_client_config(self, token: str, config: Dict[str, Any]) -> None:
        async with self._lock:
            self._client_configs[token] = config

    async def get_client_config(self, token: str) -> Optional[Dict[str, Any]]:
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

    async def flush_all(self):
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
            # Empty the queue
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
        # No lock needed for asyncio.Queue.qsize()
        return self._job_queue.qsize()

    async def get_active_worker_count(self) -> int:
        async with self._lock:
            now = monotonic()
            # Create a copy of keys to avoid issues with concurrent modifications
            worker_ids = list(self._workers.keys())
            return sum(self._worker_ttls.get(worker_id, 0) > now for worker_id in worker_ids)

    async def get_worker_info(self, worker_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._workers.get(worker_id)

    async def set_worker_token(self, worker_id: str, token: str) -> None:
        async with self._lock:
            self._worker_tokens[worker_id] = token

    async def get_worker_token(self, worker_id: str) -> Optional[str]:
        async with self._lock:
            return self._worker_tokens.get(worker_id)

    async def set_task_cancellation_flag(self, task_id: str) -> None:
        key = f"task_cancel:{task_id}"
        await self.increment_key_with_ttl(key, 3600)

    async def get_priority_queue_stats(self, task_type: str) -> Dict[str, Any]:
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

            # If lock exists and hasn't expired
            if current_lock and current_lock[1] > now:
                # If explicitly owned by us, we can extend/re-enter (optional behavior)
                # But for strict locking, if it's held, return False (unless it's us? let's simpler: just False if held)
                return False

            # Acquire lock
            self._locks[key] = (holder_id, now + ttl)
            return True

    async def release_lock(self, key: str, holder_id: str) -> bool:
        async with self._lock:
            current_lock = self._locks.get(key)
            if current_lock:
                owner, expiry = current_lock
                # Only release if we are the owner
                if owner == holder_id:
                    del self._locks[key]
                    return True
            return False
