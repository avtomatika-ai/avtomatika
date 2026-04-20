# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock

import pytest
from rxon.models import InstalledArtifact, Resources
from rxon.utils import from_dict
from src.avtomatika.context import ActionFactory
from src.avtomatika.dispatcher import Dispatcher

# --- Sample Worker Data ---
GPU_WORKER = {
    "worker_id": "gpu-worker-01",
    "address": "http://gpu-worker",
    "dynamic_token": "gpu-secret",
    "supported_skills": [
        {"name": "image_generation", "type": "image_generation"},
        {"name": "video_montage", "type": "video_montage"},
    ],
    "resources": {
        "devices": [{"type": "gpu", "model": "NVIDIA T4", "properties": {"memory_gb": 16}}],
    },
    "installed_artifacts": [
        {"name": "stable-diffusion-1.5", "version": "1.0"},
    ],
}

CPU_WORKER = {
    "worker_id": "cpu-worker-01",
    "address": "http://cpu-worker",
    "dynamic_token": "cpu-secret",
    "supported_skills": [{"name": "text_analysis", "type": "text_analysis"}],
    "resources": {"devices": None},
    "installed_artifacts": [],
}


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.find_workers_for_skill = AsyncMock(return_value=[])
    storage.find_hot_workers = AsyncMock(return_value=[])
    storage.find_workers_by_hot_skill = AsyncMock(return_value=[])
    storage.get_workers = AsyncMock(return_value=[])
    storage.enqueue_task_for_worker = AsyncMock()
    storage.save_job_state = AsyncMock()
    storage.increment_worker_load = AsyncMock()
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
        "supported_skills": [{"name": "test_task"}],
    }
    mock_storage.find_workers_for_skill.return_value = ["worker-123"]
    mock_storage.get_workers.return_value = [mock_worker]
    mock_storage.enqueue_task_for_worker = AsyncMock()

    job_state = {"id": "job-abc", "tracing_context": {}}
    task_info = {"type": "test_task", "params": {"x": 1}}
    await dispatcher.dispatch(job_state, task_info)

    mock_storage.find_workers_for_skill.assert_called_once_with("test_task")
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
    mock_storage.find_workers_for_skill.return_value = []

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
        "supported_skills": [{"name": "test_task"}],
    }
    mock_storage.find_workers_for_skill.return_value = ["worker-123"]
    mock_storage.get_workers.return_value = [mock_worker]
    mock_storage.enqueue_task_for_worker = AsyncMock()

    job_state = {"id": "job-abc", "tracing_context": {}}
    task_info = {"type": "test_task", "params": {"x": 1}, "priority": 7.5}
    await dispatcher.dispatch(job_state, task_info)

    mock_storage.enqueue_task_for_worker.assert_called_once()
    called_args, _ = mock_storage.enqueue_task_for_worker.call_args
    assert called_args[2] == 7.5  # priority


