from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from src.avtomatika.blueprint import StateMachineBlueprint
from src.avtomatika.context import ActionFactory
from src.avtomatika.executor import JobExecutor


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.storage = AsyncMock()
    engine.history_storage = AsyncMock()
    engine.dispatcher = AsyncMock()
    engine.blueprints = {}
    engine.config = MagicMock()
    engine.config.JOB_MAX_RETRIES = 3  # Default max retries for tests
    return engine


@pytest.fixture
def job_executor(mock_engine):
    return JobExecutor(mock_engine, mock_engine.history_storage)


@pytest.mark.asyncio
async def test_process_job_not_found(job_executor, caplog):
    job_executor.storage.get_job_state.return_value = None
    job_executor.storage.ack_job = AsyncMock()
    await job_executor._process_job("test-job", "msg-123")
    assert "Job test-job not found in storage" in caplog.text
    job_executor.storage.ack_job.assert_called_with("msg-123")


@pytest.mark.asyncio
async def test_process_job_in_terminal_state(job_executor, caplog):
    job_executor.storage.get_job_state.return_value = {
        "status": "finished",
        "blueprint_name": "test-bp",
        "current_state": "start",
    }
    job_executor.storage.ack_job = AsyncMock()
    await job_executor._process_job("test-job", "msg-123")
    assert "Job test-job is already in a terminal state" in caplog.text
    job_executor.storage.ack_job.assert_called_with("msg-123")


@pytest.mark.asyncio
async def test_process_job_calls_webhook(job_executor):
    """
    Tests that send_job_webhook is called when a job reaches a terminal state.
    """
    bp = StateMachineBlueprint(name="webhook-test-bp")

    @bp.handler_for("start", is_start=True)
    async def start_handler(actions):
        actions.transition_to("finished")

    @bp.handler_for("finished", is_end=True)
    async def finished_handler():
        pass

    bp.validate()
    job_executor.engine.blueprints["webhook-test-bp"] = bp

    # Mock send_job_webhook on the engine
    job_executor.engine.send_job_webhook = AsyncMock()

    job_id = "test-job-webhook"
    job_state = {
        "id": job_id,
        "blueprint_name": "webhook-test-bp",
        "current_state": "start",
        "initial_data": {},
        "state_history": {},
        "client_config": {},
        "webhook_url": "http://example.com/webhook",
    }
    job_executor.storage.get_job_state.return_value = job_state
    job_executor.storage.ack_job = AsyncMock()

    # Run the job processor
    await job_executor._process_job(job_id, "msg-123")

    # Assertions
    # Verify that send_job_webhook was called with the correct event type
    job_executor.engine.send_job_webhook.assert_called_once()
    call_args = job_executor.engine.send_job_webhook.call_args
    assert call_args[0][0]["id"] == job_id  # job_state
    assert call_args[0][1] == "job_finished"  # event_type


@pytest.mark.asyncio
async def test_process_job_blueprint_not_found(job_executor):
    job_executor.engine.config.JOB_MAX_RETRIES = 3
    job_state = {"id": "test-job", "blueprint_name": "test-bp", "current_state": "start", "initial_data": {}}
    job_executor.storage.get_job_state.return_value = job_state
    job_executor.storage.ack_job = AsyncMock()
    await job_executor._process_job("test-job", "msg-123")
    job_executor.storage.save_job_state.assert_called_with(
        "test-job",
        {
            "id": "test-job",
            "blueprint_name": "test-bp",
            "current_state": "start",
            "initial_data": {},
            "retry_count": 1,
            "status": "awaiting_retry",
            "error_message": "Blueprint 'test-bp' not found",
            "tracing_context": ANY,
        },
    )
    job_executor.storage.ack_job.assert_called_with("msg-123")


@pytest.mark.asyncio
async def test_process_job_handler_not_found(job_executor):
    bp = MagicMock()
    bp.find_handler.side_effect = ValueError("Handler not found")
    job_executor.engine.blueprints["test-bp"] = bp
    job_executor.engine.config.JOB_MAX_RETRIES = 3
    job_state = {"id": "test-job", "blueprint_name": "test-bp", "current_state": "start", "initial_data": {}}
    job_executor.storage.get_job_state.return_value = job_state
    job_executor.storage.ack_job = AsyncMock()
    await job_executor._process_job("test-job", "msg-123")
    job_executor.storage.save_job_state.assert_called_with(
        "test-job",
        {
            "id": "test-job",
            "blueprint_name": "test-bp",
            "current_state": "start",
            "initial_data": {},
            "retry_count": 1,
            "status": "awaiting_retry",
            "error_message": "Handler not found",
            "tracing_context": ANY,
        },
    )
    job_executor.storage.ack_job.assert_called_with("msg-123")


