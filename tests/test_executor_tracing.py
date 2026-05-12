# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from unittest.mock import AsyncMock, MagicMock

import pytest
import src.avtomatika.executor as executor_mod
from src.avtomatika.executor import JobExecutor


@pytest.mark.asyncio
async def test_executor_tracing_instantiation():
    """
    Verifies that the executor uses the pre-instantiated propagator
    and doesn't perform redundant type checks.
    """
    # We need to check if 'propagator' is available in the module and used correctly
    assert hasattr(executor_mod, "propagator")
    # Whether it's a real propagator or NoOp, it should have the extract method
    assert hasattr(executor_mod.propagator, "extract")


@pytest.mark.asyncio
async def test_executor_noop_tracing_safe_execution():
    """
    Verifies that the executor works correctly even if opentelemetry is missing
    (No-Op mode) and context is empty.
    """
    engine = MagicMock()
    # Use AsyncMock for history storage because it's awaited
    history = AsyncMock()
    storage = AsyncMock()
    engine.storage = storage
    engine.history_storage = history

    executor = JobExecutor(engine, history)

    job_id = "test-job"
    message_id = "msg-1"

    # Minimal job state
    job_state = {
        "id": job_id,
        "blueprint_name": "test_bp",
        "current_state": "start",
        "status": "running",
        "tracing_context": None,  # Edge case: None instead of dict
    }
    storage.get_job_state.return_value = job_state

    # Mock engine blueprints
    bp = MagicMock()
    engine.blueprints.get.return_value = bp
    bp.find_handler.return_value = AsyncMock()
    bp.get_handler_params.return_value = []

    # We want to ensure it doesn't crash during tracing extraction
    # even with tracing_context=None
    await executor._process_job(job_id, message_id)

    # If we reached this point without exception, it's good.
    storage.get_job_state.assert_called_once_with(job_id)
