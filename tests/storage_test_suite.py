import asyncio

import pytest
from src.avtomatika.storage.base import StorageBackend


@pytest.mark.asyncio
class StorageTestSuite:
    """
    Base class for testing implementations of StorageBackend.
    """

    async def test_enqueue_dequeue_job(self, storage: StorageBackend):
        job_id = "test-job-123"
        await storage.enqueue_job(job_id)

        # Dequeue should return (job_id, message_id)
        result = await storage.dequeue_job()
        assert result is not None
        dequeued_job_id, message_id = result
        assert dequeued_job_id == job_id
        assert isinstance(message_id, str)
        assert message_id != ""

        # Acknowledge the job
        await storage.ack_job(message_id)

    async def test_job_state_serialization(self, storage: StorageBackend):
        job_id = "test-state-123"
        initial_state = {
            "id": job_id,
            "status": "pending",
            "retry_count": 0,
            "data": {"foo": "bar", "num": 123},
            # Test binary data handling if supported by serializer (msgpack supports bytes)
            "binary_data": b"some_bytes",
        }

        await storage.save_job_state(job_id, initial_state)

        fetched_state = await storage.get_job_state(job_id)
        assert fetched_state is not None
        assert fetched_state["id"] == job_id
        assert fetched_state["data"]["foo"] == "bar"
        assert fetched_state["binary_data"] == b"some_bytes"

        # Update state
        update_data = {"status": "running", "retry_count": 1}
        updated_state = await storage.update_job_state(job_id, update_data)

        assert updated_state["status"] == "running"
        assert updated_state["retry_count"] == 1
        assert updated_state["data"]["foo"] == "bar"  # Ensure other fields are preserved

        # Verify update persistence
        final_state = await storage.get_job_state(job_id)
        assert final_state["status"] == "running"
        assert final_state["retry_count"] == 1

    async def test_register_and_get_worker_info(self, storage: StorageBackend):
        worker_id = "worker-1"
        info = {
            "worker_id": worker_id,
            "status": "idle",
            "resources": {"cpu": 4},
            # Test serialization of lists/dicts
            "tags": ["gpu", "fast"],
        }
        ttl = 60

        await storage.register_worker(worker_id, info, ttl)

        fetched_info = await storage.get_worker_info(worker_id)
        assert fetched_info is not None
        assert fetched_info["worker_id"] == worker_id
        assert fetched_info["resources"]["cpu"] == 4
        assert "gpu" in fetched_info["tags"]

    async def test_dequeue_empty_queue(self, storage: StorageBackend):
        # We assume queue is empty initially or flushed
        # For RedisStorage, dequeue has a timeout. MemoryStorage waits indefinitely.
        # This test is tricky because MemoryStorage blocks forever.
        # We will skip it for now or needs a storage-specific override/timeout check.
        pass

    async def test_recovery_logic(self, storage: StorageBackend):
        """Tests that unacknowledged messages are recovered by the same consumer."""
        # This test is specific to persistent streams (Redis).
        if storage.__class__.__name__ == "MemoryStorage":
            return

        job_id = "job-recovery-test"
        await storage.enqueue_job(job_id)

        # 1. Consumer takes the job but "crashes" (does not ACK)
        result1 = await storage.dequeue_job()
        assert result1 is not None
        id1, msg_id1 = result1
        assert id1 == job_id

        # 2. Consumer restarts (calls dequeue_job again)
        # It should receive the SAME message (from PEL) because it wasn't ACKed.
        # Wait a bit for min_idle_time if using Redis
        if storage.__class__.__name__ == "RedisStorage":
            await asyncio.sleep(0.2)
        result2 = await storage.dequeue_job()
        assert result2 is not None
        id2, msg_id2 = result2

        assert id2 == job_id
        assert msg_id2 == msg_id1

        # 3. Now ACK it
        await storage.ack_job(msg_id2)

        # 4. Should now get new messages
        job_id_2 = "job-2"
        await storage.enqueue_job(job_id_2)

        result3 = await storage.dequeue_job()
        assert result3 is not None
        assert result3[0] == job_id_2
        await storage.ack_job(result3[1])