@pytest.mark.asyncio
async def test_process_job_handler_dependency_injection(job_executor, mocker):
    """
    Tests that the new dependency injection for handlers works correctly.
    It should inject fields from JobContext, state_history, and initial_data.
    """
    bp = StateMachineBlueprint(name="di-test-bp")
    captured_args = {}

    # This handler uses the new dependency injection style
    async def di_handler(
        job_id: str,
        actions: ActionFactory,
        initial_data: dict,
        state_history: dict,
        # This field comes from state_history
        worker_field: str,
        # This field comes from initial_data
        initial_field: str,
    ):
        captured_args["job_id"] = job_id
        captured_args["actions"] = actions
        captured_args["initial_data"] = initial_data
        captured_args["state_history"] = state_history
        captured_args["worker_field"] = worker_field
        captured_args["initial_field"] = initial_field
        actions.transition_to("end")

    bp.handler_for("start", is_start=True)(di_handler)

    @bp.handler_for("end", is_end=True)
    async def end_handler():
        pass

    bp.validate()
    job_executor.engine.blueprints["di-test-bp"] = bp

    job_id = "test-job-di"
    job_state = {
        "id": job_id,
        "blueprint_name": "di-test-bp",
        "current_state": "start",
        "initial_data": {"initial_field": "initial_value"},
        "state_history": {"worker_field": "worker_value"},
        "client_config": {},
    }
    job_executor.storage.get_job_state.return_value = job_state
    job_executor.storage.ack_job = AsyncMock()

    # Run the job processor
    await job_executor._process_job(job_id, "msg-123")

    # --- Assertions ---
    # 1. Check that the handler received the correct arguments
    assert captured_args["job_id"] == job_id
    assert isinstance(captured_args["actions"], ActionFactory)
    assert captured_args["initial_data"] == {"initial_field": "initial_value"}
    assert captured_args["state_history"] == {"worker_field": "worker_value"}
    assert captured_args["worker_field"] == "worker_value"
    assert captured_args["initial_field"] == "initial_value"

    # 2. Check that the correct state transition was triggered and saved
    job_executor.storage.save_job_state.assert_called_with(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "di-test-bp",
            "current_state": "end",  # Check for transition
            "initial_data": {"initial_field": "initial_value"},
            "state_history": {"worker_field": "worker_value"},
            "client_config": {},
            "retry_count": 0,
            "status": "running",
            "tracing_context": ANY,
        },
    )
    # 3. Check that the job was re-enqueued for the next state
    job_executor.storage.enqueue_job.assert_called_with(job_id)
    job_executor.storage.ack_job.assert_called_with("msg-123")


@pytest.mark.asyncio
async def test_process_job_handler_backward_compatibility(job_executor):
    """
    Tests that the old handler style (context, actions) still works.
    """
    bp = StateMachineBlueprint(name="compat-test-bp")

    # This handler uses the old style
    handler_mock = AsyncMock()

    @bp.handler_for("start", is_start=True)
    async def old_style_handler(context, actions):
        handler_mock(context, actions)
        actions.transition_to("end")

    @bp.handler_for("end", is_end=True)
    async def end_handler():
        pass

    bp.validate()
    job_executor.engine.blueprints["compat-test-bp"] = bp

    job_id = "test-job-compat"
    job_state = {
        "id": job_id,
        "blueprint_name": "compat-test-bp",
        "current_state": "start",
        "initial_data": {},
        "state_history": {},
        "client_config": {},
    }
    job_executor.storage.get_job_state.return_value = job_state
    job_executor.storage.ack_job = AsyncMock()

    # Run the job processor
    await job_executor._process_job(job_id, "msg-123")

    # --- Assertions ---
    # 1. Check that the handler was called with the correct arguments
    handler_mock.assert_called_once()
    call_args = handler_mock.call_args[0]
    assert len(call_args) == 2
    # The first argument should be the JobContext object
    assert call_args[0].job_id == job_id
    # The second argument should be the ActionFactory
    assert isinstance(call_args[1], ActionFactory)

    # 2. Check that the job was transitioned
    job_executor.storage.save_job_state.assert_called_with(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "compat-test-bp",
            "current_state": "end",
            "initial_data": {},
            "state_history": {},
            "client_config": {},
            "retry_count": 0,
            "status": "running",
            "tracing_context": ANY,
        },
    )
    job_executor.storage.enqueue_job.assert_called_with(job_id)
    job_executor.storage.ack_job.assert_called_with("msg-123")


