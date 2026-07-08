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

try:
    from importlib.metadata import distribution

    __version__ = distribution("avtomatika").metadata.get("Version") or "0.0.0-dev"  # type: ignore[attr-defined]
except Exception:
    __version__ = "0.0.0-dev"

from fast_filter import F, FastF
from rxon.validators import is_valid_identifier, validate_identifier

from . import telemetry
from .blueprint import Blueprint
from .context import ActionFactory
from .data_types import JobContext
from .engine import OrchestratorEngine
from .storage.base import StorageBackend

__all__ = [
    "ActionFactory",
    "Blueprint",
    "F",
    "FastF",
    "JobContext",
    "OrchestratorEngine",
    "StorageBackend",
    "is_valid_identifier",
    "validate_identifier",
    "telemetry",
]

with suppress(ImportError):
    from .storage.redis import RedisStorage  # noqa: F401

    __all__.append("RedisStorage")
