from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class StorageBackend(ABC):
    """Abstract base class for job state stores.
    Defines the interface that all stores must implement.
    """

    @abstractmethod
    async def get_job_state(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get the full state of a job by its ID.

        :param job_id: Unique identifier for the job.
        :return: A dictionary with the job state or None if the job is not found.
        """
        raise NotImplementedError

    @abstractmethod
    async def update_worker_data(
        self,
        worker_id: str,
        update_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Partially update worker information without affecting its TTL.
        Used for background processes like the reputation calculator.

        :param worker_id: Unique identifier for the worker.
        :param update_data: A dictionary with the fields to update (e.g., 'reputation').
        :return: The updated full state of the worker or None if the worker is not found.
        """
        raise NotImplementedError

    @abstractmethod
    async def remove_job_from_watch(self, job_id: str) -> None:
        """Remove a job from the timeout tracking list (when it completes).

        :param job_id: The job identifier.
        """
        raise NotImplementedError

    @abstractmethod
    async def refresh_worker_ttl(self, worker_id: str, ttl: int) -> bool:
        """Refresh the TTL for a worker's key without changing its data.
        Used for "empty" heartbeat messages.

        :param worker_id: Unique identifier for the worker.
        :param ttl: The new time-to-live for the record in seconds.
        :return: True if the key was found and the TTL was updated, otherwise False.
        """
        raise NotImplementedError

    @abstractmethod
    async def update_worker_status(
        self,
        worker_id: str,
        status_update: Dict[str, Any],
        ttl: int,
    ) -> Optional[Dict[str, Any]]:
        """Partially update worker information and extend its TTL.
        Used for heartbeat messages.

        :param worker_id: Unique identifier for the worker.
        :param status_update: A dictionary with the fields to update (e.g., 'load').
        :param ttl: The new time-to-live for the record in seconds.
        :return: The updated full state of the worker or None if the worker is not found.
        """
        raise NotImplementedError

    @abstractmethod
    async def save_job_state(self, job_id: str, state: Dict[str, Any]) -> None:
        """Save the full state of a job.

        :param job_id: Unique identifier for the job.
        :param state: A dictionary representing the full state of the job.
        """
        raise NotImplementedError

    @abstractmethod
    async def update_job_state(
        self,
        job_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Partially update the state of a job.

        :param job_id: Unique identifier for the job.
        :param update_data: A dictionary with the data to update.
        :return: The updated full state of the job.
        """
        raise NotImplementedError

    @abstractmethod
    async def register_worker(
        self,
        worker_id: str,
        worker_info: Dict[str, Any],
        ttl: int,
    ) -> None:
        """Registers a new worker or updates information about an existing one.

        :param worker_id: Unique identifier for the worker.
        :param worker_info: A dictionary with information about the worker (without address).
        :param ttl: The time-to-live for the record in seconds.
        """
        raise NotImplementedError

    @abstractmethod
    async def enqueue_task_for_worker(
        self,
        worker_id: str,
        task_payload: Dict[str, Any],
        priority: float,
    ) -> None:
        """Adds a task to the priority queue for a specific worker.

        :param worker_id: The ID of the worker for whom the task is intended.
        :param task_payload: A dictionary with the task data.
        :param priority: The priority of the task (the higher, the sooner it will be executed).
        """
        raise NotImplementedError

    @abstractmethod
    async def dequeue_task_for_worker(
        self,
        worker_id: str,
        timeout: int,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves the highest priority task from the queue for a worker (blocking operation).

        :param worker_id: The ID of the worker for whom to retrieve the task.
        :param timeout: The maximum time to wait for a task in seconds.
        :return: A dictionary with the task data or None if the timeout has expired.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_available_workers(self) -> list[Dict[str, Any]]:
        """Get a list of all active (not expired) workers.

        :return: A list of dictionaries, where each dictionary represents information about a worker.
        """
        raise NotImplementedError

    @abstractmethod
    async def add_job_to_watch(self, job_id: str, timeout_at: float) -> None:
        """Add a job to the list for timeout tracking.

        :param job_id: The job identifier.
        :param timeout_at: The time (timestamp) when the job will be considered overdue.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_timed_out_jobs(self) -> list[str]:
        """Get a list of job IDs that are overdue and remove them from the tracking list.

        :return: A list of overdue job IDs.
        """
        raise NotImplementedError

    @abstractmethod
    async def enqueue_job(self, job_id: str) -> None:
        """Add a job ID to the execution queue."""
        raise NotImplementedError

    @abstractmethod
    async def dequeue_job(self) -> Optional[str]:
        """Retrieve a job ID from the execution queue (blocking operation)."""
        raise NotImplementedError

    @abstractmethod
    async def quarantine_job(self, job_id: str) -> None:
        """Move a job ID to the quarantine queue."""
        raise NotImplementedError

    @abstractmethod
    async def get_quarantined_jobs(self) -> list[str]:
        """Get a list of all job IDs from the quarantine queue (for testing)."""
        raise NotImplementedError

    @abstractmethod
    async def deregister_worker(self, worker_id: str) -> None:
        """Remove a worker from the registry."""
        raise NotImplementedError

    @abstractmethod
    async def increment_key_with_ttl(self, key: str, ttl: int) -> int:
        """Atomically increments the value of a key and sets a TTL if the key is new.
        Used for rate limiting.

        :param key: The key to increment.
        :param ttl: The time-to-live for the key in seconds.
        :return: The new value of the key after the increment.
        """
        raise NotImplementedError

    @abstractmethod
    async def save_client_config(self, token: str, config: Dict[str, Any]) -> None:
        """Saves the static configuration of a client."""
        raise NotImplementedError

    @abstractmethod
    async def get_client_config(self, token: str) -> Optional[Dict[str, Any]]:
        """Gets the static configuration of a client."""
        raise NotImplementedError

    @abstractmethod
    async def initialize_client_quota(self, token: str, quota: int) -> None:
        """Initializes (or resets) the quota counter for a client."""
        raise NotImplementedError

    @abstractmethod
    async def check_and_decrement_quota(self, token: str) -> bool:
        """Atomically checks if a client has a quota (>0) and decrements it by 1.
        :return: True if the quota existed and was decremented, otherwise False.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_job_queue_length(self) -> int:
        """Get the current length of the main job queue.
        Used for metrics.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_priority_queue_stats(self, task_type: str) -> Dict[str, Any]:
        """Get statistics on the priority queue for a given task type.

        :param task_type: The type of task (used as part of the queue key).
        :return: A dictionary with statistics (e.g., count, min/max/average priority).
        """
        raise NotImplementedError

    @abstractmethod
    async def set_task_cancellation_flag(self, task_id: str) -> None:
        """Set a cancellation flag for a task with a TTL."""
        raise NotImplementedError

    @abstractmethod
    async def set_worker_token(self, worker_id: str, token: str):
        """Saves an individual token for a specific worker."""
        raise NotImplementedError

    @abstractmethod
    async def get_worker_token(self, worker_id: str) -> Optional[str]:
        """Retrieves an individual token for a specific worker."""
        raise NotImplementedError

    @abstractmethod
    async def get_worker_info(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Get complete information about a worker by its ID."""
        raise NotImplementedError

    @abstractmethod
    async def flush_all(self):
        """Completely clears the storage. Used mainly for tests."""
        raise NotImplementedError

    @abstractmethod
    async def get_active_worker_count(self) -> int:
        """Get the current number of active (registered) workers.
        Used for metrics.
        """
        raise NotImplementedError

    @abstractmethod
    async def acquire_lock(self, key: str, holder_id: str, ttl: int) -> bool:
        """
        Attempts to acquire a distributed lock.

        :param key: The unique key of the lock (e.g., 'watcher_lock').
        :param holder_id: A unique identifier for the caller (e.g., UUID).
        :param ttl: Time-to-live for the lock in seconds.
        :return: True if the lock was acquired, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    async def release_lock(self, key: str, holder_id: str) -> bool:
        """
        Releases a distributed lock if it is held by the specified holder_id.

        :param key: The unique key of the lock.
        :param holder_id: The identifier of the caller who presumably holds the lock.
        :return: True if the lock was successfully released, False otherwise.
        """
        raise NotImplementedError
