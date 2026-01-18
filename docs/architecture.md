> **Note:** This document describes the **Python implementation** of the RCA standard. For the high-level architectural specification, please refer to the `rca` package.

# Orchestrator Architecture

This document describes the high-level architecture of the orchestration system, its key components, and their interaction.

As presented in `README.md`, the architecture can be viewed through the analogy **"Director-Script-Performers"**. This document details the technical implementation of each of these elements.

## General Scheme

The system consists of a central **Orchestrator** and multiple **Workers**. All external interactions (from clients and workers) pass through **NGINX Reverse Proxy**, which provides HTTP/2 support and terminates TLS connections.

### Component Diagram
```mermaid
graph TD
    subgraph "External World"
        Client[API Client]
    end

    subgraph "Infrastructure"
        NGINX(NGINX Reverse Proxy)
    end

    subgraph "Orchestration System"
        O_API(Orchestrator API)
        O_Engine{Engine}
        O_Storage[(Storage: Redis)]
        W1(Worker 1)
        W2(Worker 2)
    end

    Client --"1. Create Job (HTTPS/2)"--> NGINX
    NGINX --"2. Proxy Request"--> O_API
    O_API --> O_Engine
    O_Engine --> O_Storage

    W1 --"3. Long-Polling for tasks (HTTP)"--> NGINX
    NGINX --> O_API
    O_API --"4. Dispatch Task"--> NGINX
    NGINX --> W1

    W1 --"5. Submit Result"--> NGINX

    W1 <-."6. 'cancel' command (WebSocket)".-> NGINX
    NGINX <-." ".-> O_API

    W1 --"Heartbeat"--> NGINX
    W2 --"Heartbeat"--> NGINX
```

## Key Orchestrator Components

### 1. `OrchestratorEngine`
**Location:** `src/avtomatika/engine.py`

This is the central class that brings all components together. Its main tasks:
- Initialize the `aiohttp` web application and delegate route setup to the API layer.
- Register "Blueprints" (`StateMachineBlueprint`).
- Manage the lifecycle of background processes (`JobExecutor`, `Watcher`, `HealthChecker`, `ReputationCalculator`, `Scheduler`).
- Provide access to shared resources such as `StorageBackend` and `Config` via `aiohttp.web.AppKey`.

### 1.1. API Layer
**Location:** `src/avtomatika/api/`

The HTTP API handling logic has been decoupled from the core engine to improve maintainability.
-   **`routes.py`**: Responsible for setting up the application routing table, including versioned API groups and mounting sub-applications (Public, Protected, Worker).
-   **`handlers.py`**: Contains the actual request handlers for all endpoints. These handlers access the engine and other components via the application instance attached to the request.

### 2. `StateMachineBlueprint`
**Location:** `src/avtomatika/blueprint.py`

This is a declarative way to define a workflow (pipeline).
- **State Machine:** Each blueprint represents a state machine where states are process steps, and transitions are defined by logic inside "handlers".
- **Handlers:** Functions bound to specific states using the `@blueprint.handler_for("state_name")` decorator. They receive `JobContext` and `ActionFactory` to perform actions.
- **Explicit State Definition:** The `@blueprint.handler_for` decorator accepts two boolean flags:
    - `is_start=True`: Marks the state as **initial**. Each blueprint must have exactly one such state.
    - `is_end=True`: Marks the state as **final** (terminal). A blueprint can have multiple such states.
- **Validation:** When registering a blueprint in `OrchestratorEngine`, the `validate()` method is automatically called, which checks that the blueprint has exactly one start state. This prevents configuration errors at an early stage.
- **Conditions:** Supports conditional transitions using the `.when("context.area.field == 'value'")` modifier, allowing for flexible routing logic.
- **Visualization:** Provides a `.render_graph()` method to automatically generate a state diagram using `graphviz`, simplifying analysis and documentation of pipeline logic.
- **Parallel Execution and Aggregation:** Allows running multiple independent tasks simultaneously. Upon their completion, a special **aggregator** handler collects all results for further processing.

### 3. `JobExecutor`
**Location:** `src/avtomatika/executor.py`

