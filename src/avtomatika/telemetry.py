# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from logging import getLogger
from os import getenv
from typing import Any

logger = getLogger(__name__)


class NoOpPropagator:
    def inject(self, carrier: Any, context: Any = None, setter: Any = None) -> None:
        pass

    def extract(self, carrier: Any, context: Any = None, getter: Any = None) -> Any:
        return context


class DummySpan:
    def __enter__(self) -> "DummySpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def add_event(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_span_context(self) -> Any:
        class DummyContext:
            trace_id = 0

        return DummyContext()


class DummyTracer:
    @staticmethod
    def start_as_current_span(name: str, context: Any = None, **kwargs: Any) -> DummySpan:
        return DummySpan()


class DummyInstrument:
    def add(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass


class DummyMeter:
    def create_counter(self, *args: Any, **kwargs: Any) -> DummyInstrument:
        return DummyInstrument()

    def create_up_down_counter(self, *args: Any, **kwargs: Any) -> DummyInstrument:
        return DummyInstrument()

    def create_observable_gauge(self, *args: Any, **kwargs: Any) -> DummyInstrument:
        return DummyInstrument()

    def create_histogram(self, *args: Any, **kwargs: Any) -> DummyInstrument:
        return DummyInstrument()


class NoOpTrace:
    class SpanKind:
        INTERNAL = 0
        SERVER = 1
        CLIENT = 2
        PRODUCER = 3
        CONSUMER = 4

    class StatusCode:
        UNSET = 0
        OK = 1
        ERROR = 2

    class Status:
        def __init__(self, status_code: int, description: str | None = None):
            self.status_code = status_code
            self.description = description

    def get_tracer(self, name: str) -> DummyTracer:
        return DummyTracer()

    def get_tracer_provider(self) -> Any:
        return None

    def set_tracer_provider(self, provider: Any) -> None:
        pass

    def get_current_span(self, context: Any = None) -> DummySpan:
        return DummySpan()


class NoOpMetrics:
    class Observation:
        def __init__(self, value: float, attributes: Any = None):
            self.value = value
            self.attributes = attributes

    def get_meter(self, name: str) -> DummyMeter:
        return DummyMeter()

    def get_meter_provider(self) -> Any:
        return None

    def set_meter_provider(self, provider: Any) -> None:
        pass


metrics: Any = None
trace: Any = None
inject: Any = None
extract: Any = None


try:
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import propagate as otel_propagate
    from opentelemetry import trace as otel_trace
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:
        OTLPSpanExporter = None
        OTLPMetricExporter = None

    metrics = otel_metrics
    trace = otel_trace
    inject = otel_propagate.inject
    extract = otel_propagate.extract
    TELEMETRY_ENABLED = True

    # Set the global propagator to W3C TraceContext
    set_global_textmap(TraceContextTextMapPropagator())

except ImportError:
    TELEMETRY_ENABLED = False
    TracerProvider = object
    MeterProvider = object
    BatchSpanProcessor = None
    ConsoleSpanExporter = None
    OTLPSpanExporter = None
    OTLPMetricExporter = None
    inject = NoOpPropagator().inject
    extract = NoOpPropagator().extract
    trace = NoOpTrace()
    metrics = NoOpMetrics()


def get_tracer(name: str) -> Any:
    """Returns a tracer for the given name."""
    return trace.get_tracer(name)


def setup_telemetry(service_name: str = "avtomatika") -> Any:
    """Configures OpenTelemetry for the application if installed."""
    if not TELEMETRY_ENABLED:
        logger.info("opentelemetry-sdk not found. Telemetry is disabled.")
        return trace.get_tracer(__name__)

    # Avoid re-initializing if provider is already set
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return trace.get_tracer(__name__)

    try:
        from importlib.metadata import distribution  # noqa: PLC0415

        project_version = distribution("avtomatika").metadata.get("Version") or "0.0.0-dev"  # type: ignore[attr-defined]
    except Exception:
        project_version = "0.0.0-dev"

    resource = Resource(
        attributes={
            "service.name": service_name,
            "service.version": project_version,
        }
    )
    otlp_endpoint = getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    otlp_metrics_endpoint = getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", otlp_endpoint)

    tracer_provider = TracerProvider(resource=resource)
    if otlp_endpoint and OTLPSpanExporter:
        logger.info(f"OTLP Trace exporter enabled, sending to {otlp_endpoint}")
        trace_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    else:
        logger.info("Using ConsoleSpanExporter for tracing.")
        trace_processor = BatchSpanProcessor(ConsoleSpanExporter())

    tracer_provider.add_span_processor(trace_processor)
    trace.set_tracer_provider(tracer_provider)

    if otlp_metrics_endpoint and OTLPMetricExporter:
        logger.info(f"OTLP Metric exporter enabled, sending to {otlp_metrics_endpoint}")
        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=otlp_metrics_endpoint, insecure=True))
    else:
        logger.info("Using ConsoleMetricExporter for metrics.")
        metric_reader = PeriodicExportingMetricReader(ConsoleMetricExporter())

    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    return trace.get_tracer(__name__)
