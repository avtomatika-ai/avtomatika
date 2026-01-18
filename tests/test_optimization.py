from unittest.mock import MagicMock, patch

import pytest

from avtomatika.blueprint import StateMachineBlueprint
from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


@pytest.fixture
def engine():
    storage = MemoryStorage()
    config = Config()
    return OrchestratorEngine(storage, config)


@pytest.mark.asyncio
async def test_signature_caching(engine):
    """
    Verifies that inspect.signature is called during validation but NOT during execution.
    """
    bp = StateMachineBlueprint("test_bp")

    # Define a simple handler
    @bp.handler_for("start", is_start=True)
    async def start_handler(job_id, actions):
        actions.transition_to("end")

    @bp.handler_for("end", is_end=True)
    async def end_handler():
        pass

    # Mock inspect.signature
    # We use a side_effect to call the real signature but track calls
    real_signature = __import__("inspect").signature
    mock_signature = MagicMock(side_effect=real_signature)

    with patch("inspect.signature", mock_signature):
        # Register blueprint -> calls validate() -> calls _analyze_handlers() -> calls inspect.signature
        engine.register_blueprint(bp)

        # Should be called once for each handler (start_handler, end_handler) + any internal calls
        assert mock_signature.call_count >= 2
        initial_call_count = mock_signature.call_count

        # Setup engine properly for execution
        app = engine.app
        await engine.on_startup(app)

        # Create and execute a job
        job_id = await engine.create_background_job("test_bp", {})

        # Wait a bit for the job to be picked up by the executor
        import asyncio

        # We need to give enough time for the executor to pick up the job and run the handler
        # Since we use MemoryStorage and local loop, it should be fast.
        for _ in range(5):
            await asyncio.sleep(0.05)
            job_state = await engine.storage.get_job_state(job_id)
            if job_state["current_state"] == "end":
                break

        await engine.on_shutdown(app)

        # Verify that inspect.signature was NOT called additional times
        # The executor calls blueprint.get_handler_params which uses the cache.
        # Note: on_startup might trigger some signature inspections if other components use it,
        # but our JobExecutor loop should not.
        # We allow a small margin if frameworks do something, but ideally it should be equal.
        # Actually, let's be strict. If on_startup calls it, we should know.
        # But we are testing that *our handlers* are not inspected again.
        # The mock logs all calls. We can check if our handlers were inspected.

        # Get calls after registration
        calls_after_registration = mock_signature.mock_calls[initial_call_count:]

        # Check if start_handler or end_handler were passed to inspect.signature
        for call in calls_after_registration:
            arg = call.args[0]
            if arg in (start_handler, end_handler):
                pytest.fail(f"inspect.signature called for handler {arg} during execution!")