This is the main background process responsible for executing jobs.
- **Execution Loop:** Constantly retrieves jobs from the queue in Redis (`dequeue_job`).
- **Job Processing:** For each job, it finds the corresponding handler in the blueprint and executes it.
- **State Management:** After executing the handler, it processes actions requested via `ActionFactory`. This can be a simple transition to a new state (`transition_to`) or more complex logic, such as dispatching a task to a worker (`dispatch_task`).

  **Asynchronous Transitions with `dispatch_task`**

  One of the key capabilities is managing a process that depends on the result of an asynchronous task performed by a worker. This is implemented using the `transitions` parameter in the `dispatch_task` method.

  ```python
  # Example handler
  async def generate_data_handler(context, actions):
      actions.dispatch_task(
          task_type="data_generation",
          params=context.initial_data,
          transitions={
              "success": "awaiting_approval",
              "failure": "data_generation_failed",
              "needs_review": "manual_review_step"
          }
      )
  ```

  In this example:
  1. The Orchestrator dispatches the task to a worker and saves the `transitions` dictionary in the `Job` state.
  2. The `Job` transitions to a waiting state (`waiting_for_worker`).
  3. The worker performs the task and returns a result containing a status (e.g., `"success"`, `"failure"`, or a custom status `"needs_review"`).
  4. The Orchestrator uses this status as a key in the saved `transitions` dictionary to determine the next state.

  **Important Behavior Rules:**
  - **Default Status:** If the worker does not return a status, the system defaults to `"success"`.
  - **Data Transfer from Worker:** Besides `status`, the worker can return a `data` object. The content of this object (if it is a dictionary) will be automatically copied to `context.state_history` and become available in subsequent blueprint steps.
  - **Unknown Status:** If the worker returns a status for which there is no key in the `transitions` dictionary, the process automatically transitions to a state named `"failed"`. This ensures fault tolerance and prevents processes from hanging.

- **Fault Tolerance:** Implements retry logic and moving failed tasks to quarantine. The Orchestrator handles errors from workers based on their type, allowing flexible management of failed tasks.

  **Error Types Returned by Worker:**
  A worker can return one of three error types in the `result.error.code` field:
  - `TRANSIENT_ERROR` (Default): Indicates a temporary problem (e.g., network failure, external service unavailability). The Orchestrator will **retry** dispatching the task several times before moving it to quarantine.
  - `PERMANENT_ERROR`: Indicates a permanent problem that will not be resolved by retrying (e.g., corrupted file, incompatible version). The task will be immediately **moved to quarantine** without retries.
  - `INVALID_INPUT_ERROR`: Indicates a fundamental problem with the task input data (invalid format, missing parameters). The `Job` to which the task belongs will be immediately **moved to the `failed` state**, as further execution is pointless.

  **Fault Tolerance Levels:**
  - **Task Level Retry:** The Orchestrator manages retries for `TRANSIENT_ERROR`.
  - **Job Level Retry:** If an exception occurs in the handler code itself (in the Orchestrator), `JobExecutor` will retry executing the same handler several times before placing the entire `Job` in quarantine.

  **Parallel Task Execution**

  The system allows running multiple tasks in parallel and then collecting their results. This is achieved through special use of the `dispatch_task` method and aggregator handlers.

  1.  **Starting Parallel Branches:**
      - In one handler, you can call `actions.dispatch_task` multiple times, each time for a different task.
      - **Key Point:** All these `dispatch_task` calls must have the same next step (`transition`) leading to the aggregator handler.

      ```python
      # Handler starting parallel tasks
      @blueprint.handler_for("start_parallel_tasks")
      async def start_parallel_work(context, actions):
          # Dispatch task A
          actions.dispatch_task(
              task_type="task_a",
              params={"id": 1},
              transitions={"success": "aggregate_results"}
          )
          # Dispatch task B
          actions.dispatch_task(
              task_type="task_b",
              params={"id": 2},
              transitions={"success": "aggregate_results"}
          )
      ```
  2.  **Result Aggregation:**
      - The handler intended to collect results is marked with the special decorator `@blueprint.aggregator_for("state_name")`.
      - This handler will be executed only **after ALL parallel branches leading to this state are complete**.
      - Inside the aggregator, results of all executed tasks are available via `context.aggregation_results`.

      ```python
      # Aggregator handler
      @blueprint.aggregator_for("aggregate_results")
      async def aggregator(context, actions):
          # Results are available as a dictionary: {task_id: result_dict}
          results = context.aggregation_results

          summary = {}
          for task_id, result in results.items():
              summary[task_id] = result.get("data")

          # Save final result and proceed
          context.state_history["summary"] = summary
          actions.transition_to("final_step")
      ```
      - `context.aggregation_results` is a dictionary where keys are task IDs and values are full result objects returned by workers.

