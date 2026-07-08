**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/observability.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/observability.md)

# Avtomatika Observability System

The project implements a modern observation system based on the **OpenTelemetry (OTel)** standard. It combines distributed tracing and metric collection into a single data stream, providing full transparency of the orchestrator and workers.

---

## 1. Core Principles

The system is built on three pillars:

1.  **Unification**: Using the **OTLP** protocol for transmitting both metrics and traces.
2.  **End-to-End Context**: Passing `trace_id` through RXON protocol messages from the API to the final worker.
3.  **Optionality**: Telemetry does not require mandatory SDK installation. If `opentelemetry-*` packages are missing, the system uses lightweight No-Op stubs without affecting performance.

---

## 2. Distributed Tracing

Tracing allows you to track the complete execution path of a task.

### Span Structure

- **`rxon_message:{type}`**: Created in `engine.py` upon receiving any message from a worker (`poll`, `result`, `heartbeat`).
  - _Attributes_: `worker.id_hint`, `message.type`, `auth.worker_id`.
- **`JobExecutor:{blueprint}:{state}`**: Created during the execution of a logic step in the orchestrator.
  - _Attributes_: `job.id`, `job.blueprint`, `job.client_token`, `job.retry_count`.
  - _Events_: Errors are recorded via `span.record_exception(e)` with a full stack trace.

### Context Propagation

The orchestrator extracts context from HTTP API request headers and stores it in the `tracing_context` field of the job object. This context is passed to the worker in the task metadata, allowing the work of the orchestrator and the external worker to be combined into a single trace.

---

## 3. Metrics

Metrics are collected in real-time and sent to the collector via the OTLP protocol.

### Key Indicators

| Metric                                        | Type      | Description                                        |
| :-------------------------------------------- | :-------- | :------------------------------------------------- |
| `orchestrator_jobs_total`                     | Counter   | Total number of jobs created (by blueprints).      |
| `orchestrator_jobs_failed_total`              | Counter   | Number of failed jobs.                             |
| `orchestrator_job_duration_seconds`           | Histogram | Distribution of job execution time (P95/P99).      |
| `orchestrator_task_queue_length`              | Gauge     | Current number of tasks in the Redis queue.        |
| `orchestrator_active_workers`                 | Gauge     | Number of active workers in the network.           |
| `orchestrator_loop_lag_seconds`               | Gauge     | Asyncio event loop lag (CPU overload indicator).   |
| `orchestrator_ratelimit_blocked_total`        | Counter   | Number of requests blocked by the rate limiter.    |
| `orchestrator_jobs_timeouts_total`            | Counter   | Total number of jobs that have timed out.          |
| `orchestrator_tasks_ignored_total`            | Counter   | Number of ignored results (late or cancelled).     |
| `orchestrator_tasks_hot_dispatched_total`     | Counter   | Tasks sent to HOT workers (with pre-warmed cache). |
| `orchestrator_s3_operations_total`            | Counter   | Total number of operations with S3 storage.        |
| `orchestrator_s3_operation_duration_seconds`  | Histogram | Duration of S3 operations.                         |
| `orchestrator_scheduler_jobs_triggered_total` | Counter   | Total number of jobs triggered by the scheduler.   |

### Security Metrics (Zero Trust)

| Metric                                          | Type    | Description                                                    |
| :---------------------------------------------- | :------ | :------------------------------------------------------------- |
| `orchestrator_security_auth_failures_total`     | Counter | Total failed authentication attempts (invalid token/cert).     |
| `orchestrator_security_replay_detected_total`   | Counter | Number of detected replay attacks (expired/missing timestamp). |
| `orchestrator_security_identity_mismatch_total` | Counter | Mismatches between certificate CN and claimed worker_id.       |

---

## 4. Setup and Launch

Telemetry is enabled automatically if the necessary libraries and environment settings are present.

### Environment Variables

- `OTEL_EXPORTER_OTLP_ENDPOINT`: URL of your OTel Collector or Jaeger (e.g., `http://localhost:4317`).
- `OTEL_SERVICE_NAME`: Service name (defaults to `avtomatika`).
- **Automatic Attributes**: The orchestrator automatically adds `service.version` (from package metadata) to every trace and metric.
- `LOG_LEVEL`: Set to `DEBUG` to debug telemetry export.

### Launch Example with Jaeger (locally)

1. Start Jaeger via Docker:

```bash
docker run -d --name jaeger \
  -e COLLECTOR_OTLP_ENABLED=true \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest
```

2. Start the orchestrator with the endpoint specified:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
pip install "avtomatika[telemetry]"
python -m avtomatika.engine
```

Now all traces will be available in the Jaeger interface at `http://localhost:16686`.

---

## 5. Usage in Code (for developers)

If your blueprint logic contains complex calculations, work with external databases, or APIs, you can detail the tracing by creating custom sub-spans.

### Why is this important?

The automatic `JobExecutor` span shows the total execution time of a step. Custom spans allow you to see how much time a specific operation (e.g., `Data:Fetch` or `LLM:Preprocessing`) took, which is critical for optimizing pipelines.

### Example in a Blueprint Handler:

```python
from avtomatika.telemetry import trace

tracer = trace.get_tracer("my_blueprint")

@bp.handler
async def process_data(initial_data, actions):
    # 1. Create a custom span within the step
    with tracer.start_as_current_span("Internal:Transform") as span:
        span.set_attribute("data.size", len(initial_data))
        # ... your heavy transformation logic ...
        result = {"status": "ok"}

    # 2. Main orchestrator logic continues
    actions.go_to("next_step")
```

### Example of adding a metric:

```python
from avtomatika import metrics

# In the initialization method
meter = metrics.get_meter("avtomatika")
my_counter = meter.create_counter("custom_event_total")

# In the code
my_counter.add(1, {"type": "alert"})
```

---

_Note: If `OTEL_EXPORTER_OTLP_ENDPOINT` is not set, data will be output to the console (ConsoleExporter), which is convenient for verifying correct operation without deploying Jaeger._
