from unittest.mock import AsyncMock, MagicMock

import pytest
from src.avtomatika.dispatcher import Dispatcher

# --- Sample Worker Data ---
GPU_WORKER = {
    "worker_id": "gpu-worker-01",
    "address": "http://gpu-worker",
    "dynamic_token": "gpu-secret",
    "supported_tasks": ["image_generation", "video_montage"],
    "resources": {
        "gpu_info": {"model": "NVIDIA T4", "vram_gb": 16},
    },
    "installed_models": [
        {"name": "stable-diffusion-1.5", "version": "1.0"},
    ],
}

CPU_WORKER = {
    "worker_id": "cpu-worker-01",
    "address": "http://cpu-worker",
    "dynamic_token": "cpu-secret",
    "supported_tasks": ["text_analysis"],
    "resources": {"gpu_info": None},
    "installed_models": [],
}


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.find_workers_for_task = AsyncMock(return_value=[])
    storage.get_workers = AsyncMock(return_value=[])
    storage.enqueue_task_for_worker = AsyncMock()
    storage.save_job_state = AsyncMock()
    return storage


@pytest.fixture
def mock_session():
    mock_sess = MagicMock()
    mock_post_response = AsyncMock()
    mock_post_response.status = 200
    mock_sess.post.return_value.__aenter__.return_value = mock_post_response
    mock_sess.post.return_value.__aexit__.return_value = None
    return mock_sess


@pytest.fixture
def mock_config():
    mock_conf = MagicMock()
    mock_conf.WORKER_TOKEN = "test-token"
    return mock_conf


@pytest.fixture
def dispatcher(mock_storage, mock_config):
    return Dispatcher(storage=mock_storage, config=mock_config)


@pytest.mark.asyncio
async def test_dispatch_selects_worker_and_queues_task(dispatcher, mock_storage):
    """Tests that the dispatcher gets workers, selects one, and queues a task for it."""
    mock_worker = {
        "worker_id": "worker-123",
        "supported_tasks": ["test_task"],
    }
    mock_storage.find_workers_for_task.return_value = ["worker-123"]
    mock_storage.get_workers.return_value = [mock_worker]
    mock_storage.enqueue_task_for_worker = AsyncMock()

    job_state = {"id": "job-abc", "tracing_context": {}}
    task_info = {"type": "test_task", "params": {"x": 1}}
    await dispatcher.dispatch(job_state, task_info)

    # Assertions
    mock_storage.find_workers_for_task.assert_called_once_with("test_task")
    mock_storage.get_workers.assert_called_once_with(["worker-123"])
    mock_storage.enqueue_task_for_worker.assert_called_once()

    # Check the data passed to enqueue_task_for_worker
    called_args, _ = mock_storage.enqueue_task_for_worker.call_args
    assert called_args[0] == mock_worker["worker_id"]  # worker_id
    payload = called_args[1]  # task_payload
    assert payload["job_id"] == job_state["id"]
    assert payload["type"] == task_info["type"]
    assert "task_id" in payload


@pytest.mark.asyncio
async def test_dispatch_logs_warning_for_busy_mo_worker(dispatcher, mock_storage, caplog):
    """Tests that the dispatcher logs a warning if no workers found.
    Updated for O(1) dispatcher: checks for 'No idle workers found' warning.
    """
    mock_storage.find_workers_for_task.return_value = []

    job_state = {"id": "job-abc", "tracing_context": {}}
    task_info = {"type": "test_task"}

    with pytest.raises(RuntimeError, match="No suitable workers"):
        await dispatcher.dispatch(job_state, task_info)

    assert "No idle workers found for task 'test_task'" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_sends_priority(dispatcher, mock_storage):
    """Tests that the dispatcher correctly passes the priority to the storage."""
    mock_worker = {
        "worker_id": "worker-123",
        "supported_tasks": ["test_task"],
    }
    mock_storage.find_workers_for_task.return_value = ["worker-123"]
    mock_storage.get_workers.return_value = [mock_worker]
    mock_storage.enqueue_task_for_worker = AsyncMock()

    job_state = {"id": "job-abc", "tracing_context": {}}
    task_info = {"type": "test_task", "params": {"x": 1}, "priority": 7.5}
    await dispatcher.dispatch(job_state, task_info)

    mock_storage.enqueue_task_for_worker.assert_called_once()
    called_args, _ = mock_storage.enqueue_task_for_worker.call_args
    assert called_args[2] == 7.5  # priority