### 3.1. `JobContext` (Context Object)
Each handler receives a `context` object as input, which contains all necessary information about the current job and provides access to resources. This is the primary way to access data within the pipeline.

**Key Attributes:**
- `context.job_id` (str): Unique identifier of the current job.
- `context.current_state` (str): Name of the current state where the handler is executing.
- `context.initial_data` (dict): Initial data with which the job was created.
- `context.state_history` (dict): A dictionary where you can store and read data persistent within a single `Job`. Useful for passing information between states.
- `context.aggregation_results` (dict): Available **only in aggregator handlers**. Contains results of all parallel tasks that ran before aggregation. Keys are `task_id`, values are result dictionaries.
- `context.client` (ClientConfig): Object containing information about the client who initiated the job (including their plan and custom parameters from `clients.toml`).
- `context.data_stores` (namespace): Object providing access to all `data_store` attached to the blueprint. Access is by name, e.g., `context.data_stores.my_cache.get("key")`.
- `context.tracing_context` (dict): Dictionary with information for distributed OpenTelemetry tracing.

### 3.2. `ActionFactory` (Action Factory)
As a second argument, each handler receives an `actions` object. This object provides methods by which the handler determines what should happen after its execution. **Only one** `actions` method can be called within a single handler.

**Key Methods:**
- `actions.transition_to(state: str)`: Simply transitions the state machine to the next state. Execution will continue immediately.
- `actions.dispatch_task(task_type, params, transitions, priority, ...)`: Dispatches a task for execution to a worker and pauses the pipeline until a result is received. The `transitions` dictionary determines which state the pipeline will transition to depending on the `status` returned by the worker. The `priority` parameter allows specifying task priority.
- `actions.await_human_approval(message, transitions)`: Pauses the pipeline until an external system or human sends a webhook with a decision.
- `actions.run_blueprint(blueprint_name, initial_data, transitions)`: Runs another (child) blueprint as part of the current pipeline. The main pipeline will be paused until the child one completes.

### 3.3. Dependency Injection (`DataStore`)
The system supports a dependency injection mechanism that allows providing handlers access to external resources, such as database clients, API clients, or caches.

- **Registration:** Any object (e.g., a `redis.Redis` client instance or a custom API client) can be registered as a `DataStore` when creating a blueprint.
  ```python
  # Example registration
  from .datastore import AsyncDictStore

  my_cache = AsyncDictStore({"initial_key": "initial_value"})

  bp = StateMachineBlueprint(
      "my_blueprint_with_datastore",
      data_stores={"cache": my_cache}
  )
  ```
- **Access in Handler:** Registered `DataStores` become available inside any handler of this blueprint via the `context` object. Access is by name specified during registration.
  ```python
  @bp.handler_for("some_state")
  async def my_handler(context, actions):
      # Access our cache
      value = await context.data_stores.cache.get("some_key")
      print(f"Value from cache: {value}")
  ```

This mechanism allows decoupling business process logic from specific data access client implementations, simplifying testing and component reuse.

### 4. `Dispatcher`
**Location:** `src/avtomatika/dispatcher.py`

Responsible for queuing a task (`task`) for the most suitable worker.
- **Worker Selection:** Applies multi-level filtering:
    1.  **By Status:** Finds all workers with `idle` status (or no status for backward compatibility).
    2.  **By Task Type:** From free workers, finds those whose `supported_tasks` contain the required `task_type`.
    3.  **By Resource Requirements:** If `resource_requirements` are specified in the task, filters out workers that do not meet these requirements (e.g., GPU model, VRAM size, or presence of ML models).
- **Strategies:** Applies one of the selection strategies to the remaining pool of workers:
    - `default`: Prefers "warm" workers (who already have necessary models in memory), and then selects the cheapest among them.
    - `round_robin`: Distributes load sequentially among all available workers.
    - `least_connections`: Selects the worker with the fewest active tasks.
    - `cheapest`: Selects the worker with the lowest cost per second of work (based on `cost_per_second` field).
    - `best_value`: Selects the worker with the best "price/quality" ratio using their **reputation**. This strategy divides worker cost by their reputation, preferring more reliable and cheaper executors.
