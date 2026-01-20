# Configuration Files Reference

Avtomatika uses [TOML](https://toml.io/en/) format files to manage access and settings for clients and workers.

The system implements the **Fail Fast** principle: if a TOML syntax error or missing mandatory fields is detected at startup, the Orchestrator **will not start** and will raise an exception. This ensures the system does not run in an incorrect or unsafe state.

---

## 1. clients.toml

This file defines the list of API clients authorized to create jobs.

**Default Path:** Can be set via `CLIENTS_CONFIG_PATH` environment variable. Usually located in the project root.

### Structure

The file consists of sections (tables), where the section name is the client's unique identifier (for logs and convenience).

| Field | Type | Mandatory | Description |
| :--- | :--- | :--- | :--- |
| `token` | String | **Yes** | Secret token the client must pass in `X-Avtomatika-Token` header. |
| `plan` | String | No | Tariff plan name (e.g., "free", "premium"). Used in blueprints for logic. |
| `monthly_attempts` | Integer | No | Monthly request quota. If set, Orchestrator will track and block requests exceeding the limit. |
| `*` | Any | No | Any other fields (e.g., `languages`, `callback_url`) will be available in `context.client.params`. |

### Example

```toml
# Premium client
[client_premium_user]
token = "sec_vip_token_123"
plan = "premium"
monthly_attempts = 100000
# Custom parameters
languages = ["en", "de", "fr"]
priority_support = true

# Free client
[client_free_user]
token = "sec_free_token_456"
plan = "free"
monthly_attempts = 100
languages = ["en"]
```

---

## 2. workers.toml

This file is used to configure individual worker authentication. This is a safer alternative to using a single global token.

**Default Path:** Can be set via `WORKERS_CONFIG_PATH` environment variable.

### Structure

The file consists of sections, where the section name must **exactly match** the `worker_id` the worker uses when registering.

| Field | Type | Mandatory | Description |
| :--- | :--- | :--- | :--- |
| `token` | String | **Yes** | Individual secret token for this worker. |
| `description` | String | No | Worker description for administrators. |

### Security Features
*   At startup, Orchestrator calculates SHA-256 hash of the token and stores only the hash in memory (Redis). The original token is not stored anywhere.
*   Upon incoming request from a worker, the hash of the provided token is compared with the stored one.

### Example

```toml
# Section name must be the same as WORKER_ID on the worker side
[gpu-worker-01]
token = "super-secret-token-for-gpu-01"
description = "Primary GPU node for video rendering"

[cpu-worker-01]
token = "another-secret-token-for-cpu-01"
description = "General purpose CPU worker"
```

---

## 3. schedules.toml

This file configures the Native Scheduler, allowing you to run blueprints periodically without external cron jobs.

**Default Path:** Can be set via `SCHEDULES_CONFIG_PATH` environment variable.

### Structure

Each section represents a scheduled job. The section name serves as the job's unique identifier.

| Field | Type | Mandatory | Description |
| :--- | :--- | :--- | :--- |
| `blueprint` | String | **Yes** | The name of the blueprint to execute. |
| `input_data` | Dictionary | No | Initial data payload for the job. Defaults to empty dict. |
| `interval_seconds` | Integer | *One of* | Run job every N seconds. |
| `daily_at` | String | *One of* | Run daily at specific time ("HH:MM"). |
| `weekly_days` | List[String] | *One of* | Run on specific days ("mon", "tue", ...) at `time`. |
| `monthly_dates` | List[Integer] | *One of* | Run on specific dates (1-31) at `time`. |
| `time` | String | *One of* | Required for `weekly_days` and `monthly_dates`. Format "HH:MM". |

**Note on Timezones:** All time fields are interpreted according to the global `TZ` environment variable (default "UTC").

### Example

```toml
[cleanup_job]
blueprint = "system_cleanup"
interval_seconds = 3600
input_data = { target = "temp_files" }

[daily_report]
blueprint = "generate_report"
daily_at = "09:00" # Runs at 09:00 TZ time
input_data = { type = "full_daily" }

[weekly_backup]
blueprint = "full_backup"
weekly_days = ["fri"]
time = "23:00"
input_data = { compression = "max" }
```

---

## Dynamic Reloading

You can update `workers.toml` without restarting the Orchestrator.
To do this, send a POST request (with a valid client token) to the endpoint:

`POST /api/v1/admin/reload-workers`

This forces the Orchestrator to re-read the file and update token hashes in Redis.

---

## Environment Variables

In addition to configuration files, the Orchestrator is configured via environment variables.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `API_HOST` | Host to bind the API server to. | `0.0.0.0` |
| `API_PORT` | Port to bind the API server to. | `8080` |
| `REDIS_HOST` | Hostname of the Redis server. Required for production. | `""` (MemoryStorage) |
| `REDIS_PORT` | Redis server port. | `6379` |
| `REDIS_DB` | Redis database index. | `0` |
| `INSTANCE_ID` | **Important for Scaling:** Unique identifier for this Orchestrator instance. Used as consumer name in Redis Streams. Defaults to hostname if not set. | `hostname` |
| `CLIENT_TOKEN` | Global token for API clients (fallback if `clients.toml` not used). | `secure-orchestrator-token` |
| `GLOBAL_WORKER_TOKEN` | Global token for workers (fallback if `workers.toml` not used). | `secure-worker-token` |
| `WORKERS_CONFIG_PATH` | Path to `workers.toml`. | `""` |
| `CLIENTS_CONFIG_PATH` | Path to `clients.toml`. | `""` |
| `SCHEDULES_CONFIG_PATH` | Path to `schedules.toml`. | `""` |
| `TZ` | **Global Timezone:** Affects scheduler triggers, log timestamps, and history API output (e.g., "Europe/Moscow", "UTC"). | `UTC` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` |
| `LOG_FORMAT` | Log format (`text` or `json`). | `json` |
| `WORKER_TIMEOUT_SECONDS` | Maximum time allowed for a worker to complete a task. | `300` |
| `WORKER_POLL_TIMEOUT_SECONDS` | Timeout for long-polling task requests from workers. | `30` |
| `WORKER_HEALTH_CHECK_INTERVAL_SECONDS` | Interval for updating worker TTL (used for health checks). | `60` |
| `JOB_MAX_RETRIES` | Maximum number of retries for transient task failures. | `3` |
| `WATCHER_INTERVAL_SECONDS` | Interval for the Watcher background process to check for timed-out jobs. | `20` |
| `EXECUTOR_MAX_CONCURRENT_JOBS` | Maximum number of concurrent jobs (handlers) processed by the Orchestrator. | `100` |
| `HISTORY_DATABASE_URI` | URI for history storage (`sqlite:///...` or `postgresql://...`). | `""` (Disabled) |

### S3 Storage (Optional)

Configure these variables to enable S3 Payload Offloading.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `S3_ENDPOINT_URL` | URL of the S3-compatible storage. **Required** to enable S3. | `""` |
| `S3_ACCESS_KEY` | Access Key ID. **Required**. | `""` |
| `S3_SECRET_KEY` | Secret Access Key. **Required**. | `""` |
| `S3_DEFAULT_BUCKET` | Default bucket name for job payloads. | `avtomatika-payloads` |
| `S3_REGION` | S3 Region. | `us-east-1` |
| `S3_MAX_CONCURRENCY` | Maximum number of concurrent connections to S3 across all jobs. Prevents file descriptor exhaustion. | `100` |
| `TASK_FILES_DIR` | Local directory for temporary file storage during job execution. | `/tmp/avtomatika-payloads` |