class TestDispatcherFiltering:
    def test_is_worker_compliant_gpu_success(self, dispatcher):
        requirements = {"gpu_info": {"model": "NVIDIA T4", "vram_gb": 16}}
        assert dispatcher._is_worker_compliant(GPU_WORKER, requirements) is True

    def test_is_worker_compliant_gpu_fail_model(self, dispatcher):
        requirements = {"gpu_info": {"model": "RTX 3090"}}
        assert dispatcher._is_worker_compliant(GPU_WORKER, requirements) is False

    def test_is_worker_compliant_model_success(self, dispatcher):
        requirements = {"installed_models": ["stable-diffusion-1.5"]}
        assert dispatcher._is_worker_compliant(GPU_WORKER, requirements) is True

    @pytest.mark.asyncio
    async def test_dispatch_filters_by_gpu_from_action_factory(
        self,
        dispatcher,
        mock_storage,
    ):
        from src.avtomatika.context import ActionFactory

        workers = [GPU_WORKER, CPU_WORKER]
        mock_storage.find_workers_for_task.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        # 1. Simulate a blueprint handler creating an action
        actions = ActionFactory("job-1")
        actions.dispatch_task(
            task_type="image_generation",
            params={},
            transitions={"success": "finished"},
            resource_requirements={"gpu_info": {"model": "NVIDIA T4"}},
        )
        task_info = actions.task_to_dispatch

        # 2. Dispatch the task
        await dispatcher.dispatch({"id": "job-1", "tracing_context": {}}, task_info)

        # 3. Assert that the task was queued for the correct worker
        mock_storage.enqueue_task_for_worker.assert_called_once()
        called_args, _ = mock_storage.enqueue_task_for_worker.call_args
        dispatched_worker_id = called_args[0]
        assert dispatched_worker_id == GPU_WORKER["worker_id"]

    @pytest.mark.asyncio
    async def test_dispatch_raises_error_if_no_worker_meets_requirements(
        self,
        dispatcher,
        mock_storage,
    ):
        workers = [GPU_WORKER, CPU_WORKER]
        mock_storage.find_workers_for_task.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        task_info = {
            "type": "image_generation",
            "resource_requirements": {
                "gpu_info": {"model": "A100"},
            },  # No worker has this
        }

        with pytest.raises(RuntimeError) as excinfo:
            await dispatcher.dispatch({"id": "job-1", "tracing_context": {}}, task_info)

        assert "No worker satisfies the resource requirements" in str(
            excinfo.value,
        )

    @pytest.mark.asyncio
    async def test_dispatch_filters_by_max_cost(self, dispatcher, mock_storage):
        """Tests that filtering by `max_cost` works correctly."""
        cheapest_worker = {
            "worker_id": "worker-cheap",
            "supported_tasks": ["test_task"],
            "cost_per_second": 0.01,
        }
        expensive_worker = {
            "worker_id": "worker-expensive",
            "supported_tasks": ["test_task"],
            "cost_per_second": 0.05,
        }
        workers = [expensive_worker, cheapest_worker]
        mock_storage.find_workers_for_task.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        job_state = {"id": "job-max-cost-test", "tracing_context": {}}
        # 1. Set max_cost that allows only the cheap worker
        task_info_pass = {"type": "test_task", "max_cost": 0.02}

        await dispatcher.dispatch(job_state, task_info_pass)
        mock_storage.enqueue_task_for_worker.assert_called_once()
        called_args, _ = mock_storage.enqueue_task_for_worker.call_args
        assert called_args[0] == cheapest_worker["worker_id"]

        # 2. Set max_cost that does not allow any worker
        task_info_fail = {"type": "test_task", "max_cost": 0.005}
        with pytest.raises(RuntimeError) as excinfo:
            await dispatcher.dispatch(job_state, task_info_fail)
        assert "No worker meets the maximum cost" in str(excinfo.value)


class TestDispatcherStrategies:
    @pytest.mark.asyncio
    async def test_dispatch_selects_cheapest_worker(self, dispatcher, mock_storage):
        """Tests that with the 'cheapest' strategy, the cheapest worker is selected."""
        cheapest_worker = {
            "worker_id": "worker-cheap",
            "supported_tasks": ["test_task"],
            "cost_per_second": 0.01,
        }
        expensive_worker = {
            "worker_id": "worker-expensive",
            "supported_tasks": ["test_task"],
            "cost_per_second": 0.05,
        }
        workers = [expensive_worker, cheapest_worker]
        mock_storage.find_workers_for_task.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        job_state = {"id": "job-cost-test", "tracing_context": {}}
        task_info = {"type": "test_task", "dispatch_strategy": "cheapest"}

        await dispatcher.dispatch(job_state, task_info)

        # Check that the task was sent to the cheap worker
        mock_storage.enqueue_task_for_worker.assert_called_once()
        called_args, _ = mock_storage.enqueue_task_for_worker.call_args
        dispatched_worker_id = called_args[0]
        assert dispatched_worker_id == cheapest_worker["worker_id"]

    @pytest.mark.asyncio
    async def test_dispatch_selects_best_value_worker(self, dispatcher, mock_storage):
        """Tests that the 'best_value' strategy selects the worker with the best
        cost/reputation ratio.
        """
        # Cheap, but with a bad reputation. Score = 0.02 / 0.5 = 0.04
        worker_A = {
            "worker_id": "worker-A",
            "supported_tasks": ["test_task"],
            "cost_per_second": 0.02,
            "reputation": 0.5,
        }
        # Expensive, but with a perfect reputation. Score = 0.03 / 1.0 = 0.03
        worker_B = {
            "worker_id": "worker-B",
            "supported_tasks": ["test_task"],
            "cost_per_second": 0.03,
            "reputation": 1.0,
        }
        workers = [worker_A, worker_B]
        mock_storage.find_workers_for_task.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        job_state = {"id": "job-best-value-test", "tracing_context": {}}
        task_info = {"type": "test_task", "dispatch_strategy": "best_value"}

        await dispatcher.dispatch(job_state, task_info)

        mock_storage.enqueue_task_for_worker.assert_called_once()
        called_args, _ = mock_storage.enqueue_task_for_worker.call_args
        dispatched_worker_id = called_args[0]
        assert dispatched_worker_id == worker_B["worker_id"]
