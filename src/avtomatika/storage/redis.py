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

    async def get_worker_info(self, worker_id: str) -> dict[str, Any] | None:
        """Gets the full info for a worker by its ID."""
        key = f"orchestrator:worker:info:{worker_id}"
        data = await self._redis.get(key)
        return self._unpack(data) if data else None

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
        pipe.zcard(key)
        pipe.zrange(key, -3, -1, withscores=True, score_cast_func=float)
        pipe.zrange(key, 0, 2, withscores=True, score_cast_func=float)
        results = await pipe.execute()

        count, top_bids_raw, bottom_bids_raw = results
        top_bids = [score for _, score in reversed(top_bids_raw)]
        bottom_bids = [score for _, score in bottom_bids_raw]

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
        """Registers a worker in Redis and updates indexes."""
        worker_info.setdefault("reputation", 1.0)
        key = f"orchestrator:worker:info:{worker_id}"
        tasks_key = f"orchestrator:worker:tasks:{worker_id}"

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, self._pack(worker_info), ex=ttl)
            pipe.sadd("orchestrator:index:workers:all", worker_id)

            if worker_info.get("status", "idle") == "idle":
                pipe.sadd("orchestrator:index:workers:idle", worker_id)
            else:
                pipe.srem("orchestrator:index:workers:idle", worker_id)

            supported_tasks = worker_info.get("supported_tasks", [])
            if supported_tasks:
                pipe.sadd(tasks_key, *supported_tasks)
                for task in supported_tasks:
                    pipe.sadd(f"orchestrator:index:workers:task:{task}", worker_id)

            await pipe.execute()

    async def deregister_worker(self, worker_id: str) -> None:
        """Deletes the worker key and removes it from all indexes."""
        key = f"orchestrator:worker:info:{worker_id}"
        tasks_key = f"orchestrator:worker:tasks:{worker_id}"

        tasks = await self._redis.smembers(tasks_key)  # type: ignore

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.delete(tasks_key)
            pipe.srem("orchestrator:index:workers:all", worker_id)
            pipe.srem("orchestrator:index:workers:idle", worker_id)

            for task in tasks:
                task_str = task.decode("utf-8") if isinstance(task, bytes) else task
                pipe.srem(f"orchestrator:index:workers:task:{task_str}", worker_id)

            await pipe.execute()

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
                new_state = current_state.copy()
                new_state.update(status_update)

                pipe.multi()

                if new_state != current_state:
                    pipe.set(key, self._pack(new_state), ex=ttl)
                    old_status = current_state.get("status", "idle")
                    new_status = new_state.get("status", "idle")

                    if old_status != new_status:
                        if new_status == "idle":
                            pipe.sadd("orchestrator:index:workers:idle", worker_id)
                        else:
                            pipe.srem("orchestrator:index:workers:idle", worker_id)
                    current_state = new_state
                else:
                    pipe.expire(key, ttl)

                await pipe.execute()
                return current_state
            except WatchError:
                return None

    async def find_workers_for_task(self, task_type: str) -> list[str]:
        """Finds idle workers that support the given task using set intersection."""
        task_index = f"orchestrator:index:workers:task:{task_type}"
        idle_index = "orchestrator:index:workers:idle"
        worker_ids = await self._redis.sinter(task_index, idle_index)  # type: ignore
        return [wid.decode("utf-8") if isinstance(wid, bytes) else wid for wid in worker_ids]

    async def enqueue_task_for_worker(self, worker_id: str, task_payload: dict[str, Any], priority: float) -> None:
        key = f"orchestrator:task_queue:{worker_id}"
        await self._redis.zadd(key, {self._pack(task_payload): priority})

    async def dequeue_task_for_worker(self, worker_id: str, timeout: int) -> dict[str, Any] | None:
        key = f"orchestrator:task_queue:{worker_id}"
        try:
            result = await self._redis.bzpopmax([key], timeout=timeout)
            return self._unpack(result[1]) if result else None
        except CancelledError:
            return None
        except ResponseError as e:
            if "unknown command" in str(e).lower() or "wrong number of arguments" in str(e).lower():
                res = await self._redis.zpopmax(key)
                if res:
                    return self._unpack(res[0][0])
            raise e

    async def refresh_worker_ttl(self, worker_id: str, ttl: int) -> bool:
        was_set = await self._redis.expire(f"orchestrator:worker:info:{worker_id}", ttl)
        return bool(was_set)

    async def update_worker_data(self, worker_id: str, update_data: dict[str, Any]) -> dict[str, Any] | None:
        key = f"orchestrator:worker:info:{worker_id}"
        async with self._redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if not raw:
                    return None
                data = self._unpack(raw)
                data.update(update_data)
                pipe.multi()
                pipe.set(key, self._pack(data))
                await pipe.execute()
                return data
            except WatchError:
                return await self.update_worker_data(worker_id, update_data)

    async def get_available_workers(self) -> list[dict[str, Any]]:
        worker_keys = [key async for key in self._redis.scan_iter("orchestrator:worker:info:*")]  # type: ignore
        if not worker_keys:
            return []
        data_list = await self._redis.mget(worker_keys)
        return [self._unpack(data) for data in data_list if data]

    async def get_workers(self, worker_ids: list[str]) -> list[dict[str, Any]]:
        if not worker_ids:
            return []
        keys = [f"orchestrator:worker:info:{wid}" for wid in worker_ids]
        data_list = await self._redis.mget(keys)
        return [self._unpack(data) for data in data_list if data]

    async def get_active_worker_ids(self) -> list[str]:
        worker_ids = await self._redis.smembers("orchestrator:index:workers:all")  # type: ignore
        return [wid.decode("utf-8") if isinstance(wid, bytes) else wid for wid in worker_ids]

    async def cleanup_expired_workers(self) -> None:
        worker_ids = await self.get_active_worker_ids()
        if not worker_ids:
            return
        pipe = self._redis.pipeline()
        for wid in worker_ids:
            pipe.exists(f"orchestrator:worker:info:{wid}")
        existence = await pipe.execute()
        dead_ids = [worker_ids[i] for i, exists in enumerate(existence) if not exists]
        for wid in dead_ids:
            tasks = await self._redis.smembers(f"orchestrator:worker:tasks:{wid}")  # type: ignore
            async with self._redis.pipeline(transaction=True) as p:
                p.delete(f"orchestrator:worker:tasks:{wid}")
                p.srem("orchestrator:index:workers:all", wid)
                p.srem("orchestrator:index:workers:idle", wid)
                for t in tasks:
                    p.srem(f"orchestrator:index:workers:task:{t.decode() if isinstance(t, bytes) else t}", wid)
                await p.execute()

    async def add_job_to_watch(self, job_id: str, timeout_at: float) -> None:
        await self._redis.zadd("orchestrator:watched_jobs", {job_id: timeout_at})

    async def remove_job_from_watch(self, job_id: str) -> None:
        await self._redis.zrem("orchestrator:watched_jobs", job_id)

    async def get_timed_out_jobs(self, limit: int = 100) -> list[str]:
        now = get_running_loop().time()
        ids = await self._redis.zrangebyscore("orchestrator:watched_jobs", 0, now, start=0, num=limit)
        if ids:
            await self._redis.zrem("orchestrator:watched_jobs", *ids)  # type: ignore
            return [i.decode("utf-8") for i in ids]
        return []

    async def enqueue_job(self, job_id: str) -> None:
        await self._redis.xadd(self._stream_key, {"job_id": job_id})

    async def dequeue_job(self, block: int | None = None) -> tuple[str, str] | None:
        if not self._group_created:
            try:
                await self._redis.xgroup_create(self._stream_key, self._group_name, id="0", mkstream=True)
            except ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise e
            self._group_created = True
        try:
            claim = await self._redis.xautoclaim(
                self._stream_key,
                self._group_name,
                self._consumer_name,
                min_idle_time=self._min_idle_time_ms,
                start_id="0-0",
                count=1,
            )
            if claim and claim[1]:
                msg_id, data = claim[1][0]
                return data[b"job_id"].decode("utf-8"), msg_id.decode("utf-8")
            read = await self._redis.xreadgroup(
                self._group_name, self._consumer_name, {self._stream_key: ">"}, count=1, block=block
            )
            if read:
                msg_id, data = read[0][1][0]
                return data[b"job_id"].decode("utf-8"), msg_id.decode("utf-8")
            return None
        except CancelledError:
            return None

    async def ack_job(self, message_id: str) -> None:
        await self._redis.xack(self._stream_key, self._group_name, message_id)

    async def quarantine_job(self, job_id: str) -> None:
        await self._redis.lpush("orchestrator:quarantine_queue", job_id)  # type: ignore

    async def get_quarantined_jobs(self) -> list[str]:
        jobs = await self._redis.lrange("orchestrator:quarantine_queue", 0, -1)
        return [j.decode("utf-8") for j in jobs]

    async def increment_key_with_ttl(self, key: str, ttl: int) -> int:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
            return results[0]

    async def save_client_config(self, token: str, config: dict[str, Any]) -> None:
        await self._redis.hset(
            f"orchestrator:client_config:{token}", mapping={k: self._pack(v) for k, v in config.items()}
        )

    async def get_client_config(self, token: str) -> dict[str, Any] | None:
        raw = await self._redis.hgetall(f"orchestrator:client_config:{token}")  # type: ignore
        if not raw:
            return None
        return {k.decode("utf-8"): self._unpack(v) for k, v in raw.items()}

    async def initialize_client_quota(self, token: str, quota: int) -> None:
        await self._redis.set(f"orchestrator:quota:{token}", quota)

    async def check_and_decrement_quota(self, token: str) -> bool:
        key = f"orchestrator:quota:{token}"
        LUA = (
            "local c = redis.call('GET', KEYS[1]) "
            "if c and tonumber(c) > 0 then redis.call('DECR', KEYS[1]) return 1 else return 0 end"
        )
        try:
            sha = await self._redis.script_load(LUA)
            res = await self._redis.evalsha(sha, 1, key)
        except NoScriptError:
            res = await self._redis.eval(LUA, 1, key)
        except ResponseError as e:
            if "unknown command" in str(e):
                cur = await self._redis.get(key)
                if cur and int(cur) > 0:
                    await self._redis.decr(key)
                    return True
                return False
            raise
        return bool(res)

    async def flush_all(self):
        await self._redis.flushdb()

    async def get_job_queue_length(self) -> int:
        return await self._redis.xlen(self._stream_key)

    async def get_active_worker_count(self) -> int:
        c = 0
        async for _ in self._redis.scan_iter("orchestrator:worker:info:*"):
            c += 1
        return c

    async def set_nx_ttl(self, key: str, value: str, ttl: int) -> bool:
        return bool(await self._redis.set(key, value, nx=True, ex=ttl))

    async def get_str(self, key: str) -> str | None:
        val = await self._redis.get(key)
        return val.decode("utf-8") if isinstance(val, bytes) else str(val) if val is not None else None

    async def set_str(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._redis.set(key, value, ex=ttl)

    async def set_worker_token(self, worker_id: str, token: str):
        await self._redis.set(f"orchestrator:worker:token:{worker_id}", token)

    async def get_worker_token(self, worker_id: str) -> str | None:
        token = await self._redis.get(f"orchestrator:worker:token:{worker_id}")
        return token.decode("utf-8") if token else None

    async def acquire_lock(self, key: str, holder_id: str, ttl: int) -> bool:
        return bool(await self._redis.set(f"orchestrator:lock:{key}", holder_id, nx=True, ex=ttl))

    async def release_lock(self, key: str, holder_id: str) -> bool:
        LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        try:
            return bool(await self._redis.eval(LUA, 1, f"orchestrator:lock:{key}", holder_id))
        except ResponseError as e:
            if "unknown command" in str(e):
                cur = await self._redis.get(f"orchestrator:lock:{key}")
                if cur and cur.decode("utf-8") == holder_id:
                    await self._redis.delete(f"orchestrator:lock:{key}")
                    return True
                return False
            raise e

    async def ping(self) -> bool:
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def reindex_workers(self) -> None:
        """Scan existing worker keys and rebuild indexes."""
        async for key in self._redis.scan_iter("orchestrator:worker:info:*"):  # type: ignore
            worker_id = key.decode("utf-8").split(":")[-1]
            raw = await self._redis.get(key)
            if raw:
                info = self._unpack(raw)
                await self.register_worker(worker_id, info, int(await self._redis.ttl(key)))