- **Queuing:** After selecting a worker, `Dispatcher` places the task in that worker's personal priority queue in `Storage` (e.g., Redis), using the `priority` value, from where the worker can pick it up.

### 4.1. Interaction with Workers (Pull Model)

The system uses a **Pull Model**, where workers initiate connection to the orchestrator to receive tasks. This allows workers to be behind NAT or firewalls.

- **Registration and Heartbeats:** Worker registers with the orchestrator at startup, reporting its capabilities. Then it periodically sends heartbeat messages to confirm activity. This mechanism remains the same.
- **Task Retrieval (Long-Polling):**
    1. Worker sends a `GET` request to endpoint `/_worker/workers/{worker_id}/tasks/next`.
    2. If there is already a task in the queue for this worker (in Redis), the orchestrator immediately returns it in a `200 OK` response.
    3. If there are no tasks, the orchestrator holds the connection open (long-polling) for a certain timeout (e.g., 30 seconds).
    4. If during this time `Dispatcher` places a task in the queue for this worker, it is immediately sent to the waiting worker.
    5. If timeout expires and no tasks appeared, the orchestrator responds `204 No Content`, and the worker immediately makes a new request.
- **Passive HealthChecker:** The `HealthChecker` component remains passive. Worker "health" is determined by the presence of current heartbeat messages in Redis (via TTL mechanism). If a worker stops sending them, it is automatically considered inactive and excluded from dispatching.

#### **Fault Tolerance and Load Balancing on Worker Side**

To increase reliability and load distribution, `worker_sdk` supports connection to multiple Orchestrators. This behavior is configured on the worker side and is transparent to the Orchestrator.

-   **Configuration:** A list of Orchestrator addresses is passed to the worker via the `ORCHESTRATORS_CONFIG` environment variable.
-   **Operation Modes (`MULTI_ORCHESTRATOR_MODE`):**
    -   `FAILOVER` (default mode): Worker will constantly poll the first Orchestrator in the list. If the request fails (e.g., due to network failure or server unavailability), it immediately switches to the next Orchestrator in the list and continues polling it. This ensures high availability.
    -   `ROUND_ROBIN`: Worker sends task requests sequentially to each Orchestrator in the list. This mode is useful for horizontal scaling and load distribution between multiple independent Orchestrator instances.
-   **Registration:** Upon startup, the worker registers and sends heartbeat messages to *all* Orchestrators in the list so that each is aware of its status and capabilities.

### 4.2. Advanced Communication: WebSocket

In addition to the Pull model for task retrieval, the system supports an optional **bidirectional communication channel via WebSocket**.

- **Initiation:** Worker, if it supports this feature (and it is enabled via `WORKER_ENABLE_WEBSOCKETS=true`), establishes a persistent WebSocket connection with the Orchestrator.
- **Purpose:**
    - **Commands from Orchestrator:** The Orchestrator can send commands to the worker in real-time. A primary example is the command to cancel a running task (`cancel_task`).
    - **Updates from Worker:** The worker can use the same channel to send intermediate updates about task progress (`progress_update`).
- **Fault Tolerance:** Worker SDK automatically manages reconnection in case of connection loss.

This hybrid model (HTTP for tasks, WebSocket for commands and updates) allows combining reliability and simplicity of the Pull model with interactivity of Push notifications.

### 5. `Watcher`
**Location:** `src/avtomatika/watcher.py`

A background process that watches for "stuck" or timed-out tasks.
- **Tracking:** Checks a sorted set in Redis containing `job_id` and their timeout times.
- **Timeout Handling:** If a task has exceeded the timeout, `Watcher` moves it to `failed` state to prevent system blocking.

### 6. `ReputationCalculator`
**Location:** `src/avtomatika/reputation.py`

This is a background process responsible for analyzing worker performance and calculating their reputation.
- **History Analysis:** Periodically requests task history executed by each worker from `HistoryStorage`.
- **Reputation Calculation:** Based on the ratio of successfully and unsuccessfully completed tasks, as well as other factors (e.g., timeouts), it calculates a "reputation score" for each worker (a number from 0 to 1).
- **State Update:** Saves the calculated reputation to the worker state record in `StorageBackend`.
- **Usage:** This reputation score is used by the `best_value` dispatch strategy to make more informed decisions about selecting the most reliable and efficient executor.

### 7. `Scheduler`
**Location:** `src/avtomatika/scheduler.py`

