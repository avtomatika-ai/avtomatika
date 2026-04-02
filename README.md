**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/README.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/README.md)

# Avtomatika Orchestrator

[![License: MPL 2.0](https://img.shields.io/badge/License-MPL%202.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)
[![PyPI Version](https://img.shields.io/pypi/v/avtomatika.svg)](https://pypi.org/project/avtomatika/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/release/python-3110/)

Avtomatika is a high-performance, state-driven engine for managing complex asynchronous workflows in Python. It provides a robust framework for building scalable and resilient applications by separating process logic from execution logic.

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
  - [S3 Payload Offloading](#s3-payload-offloading)
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

*   **[Avtomatika Protocol](https://github.com/avtomatika-ai/rxon)**: Shared package containing protocol definitions, data models, and utilities ensuring consistency across all components.
*   **[Avtomatika Worker SDK](https://github.com/avtomatika-ai/avtomatika-worker)**: The official Python SDK for building workers that connect to this engine.
*   **[HLN Protocol](https://github.com/avtomatika-ai/hln)**: The architectural specification and manifesto behind the system (Hierarchical Logic Network).
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

*   **Install with S3 support (Payload Offloading):**
    ```bash
    pip install "avtomatika[s3]"
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
from avtomatika import OrchestratorEngine, Blueprint
from avtomatika.context import ActionFactory
from avtomatika.storage import MemoryStorage
from avtomatika.config import Config

# 1. General Configuration
storage = MemoryStorage()
config = Config() # Loads configuration from environment variables

# Explicitly set tokens for this example
# Client token must be sent in the 'X-Client-Token' header.
config.CLIENT_TOKEN = "my-secret-client-token"
# Worker token must be sent in the 'X-Worker-Token' header.
config.GLOBAL_WORKER_TOKEN = "my-secret-worker-token"

# 2. Define the Workflow Blueprint
bp = Blueprint(
    name="bp",
    api_version="v1",
    api_endpoint="/jobs/my_flow"
)

# Use dependency injection to get only the data you need.
@bp.handler(is_start=True)
async def start(job_id: str, initial_data: dict, actions: ActionFactory):
    """The initial state for each new job."""
    print(f"Job {job_id} | Start: {initial_data}")
    actions.go_to("end")

# You can still request the full context object if you prefer.
@bp.handler(is_end=True)
async def end(context):
    """The final state. The pipeline ends here."""
    print(f"Job {context.job_id} | Complete.")

# 3. Initialize the Orchestrator Engine
engine = OrchestratorEngine(storage, config)
engine.register_blueprint(bp)

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

> **Tip:** The state name in `@bp.handler()` and `@bp.aggregator()` is now optional. If omitted, the name of the function will be used as the state name.

The following arguments can be injected by name:

*   **From the core job context:**
    *   `job_id` (str): The ID of the current job.
    *   `initial_data` (dict): The data the job was created with.
    *   `state_history` (dict): A dictionary for storing and passing data between steps. Data returned by workers is automatically merged into this dictionary.
    *   `actions` (ActionFactory): The object used to tell the orchestrator what to do next (e.g., `actions.go_to(...)`).
    *   `client` (ClientConfig): Information about the API client that started the job.
    *   `data_stores` (SimpleNamespace): Access to shared resources like database connections or caches.
*   **From worker results:**
    *   Any key from a dictionary returned by a previous worker can be injected by name.

### Example: Dependency Injection

This is the recommended way to write handlers.

```python
# A worker for this task returned: {"output_path": "/videos/123.mp4", "duration": 95}
# This dictionary was automatically merged into `state_history`.

@bp.handler
async def publish_video(
    job_id: str,
    output_path: str, # Injected from state_history
    duration: int,    # Injected from state_history
    actions: ActionFactory
):
    print(f"Job {job_id}: Publishing video at {output_path} ({duration}s).")
    actions.go_to("complete")
```

### The `actions` Object

This is the most important injected argument. It tells the orchestrator what to do next. **Only one** `actions` method can be called in a single handler.

*   `actions.go_to("next_state")`: Moves the job to a new state.
*   `actions.dispatch_task(...)`: Delegates work to a Worker.
*   `actions.dispatch_parallel(...)`: Runs multiple tasks at once.
*   `actions.await_human_approval(...)`: Pauses the workflow for external input.
*   `actions.run_blueprint(...)`: Starts a child workflow.

### Backward Compatibility: The `context` Object

For backward compatibility or if you prefer to have a single object, you can still ask for `context`.

```python
# This handler is equivalent to the one above.
@bp.handler
async def publish_video(context):
    output_path = context.state_history.get("output_path")
    duration = context.state_history.get("duration")

    print(f"Job {context.job_id}: Publishing video at {output_path} ({duration}s).")
    context.actions.go_to("complete")
```
## Key Concepts: JobContext and Actions

### High Performance Architecture

Avtomatika is engineered for high-load environments with thousands of concurrent workers.

*   **Standardized Holon Matching (RXON v1.0b7)**:
    *   **Unified Matching**: Migrated to the formalized `rxon` matching logic. All resource checks (CPU, RAM, GPU, custom properties) are now strictly governed by the HLN protocol standard.
    *   **Smart Numeric Comparison**: Automatically performs **GE (Greater or Equal)** checks for numbers (e.g., minimum VRAM or RAM), ensuring flexible but reliable dispatching.
    *   **Hot Cache & Skill Awareness**: Prioritizes workers that already have specific AI models loaded.
    *   **Overflow Strategy**: Automatically spills load to more expensive workers if cheaper ones are saturated.
    *   **Work Stealing**: Idle workers can atomically steal tasks from heavily loaded colleagues at O(1) speed.
*   **Self-Regulating Reputation**:
    *   **Penalty System**: Immediate reputation slashing for contract violations (-0.2) or permanent task failures (-0.05).
    *   **Recovery Loop**: Small reputation rewards for every successful task completion (+0.001), encouraging consistent quality.
*   **Contract-First & Zero Trust**:
    *   **Identity Chain Verification**: Validates the entire bubbling path for events in deep holarchies.
    *   **mTLS & STS**: Mutual authentication and short-lived token rotation for secure communication.
    *   **Signature Support**: Ready for protocol-level digital signatures for end-to-end task verification.

## Blueprint Cookbook: Key Features

### 1. Conditional Transitions (`.when()`)

Use `.when()` to create conditional logic branches. The condition string is evaluated by the engine before the handler is called, so it still uses the `context.` prefix. The handler itself, however, can use dependency injection.

```python
# The `.when()` condition still refers to `context`.
@bp.handler().when("context.initial_data.type == 'urgent'")
async def decision_step(actions):
    actions.go_to("urgent_processing")

# The default handler if no `.when()` condition matches.
@bp.handler
async def decision_step(actions):
    actions.go_to("normal_processing")
```

### 2. Delegating Tasks to Workers (`dispatch_task`)

This is the primary function for delegating work. The orchestrator will queue the task and wait for a worker to pick it up and return a result.

```python
@bp.handler
async def transcode_video(initial_data, actions):
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
@bp.handler
async def process_files(initial_data, actions):
    tasks_to_dispatch = [
        {"task_type": "file_analysis", "params": {"file": file}}
        for file in initial_data.get("files", [])
    ]
    # Use dispatch_parallel to send all tasks at once.
    # All successful tasks will implicitly lead to the 'aggregate_into' state.
    actions.dispatch_parallel(
        tasks=tasks_to_dispatch,
        aggregate_into="aggregate_results"
    )

# 2. Fan-in: Collect results using the @aggregator decorator
@bp.aggregator
async def aggregate_results(aggregation_results, state_history, actions):
    # This handler will only execute AFTER ALL tasks
    # dispatched by dispatch_parallel are complete.

    # aggregation_results is a dictionary of {task_id: result_dict}
    summary = [res.get("data") for res in aggregation_results.values()]
    state_history["summary"] = summary
    actions.go_to("processing_complete")
```

### 4. Dependency Injection (DataStore)

Provide handlers with access to external resources (like a cache or DB client).

```python
import redis.asyncio as redis

# 1. Initialize and register your DataStore
redis_client = redis.Redis(decode_responses=True)
bp = Blueprint(
    "blueprint_with_datastore",
    data_stores={"cache": redis_client}
)

# 2. Use it in a handler via dependency injection
@bp.handler
async def get_from_cache(data_stores):
    # Access the redis_client by the name "cache"
    user_data = await data_stores.cache.get("user:123")
    print(f"User from cache: {user_data}")
```

### 5. Native Scheduler

Avtomatika includes a built-in distributed scheduler. It allows you to trigger blueprints periodically (interval, daily, weekly, monthly) without external tools like cron.

*   **Configuration:** Defined in `schedules.toml`.
*   **Timezone Aware:** Supports global timezone configuration (e.g., `TZ="Europe/Moscow"`).
*   **Expiration Support:** Supports `dispatch_timeout` and `result_timeout` to ensure tasks don't run or complete too late.
*   **Distributed Locking:** Safe to run with multiple orchestrator instances; jobs are guaranteed to run only once per interval using distributed locks (Redis/Memory).

```toml
# schedules.toml example
[nightly_backup]
blueprint = "backup_flow"
daily_at = "02:00"
dispatch_timeout = 60 # Fail if no worker picks it up within 1 minute
```

### 6. Webhook Notifications

The orchestrator can send asynchronous notifications to an external system when a job completes, fails, or is quarantined. This eliminates the need for clients to constantly poll the API for status updates.

### 7. S3 Payload Offloading

Orchestrator provides first-class support for handling large files via S3-compatible storage, powered by the high-performance `obstore` library (Rust bindings).

*   **Memory Safe (Streaming)**: Uses streaming for uploads and downloads, allowing processing of files larger than available RAM without OOM errors.
*   **Managed Mode**: The Orchestrator manages file lifecycle (automatic cleanup of S3 objects and local temporary files on job completion).
*   **Dependency Injection**: Use the `task_files` argument in your handlers to easily read/write data.
*   **Directory Support**: Supports recursive download and upload of entire directories.

```python
@bp.handler
async def process_data(task_files, actions):
    # Streaming download of a large file
    local_path = await task_files.download("large_dataset.csv")
    
    # ... process data ...
    
    # Upload results
    await task_files.write_json("results.json", {"status": "done"})
    
    actions.go_to("finished")
```

## API Groups & Versioning

All external API endpoints are strictly versioned and prefixed with `/api/v1/`.

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
    "webhook_url": "https://my-app.com/webhooks/avtomatika",
    "dispatch_timeout": 30,
    "result_timeout": 120
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

The orchestrator's behavior can be configured through environment variables. Additionally, any configuration parameter loaded from environment variables can be programmatically overridden in your application code after the `Config` object has been initialized.

**Important:** The system employs **strict validation** for configuration files (`clients.toml`, `workers.toml`) at startup.

### Configuration Files

-   **`clients.toml`**: Defines API clients, their tokens, plans, and quotas.
-   **`workers.toml`**: Defines individual tokens for workers to enhance security.
-   **`schedules.toml`**: Defines periodic tasks (CRON-like) for the native scheduler.

For detailed specifications and examples, please refer to the [**Configuration Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/configuration.md).

### Fault Tolerance

The orchestrator handles failures based on the `error.code` field in a worker's response.

*   **TRANSIENT_ERROR**: Temporary errors (network, timeouts). Automatic retries.
*   **PERMANENT_ERROR**: Permanent errors (logic, security). Immediate quarantine.
*   **INVALID_INPUT_ERROR**: Data errors. Job fails immediately.

### Security & Stability Guardrails

*   **Exponential Backoff:** Core loops (`JobExecutor`, `Watcher`) automatically implement exponential backoff on infrastructure failures.
*   **Job Hijacking Protection:** Only the assigned worker can submit task results.
*   **Infinite Loop Protection:** `MAX_TRANSITIONS_PER_JOB` (default 100) terminates cycling blueprints.
*   **Stale Result Protection:** Ignores results for timed-out or re-dispatched tasks.

### Concurrency & Performance

*   **`EXECUTOR_MAX_CONCURRENT_JOBS`**: Limits internal job handlers (default: `1000`).
*   **`WATCHER_LIMIT`**: Number of timeouts checked per cycle (default: `500`).
*   **`DISPATCHER_MAX_CANDIDATES`**: Limits worker compliance checks (default: `50`).

### High Availability & Distributed Locking

Multiple Orchestrator instances can run behind a load balancer.

*   **Stateless API:** All state is persisted in Redis.
*   **Distributed Locking:** `Watcher` and `ReputationCalculator` use Redis `SET NX` locks.

### Logging & Observability

*   **Structured JSON Logging**: Easy to parse and index.
*   **Asynchronous processing**: Non-blocking `QueueHandler` prevents event loop blocking.
*   **Metrics**: Available at `/_public/metrics`, with `orchestrator_` prefix and `orchestrator_loop_lag_seconds`.

### Rate Limiting

Granular, context-aware Redis-based rate limiter (Heartbeats: 120/min, Polling: 60/min, General: 100/min).

### Dynamic Blueprint Loading

Automatic loading from `BLUEPRINTS_DIR`. Scans, imports, and registers `.py` files on startup.

### Pure Holon Mode
Disable public API with `ENABLE_CLIENT_API="false"` to only accept tasks via RXON from parent holons.

## Contributor Guide

### Setup Environment

```bash
pip install -e ../rxon
pip install -e ".[all,test]"
```

### Running Tests

```bash
pytest tests/
```

### Interactive API Documentation

Available at `/_public/docs`. Features dynamic blueprint documentation and interactive testing.

## Detailed Documentation

- [**Architecture Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/architecture.md)
- [**API Reference**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md)
- [**Configuration Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/configuration.md)
- [**Deployment Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/deployment.md)
- [**Cookbook**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/README.md)
