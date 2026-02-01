import asyncio
import logging

import pytest
from fakeredis import aioredis as fake_redis

from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.redis import RedisStorage

# Configure logging to verify locks
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_horizontal_scaling_scheduler_locks():
    """
    Verifies that multiple Orchestrator instances connected to the same Redis
    do not duplicate scheduled jobs due to distributed locking.
    """
    # 1. Shared Infrastructure (The "Network")
    shared_redis = fake_redis.FakeRedis(decode_responses=False)

    # 2. Setup Multiple Instances (Nodes)
    num_nodes = 3
    engines = []

    for i in range(num_nodes):
        config = Config()
        config.INSTANCE_ID = f"node-{i}"
        config.REDIS_STREAM_BLOCK_MS = 100  # Low block time for tests

        # All nodes share the same Redis backend
        storage = RedisStorage(shared_redis, consumer_name=config.INSTANCE_ID)
        engine = OrchestratorEngine(storage, config)

        # Hack: Inject a blueprint directly to avoid full setup overhead if not needed
        # but create_background_job needs it.
        # We will mock the blueprint registration.
        from avtomatika.blueprint import StateMachineBlueprint

        bp = StateMachineBlueprint("scheduled_bp")

        @bp.handler_for("start", is_start=True)
        async def start(actions):
            pass

        bp.validate()
        engine.blueprints["scheduled_bp"] = bp

        engines.append(engine)

    # 3. Setup Scheduler on all nodes
    # We manually trigger the scheduler logic to verify locking without waiting for real time
    # or relying on the full background loop which might be slow.

    from avtomatika.scheduler_config_loader import ScheduledJobConfig

    test_job = ScheduledJobConfig(
        name="test_concurrent_job",
        blueprint="scheduled_bp",
        interval_seconds=10,  # Not used in direct trigger test, but required
        input_data={},
    )

    # 4. Simulate Concurrent Execution
    # All nodes try to trigger the same job at the same "time"

    async def try_trigger(eng):
        # Access private scheduler method for testing lock
        # In reality, Scheduler.run() calls _process_interval_job
        # We simulate the critical section of _process_interval_job

        scheduler = eng.app.get("avtomatika_scheduler")  # Setup hasn't run, so this might be missing
        # Let's manually init scheduler since we didn't call engine.setup() fully
        if not scheduler:
            from avtomatika.scheduler import Scheduler

            scheduler = Scheduler(eng)

        # Manually invoke the locking logic we want to test
        # lock_key = f"scheduler:lock:interval:{job.name}"
        lock_key = f"scheduler:lock:interval:{test_job.name}"

        acquired = await eng.storage.set_nx_ttl(lock_key, "locked", ttl=5)
        if acquired:
            await eng.create_background_job(test_job.blueprint, test_job.input_data)
            return True
        return False

    # Run setup to initialize internal components (like app keys)
    for eng in engines:
        # Minimal setup manually
        from avtomatika.scheduler import Scheduler

        eng.app["avtomatika_scheduler"] = Scheduler(eng)
        await eng.storage.ping()  # Ensure connection

    # Fire all triggers concurrently
    results = await asyncio.gather(*[try_trigger(eng) for eng in engines])

    # 5. Assertions
    successful_triggers = sum(results)

    print(f"Nodes attempted trigger: {num_nodes}")
    print(f"Successful triggers (lock acquired): {successful_triggers}")

    assert successful_triggers == 1, "Only one node should acquire the lock and trigger the job"

    # Verify only one job was created in storage
    # We can check the stream length
    stream_len = await shared_redis.xlen("orchestrator:job_stream")
    assert stream_len == 1, f"Expected 1 job in stream, found {stream_len}"

    await shared_redis.aclose()


@pytest.mark.asyncio
async def test_horizontal_scaling_watcher_locks():
    """
    Verifies that multiple Watcher instances do not race to process the same timed-out jobs.
    """
    shared_redis = fake_redis.FakeRedis(decode_responses=False)

    # Setup 2 nodes
    node1_storage = RedisStorage(shared_redis, consumer_name="node-1")
    node2_storage = RedisStorage(shared_redis, consumer_name="node-2")

    # Add a "stuck" job
    job_id = "stuck-job-1"
    # Add to watched set with timestamp 0 (expired long ago)
    await node1_storage.add_job_to_watch(job_id, 0)

    # Verify it's there
    pending = await node1_storage.get_timed_out_jobs(limit=10)
    assert len(pending) == 1
    assert pending[0] == job_id

    # Re-add for the race test
    await node1_storage.add_job_to_watch(job_id, 0)

    # Simulate Watcher logic from both nodes concurrently
    # The Watcher uses a global lock "watcher_lock" BEFORE fetching jobs

    async def watcher_tick(storage, node_name):
        # Simulate Watcher._check_timeouts logic
        lock_key = "global_watcher_lock"
        acquired = await storage.acquire_lock(lock_key, node_name, ttl=5)
        processed = []
        if acquired:
            try:
                jobs = await storage.get_timed_out_jobs(limit=100)
                processed = jobs
                # Simulate processing time
                await asyncio.sleep(0.01)
            finally:
                await storage.release_lock(lock_key, node_name)
        return processed

    # Run concurrently
    results = await asyncio.gather(watcher_tick(node1_storage, "node-1"), watcher_tick(node2_storage, "node-2"))

    node1_processed = results[0]
    node2_processed = results[1]

    total_processed = len(node1_processed) + len(node2_processed)

    print(f"Node 1 processed: {node1_processed}")
    print(f"Node 2 processed: {node2_processed}")

    # Since we use a lock, only ONE node should have entered the critical section
    # and fetched the job. The other should have failed to acquire lock or found empty list (if serial).
    # But get_timed_out_jobs is atomic (zrange + zrem), so even without lock, duplicates shouldn't happen.
    # The Lock is for efficiency to prevent redundant DB calls.

    assert total_processed == 1, "The stuck job should be processed exactly once"

    await shared_redis.aclose()