A background process that triggers jobs based on a schedule defined in `schedules.toml`.
-   **Triggers:** Supports interval (every N seconds), daily, weekly, and monthly triggers.
-   **Timezones:** Aware of the globally configured timezone (`TZ`).
-   **Distributed Locking:** Uses atomic locks in `StorageBackend` (`set_nx_ttl`) to ensure that a scheduled job runs exactly once across all Orchestrator instances.
-   **Integration:** Creates jobs directly via `OrchestratorEngine.create_background_job`, bypassing the HTTP API but logging creation events to history.

### 9. `StorageBackend`
**Location:** `src/avtomatika/storage/`

Abstraction for storing all **current** states of jobs, workers, and queues.

-   **Implementations:**
    -   **`MemoryStorage` (default):** Implementation storing all data in memory. Default implementation if no other storage is configured. Allows running the application without external dependencies, but all states are lost upon restart.
    -   **`RedisStorage` (recommended for production):** Implementation using Redis for persistent storage. Activated when `REDIS_HOST` is specified in configuration.
        -   **State Storage:** Uses `msgpack` for efficient binary serialization of job and worker states.
        -   **Task Queues:** Uses **Redis Streams** (Consumer Groups) to ensure reliable task delivery (At-least-once). Supports recovery of pending messages upon restart using `INSTANCE_ID`.
-   **Interface:** `storage/base.py` defines methods that must be implemented in any storage implementation.

### 9.1. `HistoryStorage`

**Location:** `src/avtomatika/history/`



This is an optional component responsible for recording task execution **history** for subsequent analysis and debugging. Unlike `StorageBackend`, which stores only the last actual state, `HistoryStorage` records every event in a job's life.



-   **Activation:** The component is activated via the `HISTORY_DATABASE_URI` environment variable. If the variable is not set, the "empty" `NoOpHistoryStorage` implementation is used, which performs no actions.

-   **Supported DBs:**

    - **SQLite:** if URI starts with `sqlite:` (e.g., `sqlite:history.db`).

        - **Timestamp Storage:** Uses Unix timestamp (REAL) in UTC for correct sorting.

        - **Timezone Handling:** Automatically converts timestamps to the globally configured `TZ` upon retrieval.

    - **PostgreSQL:** if URI starts with `postgresql:` (e.g., `postgresql://user:pass@host/db`).

        - **Timestamp Storage:** Uses native `TIMESTAMPTZ`.

        - **JSON Handling:** Explicitly serializes/deserializes JSONB fields for compatibility.

-   **Logged Events:**

    -   **Jobs:** `state_started`, `state_finished`, `state_failed`, `task_dispatched`. Full "snapshot" of job state, duration, and other meta-information is saved for each event.

    -   **Workers:** `registered`, `status_update`.

-   **Fault Tolerance:** Errors during writing to `HistoryStorage` do not interrupt the main job execution process. The error is logged, but the job continues to execute, ensuring main system reliability.

-   **Data Access:** History is available via new API endpoint `GET /api/jobs/{job_id}/history`.



### 9.2. Access to Blueprint Graph

For visualization and debugging purposes, the system provides an endpoint to retrieve the structure of any registered blueprint as a graph.

-   **Endpoint:** `GET /api/v1/blueprints/{blueprint_name}/graph`
-   **Description:** Returns textual representation of the specified blueprint's graph in DOT format (graph description language). This format can be rendered into an image using Graphviz utility or compatible JavaScript libraries (e.g., `viz.js`).
-   **Path Parameters:**
    -   `blueprint_name` (string): Name of the blueprint whose graph needs to be retrieved.
-   **Success Response (200 OK):**
    -   **Content-Type:** `text/vnd.graphviz`
    -   **Response Body:** String in DOT format describing the graph.
-   **Possible Errors:**
    -   `404 Not Found`: If blueprint with specified name is not found.
    -   `501 Not Implemented`: If `graphviz` utility required for graph generation is not installed on the orchestrator server.

## 10. Authentication and Authorization

The system uses two-level processing of incoming requests: first configuration loading, and then applying a chain of `middlewares` for authentication and authorization.

### 10.1. Client Configuration Loading
**Location:** `src/avtomatika/client_config_loader.py`

