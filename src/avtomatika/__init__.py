"""Avtomatika Library
=======================

This module exposes the primary classes for building and running state-driven automations.
"""

import contextlib
from importlib.metadata import version

__version__ = version("avtomatika")

from .blueprint import StateMachineBlueprint
from .context import ActionFactory
from .data_types import JobContext
from .engine import OrchestratorEngine
from .storage.base import StorageBackend

__all__ = [
    "ActionFactory",
    "JobContext",
    "OrchestratorEngine",
    "StateMachineBlueprint",
    "StorageBackend",
]

with contextlib.suppress(ImportError):
    from .storage.redis import RedisStorage  # noqa: F401

    __all__.append("RedisStorage")
