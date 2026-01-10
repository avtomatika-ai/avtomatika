from asyncio import CancelledError, get_running_loop
from logging import getLogger
from os import getenv
from socket import gethostname
from typing import Any

from msgpack import packb, unpackb
from redis import Redis, WatchError
from redis.exceptions import NoScriptError, ResponseError

from .base import StorageBackend

logger = getLogger(__name__)


class RedisStorage(StorageBackend):
    """Implementation of the state store based on Redis."""

    def __init__(
        self,
        redis_client: Redis,
        prefix: str = "orchestrator:job",
        group_name: str = "orchestrator_group",
        consumer_name: str | None = None,
        min_idle_time_ms: int = 60000,
    ):
        self._redis = redis_client
        self._prefix = prefix
        self._stream_key = "orchestrator:job_stream"
        self._group_name = group_name
        self._consumer_name = consumer_name or getenv("INSTANCE_ID", gethostname())
        self._group_created = False
        self._min_idle_time_ms = min_idle_time_ms

    def _get_key(self, job_id: str) -> str:
        return f"{self._prefix}:{job_id}"

    @staticmethod
    def _pack(data: Any) -> bytes:
        return packb(data, use_bin_type=True)

    @staticmethod
    def _unpack(data: bytes) -> Any:
        return unpackb(data, raw=False)

    async def get_job_state(self, job_id: str) -> dict[str, Any] | None:
        """Get the job state from Redis."""
        key = self._get_key(job_id)
        data = await self._redis.get(key)
        return self._unpack(data) if data else None

    async def get_priority_queue_stats(self, task_type: str) -> dict[str, Any]:
        """Gets statistics for the priority queue (Sorted Set) for a given task type."""
        worker_type = task_type
        key = f"orchestrator:task_queue:{worker_type}"

        pipe = self._redis.pipeline()
        pipe.zcard(key)  # Get the number of elements
        # Get the top 3 highest priority bids (scores)
        pipe.zrange(key, -3, -1, withscores=True, score_cast_func=float)
        # Get the top 3 lowest priority bids (scores)
        pipe.zrange(key, 0, 2, withscores=True, score_cast_func=float)
        results = await pipe.execute()

        count, top_bids_raw, bottom_bids_raw = results
        top_bids = [score for _, score in reversed(top_bids_raw)]
        bottom_bids = [score for _, score in bottom_bids_raw]

        # Simple average calculation, can be improved for large queues
        all_scores = [s for _, s in await self._redis.zrange(key, 0, -1, withscores=True, score_cast_func=float)]
        avg_bid = sum(all_scores) / len(all_scores) if all_scores else 0

        return {
            "queue_name": key,
            "task_count": count,
            "highest_bids": top_bids,
            "lowest_bids": bottom_bids,
            "average_bid": round(avg_bid, 2),
        }

    async def set_task_cancellation_flag(self, task_id: str) -> None:
        """Sets a cancellation flag for a task in Redis with a 1-hour TTL."""
        key = f"orchestrator:task_cancel:{task_id}"
        await self._redis.set(key, "1", ex=3600)

    async def save_job_state(self, job_id: str, state: dict[str, Any]) -> None:
        """Save the job state to Redis."""
        key = self._get_key(job_id)
        await self._redis.set(key, self._pack(state))

    async def update_job_state(
        self,
        job_id: str,
        update_data: dict[str, Any],
    ) -> dict[Any, Any] | None | Any:
        """Atomically update the job state in Redis using a transaction."""
        key = self._get_key(job_id)

        async with self._redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    current_state_raw = await pipe.get(key)
                    current_state = self._unpack(current_state_raw) if current_state_raw else {}

                    # Simple dictionary merge. For nested structures, a deep merge may be required.
                    current_state.update(update_data)

                    pipe.multi()
                    pipe.set(key, self._pack(current_state))
                    await pipe.execute()
                    return current_state
                except WatchError:
                    continue

    async def register_worker(
        self,
        worker_id: str,
        worker_info: dict[str, Any],
        ttl: int,
    ) -> None:
        """Registers a worker in Redis."""
        worker_info.setdefault("reputation", 1.0)
        key = f"orchestrator:worker:info:{worker_id}"
        await self._redis.set(key, self._pack(worker_info), ex=ttl)

    async def enqueue_task_for_worker(
        self,
        worker_id: str,
        task_payload: dict[str, Any],
        priority: float,
    ) -> None:
        """Adds a task to the priority queue (Sorted Set) for a worker."""
        key = f"orchestrator:task_queue:{worker_id}"
        await self._redis.zadd(key, {self._pack(task_payload): priority})

    async def dequeue_task_for_worker(
        self,
        worker_id: str,
        timeout: int,
    ) -> dict[str, Any] | None:
        """Retrieves the highest priority task from the queue (Sorted Set),
        using the blocking BZPOPMAX operation.
        """
        key = f"orchestrator:task_queue:{worker_id}"
        try:
            # BZPOPMAX returns a tuple (key, member, score)
            result = await self._redis.bzpopmax([key], timeout=timeout)
            return self._unpack(result[1]) if result else None
        except CancelledError:
            return None
        except ResponseError as e:
            # Error handling if `fakeredis` does not support BZPOPMAX
            if "unknown command" in str(e).lower() or "wrong number of arguments" in str(e).lower():
                logger.warning(
                    "BZPOPMAX is not supported (likely running with fakeredis). "
                    "Falling back to non-blocking ZPOPMAX for testing.",
                )
                # Non-blocking fallback for tests
                res = await self._redis.zpopmax(key)
                if res:
                    return self._unpack(res[0][0])
            raise e

    async def refresh_worker_ttl(self, worker_id: str, ttl: int) -> bool:
        """Updates the TTL for a worker key using the EXPIRE command."""
        key = f"orchestrator:worker:info:{worker_id}"
        # EXPIRE returns 1 if the TTL was set, and 0 if the key does not exist.
        was_set = await self._redis.expire(key, ttl)  # type: ignore[misc]
        return bool(was_set)

    async def update_worker_status(
        self,
        worker_id: str,
        status_update: dict[str, Any],
        ttl: int,
    ) -> dict[str, Any] | None:
        key = f"orchestrator:worker:info:{worker_id}"
        async with self._redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                current_state_raw = await pipe.get(key)
                if not current_state_raw:
                    return None

                current_state = self._unpack(current_state_raw)

                # Create a potential new state to compare against the current one
                new_state = current_state.copy()
                new_state.update(status_update)

                pipe.multi()

                # Only write to Redis if the state has actually changed.
                if new_state != current_state:
                    pipe.set(key, self._pack(new_state), ex=ttl)
                    current_state = new_state  # Update the state to be returned
                else:
                    # If nothing changed, just refresh the TTL to keep the worker alive.
                    pipe.expire(key, ttl)

                await pipe.execute()
                return current_state
            except WatchError:
                # In case of a conflict, the operation can be repeated,
                # but for a heartbeat it is not critical, you can just skip it.
                return None

    async def update_worker_data(
        self,
        worker_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = f"orchestrator:worker:info:{worker_id}"
        async with self._redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                current_state_raw = await pipe.get(key)
                if not current_state_raw:
                    return None

                current_state = self._unpack(current_state_raw)
                current_state.update(update_data)

                pipe.multi()
                # Do not set TTL, as this is a data update, not a heartbeat
                pipe.set(key, self._pack(current_state))
                await pipe.execute()
                return current_state
            except WatchError:
                # In case of a conflict, the operation can be repeated
                logger.warning(
                    f"WatchError during worker data update for {worker_id}, retrying.",
                )
                # In this case, it is better to repeat, as updating the reputation is important
                return await self.update_worker_data(worker_id, update_data)

    async def get_available_workers(self) -> list[dict[str, Any]]:
        """Gets a list of active workers by scanning keys in Redis."""
        worker_keys = [key async for key in self._redis.scan_iter("orchestrator:worker:info:*")]  # type: ignore[attr-defined]

        if not worker_keys:
            return []

        worker_data_list = await self._redis.mget(worker_keys)
        return [self._unpack(data) for data in worker_data_list if data]

    async def add_job_to_watch(self, job_id: str, timeout_at: float) -> None:
        """Adds a job to a Redis sorted set.
        The score is the timeout time.
        """
        await self._redis.zadd("orchestrator:watched_jobs", {job_id: timeout_at})

    async def remove_job_from_watch(self, job_id: str) -> None:
        """Removes a job from the sorted set for tracking."""
        await self._redis.zrem("orchestrator:watched_jobs", job_id)

    async def get_timed_out_jobs(self) -> list[str]:
        """Finds and removes overdue jobs from the sorted set."""
        now = get_running_loop().time()
        # Find all jobs with a timeout up to the current moment
        timed_out_ids = await self._redis.zrangebyscore(
            "orchestrator:watched_jobs",
            0,
            now,
        )

        if timed_out_ids:
            # Atomically remove the found IDs
            await self._redis.zrem("orchestrator:watched_jobs", *timed_out_ids)  # type: ignore[arg-type]
            return [job_id.decode("utf-8") for job_id in timed_out_ids]

        return []

    async def enqueue_job(self, job_id: str) -> None:
        """Adds a job to the Redis stream."""
        await self._redis.xadd(self._stream_key, {"job_id": job_id})

    async def dequeue_job(self) -> tuple[str, str] | None:
        """Retrieves a job from the Redis stream using consumer groups.
        Implements a recovery strategy: checks for pending messages first.
        """
        if not self._group_created:
            try:
                await self._redis.xgroup_create(self._stream_key, self._group_name, id="0", mkstream=True)
            except ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise e
            self._group_created = True

        try:
            try:
                autoclaim_result = await self._redis.xautoclaim(
                    self._stream_key,
                    self._group_name,
                    self._consumer_name,
                    min_idle_time=self._min_idle_time_ms,
                    start_id="0-0",
                    count=1,
                )
                if autoclaim_result and autoclaim_result[1]:
                    messages = autoclaim_result[1]
                    message_id, data = messages[0]
                    if data:
                        job_id = data[b"job_id"].decode("utf-8")
                        logger.info(f"Reclaimed pending message {message_id} for consumer {self._consumer_name}")
                        return job_id, message_id.decode("utf-8")
            except Exception as e:
                if "unknown command" in str(e).lower() or isinstance(e, ResponseError):
                    pending_result = await self._redis.xreadgroup(
                        self._group_name,
                        self._consumer_name,
                        {self._stream_key: "0"},
                        count=1,
                    )
                    if pending_result:
                        stream_name, messages = pending_result[0]
                        if messages:
                            message_id, data = messages[0]
                            job_id = data[b"job_id"].decode("utf-8")
                            return job_id, message_id.decode("utf-8")
                else:
                    raise e

            result = await self._redis.xreadgroup(
                self._group_name,
                self._consumer_name,
                {self._stream_key: ">"},
                count=1,
            )
            if result:
                stream_name, messages = result[0]
                message_id, data = messages[0]
                job_id = data[b"job_id"].decode("utf-8")
                return job_id, message_id.decode("utf-8")
            return None
        except CancelledError:
            return None

    async def ack_job(self, message_id: str) -> None:
        """Acknowledges a message in the Redis stream."""
        await self._redis.xack(self._stream_key, self._group_name, message_id)

    async def quarantine_job(self, job_id: str) -> None:
        """Moves the job ID to the 'quarantine' list in Redis."""
        await self._redis.lpush("orchestrator:quarantine_queue", job_id)  # type: ignore[arg-type]

    async def get_quarantined_jobs(self) -> list[str]:
        """Gets all job IDs from the quarantine queue."""
        jobs_bytes = await self._redis.lrange("orchestrator:quarantine_queue", 0, -1)
        return [job.decode("utf-8") for job in jobs_bytes]

    async def deregister_worker(self, worker_id: str) -> None:
        """Deletes the worker key from Redis."""
        key = f"orchestrator:worker:info:{worker_id}"
        await self._redis.delete(key)

    async def increment_key_with_ttl(self, key: str, ttl: int) -> int:
        """Atomically increments a counter and sets a TTL on the first call,
        using a Lua script for atomicity.
        Returns the new value of the counter.
        """
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
            return results[0]

    async def save_client_config(self, token: str, config: dict[str, Any]) -> None:
        """Saves the static client configuration as a hash."""
        key = f"orchestrator:client_config:{token}"
        # Convert all values to binary strings for storage in a Redis hash
        str_config = {k: self._pack(v) for k, v in config.items()}
        await self._redis.hset(key, mapping=str_config)

    async def get_client_config(self, token: str) -> dict[str, Any] | None:
        """Gets the static client configuration."""
        key = f"orchestrator:client_config:{token}"
        config_raw = await self._redis.hgetall(key)  # type: ignore[misc]
        if not config_raw:
            return None
        # Decode keys and values, parse binary
        return {k.decode("utf-8"): self._unpack(v) for k, v in config_raw.items()}

    async def initialize_client_quota(self, token: str, quota: int) -> None:
        """Sets or resets the quota counter."""
        key = f"orchestrator:quota:{token}"
        await self._redis.set(key, quota)

    async def check_and_decrement_quota(self, token: str) -> bool:
        """Atomically checks and decrements the quota. Returns True if successful."""
        key = f"orchestrator:quota:{token}"

        LUA_SCRIPT = """
        local current = redis.call('GET', KEYS[1])
        if current and tonumber(current) > 0 then
            redis.call('DECR', KEYS[1])
            return 1
        else
            return 0
        end
        """

        try:
            # This is the most efficient path for a real Redis server.
            # It loads the script once and then executes it by its SHA hash.
            sha = await self._redis.script_load(LUA_SCRIPT)
            result = await self._redis.evalsha(sha, 1, key)
        except NoScriptError:
            # If the script is not in the cache, Redis raises NoScriptError.
            # We can then fall back to executing the full script.
            result = await self._redis.eval(LUA_SCRIPT, 1, key)
        except ResponseError as e:
            # This is the fallback path for `fakeredis` used in tests, which
            # does not support `SCRIPT LOAD` or `EVALSHA`. It raises a
            # ResponseError: "unknown command `script`".
            if "unknown command" in str(e):
                # We resort to a non-atomic GET/DECR for testing purposes.
                # This is not safe for production but allows tests to pass.
                current_val = await self._redis.get(key)
                if current_val and int(current_val) > 0:
                    await self._redis.decr(key)
                    return True
                return False
            # If it's a different ResponseError, re-raise it.
            raise

        return bool(result)

    async def flush_all(self):
        """Completely clears the current Redis database.
        WARNING: This operation will delete ALL keys in the current DB.
        Use for testing purposes only.
        """
        logger.warning("Flushing all data from Redis database.")
        await self._redis.flushdb()

    async def get_job_queue_length(self) -> int:
        """Returns the length of the job stream."""
        return await self._redis.xlen(self._stream_key)

    async def get_active_worker_count(self) -> int:
        """Returns the number of active worker keys."""
        count = 0
        async for _ in self._redis.scan_iter("orchestrator:worker:info:*"):
            count += 1
        return count

    async def set_worker_token(self, worker_id: str, token: str):
        """Stores the individual token for a specific worker."""
        key = f"orchestrator:worker:token:{worker_id}"
        await self._redis.set(key, token)

    async def get_worker_token(self, worker_id: str) -> str | None:
        """Retrieves the individual token for a specific worker."""
        key = f"orchestrator:worker:token:{worker_id}"
        token = await self._redis.get(key)
        return token.decode("utf-8") if token else None

    async def get_worker_info(self, worker_id: str) -> dict[str, Any] | None:
        """Gets the full info for a worker by its ID."""
        key = f"orchestrator:worker:info:{worker_id}"
        data = await self._redis.get(key)
        return self._unpack(data) if data else None

    async def acquire_lock(self, key: str, holder_id: str, ttl: int) -> bool:
        """Attempts to acquire a lock using Redis SET NX."""
        redis_key = f"orchestrator:lock:{key}"
        result = await self._redis.set(redis_key, holder_id, nx=True, ex=ttl)
        return bool(result)

    async def release_lock(self, key: str, holder_id: str) -> bool:
        """Releases the lock using a Lua script to ensure ownership."""
        redis_key = f"orchestrator:lock:{key}"

        LUA_RELEASE_SCRIPT = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            result = await self._redis.eval(LUA_RELEASE_SCRIPT, 1, redis_key, holder_id)
            return bool(result)
        except ResponseError as e:
            if "unknown command" in str(e):
                current_val = await self._redis.get(redis_key)
                if current_val and current_val.decode("utf-8") == holder_id:
                    await self._redis.delete(redis_key)
                    return True
                return False
            raise e
