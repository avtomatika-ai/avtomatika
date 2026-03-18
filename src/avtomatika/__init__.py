# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


"""Avtomatika Library
=======================

This module exposes the primary classes for building and running state-driven automations.
"""

from contextlib import suppress
from importlib.metadata import version

__version__ = version("avtomatika")

from rxon.validators import is_valid_identifier, validate_identifier

from .blueprint import Blueprint
from .context import ActionFactory
from .data_types import JobContext
from .engine import OrchestratorEngine
from .storage.base import StorageBackend

__all__ = [
    "ActionFactory",
    "Blueprint",
    "JobContext",
    "OrchestratorEngine",
    "StorageBackend",
    "is_valid_identifier",
    "validate_identifier",
]

with suppress(ImportError):
    from .storage.redis import RedisStorage  # noqa: F401

    __all__.append("RedisStorage")
