# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import patch

import pytest
from src.avtomatika.telemetry import TELEMETRY_ENABLED, setup_telemetry


@pytest.mark.skipif(not TELEMETRY_ENABLED, reason="opentelemetry-sdk not installed")
def test_setup_telemetry_enabled():
    """Tests that telemetry is set up correctly when the SDK is installed."""
    with patch("opentelemetry.trace.set_tracer_provider") as mock_set_provider:
        tracer = setup_telemetry()
        assert mock_set_provider.called
        assert tracer is not None


@pytest.mark.skipif(TELEMETRY_ENABLED, reason="opentelemetry-sdk is installed")
def test_setup_telemetry_disabled(caplog):
    """Tests that a warning is logged when the telemetry SDK is not installed."""
    tracer = setup_telemetry()
    assert "opentelemetry-sdk not found" in caplog.text
    assert tracer is not None
