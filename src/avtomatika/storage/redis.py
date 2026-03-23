# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from asyncio import CancelledError, get_running_loop
from logging import getLogger
from os import getenv
from socket import gethostname
from typing import Any, Callable, cast

from msgpack import packb, unpackb
from redis import Redis, WatchError
from redis.exceptions import NoScriptError, ResponseError

from .base import StorageBackend

logger = getLogger(__name__)

LUA_INCR_LOAD = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local data = cmsgpack.unpack(raw)
data['_internal_load'] = (data['_internal_load'] or 0) + 1
-- Also set status to busy if needed, though load balancing usually handles numeric load
redis.call('SET', KEYS[1], cmsgpack.pack(data), 'KEEPTTL')
return 1
"""

LUA_MERGE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return nil end
local data = cmsgpack.unpack(raw)
local update = cmsgpack.unpack(ARGV[1])
for k, v in pairs(update) do
    data[k] = v
end
redis.call('SET', KEYS[1], cmsgpack.pack(data), 'EX', ARGV[2])
return cmsgpack.pack(data)
"""

LUA_STEAL = """
local skill_index = KEYS[1]
local my_id = KEYS[2]
local workers = redis.call('SRANDMEMBER', skill_index, 10)
local best_worker = nil
local max_len = 0

for i, wid in ipairs(workers) do
    if wid ~= my_id then
        local q_key = "orchestrator:task_queue:" .. wid
        local q_len = redis.call('ZCARD', q_key)
        if q_len > max_len then
            max_len = q_len
            best_worker = wid
        end
    end
end

if best_worker then
    local target_q = "orchestrator:task_queue:" .. best_worker
    local task = redis.call('ZPOPMAX', target_q)
    if task and #task > 0 then
        return task[1]
    end
end
return nil
"""

