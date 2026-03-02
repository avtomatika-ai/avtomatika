# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


import asyncio
import contextlib
from unittest.mock import MagicMock

import pytest
from src.avtomatika.health_checker import HealthChecker


@pytest.fixture
def mock_engine():
    """Mocks the orchestrator engine and its dependencies."""
    engine = MagicMock()
    return engine


@pytest.mark.asyncio
async def test_passive_health_checker_does_not_error(mock_engine):
    """Tests that the new passive HealthChecker can be instantiated and run
    without causing errors. Its run loop is expected to do nothing but sleep.
    """
    health_checker = HealthChecker(mock_engine)

    # We test that run() can be started and cancelled without exceptions
    run_task = asyncio.create_task(health_checker.run())
    await asyncio.sleep(0.01)  # Give it a moment to start
    run_task.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await run_task

    # The stop method should also be callable without error
    health_checker.stop()
