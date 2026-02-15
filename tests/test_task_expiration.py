import asyncio
from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from avtomatika.constants import JOB_STATUS_FAILED, JOB_STATUS_WAITING_FOR_WORKER, TASK_STATUS_SUCCESS
from avtomatika.services.worker_service import WorkerService
from avtomatika.watcher import Watcher


@pytest.mark.asyncio
async def test_dispatch_timeout():
    """Проверка таймаута ожидания в очереди (dispatch timeout)."""
    engine = MagicMock()
    # Имитируем протухшую задачу в очереди (picked_up is None)
    job_id = "expired-in-queue"
    engine.storage.get_timed_out_jobs = AsyncMock(return_value=[job_id])
    engine.storage.get_job_state = AsyncMock(
        return_value={
            "id": job_id,
            "status": JOB_STATUS_WAITING_FOR_WORKER,
            "blueprint_name": "test_bp",
            "dispatch_deadline": monotonic() - 10,  # Дедлайн в прошлом
            "task_picked_up_at": None,  # Воркер не забирал
        }
    )
    engine.storage.acquire_lock = AsyncMock(return_value=True)
    engine.storage.release_lock = AsyncMock(return_value=True)
    engine.storage.save_job_state = AsyncMock()
    engine.send_job_webhook = AsyncMock()
    engine.app.get = MagicMock(return_value=None)  # Без S3
    engine.handle_job_timeout = AsyncMock()

    watcher = Watcher(engine)
    watcher.watch_interval_seconds = 0.01

    # Один цикл вочера
    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(0.05)
    watcher.stop()
    await task

    # Проверяем, что был вызван метод обработки таймаута
    engine.handle_job_timeout.assert_called()


@pytest.mark.asyncio
async def test_result_deadline_timeout():
    """Проверка абсолютного дедлайна результата (result timeout)."""
    engine = MagicMock()
    # Задача взята воркером, но дедлайн результата прошел
    job_id = "expired-during-execution"
    engine.storage.get_timed_out_jobs = AsyncMock(return_value=[job_id])
    engine.storage.get_job_state = AsyncMock(
        return_value={
            "id": job_id,
            "status": JOB_STATUS_WAITING_FOR_WORKER,
            "blueprint_name": "test_bp",
            "result_deadline": monotonic() - 5,
            "task_picked_up_at": monotonic() - 10,  # Взята 10 сек назад
        }
    )
    engine.storage.acquire_lock = AsyncMock(return_value=True)
    engine.storage.release_lock = AsyncMock(return_value=True)
    engine.storage.save_job_state = AsyncMock()
    engine.send_job_webhook = AsyncMock()
    engine.app.get = MagicMock(return_value=None)
    engine.handle_job_timeout = AsyncMock()

    watcher = Watcher(engine)
    watcher.watch_interval_seconds = 0.01

    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(0.05)
    watcher.stop()
    await task

    engine.handle_job_timeout.assert_called()


@pytest.mark.asyncio
async def test_late_result_handling():
    """Проверка отклонения результата, если задача уже протухла."""
    storage = MagicMock()
    history = MagicMock()
    config = MagicMock()
    engine = MagicMock()

    service = WorkerService(storage, history, config, engine)

    job_id = "late-job"
    # Задача уже в статусе failed (например, отменена вочером)
    storage.get_job_state = AsyncMock(
        return_value={
            "id": job_id,
            "status": JOB_STATUS_FAILED,
            "error_message": "Worker task timed out while waiting in queue (dispatch timeout).",
        }
    )

    result_payload = {
        "job_id": job_id,
        "task_id": "task-1",
        "worker_id": "worker-1",
        "status": TASK_STATUS_SUCCESS,
        "data": {"secret": "data"},
    }

    response = await service.process_task_result(result_payload, "worker-1")

    # Проверяем, что ответ содержит код LATE_RESULT
    assert isinstance(response, dict)
    assert response["status"] == "ignored"
    # Reason code for late/timeout
    from src.avtomatika.constants import IGNORED_REASON_LATE

    assert response["reason"] == IGNORED_REASON_LATE

    # Проверяем, что состояние НЕ обновлялось (save_job_state не вызывался)
    storage.save_job_state.assert_not_called()