class TestDispatcherFiltering:
    def test_check_worker_compliance_gpu_success(self, dispatcher):
        requirements = {"resources": {"devices": [{"type": "gpu", "model": "NVIDIA T4", "memory_gb": 16}]}}
        parsed_resources = from_dict(Resources, requirements["resources"])
        is_compliant, _ = dispatcher._check_worker_compliance(
            GPU_WORKER, requirements, parsed_resources=parsed_resources
        )
        assert is_compliant is True

    def test_check_worker_compliance_gpu_fail_model(self, dispatcher):
        requirements = {"resources": {"devices": [{"type": "gpu", "model": "RTX 3090"}]}}
        parsed_resources = from_dict(Resources, requirements["resources"])
        is_compliant, _ = dispatcher._check_worker_compliance(
            GPU_WORKER, requirements, parsed_resources=parsed_resources
        )
        assert is_compliant is False

    def test_check_worker_compliance_model_success(self, dispatcher):
        requirements = {"installed_artifacts": ["stable-diffusion-1.5"]}
        parsed_artifacts = [InstalledArtifact(name="stable-diffusion-1.5")]
        is_compliant, _ = dispatcher._check_worker_compliance(
            GPU_WORKER, requirements, parsed_artifacts=parsed_artifacts
        )
        assert is_compliant is True

    def test_check_worker_compliance_artifact_properties_success(self, dispatcher):
        worker = {
            "installed_artifacts": [{"name": "test-model", "version": "2.0", "properties": {"quantization": "int8"}}]
        }
        requirements = {"installed_artifacts": [{"name": "test-model", "properties": {"quantization": "int8"}}]}
        parsed_artifacts = [from_dict(InstalledArtifact, requirements["installed_artifacts"][0])]
        is_compliant, _ = dispatcher._check_worker_compliance(worker, requirements, parsed_artifacts=parsed_artifacts)
        assert is_compliant is True

    def test_check_worker_compliance_artifact_properties_failure(self, dispatcher):
        worker = {
            "installed_artifacts": [{"name": "test-model", "version": "2.0", "properties": {"quantization": "fp16"}}]
        }
        requirements = {"installed_artifacts": [{"name": "test-model", "properties": {"quantization": "int8"}}]}
        parsed_artifacts = [from_dict(InstalledArtifact, requirements["installed_artifacts"][0])]
        is_compliant, _ = dispatcher._check_worker_compliance(worker, requirements, parsed_artifacts=parsed_artifacts)
        assert is_compliant is False

    def test_check_worker_compliance_missing_resources(self, dispatcher):
        # Test case: worker is missing resources completely
        worker_no_res = {"worker_id": "test"}
        parsed_resources = from_dict(Resources, {"properties": {"cpu_cores": 2}})
        is_compliant, reason = dispatcher._check_worker_compliance(
            worker_no_res, {"resources": {"properties": {"cpu_cores": 2}}}, parsed_resources=parsed_resources
        )
        assert is_compliant is False
        assert reason == "missing_worker_resources"

    def test_check_worker_compliance_skip_parsing(self, dispatcher):
        # Test case: parsed_resources is None, so resources check is skipped even if requirements has 'resources'.
        # Since backward compatibility is removed, it should NOT parse them from 'requirements'.
        requirements = {"resources": {"devices": [{"type": "gpu", "model": "NVIDIA T4", "memory_gb": 16}]}}
        # But wait! The current implementation of `_check_worker_compliance` ONLY checks resources
        # `if parsed_resources is not None:`
        # So if we don't pass `parsed_resources`, it will skip resource validation completely
        # and return True if no other requirements fail.
        is_compliant, reason = dispatcher._check_worker_compliance(CPU_WORKER, requirements)
        assert is_compliant is True

    def test_check_worker_compliance_invalid_artifact(self, dispatcher):
        worker = {"installed_artifacts": [{"name": "v1"}]}
        requirements = {"installed_artifacts": ["v2"]}
        parsed_artifacts = [InstalledArtifact(name="v2")]
        is_compliant, reason = dispatcher._check_worker_compliance(
            worker, requirements, parsed_artifacts=parsed_artifacts
        )
        assert is_compliant is False
        assert reason == "missing_artifact: v2"

    @pytest.mark.asyncio
    async def test_dispatch_filters_by_gpu_from_action_factory(
        self,
        dispatcher,
        mock_storage,
    ):
        workers = [GPU_WORKER, CPU_WORKER]
        mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        actions = ActionFactory("job-1")
        actions.dispatch_task(
            task_type="image_generation",
            params={},
            transitions={"success": "finished"},
            resource_requirements={"resources": {"devices": [{"type": "gpu", "model": "NVIDIA T4"}]}},
        )
        task_info = actions.task_to_dispatch

        await dispatcher.dispatch({"id": "job-1", "tracing_context": {}}, task_info)

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
        mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        task_info = {
            "type": "image_generation",
            "resource_requirements": {
                "resources": {"devices": [{"type": "gpu", "model": "A100"}]},
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
            "supported_skills": [{"name": "test_task"}],
            "capabilities": {"cost_per_skill": {"test_task": 0.01}},
        }
        expensive_worker = {
            "worker_id": "worker-expensive",
            "supported_skills": [{"name": "test_task"}],
            "capabilities": {"cost_per_skill": {"test_task": 0.05}},
        }
        workers = [expensive_worker, cheapest_worker]
        mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        job_state = {"id": "job-max-cost-test", "tracing_context": {}}
        task_info_pass = {"type": "test_task", "max_cost": 0.02}

        await dispatcher.dispatch(job_state, task_info_pass)
        mock_storage.enqueue_task_for_worker.assert_called_once()
        called_args, _ = mock_storage.enqueue_task_for_worker.call_args
        assert called_args[0] == cheapest_worker["worker_id"]

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
            "supported_skills": [{"name": "test_task"}],
            "capabilities": {"cost_per_skill": {"test_task": 0.01}},
        }
        expensive_worker = {
            "worker_id": "worker-expensive",
            "supported_skills": [{"name": "test_task"}],
            "capabilities": {"cost_per_skill": {"test_task": 0.05}},
        }
        workers = [expensive_worker, cheapest_worker]
        mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
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
            "supported_skills": [{"name": "test_task"}],
            "capabilities": {"cost_per_skill": {"test_task": 0.02}},
            "reputation": 0.5,
        }
        # Expensive, but with a perfect reputation. Score = 0.03 / 1.0 = 0.03
        worker_B = {
            "worker_id": "worker-B",
            "supported_skills": [{"name": "test_task"}],
            "capabilities": {"cost_per_skill": {"test_task": 0.03}},
            "reputation": 1.0,
        }
        workers = [worker_A, worker_B]
        mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in workers]
        mock_storage.get_workers.return_value = workers
        mock_storage.enqueue_task_for_worker = AsyncMock()

        job_state = {"id": "job-best-value-test", "tracing_context": {}}
        task_info = {"type": "test_task", "dispatch_strategy": "best_value"}

        await dispatcher.dispatch(job_state, task_info)

        mock_storage.enqueue_task_for_worker.assert_called_once()
        called_args, _ = mock_storage.enqueue_task_for_worker.call_args
        dispatched_worker_id = called_args[0]
        assert dispatched_worker_id == worker_B["worker_id"]


