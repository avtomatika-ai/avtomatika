from importlib.util import module_from_spec, spec_from_file_location
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

from .blueprint import StateMachineBlueprint

if TYPE_CHECKING:
    from .engine import OrchestratorEngine

logger = getLogger(__name__)


def load_blueprints_from_dir(engine: "OrchestratorEngine", blueprints_dir: str) -> None:
    """
    Scans the specified directory for Python files and registers any
    StateMachineBlueprint instances found within them.
    """
    if not blueprints_dir:
        return

    path = Path(blueprints_dir)
    if not path.exists() or not path.is_dir():
        logger.warning(f"Blueprints directory not found or is not a directory: {blueprints_dir}")
        return

    logger.info(f"Scanning for blueprints in: {blueprints_dir}")

    # Use absolute path for reliability
    abs_dir = path.resolve()

    for file in abs_dir.glob("*.py"):
        if file.name == "__init__.py":
            continue

        # Create a unique module name for the dynamic import
        module_name = f"avtomatika.dynamic_blueprints.{file.stem}"
        try:
            spec = spec_from_file_location(module_name, str(file))
            if spec and spec.loader:
                module = module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find all instances of StateMachineBlueprint in the module
                blueprints_found = 0
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, StateMachineBlueprint):
                        try:
                            engine.register_blueprint(attr)
                            blueprints_found += 1
                            logger.info(f"Loaded blueprint '{attr.name}' from {file.name}")
                        except ValueError as e:
                            # Might happen if a blueprint with the same name is already registered
                            logger.error(
                                f"Failed to register blueprint '{getattr(attr, 'name', 'unknown')}' "
                                f"from {file.name}: {e}"
                            )

                if blueprints_found == 0:
                    logger.debug(f"No StateMachineBlueprint instances found in {file.name}")

        except Exception as e:
            logger.exception(f"Error loading blueprint file {file.name}: {e}")
