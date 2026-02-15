import pytest

try:
    from .storage_test_suite import StorageTestSuite

    redis_installed = True
except ImportError:
    redis_installed = False

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not redis_installed,
        reason="redis package not installed, skipping redis-specific tests",
    ),
]


@pytest.fixture
def storage(redis_storage):
    """Binds the `redis_storage` fixture from conftest to the `storage` name."""
    return redis_storage


class TestRedisStorage(StorageTestSuite):
    """
    Runs the common storage test suite for RedisStorage.
    """

    async def test_cleanup_expired_workers(self, storage):
        """Tests that cleanup_expired_workers removes dead workers and their queues."""
        # 1. Register a worker with short TTL (but we simulate expiration by direct deletion or sleep)
        worker_id = "dead-worker"
        ttl = 1  # 1 second TTL
        info = {
            "worker_id": worker_id,
            "status": "idle",
            "supported_skills": ["task_a"],
        }
        await storage.register_worker(worker_id, info, ttl)

        # 2. Add a task to its queue
        task_payload = {"id": 1}
        await storage.enqueue_task_for_worker(worker_id, task_payload, priority=10)

        # Verify registration and queue
        active_ids = await storage.get_active_worker_ids()
        assert worker_id in active_ids
        task = await storage.dequeue_task_for_worker(worker_id, timeout=1)
        assert task is not None
        # Re-enqueue to have something to clean up
        await storage.enqueue_task_for_worker(worker_id, task_payload, priority=10)

        # 3. Simulate expiration: Wait for TTL to expire
        import asyncio

        await asyncio.sleep(1.1)

        # At this point, the main key (info) should be gone from Redis due to TTL,
        # but the index and queue (which have no TTL) remain.
        # Verify info key is gone (using underlying redis client for test)
        exists = await storage._redis.exists(f"orchestrator:worker:info:{worker_id}")
        assert not exists, "Worker info key should have expired"

        # 4. Run cleanup
        await storage.cleanup_expired_workers()

        # 5. Verify cleanup
        # Index should be clean
        active_ids = await storage.get_active_worker_ids()
        assert worker_id not in active_ids

        # Task queue should be deleted
        queue_exists = await storage._redis.exists(f"orchestrator:task_queue:{worker_id}")
        assert not queue_exists, "Orphaned task queue should be deleted"

        # Task index should be clean
        task_index_exists = await storage._redis.sismember("orchestrator:index:workers:task:task_a", worker_id)
        assert not task_index_exists