@pytest.mark.asyncio
async def test_di_name_collision_precedence(job_executor, mocker):
    """
    Tests that JobContext fields take precedence over state_history and initial_data
    when there is a name collision during dependency injection.
    """
    bp = StateMachineBlueprint(name="precedence-test-bp")
    captured_args = {}

    async def precedence_handler(
        job_id: str,  # Should come from JobContext
        initial_data: dict,  # Should come from JobContext
        state_history: dict,  # Should come from JobContext
        actions: ActionFactory,
    ):
        captured_args["job_id"] = job_id
        captured_args["initial_data"] = initial_data
        captured_args["state_history"] = state_history
        captured_args["actions"] = actions
        actions.transition_to("end")

    bp.handler_for("start", is_start=True)(precedence_handler)

    @bp.handler_for("end", is_end=True)
    async def end_handler():
        pass

    bp.validate()
    job_executor.engine.blueprints["precedence-test-bp"] = bp

    job_id_context = "context-job-id"
    job_id_history = "history-job-id"
    job_id_initial = "initial-job-id"

    job_state = {
        "id": job_id_context,
        "blueprint_name": "precedence-test-bp",
        "current_state": "start",
        "initial_data": {"job_id": job_id_initial, "key_from_initial": "value_initial"},
        "state_history": {"job_id": job_id_history, "key_from_history": "value_history"},
        "client_config": {},
    }
    job_executor.storage.get_job_state.return_value = job_state
    job_executor.storage.ack_job = AsyncMock()

    # Run the job processor
    await job_executor._process_job(job_id_context, "msg-123")

    # --- Assertions ---
    # job_id should come from JobContext, not state_history or initial_data
    assert captured_args["job_id"] == job_id_context
    assert captured_args["initial_data"] == {"job_id": job_id_initial, "key_from_initial": "value_initial"}
    assert captured_args["state_history"] == {"job_id": job_id_history, "key_from_history": "value_history"}
    assert isinstance(captured_args["actions"], ActionFactory)

    # Verify transition
    job_executor.storage.save_job_state.assert_called_with(
        job_id_context,
        {
            "id": job_id_context,
            "blueprint_name": "precedence-test-bp",
            "current_state": "end",
            "initial_data": {"job_id": job_id_initial, "key_from_initial": "value_initial"},
            "state_history": {"job_id": job_id_history, "key_from_history": "value_history"},
            "client_config": {},
            "retry_count": 0,
            "status": "running",
            "tracing_context": ANY,
        },
    )
    job_executor.storage.ack_job.assert_called_with("msg-123")


@pytest.mark.asyncio
async def test_di_missing_argument_fails_job(job_executor, caplog):
    """
    Tests that if a handler requests an argument that cannot be injected,
    the job fails/retries.
    """
    bp = StateMachineBlueprint(name="missing-arg-test-bp")

    async def missing_arg_handler(
        job_id: str,
        actions: ActionFactory,
        non_existent_arg: str,  # This argument should not be found
    ):
        actions.transition_to("end")

    bp.handler_for("start", is_start=True)(missing_arg_handler)

    @bp.handler_for("end", is_end=True)
    async def end_handler():
        pass

    bp.validate()
    job_executor.engine.blueprints["missing-arg-test-bp"] = bp

    job_id = "test-job-missing-arg"
    job_state = {
        "id": job_id,
        "blueprint_name": "missing-arg-test-bp",
        "current_state": "start",
        "initial_data": {},
        "state_history": {},
        "client_config": {},
    }
    job_executor.storage.get_job_state.return_value = job_state
    job_executor.storage.ack_job = AsyncMock()

    # Set MAX_RETRIES to a value > 0 to test the retry mechanism
    job_executor.engine.config.JOB_MAX_RETRIES = 1

    await job_executor._process_job(job_id, "msg-123")

    # --- Assertions ---
    # The job should have been attempted to retry
    job_executor.storage.save_job_state.assert_called_with(
        job_id,
        {
            "id": job_id,
            "blueprint_name": "missing-arg-test-bp",
            "current_state": "start",  # Still in the same state, as handler failed
            "initial_data": {},
            "state_history": {},
            "client_config": {},
            "retry_count": 1,  # Should be incremented
            "status": "awaiting_retry",
            "error_message": ANY,  # Check that an error message is present
            "tracing_context": ANY,
        },
    )
    assert (
        "missing 1 required positional argument: 'non_existent_arg'"
        in job_executor.storage.save_job_state.call_args[0][1]["error_message"]
    )
    assert f"Error executing handler for job {job_id}. Attempt 1/1." in caplog.text
    job_executor.storage.enqueue_job.assert_called_with(job_id)  # Job is re-enqueued for retry
    job_executor.storage.ack_job.assert_called_with("msg-123")
