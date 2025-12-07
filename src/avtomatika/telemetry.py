from logging import getLogger
from os import getenv

logger = getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )

    TELEMETRY_ENABLED = True
except ImportError:
    TELEMETRY_ENABLED = False

    class DummySpan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def set_attribute(self, key, value):
            pass

    class DummyTracer:
        def start_as_current_span(self, name, context=None):
            return DummySpan()

    class NoOpTrace:
        def get_tracer(self, name):
            return DummyTracer()

    trace = NoOpTrace()


def setup_telemetry(service_name: str = "avtomatika"):
    """Configures OpenTelemetry for the application if installed."""
    if not TELEMETRY_ENABLED:
        logger.info("opentelemetry-sdk not found. Telemetry is disabled.")
        return trace.get_tracer(__name__)

    resource = Resource(attributes={"service.name": service_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        logger.info(f"OTLP exporter enabled, sending traces to {otlp_endpoint}")
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
        except ImportError:
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

    # Sets the global default tracer provider
    trace.set_tracer_provider(provider)

    # Returns a tracer from the global provider
    return trace.get_tracer(__name__)
