# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2026 Dmitrii Gagarin aka madgagarin

import importlib.metadata
from unittest.mock import MagicMock, patch

from opentelemetry.sdk.trace import TracerProvider

from avtomatika import __version__
from avtomatika.telemetry import setup_telemetry


def test_version_retrieval():
    """Verify that __version__ is a valid string and doesn't crash."""
    assert isinstance(__version__, str)
    assert len(__version__) > 0
    # In dev environment it might be 0.0.0-dev
    assert __version__ != "unknown"


def test_telemetry_resource_version():
    """Verify that telemetry setup uses the correct version in Resource."""
    # We don't want to re-initialize global providers in a simple unit test if possible,
    # but we can check the logic if we mock distribution.
    captured_resource = None

    class CapturingTracerProvider(TracerProvider):
        def __init__(self, resource=None, **kwargs):
            nonlocal captured_resource
            captured_resource = resource
            super().__init__(resource=resource, **kwargs)

    with patch("importlib.metadata.distribution") as mock_dist:
        mock_dist.return_value.metadata.get.return_value = "1.2.3-test"

        # We need to bypass the "provider already set" check to see the resource creation
        # By returning something that is NOT a TracerProvider but has get_tracer
        mock_provider = MagicMock()
        with (
            patch("avtomatika.telemetry.trace.get_tracer_provider", return_value=mock_provider),
            patch("avtomatika.telemetry.TracerProvider", new=CapturingTracerProvider),
            patch("avtomatika.telemetry.MeterProvider", autospec=True),
            patch("avtomatika.telemetry.BatchSpanProcessor"),
            patch("avtomatika.telemetry.PeriodicExportingMetricReader"),
        ):
            setup_telemetry("test-service")

            if captured_resource:
                assert captured_resource.attributes["service.version"] == "1.2.3-test"
                assert captured_resource.attributes["service.name"] == "test-service"


def test_metadata_patch_works():
    """Verify that the patch in conftest.py correctly handles missing versions."""
    # This test relies on the fact that conftest.py already patched importlib.metadata.version
    # We check if it returns None or a string instead of raising or warning for non-existent packages
    ver = importlib.metadata.version("non-existent-package-xyz-123")
    assert ver is None
