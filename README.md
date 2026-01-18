# Avtomatika Orchestrator

Avtomatika is a powerful, state-driven engine for managing complex asynchronous workflows in Python. It provides a robust framework for building scalable and resilient applications by separating process logic from execution logic.

This document serves as a comprehensive guide for developers looking to build pipelines (blueprints) and embed the Orchestrator into their applications.

## Table of Contents
- [Core Concept: Orchestrator, Blueprints, and Workers](#core-concept-orchestrator-blueprints-and-workers)
- [Installation](#installation)
- [Quick Start: Usage as a Library](#quick-start-usage-as-a-library)
- [Key Concepts: JobContext and Actions](#key-concepts-jobcontext-and-actions)
- [Blueprint Cookbook: Key Features](#blueprint-cookbook-key-features)
  - [Conditional Transitions (.when())](#conditional-transitions-when)
  - [Delegating Tasks to Workers (dispatch_task)](#delegating-tasks-to-workers-dispatch_task)
  - [Parallel Execution and Aggregation (Fan-out/Fan-in)](#parallel-execution-and-aggregation-fan-outfan-in)
  - [Dependency Injection (DataStore)](#dependency-injection-datastore)
  - [Native Scheduler](#native-scheduler)
  - [Webhook Notifications](#webhook-notifications)
- [Production Configuration](#production-configuration)
  - [Fault Tolerance](#fault-tolerance)
  - [Storage Backend](#storage-backend)
  - [Observability](#observability)
- [Contributor Guide](#contributor-guide)
  - [Setup Environment](#setup-environment)
  - [Running Tests](#running-tests)

## Core Concept: Orchestrator, Blueprints, and Workers

The project is based on a simple yet powerful architectural pattern that separates process logic from execution logic.

*   **Orchestrator (OrchestratorEngine)** — The Director. It manages the entire process from start to finish, tracks state, handles errors, and decides what should happen next. It does not perform business tasks itself.
*   **Blueprints (Blueprint)** — The Script. Each blueprint is a detailed plan (a state machine) for a specific business process. It describes the steps (states) and the rules for transitioning between them.
*   **Workers (Worker)** — The Team of Specialists. These are independent, specialized executors. Each worker knows how to perform a specific set of tasks (e.g., "process video," "send email") and reports back to the Orchestrator.

## Ecosystem

Avtomatika is part of a larger ecosystem:

*   **[Avtomatika Worker SDK](https://github.com/avtomatika-ai/avtomatika-worker)**: The official Python SDK for building workers that connect to this engine.
*   **[RCA Protocol](https://github.com/avtomatika-ai/rca)**: The architectural specification and manifesto behind the system.
*   **[Full Example](https://github.com/avtomatika-ai/avtomatika-full-example)**: A complete reference project demonstrating the engine and workers in action.

## Installation

*   **Install the core engine only:**
    ```bash
    pip install avtomatika
    ```

*   **Install with Redis support (recommended for production):**
    ```bash
    pip install "avtomatika[redis]"
    ```

*   **Install with history storage support (SQLite, PostgreSQL):**
    ```bash
    pip install "avtomatika[history]"
    ```

*   **Install with telemetry support (Prometheus, OpenTelemetry):**
    ```bash
    pip install "avtomatika[telemetry]"
    ```

*   **Install all dependencies, including for testing:**
    ```bash
    pip install "avtomatika[all,test]"
    ```
## Quick Start: Usage as a Library

You can easily integrate and run the orchestrator engine within your own application.

```python
# my_app.py
import asyncio
from avtomatika import OrchestratorEngine, StateMachineBlueprint
from avtomatika.context import ActionFactory
from avtomatika.storage import MemoryStorage
from avtomatika.config import Config

# 1. General Configuration
storage = MemoryStorage()
config = Config() # Loads configuration from environment variables

# Explicitly set tokens for this example
# Client token must be sent in the 'X-Avtomatika-Token' header.
config.CLIENT_TOKEN = "my-secret-client-token"
# Worker token must be sent in the 'X-Worker-Token' header.
config.GLOBAL_WORKER_TOKEN = "my-secret-worker-token"

# 2. Define the Workflow Blueprint
my_blueprint = StateMachineBlueprint(
    name="my_first_blueprint",
    api_version="v1",
    api_endpoint="/jobs/my_flow"
)

# Use dependency injection to get only the data you need.
@my_blueprint.handler_for("start", is_start=True)
async def start_handler(job_id: str, initial_data: dict, actions: ActionFactory):
    """The initial state for each new job."""
    print(f"Job {job_id} | Start: {initial_data}")
    actions.transition_to("end")

# You can still request the full context object if you prefer.
@my_blueprint.handler_for("end", is_end=True)
async def end_handler(context):
    """The final state. The pipeline ends here."""
    print(f"Job {context.job_id} | Complete.")

# 3. Initialize the Orchestrator Engine
engine = OrchestratorEngine(storage, config)
engine.register_blueprint(my_blueprint)

# 4. Accessing Components (Optional)
# You can access the internal aiohttp app and core components using AppKeys
# from avtomatika.app_keys import ENGINE_KEY, DISPATCHER_KEY
# app = engine.app
# dispatcher = app[DISPATCHER_KEY]

# 5. Define the main entrypoint to run the server
async def main():
    await engine.start()
    
    try:
        await asyncio.Event().wait()
    finally:
        await engine.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping server.")
```

### Engine Lifecycle: `run()` vs. `start()`

The `OrchestratorEngine` offers two ways to start the server:

*   **`engine.run()`**: This is a simple, **blocking** method. It's useful for dedicated scripts where the orchestrator is the only major component. It handles starting and stopping the server for you. You should not use this inside an `async def` function that is part of a larger application, as it can conflict with the event loop.

*   **`await engine.start()`** and **`await engine.stop()`**: These are the non-blocking methods for integrating the engine into a larger `asyncio` application.
    *   `start()` sets up and starts the web server in the background.
    *   `stop()` gracefully shuts down the server and cleans up resources.
    The "Quick Start" example above demonstrates the correct way to use these methods.
## Handler Arguments & Dependency Injection

State handlers are the core of your workflow logic. Avtomatika provides a powerful dependency injection system to make writing handlers clean and efficient.

Instead of receiving a single, large `context` object, your handler can ask for exactly what it needs as function arguments. The engine will automatically provide them.

The following arguments can be injected by name:

*   **From the core job context:**
    *   `job_id` (str): The ID of the current job.
    *   `initial_data` (dict): The data the job was created with.
    *   `state_history` (dict): A dictionary for storing and passing data between steps. Data returned by workers is automatically merged into this dictionary.
    *   `actions` (ActionFactory): The object used to tell the orchestrator what to do next (e.g., `actions.transition_to(...)`).
    *   `client` (ClientConfig): Information about the API client that started the job.
    *   `data_stores` (SimpleNamespace): Access to shared resources like database connections or caches.
*   **From worker results:**
    *   Any key from a dictionary returned by a previous worker can be injected by name.

### Example: Dependency Injection

This is the recommended way to write handlers.

```python
# A worker for this task returned: {"output_path": "/videos/123.mp4", "duration": 95}
# This dictionary was automatically merged into `state_history`.

@my_blueprint.handler_for("publish_video")
async def publish_handler(
    job_id: str,
    output_path: str, # Injected from state_history
    duration: int,    # Injected from state_history
    actions: ActionFactory
):
    print(f"Job {job_id}: Publishing video at {output_path} ({duration}s).")
    actions.transition_to("complete")
```

### The `actions` Object

This is the most important injected argument. It tells the orchestrator what to do next. **Only one** `actions` method can be called in a single handler.

*   `actions.transition_to("next_state")`: Moves the job to a new state.
*   `actions.dispatch_task(...)`: Delegates work to a Worker.
*   `actions.dispatch_parallel(...)`: Runs multiple tasks at once.
*   `actions.await_human_approval(...)`: Pauses the workflow for external input.
*   `actions.run_blueprint(...)`: Starts a child workflow.

### Backward Compatibility: The `context` Object

For backward compatibility or if you prefer to have a single object, you can still ask for `context`.

```python
# This handler is equivalent to the one above.
@my_blueprint.handler_for("publish_video")
async def publish_handler_old_style(context):
    output_path = context.state_history.get("output_path")
    duration = context.state_history.get("duration")

    print(f"Job {context.job_id}: Publishing video at {output_path} ({duration}s).")
    context.actions.transition_to("complete")
```
## Blueprint Cookbook: Key Features

### 1. Conditional Transitions (`.when()`)

Use `.when()` to create conditional logic branches. The condition string is evaluated by the engine before the handler is called, so it still uses the `context.` prefix. The handler itself, however, can use dependency injection.

```python
# The `.when()` condition still refers to `context`.
@my_blueprint.handler_for("decision_step").when("context.initial_data.type == 'urgent'")
async def handle_urgent(actions):
    actions.transition_to("urgent_processing")

# The default handler if no `.when()` condition matches.
@my_blueprint.handler_for("decision_step")
async def handle_normal(actions):
    actions.transition_to("normal_processing")
```

> **Note on Limitations:** The current version of `.when()` uses a simple parser with the following limitations:
> *   **No Nested Attributes:** You can only access direct fields of `context.initial_data` or `context.state_history` (e.g., `context.initial_data.field`). Nested objects (e.g., `context.initial_data.area.field`) are not supported.
> *   **Simple Comparisons Only:** Only the following operators are supported: `==`, `!=`, `>`, `<`, `>=`, `<=`. Complex logical expressions with `AND`, `OR`, or `NOT` are not allowed.
> *   **Limited Value Types:** The parser only recognizes strings (in quotes), integers, and floats. Boolean values (`True`, `False`) and `None` are not correctly parsed and will be treated as strings.

### 2. Delegating Tasks to Workers (`dispatch_task`)

This is the primary function for delegating work. The orchestrator will queue the task and wait for a worker to pick it up and return a result.

```python
@my_blueprint.handler_for("transcode_video")
async def transcode_handler(initial_data, actions):
    actions.dispatch_task(
        task_type="video_transcoding",
        params={"input_path": initial_data.get("path")},
        # Define the next step based on the worker's response status
        transitions={
            "success": "publish_video",
            "failure": "transcoding_failed",
            "needs_review": "manual_review" # Example of a custom status
        }
    )
```
If the worker returns a status not listed in `transitions`, the job will automatically transition to a failed state.

### 3. Parallel Execution and Aggregation (Fan-out/Fan-in)

Run multiple tasks simultaneously and gather their results.

```python
# 1. Fan-out: Dispatch multiple tasks to be aggregated into a single state
@my_blueprint.handler_for("process_files")
async def fan_out_handler(initial_data, actions):
    tasks_to_dispatch = [
        {"task_type": "file_analysis", "params": {"file": file}})
        for file in initial_data.get("files", [])
    ]
    # Use dispatch_parallel to send all tasks at once.
    # All successful tasks will implicitly lead to the 'aggregate_into' state.
    actions.dispatch_parallel(
        tasks=tasks_to_dispatch,
        aggregate_into="aggregate_results"
    )

# 2. Fan-in: Collect results using the @aggregator_for decorator
@my_blueprint.aggregator_for("aggregate_results")
async def aggregator_handler(aggregation_results, state_history, actions):
    # This handler will only execute AFTER ALL tasks
    # dispatched by dispatch_parallel are complete.

    # aggregation_results is a dictionary of {task_id: result_dict}
    summary = [res.get("data") for res in aggregation_results.values()]
    state_history["summary"] = summary
    actions.transition_to("processing_complete")
```

### 4. Dependency Injection (DataStore)

Provide handlers with access to external resources (like a cache or DB client).

```python
import redis.asyncio as redis

# 1. Initialize and register your DataStore
redis_client = redis.Redis(decode_responses=True)
bp = StateMachineBlueprint(
    "blueprint_with_datastore",
    data_stores={"cache": redis_client}
)

# 2. Use it in a handler via dependency injection
@bp.handler_for("get_from_cache")
async def cache_handler(data_stores):
    # Access the redis_client by the name "cache"
    user_data = await data_stores.cache.get("user:123")
    print(f"User from cache: {user_data}")
```

### 5. Native Scheduler

Avtomatika includes a built-in distributed scheduler. It allows you to trigger blueprints periodically (interval, daily, weekly, monthly) without external tools like cron.

*   **Configuration:** Defined in `schedules.toml`.
*   **Timezone Aware:** Supports global timezone configuration (e.g., `TZ="Europe/Moscow"`).
*   **Distributed Locking:** Safe to run with multiple orchestrator instances; jobs are guaranteed to run only once per interval using distributed locks (Redis/Memory).

```toml
# schedules.toml example
[nightly_backup]
blueprint = "backup_flow"
daily_at = "02:00"
```

### 6. Webhook Notifications

The orchestrator can send asynchronous notifications to an external system when a job completes, fails, or is quarantined. This eliminates the need for clients to constantly poll the API for status updates.

*   **Usage:** Pass a `webhook_url` in the request body when creating a job.
*   **Events:**
    *   `job_finished`: The job reached a final success state.
    *   `job_failed`: The job failed (e.g., due to an error or invalid input).
    *   `job_quarantined`: The job was moved to quarantine after repeated failures.

**Example Request:**
```json
POST /api/v1/jobs/my_flow
{
    "initial_data": {
        "video_url": "..."
    },
    "webhook_url": "https://my-app.com/webhooks/avtomatika"
}
```

**Example Webhook Payload:**
```json
{
    "event": "job_finished",
    "job_id": "123e4567-e89b-12d3-a456-426614174000",
    "status": "finished",
    "result": {
        "output_path": "/videos/result.mp4"
    },
    "error": null
}
```

## Production Configuration

The orchestrator's behavior can be configured through environment variables. Additionally, any configuration parameter loaded from environment variables can be programmatically overridden in your application code after the `Config` object has been initialized. This provides flexibility for different deployment and testing scenarios.

**Important:** The system employs **strict validation** for configuration files (`clients.toml`, `workers.toml`) at startup. If a configuration file is invalid (e.g., malformed TOML, missing required fields), the application will **fail fast** and exit with an error, rather than starting in a partially broken state. This ensures the security and integrity of the deployment.

### Configuration Files

To manage access and worker settings securely, Avtomatika uses TOML configuration files.

-   **`clients.toml`**: Defines API clients, their tokens, plans, and quotas.
    ```toml
    [client_premium]
    token = "secret-token-123"
    plan = "premium"
    ```
-   **`workers.toml`**: Defines individual tokens for workers to enhance security.
    ```toml
    [gpu-worker-01]
    token = "worker-secret-456"
    ```
-   **`schedules.toml`**: Defines periodic tasks (CRON-like) for the native scheduler.
    ```toml
    [nightly_backup]
    blueprint = "backup_flow"
    daily_at = "02:00"
    ```

For detailed specifications and examples, please refer to the [**Configuration Guide**](docs/configuration.md).

### Fault Tolerance

The orchestrator has built-in mechanisms for handling failures based on the `error.code` field in a worker's response.

*   **TRANSIENT_ERROR**: A temporary error (e.g., network failure, rate limit). The orchestrator will automatically retry the task several times.
*   **PERMANENT_ERROR**: A permanent error (e.g., a corrupted file). The task will be immediately sent to quarantine for manual investigation.
*   **INVALID_INPUT_ERROR**: An error in the input data. The entire pipeline (Job) will be immediately moved to the failed state.

### Concurrency & Performance

To prevent system overload during high traffic, the Orchestrator implements a backpressure mechanism for its internal job processing logic.

*   **`EXECUTOR_MAX_CONCURRENT_JOBS`**: Limits the number of job handlers running simultaneously within the Orchestrator process (default: `100`). If this limit is reached, new jobs remain in the Redis queue until a slot becomes available. This ensures the event loop remains responsive even with a massive backlog of pending jobs.

### High Availability & Distributed Locking

The architecture supports horizontal scaling. Multiple Orchestrator instances can run behind a load balancer.

*   **Stateless API:** The API is stateless; all state is persisted in Redis.
*   **Instance Identity:** Each instance should have a unique `INSTANCE_ID` (defaults to hostname) for correct handling of Redis Streams consumer groups.
*   **Distributed Locking:** Background processes (`Watcher`, `ReputationCalculator`) use distributed locks (via Redis `SET NX`) to coordinate and prevent race conditions when multiple instances are active.

### Storage Backend

By default, the engine uses in-memory storage. For production, you must configure persistent storage via environment variables.

*   **Redis (StorageBackend)**: For storing current job states (serialized with `msgpack`) and managing task queues (using Redis Streams with consumer groups).
    *   Install:
        ```bash
        pip install "avtomatika[redis]"
        ```
    *   Configure:
        ```bash
        export REDIS_HOST=your_redis_host
        ```

*   **PostgreSQL/SQLite (HistoryStorage)**: For archiving completed job history.
    *   Install:
        ```bash
        pip install "avtomatika[history]"
        ```
    *   Configure:
        ```bash
        export HISTORY_DATABASE_URI=...
        ```
        *   SQLite: `sqlite:///path/to/history.db`
        *   PostgreSQL: `postgresql://user:pass@host/db`

### Security

The orchestrator uses tokens to authenticate API requests.

*   **Client Authentication**: All API clients must provide a token in the `X-Avtomatika-Token` header. The orchestrator validates this token against client configurations.
*   **Worker Authentication**: Workers must provide a token in the `X-Worker-Token` header.
    *   `GLOBAL_WORKER_TOKEN`: You can set a global token for all workers using this environment variable. For development and testing, it defaults to `"secure-worker-token"`.
    *   **Individual Tokens**: For production, it is recommended to define individual tokens for each worker in a separate configuration file and provide its path via the `WORKERS_CONFIG_PATH` environment variable. Tokens from this file are stored in a hashed format for security.

> **Note on Dynamic Reloading:** The worker configuration file can be reloaded without restarting the orchestrator by sending an authenticated `POST` request to the `/api/v1/admin/reload-workers` endpoint. This allows for dynamic updates of worker tokens.

### Observability

When installed with the telemetry dependency, the system automatically provides:

*   **Prometheus Metrics**: Available at the `/_public/metrics` endpoint.
*   **Distributed Tracing**: Compatible with OpenTelemetry and systems like Jaeger or Zipkin.
## Contributor Guide

### Setup Environment

*   Clone the repository.
*   Install the package in editable mode with all dependencies:
    ```bash
    pip install -e ".[all,test]"
    ```
*   Ensure you have system dependencies installed, such as `graphviz`.
    *   Debian/Ubuntu:
        ```bash
        sudo apt-get install graphviz
        ```
    *   macOS (Homebrew):
        ```bash
        brew install graphviz
        ```

### Running Tests

To run the `avtomatika` test suite:
```bash
pytest avtomatika/tests/
```

### Interactive API Documentation

Avtomatika provides a built-in interactive API documentation page (similar to Swagger UI) that is automatically generated based on your registered blueprints.

*   **Endpoint:** `/_public/docs`
*   **Features:**
    *   **List of all system endpoints:** Detailed documentation for Public, Protected, and Worker API groups.
    *   **Dynamic Blueprint Documentation:** Automatically generates and lists documentation for all blueprints registered in the engine, including their specific API endpoints.
    *   **Interactive Testing:** Allows you to test API calls directly from the browser. You can provide authentication tokens, parameters, and request bodies to see real server responses.

## Detailed Documentation

For a deeper dive into the system, please refer to the following documents:

- [**Architecture Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/architecture.md): A detailed overview of the system components and their interactions.
- [**API Reference**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md): Full specification of the HTTP API.
- [**Deployment Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/deployment.md): Instructions for deploying with Gunicorn/Uvicorn and NGINX.
- [**Cookbook**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/README.md): Examples and best practices for creating blueprints.