At application startup, a special `client_config_loader` module performs the following actions:
1.  **Reading `clients.toml`:** Reads the `clients.toml` configuration file containing information about all allowed clients.
2.  **Loading into Redis:** For each client in the file:
    -   Saves their static configuration (plan, limits, custom parameters) to Redis using the client token as a key.
    -   If a quota (`monthly_attempts`) is defined for the client, initializes the corresponding counter in Redis.

This process runs once at startup, avoiding file reading on every request and ensuring high performance.

### 10.2. Middlewares

All requests to protected endpoints pass through a chain of `middlewares`.

#### **Client Authentication (`client_auth_middleware`)**
- **Task:** Verify that the client is authorized to access the system.
- **Mechanism:**
    1.  Extracts token from `X-Orchestrator-Token` header.
    2.  Searches for configuration by this token in Redis.
    3.  If configuration is found, it is attached to the `request` object (in `request["client_config"]`) for use by subsequent middlewares in the chain.
    4.  If token is invalid, request is rejected with status `401 Unauthorized`.

#### **Worker Authentication (`worker_auth_middleware`)**
- **Task:** Verify that the request comes from an authenticated worker.
- **Mechanism (Hybrid Model):**
    1.  Extracts `worker_id` from request path (or body for registration endpoint).
    2.  Extracts token from `X-Orchestrator-Worker-Token` header.
    3.  **Individual Token Check (Priority):** Middleware searches Redis for a token linked to the specific `worker_id`. If such token is found, it is compared with the provided one. In case of mismatch, request is immediately rejected (without falling back to global token check).
    4.  **Global Token Check (Fallback):** If individual token for `worker_id` is not found, middleware checks provided token against the general `WORKER_TOKEN` from Orchestrator configuration. This provides backward compatibility.
    5.  If both checks fail, request is rejected with status `401 Unauthorized`.
- **Configuration:** Individual tokens are defined in `workers.toml` file and loaded into Redis at Orchestrator startup.

#### **Quota Check (`quota_middleware`)**
- **Task:** Ensure the client has not exceeded their request limit.
- **Mechanism:**
    1.  Runs **after** `client_auth_middleware`.
    2.  Uses `token` from attached client configuration (`request["client_config"]`).
    3.  Executes atomic `check_and_decrement_quota` operation in Redis.
    4.  If quota is exhausted, request is rejected with status `429 Too Many Requests`.

#### **Rate Limiting (`ratelimit_middleware`)**
- **Task:** Protect system from too frequent requests from a single source (DDoS attacks, "noisy" clients).
- **Mechanism:**
    1.  Uses client IP address or `worker_id` as tracking key.
    2.  For each request, atomically increments a counter in Redis with a specified TTL (e.g., 5 requests per minute).
    3.  If counter exceeds limit, request is rejected with status `429 Too Many Requests`.

#### **Response Compression (`compression_middleware`)**
- **Task:** Reduce response body size to save traffic.
- **Mechanism:**
    1.  Checks `Accept-Encoding` header in client request.
    2.  If client supports `zstd` (preferred) or `gzip`, response body is compressed before sending.
    3.  Adds `Content-Encoding` header indicating used algorithm.

## 11. Horizontal Scaling (High Availability)

System architecture allows running multiple Orchestrator instances in parallel to ensure fault tolerance and load distribution.

-   **Stateless API:** HTTP API is completely stateless. All data about tasks and workers is stored in Redis. This allows a load balancer (e.g., NGINX) to distribute requests among any number of Orchestrator instances.
-   **Distributed Locking:** Background processes like `Watcher` (timeout check) and `ReputationCalculator` (reputation calculation) use distributed locking mechanism to guarantee work is performed by only one instance at any given time.
    -   **Mechanism:** Uses atomic Redis operation `SET key value NX PX ttl`.
    -   **Behavior:** At cycle start, background task attempts to acquire global lock. If lock is already held by another active instance, current instance skips iteration. This prevents race conditions and operation duplication.

## 12. Deployment and Scaling Recommendations

To ensure reliable and performant system operation in production, following practices are recommended:

- **Reverse Proxy and HTTP/2 (Recommended Approach):** For maximum performance and security, deploy Orchestrator behind a Reverse Proxy like NGINX. This gives two key benefits:
    1.  **HTTP/2 Support:** Proxy server handles HTTP/2 connection termination, significantly reducing latency and network overhead by multiplexing requests. Especially effective for frequent worker heartbeats.
    2.  **Security and Scaling:** Orchestrator is isolated from direct external access. Proxy server can also act as Load Balancer (L7), distributing requests among multiple Orchestrator instances, and handle TLS termination (HTTPS).

    Repository provides a ready-to-use deployment example using NGINX and Docker Compose, which is the recommended way to run the system.

