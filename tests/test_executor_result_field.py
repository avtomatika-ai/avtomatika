from unittest.mock import AsyncMock, MagicMock

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin
import pytest
from src.avtomatika.executor import JobExecutor


@pytest.mark.asyncio
async def test_executor_updates_result_field_multi_step():
    """
    Verifies that 'result' field is updated on every step.
    """
    engine = MagicMock()
    history = AsyncMock()
    storage = AsyncMock()
    engine.storage = storage
    # Ensure config values are integers, not mocks
    engine.config = MagicMock()
    engine.config.MAX_TRANSITIONS_PER_JOB = 100
    engine.config.JOB_MAX_RETRIES = 3
    engine.config.S3_AUTO_CLEANUP = True

    # Properly mock S3 service to return a task_files with an async cleanup
    s3_service = MagicMock()
    engine.app.get.return_value = s3_service
    task_files = MagicMock()
    s3_service.get_task_files.return_value = task_files
    task_files.cleanup = AsyncMock()

    executor = JobExecutor(engine, history, metrics=MagicMock())

    job_id = "test-job-multi"
    message_id = "msg-multi"

    # Initial state
    job_state = {
        "id": job_id,
        "blueprint_name": "test_bp",
        "current_state": "step_1",
        "status": "running",
        "state_history": {},
        "transition_count": 0,
    }
    storage.get_job_state.return_value = job_state

    bp = MagicMock()
    engine.blueprints.get.return_value = bp
    bp.end_states = ["finished"]

    # Step 1 execution
    async def handler_1(context):
        context.state_history["step_1"] = {"val": 1}
        return context.actions.go_to("step_2")

    bp.find_handler.return_value = handler_1
    bp.get_handler_params.return_value = ["context"]

    # We must mock _handle_transition properly
    executor._handle_transition = AsyncMock()

    await executor._process_job(job_id, message_id)

    # Check result after step 1 - should NOT have result anymore
    assert executor._handle_transition.called
    args, _ = executor._handle_transition.call_args
    state_after_1 = args[0]
    assert "result" not in state_after_1

    # Now simulate step 2 (Terminal state)
    job_state["current_state"] = "finished"
    job_state["status"] = "running"
    # No result here anymore
    storage.get_job_state.return_value = job_state

    async def handler_2(context):
        context.state_history["finished"] = {"val": 2}
        return context.actions.go_to(None)  # Final

    bp.find_handler.return_value = handler_2

    # Reset and mock terminal reached - but we need it to actually run to see the result
    # or we can mock it but check what it WAS CALLED WITH after it would have done its job.
    original_terminal_reached = executor._handle_terminal_reached
    executor._handle_terminal_reached = AsyncMock(side_effect=original_terminal_reached)

    await executor._process_job(job_id, message_id)

    # Check result after step 2
    assert executor._handle_terminal_reached.called
    args, _ = executor._handle_terminal_reached.call_args
    state_after_2 = args[0]
    assert state_after_2["result"] == {"val": 2}
    assert state_after_2["state_history"]["step_1"] == {"val": 1}


@pytest.mark.asyncio
async def test_executor_result_field_empty_history_safe():
    """
    Verifies that the executor doesn't crash if state_history is empty.
    """
    engine = MagicMock()
    history = AsyncMock()
    storage = AsyncMock()
    engine.storage = storage
    # Ensure config values are integers, not mocks
    engine.config = MagicMock()
    engine.config.MAX_TRANSITIONS_PER_JOB = 100
    engine.config.JOB_MAX_RETRIES = 3
    engine.config.S3_AUTO_CLEANUP = True

    # Properly mock S3 service to return a task_files with an async cleanup
    s3_service = MagicMock()
    engine.app.get.return_value = s3_service
    task_files = MagicMock()
    s3_service.get_task_files.return_value = task_files
    task_files.cleanup = AsyncMock()

    executor = JobExecutor(engine, history, metrics=MagicMock())

    job_id = "test-job-empty"
    message_id = "msg-empty"

    job_state = {
        "id": job_id,
        "blueprint_name": "test_bp",
        "current_state": "start",
        "status": "running",
        "state_history": {},
        "transition_count": 0,
    }
    storage.get_job_state.return_value = job_state

    bp = MagicMock()
    engine.blueprints.get.return_value = bp
    bp.end_states = []

    # Handler that does NOTHING (no history update)
    async def mock_handler(context):
        return context.actions.go_to("next")

    bp.find_handler.return_value = mock_handler
    bp.get_handler_params.return_value = ["context"]
    executor._handle_transition = AsyncMock()

    # This should not raise IndexError or any other exception
    await executor._process_job(job_id, message_id)

    args, _ = executor._handle_transition.call_args
    passed_state = args[0]
    assert "result" not in passed_state or passed_state["result"] is None