@pytest.mark.asyncio
async def test_dispatcher_worker_cache(dispatcher, mock_storage):
    """Checks that Dispatcher caches worker data to avoid redundant Redis hits."""
    mock_storage.get_workers.return_value = [{"worker_id": "w1", "reputation": 1.0}]

    # First call - should hit storage
    workers1 = await dispatcher._get_workers_cached(["w1"])
    assert len(workers1) == 1
    assert mock_storage.get_workers.call_count == 1

    # Second call - should hit cache
    workers2 = await dispatcher._get_workers_cached(["w1"])
    assert len(workers2) == 1
    assert mock_storage.get_workers.call_count == 1  # Still 1


@pytest.mark.asyncio
async def test_dispatcher_candidates_limit(mock_storage, mock_config):
    """Checks that Dispatcher limits compliance checks via DISPATCHER_MAX_CANDIDATES."""
    mock_config.DISPATCHER_MAX_CANDIDATES = 2
    dispatcher = Dispatcher(mock_storage, mock_config)

    # Mock compliance check to always return True
    dispatcher._check_worker_compliance = MagicMock(return_value=(True, None))

    # 10 capable workers
    capable_workers = [{"worker_id": f"w{i}"} for i in range(10)]
    mock_storage.find_workers_for_skill.return_value = [w["worker_id"] for w in capable_workers]
    mock_storage.get_workers.return_value = capable_workers

    job_state = {"id": "j1", "blueprint_name": "b1", "tracing_context": {}}
    task_info = {"type": "t1", "resource_requirements": {"cpu": 1}}

    with suppress(Exception):
        await dispatcher.dispatch(job_state, task_info)

    # Should be called exactly 2 times because limit is 2 and they all comply
    assert dispatcher._check_worker_compliance.call_count == 2
