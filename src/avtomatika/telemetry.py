# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from logging import getLogger
from os import getenv
from typing import Any

logger = getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:
        OTLPSpanExporter = None

    TELEMETRY_ENABLED = True
except ImportError:
    TELEMETRY_ENABLED = False
    TracerProvider = object
    BatchSpanProcessor = None
    ConsoleSpanExporter = None
    OTLPSpanExporter = None

    class DummySpan:
        def __enter__(self) -> "DummySpan":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def set_attribute(self, key: str, value: Any) -> None:
            pass

    class DummyTracer:
        @staticmethod
        def start_as_current_span(name: str, context: Any = None) -> DummySpan:
            return DummySpan()

    class NoOpTrace:
        def get_tracer(self, name: str) -> DummyTracer:
            return DummyTracer()

    trace: Any = NoOpTrace()  # type: ignore[no-redef]


def setup_telemetry(service_name: str = "avtomatika") -> Any:
    """Configures OpenTelemetry for the application if installed."""
    if not TELEMETRY_ENABLED:
        logger.info("opentelemetry-sdk not found. Telemetry is disabled.")
        return trace.get_tracer(__name__)

    # Avoid re-initializing if provider is already set
    # This prevents "Overriding of current TracerProvider is not allowed" warnings
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return trace.get_tracer(__name__)

    resource = Resource(attributes={"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint := getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        logger.info(f"OTLP exporter enabled, sending traces to {otlp_endpoint}")
        if OTLPSpanExporter:
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
        else:
            logger.error(
                "OTLP exporter is configured but 'opentelemetry-exporter-otlp' is not installed. "
                "Please install it with: pip install opentelemetry-exporter-otlp"
            )
            # Fallback to console exporter
            processor = BatchSpanProcessor(ConsoleSpanExporter())
    else:
        logger.info("Using ConsoleSpanExporter for telemetry.")
        processor = BatchSpanProcessor(ConsoleSpanExporter())

    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)

    return trace.get_tracer(__name__)