- **Strict Configuration Validation:** System implements **Fail Fast** principle. At startup, Orchestrator validates configuration files (`clients.toml`, `workers.toml`). If errors are found (invalid TOML, missing mandatory fields), application **will not start**, raising an exception. This prevents running the system in an incorrect or unsafe state.

- **Transport Modernization (WebSocket):** For tasks requiring real-time data exchange (e.g., task cancellation or sending intermediate progress), system supports optional WebSocket channel. Connection should also be established via Reverse Proxy.

- **Response Compression:** Compression is implemented at application level using custom middleware supporting **Zstandard** (preferred) and **Gzip** (as fallback). No additional compression needs to be enabled at load balancer level.

- **Rate Limiting:** It is recommended to configure basic limiting on the load balancer. In addition, application implements its own, more granular rate-limiting mechanism for worker endpoints (e.g., for heartbeats), limiting request frequency for each `worker_id`.

- **Alerting:** System comes with a set of recommended alerting rules for Prometheus. These rules help track critical events, such as lack of active workers or high percentage of job failures.

## 13. Payload Offloading

To process tasks requiring transfer of large data volumes (e.g., video files, large datasets), system supports "Payload Offloading" mechanism. This avoids transferring "heavy" data through Redis, which is not designed for it.

- **Technology:** Uses S3-compatible object storage (e.g., AWS S3, MinIO).
- **Mechanism:**
    1.  **Client:** When creating a `Job`, client passes URI in `s3://bucket-name/path/to/file.dat` format instead of actual data.
    2.  **Orchestrator:** Orchestrator transparently passes these URIs to worker in task parameters.
    3.  **Worker SDK:** Worker SDK automatically detects parameters whose values are `s3://` URIs.
        -   **Download:** Before calling user handler, SDK downloads files from S3 to a temporary directory on worker's local disk. In `params` passed to handler, URIs are replaced with local file paths.
        -   **Upload:** After handler completes, SDK checks if it returned local file paths in result. If so, SDK automatically uploads these files back to S3 and replaces local paths with new `s3://` URIs in final result sent to Orchestrator.
- **Security:** To prevent access to arbitrary files on worker disk, SDK will upload to S3 only files located in directory specified in `WORKER_PAYLOAD_DIR` environment variable.
- **Configuration:** Worker and Orchestrator (if it needs S3 access) are configured via standard environment variables (`S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, etc.).

## 14. Observability

**Note:** Observability functions (metrics and tracing) are optional. To activate them, install the project with additional dependency: `pip install .[telemetry]`. If dependencies are not installed, system will work in normal mode but without collecting and providing telemetry data.

System is built using **OpenTelemetry** standard, ensuring a unified approach to collecting operation data.

### 14.1. Metrics
- **Technology:** Metrics are collected using `OpenTelemetry SDK` and exposed for collection by Prometheus via `/_public/metrics` endpoint.
- **Implementation:**
    - **Instruments:** Defined in `src/avtomatika/metrics.py` using standard OpenTelemetry instruments (`Counter`, `Histogram`, `ObservableGauge`).
    - **Exporter:** `OrchestratorEngine` initializes `PrometheusMetricReader`, which automatically registers with `prometheus-client`.
    - **Gauge Callbacks:** For metrics that cannot be updated incrementally (e.g., queue length), `ObservableGauge` mechanism is used. `OrchestratorEngine` runs a background task periodically polling storage and caching values. Synchronous metric callbacks then instantly return these cached values, preventing blocking during metric collection.

### 14.2. Distributed Tracing
- **Technology:** Tracing is also based on `OpenTelemetry`.
- **Implementation:**
    - **Instrumentation:** `aiohttp` client used for communication with workers is automatically instrumented using `AioHttpClientInstrumentor`.
    - **Context Propagation:** Orchestrator automatically passes trace context to worker in HTTP headers.
    - **Export:** Configured `OTLP Exporter` allows sending trace data to any compatible collector (e.g., OpenTelemetry Collector).
    - **Visualization:** Collected traces can be visualized in systems like Jaeger or Zipkin. This allows seeing full job lifecycle as a Gantt chart, analyzing latencies, and finding bottlenecks in distributed system.