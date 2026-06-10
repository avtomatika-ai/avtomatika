# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from opentelemetry.sdk.trace import TracerProvider
except ImportError:
    TracerProvider = object  # type: ignore

from src.avtomatika.blueprint import Blueprint
from src.avtomatika.engine import OrchestratorEngine
from src.avtomatika.telemetry import TELEMETRY_ENABLED, setup_telemetry


@pytest.mark.skipif(not TELEMETRY_ENABLED, reason="opentelemetry-sdk not installed")
def test_setup_telemetry_enabled():
    """Tests that telemetry is set up correctly when the SDK is installed."""

    class MockProvider(TracerProvider):
        pass

    mock_provider = MockProvider()
    with patch("opentelemetry.trace.get_tracer_provider", return_value=mock_provider):
        with patch("opentelemetry.trace.set_tracer_provider") as mock_set_provider:
            tracer = setup_telemetry()
            # If get_tracer_provider returns mock_provider, setup_telemetry skips init
            assert not mock_set_provider.called
        assert tracer is not None


@pytest.mark.skipif(TELEMETRY_ENABLED, reason="opentelemetry-sdk is installed")
def test_setup_telemetry_disabled(caplog):
    """Tests that a warning is logged when the telemetry SDK is not installed."""
    tracer = setup_telemetry()
    assert "opentelemetry-sdk not found" in caplog.text
    assert tracer is not None


@pytest.mark.asyncio
async def test_create_background_job_injects_tracing():
    """Verifies that create_background_job injects tracing context automatically."""
    storage = AsyncMock()
    config = MagicMock()
    config.BLUEPRINTS_DIR = None
    config.LOG_LEVEL = "INFO"
    config.LOG_FORMAT = "json"
    config.TZ = "UTC"
    engine = OrchestratorEngine(storage, config)

    bp = Blueprint("test_bp")
    engine.blueprints = {"test_bp": bp}

    saved_state = {}

    async def mock_save(jid, state):
        saved_state.update(state)

    storage.save_job_state.side_effect = mock_save

    await engine.create_background_job("test_bp", initial_data={})

    assert "tracing_context" in saved_state
    assert isinstance(saved_state["tracing_context"], dict)
