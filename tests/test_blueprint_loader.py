import os
import shutil
import tempfile

import pytest

from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage

BLUEPRINT_CONTENT = """
from avtomatika.blueprint import StateMachineBlueprint

dynamic_bp = StateMachineBlueprint(name="dynamic_test_blueprint", api_endpoint="/jobs/dynamic_test")

@dynamic_bp.handler_for("start", is_start=True)
async def start_handler(actions):
    actions.transition_to("end")

@dynamic_bp.handler_for("end", is_end=True)
async def end_handler():
    pass
"""


@pytest.mark.asyncio
async def test_dynamic_blueprint_loading():
    # Create a temporary directory for blueprints
    tmp_dir = tempfile.mkdtemp()
    try:
        # Create a blueprint file
        bp_file_path = os.path.join(tmp_dir, "test_bp.py")
        with open(bp_file_path, "w") as f:
            f.write(BLUEPRINT_CONTENT)

        # Setup engine config
        config = Config()
        config.BLUEPRINTS_DIR = tmp_dir

        storage = MemoryStorage()
        engine = OrchestratorEngine(storage, config)

        # This should trigger the loader
        engine.setup()

        # Verify the blueprint is registered
        assert "dynamic_test_blueprint" in engine.blueprints
        bp = engine.blueprints["dynamic_test_blueprint"]
        assert bp.name == "dynamic_test_blueprint"
        assert bp.start_state == "start"
        assert "end" in bp.end_states

    finally:
        # Cleanup
        shutil.rmtree(tmp_dir)


@pytest.mark.asyncio
async def test_blueprint_loader_ignores_non_blueprints():
    tmp_dir = tempfile.mkdtemp()
    try:
        # Create a file with NO blueprints
        bp_file_path = os.path.join(tmp_dir, "not_a_bp.py")
        with open(bp_file_path, "w") as f:
            f.write("x = 10\ndef foo(): pass")

        config = Config()
        config.BLUEPRINTS_DIR = tmp_dir
        storage = MemoryStorage()
        engine = OrchestratorEngine(storage, config)

        engine.setup()

        # Should remain empty (or at least not contain anything from that file)
        assert len(engine.blueprints) == 0

    finally:
        shutil.rmtree(tmp_dir)