@pytest.mark.asyncio
async def test_s3_cleanup_on_timeout():
    """Проверка вызова очистки S3 при таймауте."""
    from avtomatika.engine import OrchestratorEngine

    mock_storage = AsyncMock()

    class MockConfig:
        S3_AUTO_CLEANUP = True
        LOG_LEVEL = "INFO"
        LOG_FORMAT = "text"
        TZ = "UTC"
        RATE_LIMITING_ENABLED = False
        CLIENTS_CONFIG_PATH = ""
        WORKERS_CONFIG_PATH = ""
        SCHEDULES_CONFIG_PATH = ""
        BLUEPRINTS_DIR = ""
        JOB_MAX_RETRIES = 3
        WORKER_TIMEOUT_SECONDS = 300
        WATCHER_INTERVAL_SECONDS = 20
        REDIS_STREAM_BLOCK_MS = 0  # executor uses this

    mock_config = MockConfig()

    # Use real engine to test logic inside handle_job_timeout
    engine = OrchestratorEngine(mock_storage, mock_config)
    engine.storage.save_job_state = AsyncMock()
    engine.send_job_webhook = AsyncMock()
    engine.handle_task_failure = AsyncMock()

    # Mock S3
    s3_service = MagicMock()
    task_files = MagicMock()
    task_files.cleanup = AsyncMock()
    s3_service.get_task_files.return_value = task_files

    from avtomatika.app_keys import S3_SERVICE_KEY

    engine.app[S3_SERVICE_KEY] = s3_service

    # Precondition check
    assert engine.config.S3_AUTO_CLEANUP is True
    assert engine.app.get(S3_SERVICE_KEY) is s3_service

    job_id = "s3-cleanup-job"
    engine.storage.get_timed_out_jobs = AsyncMock(return_value=[job_id])
    engine.storage.get_job_state = AsyncMock(
        return_value={
            "id": job_id,
            "status": JOB_STATUS_WAITING_FOR_WORKER,
            "blueprint_name": "test_bp",
            "task_picked_up_at": None,
            "dispatch_deadline": 0,
        }
    )
    engine.storage.acquire_lock = AsyncMock(return_value=True)
    engine.storage.release_lock = AsyncMock(return_value=True)

    watcher = Watcher(engine)
    watcher.watch_interval_seconds = 0.01

    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(0.05)
    watcher.stop()
    await task

    # Allow background create_task to run
    await asyncio.sleep(0.1)

    # Verify chain of calls
    s3_service.get_task_files.assert_called_with(job_id)
    task_files.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_retry_logic_respects_deadlines():
    """Проверка, что логика ретраев учитывает существующие дедлайны."""
    from avtomatika.engine import OrchestratorEngine

    storage = MagicMock()
    config = MagicMock()
    config.JOB_MAX_RETRIES = 3
    config.LOG_LEVEL = "INFO"
    config.LOG_FORMAT = "text"
    config.TZ = "UTC"
    config.RATE_LIMITING_ENABLED = False

    engine = OrchestratorEngine(storage, config)

    job_id = "retry-job"
    now = 1000.0
    dispatch_deadline = now + 30

    job_state = {
        "id": job_id,
        "retry_count": 0,
        "dispatch_deadline": dispatch_deadline,
        "result_deadline": None,
        "current_task_info": {"timeout_seconds": 60},
    }

    with patch("avtomatika.engine.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = now
        storage.save_job_state = AsyncMock()
        storage.add_job_to_watch = AsyncMock()
        engine.dispatcher = MagicMock()
        engine.dispatcher.dispatch = AsyncMock()

        await engine.handle_task_failure(job_state, "task-1", "error")

        # Проверяем, что таймаут в вочере установлен именно на дедлайн диспатча (30 сек),
        # а не на дефолтные 60 сек из таска.
        storage.add_job_to_watch.assert_called_with(job_id, dispatch_deadline)


@pytest.mark.asyncio
async def test_action_factory_default_timeouts():
    """Проверка использования таймаутов по умолчанию в ActionFactory."""
    from avtomatika.context import ActionFactory

    # Задаем дефолтные значения уровня Job
    factory = ActionFactory(job_id="job-1", default_dispatch_timeout=15, default_result_timeout=45)

    # Вызываем диспатч БЕЗ явных таймаутов
    factory.dispatch_task(task_type="test", params={}, transitions={"success": "next"})

    task_info = factory.task_to_dispatch
    assert task_info["dispatch_timeout_seconds"] == 15
    assert task_info["result_timeout_seconds"] == 45

    # Проверяем, что явный таймаут имеет приоритет
    factory2 = ActionFactory(job_id="job-2", default_dispatch_timeout=15)
    factory2.dispatch_task(
        task_type="test",
        params={},
        transitions={"success": "next"},
        dispatch_timeout_seconds=5,  # Явный
    )
    assert factory2.task_to_dispatch["dispatch_timeout_seconds"] == 5


@pytest.mark.asyncio
async def test_parallel_dispatch_timeouts():
    """Проверка, что параллельные ветки получают правильные дедлайны в Watcher."""
    from avtomatika.executor import JobExecutor

    engine = MagicMock()
    engine.config.WORKER_TIMEOUT_SECONDS = 100
    storage = MagicMock()
    storage.save_job_state = AsyncMock()
    storage.add_job_to_watch = AsyncMock()
    engine.storage = storage
    engine.dispatcher.dispatch = AsyncMock()

    executor = JobExecutor(engine, MagicMock())

    job_state = {"id": "parent-job", "current_state": "start", "tracing_context": {}}

    parallel_info = {
        "tasks": [{"type": "t1", "dispatch_timeout_seconds": 20}, {"type": "t2", "result_timeout_seconds": 50}],
        "aggregate_into": "target",
    }

    with patch("avtomatika.executor.monotonic") as mock_mono:
        now = 100.0
        mock_mono.return_value = now

        await executor._handle_parallel_dispatch(job_state, parallel_info, 0)

        # Проверяем, что для каждой ветки вызван add_job_to_watch с правильным временем
        # Ветка 1: now + 20 = 120
        # Ветка 2: now + 50 = 150
        calls = storage.add_job_to_watch.call_args_list
        # calls[0] это (f"{job_id}:{branch_id}", timeout_at)
        timeouts = [call[0][1] for call in calls]
        assert 120.0 in timeouts
        assert 150.0 in timeouts


@pytest.mark.asyncio
async def test_reputation_impact_on_execution_timeout():
    """Проверка, что Watcher пишет в историю провал воркера при таймауте выполнения."""
    from avtomatika.engine import OrchestratorEngine

    mock_storage = AsyncMock()
    mock_config = MagicMock()
    mock_config.S3_AUTO_CLEANUP = False
    mock_config.LOG_LEVEL = "INFO"
    mock_config.LOG_FORMAT = "text"
    mock_config.TZ = "UTC"
    mock_config.RATE_LIMITING_ENABLED = False

    engine = OrchestratorEngine(mock_storage, mock_config)
    engine.storage.save_job_state = AsyncMock()
    engine.send_job_webhook = AsyncMock()
    # Mock history storage specifically
    engine.history_storage = MagicMock()
    engine.history_storage.log_job_event = AsyncMock()
    engine.handle_task_failure = AsyncMock()

    job_id = "reputation-test-job"
    worker_id = "slacker-worker"

    engine.storage.get_timed_out_jobs = AsyncMock(return_value=[job_id])
    engine.storage.get_job_state = AsyncMock(
        return_value={
            "id": job_id,
            "status": JOB_STATUS_WAITING_FOR_WORKER,
            "blueprint_name": "test_bp",
            "current_state": "processing",
            "task_picked_up_at": 100.0,  # Был взят
            "task_worker_id": worker_id,
            "current_task_id": "task-1",
        }
    )
    engine.storage.acquire_lock = AsyncMock(return_value=True)
    engine.storage.release_lock = AsyncMock(return_value=True)

    watcher = Watcher(engine)
    watcher.watch_interval_seconds = 0.01

    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(0.05)
    watcher.stop()
    await task

    # Проверяем, что в историю ушло событие task_finished со статусом failure
    history_calls = engine.history_storage.log_job_event.call_args_list
    task_failure_logged = any(
        call[0][0].get("event_type") == "task_finished"
        and call[0][0].get("worker_id") == worker_id
        and call[0][0].get("context_snapshot", {}).get("status") == "failure"
        for call in history_calls
    )
    assert task_failure_logged, "Watcher should log task failure to history for reputation impact"


@pytest.mark.asyncio
async def test_sub_blueprint_timeout_propagation():
    """Проверка передачи таймаутов в дочерние блупринты."""
    from avtomatika.executor import JobExecutor

    engine = MagicMock()
    storage = MagicMock()
    storage.save_job_state = AsyncMock()
    storage.enqueue_job = AsyncMock()
    engine.storage = storage

    history_storage = MagicMock()
    history_storage.log_job_event = AsyncMock()
    executor = JobExecutor(engine, history_storage)

    parent_job = {"id": "parent-1", "current_state": "running_sub"}
    sub_info = {"blueprint_name": "child_bp", "initial_data": {"test": 1}, "dispatch_timeout": 33, "result_timeout": 99}

    await executor._handle_run_blueprint(parent_job, sub_info, 0)

    # Проверяем состояние созданного дочернего джоба
    # Второе сохранение в save_job_state (первое для ребенка, второе обновление родителя)
    child_state = storage.save_job_state.call_args_list[0][0][1]
    assert child_state["blueprint_name"] == "child_bp"
    assert child_state["dispatch_timeout"] == 33
    assert child_state["result_timeout"] == 99