LUA_CLEANUP = """
local wid = ARGV[1]
local skills_key = "orchestrator:worker:skills:" .. wid
local hot_list_key = "orchestrator:worker:hot_artifacts:" .. wid
local hot_skills_key = "orchestrator:worker:hot_skills:" .. wid
local queue_key = "orchestrator:task_queue:" .. wid

local skills = redis.call('SMEMBERS', skills_key)
local hot_artifacts = redis.call('SMEMBERS', hot_list_key)
local hot_skills = redis.call('SMEMBERS', hot_skills_key)
local orphaned_count = redis.call('ZCARD', queue_key)

redis.call('DEL', skills_key, hot_list_key, hot_skills_key, queue_key)
redis.call('SREM', 'orchestrator:index:workers:all', wid)
redis.call('SREM', 'orchestrator:index:workers:idle', wid)

for _, s in ipairs(skills) do
    redis.call('SREM', 'orchestrator:index:workers:skill:' .. s, wid)
end
for _, m in ipairs(hot_artifacts) do
    redis.call('SREM', 'orchestrator:index:workers:hot_cache:' .. m, wid)
end
for _, hs in ipairs(hot_skills) do
    redis.call('SREM', 'orchestrator:index:workers:hot_skill:' .. hs, wid)
end

return orphaned_count
"""


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
        self._lua_shas: dict[str, str] = {}

    async def _eval_lua(self, script: str, num_keys: int, *args: Any) -> Any:
        """Helper to execute Lua scripts using EVALSHA if possible."""
        script_hash = self._lua_shas.get(script)
        if script_hash:
            try:
                return await self._redis.evalsha(script_hash, num_keys, *args)
            except NoScriptError:
                pass

        # Load and cache
        sha = await self._redis.script_load(script)
        self._lua_shas[script] = sha
        return await self._redis.evalsha(sha, num_keys, *args)

    async def initialize(self) -> None:
        """Performs initialization, e.g., creating the consumer group."""
        try:
            await self._redis.xgroup_create(self._stream_key, self._group_name, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                logger.error(f"Failed to create Redis consumer group: {e}")
        self._group_created = True

    def _get_key(self, job_id: str) -> str:
        return f"{self._prefix}:{job_id}"

    async def increment_worker_load(self, worker_id: str) -> None:
        key = f"orchestrator:worker:info:{worker_id}"
        try:
            await self._eval_lua(LUA_INCR_LOAD, 1, key)
        except ResponseError as e:
            if "unknown command" in str(e).lower():
                # Fallback for simple increment if Lua fails or cmsgpack not available (though redis-py handles this)
                logger.warning(f"Failed to optimistically increment load for {worker_id}: {e}")
            else:
                raise e

    async def get_worker_info(self, worker_id: str) -> dict[str, Any] | None:
        """Gets the full info for a worker by its ID."""
        key = f"orchestrator:worker:info:{worker_id}"
        data = await self._redis.get(key)
        return cast(dict[str, Any], self._unpack(data)) if data else None

    @staticmethod
    def _pack(data: Any) -> bytes:
        return cast(bytes, packb(data, use_bin_type=True))

    @staticmethod
    def _unpack(data: bytes) -> Any:
        try:
            return unpackb(data, raw=False)
        except Exception as e:
            logger.error(f"Failed to unpack msgpack data: {e}")
            return None

    async def _pack_async(self, data: Any) -> bytes:
        """Packs data, using executor only for large payloads."""
        # Threshold: ~64KB. For small data, sync is faster.
        if isinstance(data, (dict, list)) and len(data) > 1000:
            loop = get_running_loop()
            return await loop.run_in_executor(None, self._pack, data)
        return self._pack(data)

    async def _unpack_async(self, data: bytes) -> Any:
        """Unpacks data, using executor only for large payloads."""
        if len(data) > 65536:
            loop = get_running_loop()
            return await loop.run_in_executor(None, self._unpack, data)
        return self._unpack(data)

    async def get_job_state(self, job_id: str) -> dict[str, Any] | None:
        """Get the job state from Redis."""
        key = self._get_key(job_id)
        data = await self._redis.get(key)
        if data:
            unpacked = await self._unpack_async(data)
            return cast(dict[str, Any], unpacked)
        return None

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
        packed = await self._pack_async(state)
        await self._redis.set(key, packed)

    async def update_job_state(
        self,
        job_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically update the job state in Redis using a transaction."""

        def _merge(state: dict[str, Any]) -> dict[str, Any]:
            state.update(update_data)
            return state

        return await self.update_job_state_atomic(job_id, _merge)

    async def update_job_state_atomic(
        self,
        job_id: str,
        update_callback: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically update the job state in Redis using a transaction and callback."""
        key = self._get_key(job_id)

        async with self._redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    current_state_raw = await pipe.get(key)

                    if current_state_raw:
                        current_state = cast(dict[str, Any], await self._unpack_async(current_state_raw))
                    else:
                        current_state = {}

                    from inspect import iscoroutinefunction

                    if iscoroutinefunction(update_callback):
                        updated_state = await update_callback(current_state)
                    else:
                        updated_state = update_callback(current_state)

                    pipe.multi()
                    packed = await self._pack_async(updated_state)
                    pipe.set(key, packed)
                    await pipe.execute()
                    return cast(dict[str, Any], updated_state)
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
        skills_key = f"orchestrator:worker:skills:{worker_id}"

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, self._pack(worker_info), ex=ttl)
            pipe.sadd("orchestrator:index:workers:all", worker_id)

            if worker_info.get("status", "idle") == "idle":
                pipe.sadd("orchestrator:index:workers:idle", worker_id)
            else:
                pipe.srem("orchestrator:index:workers:idle", worker_id)

            supported_skills = worker_info.get("supported_skills") or []
            if supported_skills:
                skill_names_to_index = []
                for skill in supported_skills:
                    name = skill.get("name")
                    skill_type = skill.get("type")

                    if name:
                        skill_names_to_index.append(name)
                        pipe.sadd(f"orchestrator:index:workers:skill:{name}", worker_id)

                    if skill_type and skill_type != name:
                        skill_names_to_index.append(skill_type)
                        pipe.sadd(f"orchestrator:index:workers:skill:{skill_type}", worker_id)
                if skill_names_to_index:
                    pipe.delete(skills_key)
                    pipe.sadd(skills_key, *skill_names_to_index)

            # Hot cache refers to loaded artifacts
            hot_cache = worker_info.get("hot_cache", [])
            if hot_cache:
                hot_list_key = f"orchestrator:worker:hot_artifacts:{worker_id}"
                pipe.delete(hot_list_key)
                pipe.sadd(hot_list_key, *hot_cache)
                for item in hot_cache:
                    pipe.sadd(f"orchestrator:index:workers:hot_cache:{item}", worker_id)

            hot_skills = worker_info.get("hot_skills", [])
            if hot_skills:
                hot_skills_key = f"orchestrator:worker:hot_skills:{worker_id}"
                pipe.delete(hot_skills_key)

                hot_skill_names = []
                for skill in hot_skills:
                    if isinstance(skill, dict):
                        name = skill.get("name")
                        skill_type = skill.get("type")
                        if name:
                            hot_skill_names.append(name)
                            pipe.sadd(f"orchestrator:index:workers:hot_skill:{name}", worker_id)
                        if skill_type and skill_type != name:
                            hot_skill_names.append(skill_type)
                            pipe.sadd(f"orchestrator:index:workers:hot_skill:{skill_type}", worker_id)
                    else:
                        hot_skill_names.append(str(skill))
                        pipe.sadd(f"orchestrator:index:workers:hot_skill:{skill}", worker_id)

                if hot_skill_names:
                    pipe.sadd(hot_skills_key, *hot_skill_names)

            await pipe.execute()

    async def deregister_worker(self, worker_id: str) -> None:
        """Deletes the worker key and removes it from all indexes."""
        key = f"orchestrator:worker:info:{worker_id}"
        skills_key = f"orchestrator:worker:skills:{worker_id}"
        hot_list_key = f"orchestrator:worker:hot_artifacts:{worker_id}"
        hot_skills_key = f"orchestrator:worker:hot_skills:{worker_id}"

        skills = await self._redis.smembers(skills_key)
        hot_cache = await self._redis.smembers(hot_list_key)
        hot_skills = await self._redis.smembers(hot_skills_key)

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.delete(skills_key)
            pipe.delete(hot_list_key)
            pipe.delete(hot_skills_key)
            pipe.srem("orchestrator:index:workers:all", worker_id)
            pipe.srem("orchestrator:index:workers:idle", worker_id)

            for skill in skills:
                skill_str = skill.decode("utf-8") if isinstance(skill, bytes) else str(skill)
                pipe.srem(f"orchestrator:index:workers:skill:{skill_str}", worker_id)

            for item in hot_cache:
                item_str = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                pipe.srem(f"orchestrator:index:workers:hot_cache:{item_str}", worker_id)

            for skill in hot_skills:
                skill_str = skill.decode("utf-8") if isinstance(skill, bytes) else str(skill)
                pipe.srem(f"orchestrator:index:workers:hot_skill:{skill_str}", worker_id)

            await pipe.execute()

    async def update_worker_status(
        self,
        worker_id: str,
        status_update: dict[str, Any],
        ttl: int,
    ) -> dict[str, Any] | None:
        key = f"orchestrator:worker:info:{worker_id}"

        # 1. Fetch current state without watch to check if we actually need to rebuild indexes
        current_state_raw = await self._redis.get(key)
        if not current_state_raw:
            return None

        current_state = cast(dict[str, Any], self._unpack(current_state_raw))

        # 2. Deep diff to avoid redundant index rebuilds
        has_new_skills = False
        if "supported_skills" in status_update and status_update["supported_skills"] != current_state.get(
            "supported_skills"
        ):
            has_new_skills = True

        old_hot_cache = set(current_state.get("hot_cache", []))
        new_hot_cache = set(status_update.get("hot_cache", current_state.get("hot_cache", [])))
        has_new_hot_cache = old_hot_cache != new_hot_cache

        old_hot_skills = current_state.get("hot_skills") or []
        new_hot_skills = status_update.get("hot_skills") or old_hot_skills
        has_new_hot_skills = False

        old_hs_names = {s["name"] for s in old_hot_skills} if old_hot_skills else set()
        new_hs_names = {s["name"] for s in new_hot_skills} if new_hot_skills else set()
        if old_hs_names != new_hs_names:
            has_new_hot_skills = True

        old_status = current_state.get("status", "idle")
        new_status = status_update.get("status", old_status)
        has_new_status = old_status != new_status

        needs_indexing = has_new_skills or has_new_hot_cache or has_new_hot_skills or has_new_status

        # 3. Fast path: Atomic merge without WATCH if no index changes are needed
        if not needs_indexing:
            try:
                res = await self._eval_lua(LUA_MERGE, 1, key, self._pack(status_update), ttl)
                return self._unpack(res) if res else None
            except ResponseError as e:
                if "unknown command" not in str(e).lower():
                    raise e
                # Fallback to WATCH if cmsgpack is missing

        # 4. Slow path: Rebuild indexes with WATCH and retries
        max_retries = 10
        for i in range(max_retries):
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    current_state_raw = await pipe.get(key)
                    if not current_state_raw:
                        return None

                    current_state = cast(dict[str, Any], await self._unpack_async(current_state_raw))
                    new_state = current_state.copy()
                    new_state.update(status_update)

                    pipe.multi()

                    if new_state != current_state or has_new_skills:
                        pipe.set(key, self._pack(new_state), ex=ttl)

                        if has_new_skills:
                            skills_key = f"orchestrator:worker:skills:{worker_id}"
                            supported_skills = status_update.get("supported_skills", [])
                            skill_names_to_index = []
                            for skill in supported_skills:
                                name = skill.get("name")
                                skill_type = skill.get("type")
                                if name:
                                    skill_names_to_index.append(name)
                                    pipe.sadd(f"orchestrator:index:workers:skill:{name}", worker_id)
                                    if skill_type and skill_type != name:
                                        skill_names_to_index.append(skill_type)
                                        pipe.sadd(f"orchestrator:index:workers:skill:{skill_type}", worker_id)
                            if skill_names_to_index:
                                pipe.delete(skills_key)
                                pipe.sadd(skills_key, *skill_names_to_index)

                        if has_new_status:
                            if new_status == "idle":
                                pipe.sadd("orchestrator:index:workers:idle", worker_id)
                            else:
                                pipe.srem("orchestrator:index:workers:idle", worker_id)

                        if has_new_hot_cache:
                            hot_list_key = f"orchestrator:worker:hot_artifacts:{worker_id}"
                            for item in old_hot_cache:
                                pipe.srem(f"orchestrator:index:workers:hot_cache:{item}", worker_id)
                            pipe.delete(hot_list_key)
                            if new_hot_cache:
                                pipe.sadd(hot_list_key, *new_hot_cache)
                                for item in new_hot_cache:
                                    pipe.sadd(f"orchestrator:index:workers:hot_cache:{item}", worker_id)

                        if has_new_hot_skills:
                            hot_skills_key = f"orchestrator:worker:hot_skills:{worker_id}"
                            for skill_name in old_hs_names:
                                pipe.srem(f"orchestrator:index:workers:hot_skill:{skill_name}", worker_id)
                            pipe.delete(hot_skills_key)

                            new_hot_skill_data_to_index = []
                            for skill in new_hot_skills:
                                name = skill["name"]
                                skill_type = skill.get("type")
                                new_hot_skill_data_to_index.append((name, skill_type))

                            if new_hs_names:
                                pipe.sadd(hot_skills_key, *new_hs_names)
                                for name, skill_type in new_hot_skill_data_to_index:
                                    pipe.sadd(f"orchestrator:index:workers:hot_skill:{name}", worker_id)
                                    if skill_type and skill_type != name:
                                        pipe.sadd(f"orchestrator:index:workers:hot_skill:{skill_type}", worker_id)

                    await pipe.execute()
                    return new_state
                except WatchError:
                    if i == max_retries - 1:
                        logger.critical(
                            f"Failed to update worker status for {worker_id} after {max_retries} retries "
                            "due to WATCH conflicts."
                        )
                    continue
        return None

    async def find_workers_for_skill(self, skill_name: str) -> list[str]:
        """Finds idle workers that support the given skill using set intersection."""
        skill_index = f"orchestrator:index:workers:skill:{skill_name}"
        idle_index = "orchestrator:index:workers:idle"
        worker_ids = await self._redis.sinter(skill_index, idle_index)
        return [wid.decode("utf-8") if isinstance(wid, bytes) else str(wid) for wid in worker_ids]

    async def find_hot_workers(self, skill_name: str, resource_id: str) -> list[str]:
        """Finds idle workers that have the specific resource (e.g. model, artifact) in hot cache."""
        skill_index = f"orchestrator:index:workers:skill:{skill_name}"
        idle_index = "orchestrator:index:workers:idle"
        hot_index = f"orchestrator:index:workers:hot_cache:{resource_id}"
        worker_ids = await self._redis.sinter(skill_index, idle_index, hot_index)
        return [wid.decode("utf-8") if isinstance(wid, bytes) else str(wid) for wid in worker_ids]

    async def find_workers_by_hot_skill(self, skill_name: str) -> list[str]:
        """Finds idle workers that have the specific skill marked as 'hot'."""
        idle_index = "orchestrator:index:workers:idle"
        hot_skill_index = f"orchestrator:index:workers:hot_skill:{skill_name}"
        worker_ids = await self._redis.sinter(idle_index, hot_skill_index)
        return [wid.decode("utf-8") if isinstance(wid, bytes) else str(wid) for wid in worker_ids]

    async def enqueue_task_for_worker(self, worker_id: str, task_payload: dict[str, Any], priority: float) -> None:
        key = f"orchestrator:task_queue:{worker_id}"
        await self._redis.zadd(key, {self._pack(task_payload): priority})

    async def dequeue_task_for_worker(self, worker_id: str, timeout: int) -> dict[str, Any] | None:
        key = f"orchestrator:task_queue:{worker_id}"

        # 1. First, check our own queue without blocking
        try:
            result_quick = await self._redis.zpopmax(key)
            if result_quick:
                # zpopmax returns [(value, score)]
                return cast(dict[str, Any], self._unpack(result_quick[0][0]))
        except Exception as e:
            logger.debug(f"Quick dequeue check failed for {worker_id}: {e}")

        # 2. If empty, try to steal from others before we commit to long polling
        # This keeps workers productive if there is work elsewhere in the cluster
        worker_info = await self.get_worker_info(worker_id)
        if worker_info:
            skills = worker_info.get("supported_skills") or []
            for skill in skills:
                skill_name = skill.get("name") or skill.get("type")
                if not skill_name:
                    continue

                skill_idx_key = f"orchestrator:index:workers:skill:{skill_name}"
                try:
                    stolen_payload_raw = await self._eval_lua(LUA_STEAL, 2, skill_idx_key, worker_id)
                    if stolen_payload_raw:
                        logger.info(f"Worker {worker_id} stole a task from another worker for skill {skill_name}")
                        return cast(dict[str, Any], self._unpack(stolen_payload_raw))
                except Exception as e:
                    logger.warning(f"Work stealing failed for worker {worker_id} on skill {skill_name}: {e}")

        # 3. Finally, wait for a task to arrive in our own queue
        try:
            result = await self._redis.bzpopmax([key], timeout=timeout)
            if result:
                # bzpopmax returns (key, (value, score))
                return cast(dict[str, Any], self._unpack(result[1][0]))
        except CancelledError:
            return None
        except Exception as e:
            logger.error(f"Error in dequeue_task_for_worker (bzpopmax) for {worker_id}: {e}")

        return None

    async def get_worker_queue_length(self, worker_id: str) -> int:
        key = f"orchestrator:task_queue:{worker_id}"
        return cast(int, await self._redis.zcard(key))

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
                data = cast(dict[str, Any], self._unpack(raw))
                data.update(update_data)
                pipe.multi()
                pipe.set(key, self._pack(data))
                await pipe.execute()
                return data
            except WatchError:
                return await self.update_worker_data(worker_id, update_data)

    async def get_available_workers(self) -> list[dict[str, Any]]:
        worker_keys = [key async for key in self._redis.scan_iter("orchestrator:worker:info:*")]
        if not worker_keys:
            return []
        data_list = await self._redis.mget(worker_keys)
        return [cast(dict[str, Any], self._unpack(data)) for data in data_list if data]

    async def get_workers(self, worker_ids: list[str]) -> list[dict[str, Any]]:
        if not worker_ids:
            return []
        keys = [f"orchestrator:worker:info:{wid}" for wid in worker_ids]
        data_list = await self._redis.mget(keys)
        return [cast(dict[str, Any], self._unpack(data)) for data in data_list if data]

    async def get_active_worker_ids(self) -> list[str]:
        worker_ids = await self._redis.smembers("orchestrator:index:workers:all")
        return [wid.decode("utf-8") if isinstance(wid, bytes) else str(wid) for wid in worker_ids]

    async def cleanup_expired_workers(self) -> None:
        """
        Removes expired workers from indexes and cleans up their task queues.
        Also re-enqueues orphaned tasks to the global queue if necessary.
        """
        # 1. Get all known workers from the index
        all_worker_ids = await self._redis.smembers("orchestrator:index:workers:all")
        if not all_worker_ids:
            return

        worker_ids_str = [wid.decode("utf-8") if isinstance(wid, bytes) else str(wid) for wid in all_worker_ids]

        # 2. Check which ones still have an info key, in chunks to avoid blocking
        dead_ids = []
        chunk_size = 500
        for i in range(0, len(worker_ids_str), chunk_size):
            chunk = worker_ids_str[i : i + chunk_size]
            pipe = self._redis.pipeline()
            for wid in chunk:
                pipe.exists(f"orchestrator:worker:info:{wid}")

            existence = await pipe.execute()
            for j, exists in enumerate(existence):
                if not exists:
                    dead_ids.append(chunk[j])

        if not dead_ids:
            return

        logger.info(f"Found {len(dead_ids)} expired workers. Cleaning up.")

        # Pre-load script to speed up processing
        try:
            sha = await self._redis.script_load(LUA_CLEANUP)
        except Exception:
            sha = None

        # Clean up dead workers
        for wid in dead_ids:
            try:
                if sha:
                    orphaned_count = await self._redis.evalsha(sha, 0, wid)
                else:
                    orphaned_count = await self._redis.eval(LUA_CLEANUP, 0, wid)

                if orphaned_count and orphaned_count > 0:
                    logger.warning(
                        f"Worker {wid} expired with {orphaned_count} pending tasks in queue. "
                        "These tasks will eventually be picked up by Watcher as timed out."
                    )
            except Exception as e:
                if "unknown command" in str(e).lower() or "eval" in str(e).lower():
                    # Fallback to pipeline for environments/tests without Lua support
                    skills_key = f"orchestrator:worker:skills:{wid}"
                    queue_key = f"orchestrator:task_queue:{wid}"
                    hot_list_key = f"orchestrator:worker:hot_artifacts:{wid}"
                    hot_skills_key = f"orchestrator:worker:hot_skills:{wid}"

                    skills = await self._redis.smembers(skills_key)
                    hot_artifacts = await self._redis.smembers(hot_list_key)
                    hot_skills = await self._redis.smembers(hot_skills_key)
                    orphaned_tasks_raw = await self._redis.zrange(queue_key, 0, -1)

                    async with self._redis.pipeline(transaction=True) as p:
                        p.delete(skills_key, queue_key, hot_list_key, hot_skills_key)
                        p.srem("orchestrator:index:workers:all", wid)
                        p.srem("orchestrator:index:workers:idle", wid)

                        for s in skills:
                            s_str = s.decode() if isinstance(s, bytes) else str(s)
                            p.srem(f"orchestrator:index:workers:skill:{s_str}", wid)
                        for m in hot_artifacts:
                            m_str = m.decode() if isinstance(m, bytes) else str(m)
                            p.srem(f"orchestrator:index:workers:hot_cache:{m_str}", wid)
                        for hs in hot_skills:
                            hs_str = hs.decode() if isinstance(hs, bytes) else str(hs)
                            p.srem(f"orchestrator:index:workers:hot_skill:{hs_str}", wid)
                        await p.execute()

                    if orphaned_tasks_raw:
                        logger.warning(
                            f"Worker {wid} expired with {len(orphaned_tasks_raw)} pending tasks in queue. "
                            "These tasks will eventually be picked up by Watcher as timed out."
                        )
                else:
                    logger.error(f"Failed to clean up expired worker {wid}: {e}")

    async def add_job_to_watch(self, job_id: str, timeout_at: float) -> None:
        await self._redis.zadd("orchestrator:watched_jobs", {job_id: timeout_at})

    async def remove_job_from_watch(self, job_id: str) -> None:
        await self._redis.zrem("orchestrator:watched_jobs", job_id)

    async def get_timed_out_jobs(self, limit: int = 100) -> list[str]:
        now = get_running_loop().time()
        # Lua script to atomically fetch and remove timed out jobs
        LUA_POP_TIMEOUTS = """
        local now = ARGV[1]
        local limit = ARGV[2]
        local ids = redis.call('ZRANGEBYSCORE', KEYS[1], 0, now, 'LIMIT', 0, limit)
        if #ids > 0 then
            redis.call('ZREM', KEYS[1], unpack(ids))
        end
        return ids
        """
        try:
            sha = await self._redis.script_load(LUA_POP_TIMEOUTS)
            ids = await self._redis.evalsha(sha, 1, "orchestrator:watched_jobs", now, limit)
        except NoScriptError:
            ids = await self._redis.eval(LUA_POP_TIMEOUTS, 1, "orchestrator:watched_jobs", now, limit)
        except ResponseError as e:
            # Fallback for Redis versions that don't support script_load/evalsha or other errors
            if "unknown command" in str(e).lower():
                logger.warning("Redis does not support LUA scripts. Falling back to non-atomic get_timed_out_jobs.")
                ids = await self._redis.zrangebyscore("orchestrator:watched_jobs", 0, now, start=0, num=limit)
                if ids:
                    await self._redis.zrem("orchestrator:watched_jobs", *ids)
            else:
                raise e

        if ids:
            return [i.decode("utf-8") if isinstance(i, bytes) else str(i) for i in ids]
        return []

    async def enqueue_job(self, job_id: str) -> None:
        # Cap stream length to prevent memory leaks in Redis.
        await self._redis.xadd(self._stream_key, {"job_id": job_id}, maxlen=100000, approximate=True)

    async def dequeue_job(self, block: int | None = None) -> tuple[str, str] | None:
        if not self._group_created:
            await self.initialize()

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
        await self._redis.lpush("orchestrator:quarantine_queue", job_id)

    async def get_quarantined_jobs(self) -> list[str]:
        jobs = await self._redis.lrange("orchestrator:quarantine_queue", 0, -1)
        return [j.decode("utf-8") for j in jobs]

    async def increment_key_with_ttl(self, key: str, ttl: int) -> int:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
            return cast(int, results[0])

    async def increment_key(self, key: str) -> int:
        return cast(int, await self._redis.incr(key))

    async def save_client_config(self, token: str, config: dict[str, Any]) -> None:
        await self._redis.hset(
            f"orchestrator:client_config:{token}", mapping={k: self._pack(v) for k, v in config.items()}
        )

    async def get_client_config(self, token: str) -> dict[str, Any] | None:
        raw = await self._redis.hgetall(f"orchestrator:client_config:{token}")
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
        return cast(bool, res)

    async def flush_all(self) -> None:
        await self._redis.flushdb()

    async def get_job_queue_length(self) -> int:
        return cast(int, await self._redis.xlen(self._stream_key))

    async def get_active_worker_count(self) -> int:
        c = 0
        async for _ in self._redis.scan_iter("orchestrator:worker:info:*"):
            c += 1
        return c

    async def set_nx_ttl(self, key: str, value: str, ttl: int) -> bool:
        return cast(bool, await self._redis.set(key, value, nx=True, ex=ttl))

    async def get_str(self, key: str) -> str | None:
        val = await self._redis.get(key)
        return val.decode("utf-8") if isinstance(val, bytes) else str(val) if val is not None else None

    async def mget(self, keys: list[str]) -> list[str | None]:
        if not keys:
            return []
        vals = await self._redis.mget(keys)
        return [(v.decode("utf-8") if isinstance(v, bytes) else str(v) if v is not None else None) for v in vals]

    async def set_str(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._redis.set(key, value, ex=ttl)

    async def set_worker_token(self, worker_id: str, token: str) -> None:
        await self._redis.set(f"orchestrator:worker:token:{worker_id}", token)

    async def get_worker_token(self, worker_id: str) -> str | None:
        token = await self._redis.get(f"orchestrator:worker:token:{worker_id}")
        return token.decode("utf-8") if token else None

    async def save_worker_access_token(self, worker_id: str, token: str, ttl: int) -> None:
        await self._redis.set(f"orchestrator:sts:token:{token}", worker_id, ex=ttl)

    async def verify_worker_access_token(self, token: str) -> str | None:
        worker_id = await self._redis.get(f"orchestrator:sts:token:{token}")
        return worker_id.decode("utf-8") if worker_id else None

    async def acquire_lock(self, key: str, holder_id: str, ttl: int) -> bool:
        return cast(bool, await self._redis.set(f"orchestrator:lock:{key}", holder_id, nx=True, ex=ttl))

    async def release_lock(self, key: str, holder_id: str) -> bool:
        LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        try:
            return cast(bool, await self._redis.eval(LUA, 1, f"orchestrator:lock:{key}", holder_id))
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
            return cast(bool, await self._redis.ping())
        except Exception:
            return False

    async def save_blueprint_contract(self, name: str, contract: dict[str, Any]) -> None:
        key = f"orchestrator:blueprint:contract:{name}"
        await self._redis.set(key, self._pack(contract))

    async def get_blueprint_contract(self, name: str) -> dict[str, Any] | None:
        key = f"orchestrator:blueprint:contract:{name}"
        data = await self._redis.get(key)
        return cast(dict[str, Any], self._unpack(data)) if data else None

    async def reindex_workers(self) -> None:
        """Scan existing worker keys and rebuild indexes."""
        async for key in self._redis.scan_iter("orchestrator:worker:info:*"):
            worker_id = key.decode("utf-8").split(":")[-1]
            raw = await self._redis.get(key)
            if raw:
                info = self._unpack(raw)
                await self.register_worker(worker_id, info, int(await self._redis.ttl(key)))
