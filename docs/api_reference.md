**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/api_reference.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/api_reference.md)

# Orchestrator API Reference

This document describes all HTTP endpoints provided by the Orchestrator server. The API is divided into three logical groups: Public, Client, and Internal (for Workers).

## Authentication

-   **Client -> Orchestrator:** All requests to client endpoints (default prefix `/api/v1/*`) must contain the `X-Client-Token` header. The `/api` part is configurable via `CLIENT_API_PREFIX`.
-   **Worker -> Orchestrator:** All requests to `/_worker/*` endpoints must contain the `X-Worker-Token` header with a valid worker token (individual or global).

---

## 1. Public Endpoints (`/_public`)

These endpoints do not require authentication and always use the fixed `/_public` prefix.

### Service Status Check

-   **Endpoint:** `GET /_public/status`
-   **Description:** Returns a simple response confirming that the service is running.
-   **Response (`200 OK`):** `{"status": "ok"}`

### Prometheus Metrics

-   **Endpoint:** `GET /_public/metrics`
-   **Description:** Returns application metrics in a format compatible with Prometheus. Includes loop lag monitoring.
-   **Response (`200 OK`):** Text response with metrics.

### Webhook for "Human Approval"

-   **Endpoint:** `POST /_public/webhooks/approval/{job_id}`
-   **Description:** Allows an external system to approve or reject a step in the pipeline.
-   **Request Body:** `{"decision": "approved"}`
-   **Response (`200 OK`):** `{"status": "approval_received", "job_id": "..."}`

### Debug Endpoint for DB Flushing

-   **Endpoint:** `POST /_public/debug/flush_db`
-   **Description:** **(Development only!)** Clears the entire Redis database.
-   **Response (`200 OK`):** `{"status": "db_flushed"}`

### Interactive API Documentation

-   **Endpoint:** `GET /_public/docs`
-   **Description:** Returns an interactive HTML page with API documentation (Swagger UI style).
-   **Response (`200 OK`):** HTML page.

---

## 2. Client Endpoints (`/{CLIENT_API_PREFIX}/v1`)

These endpoints are designed for external systems that initiate and monitor workflows. Requires `X-Client-Token` header.
The base path for these endpoints is configurable via the `CLIENT_API_PREFIX` environment variable (default is `api`). If set to an empty string, these endpoints will be available at the root (e.g. `/v1/...`).

### Create New Job

-   **Endpoint:** `POST /{CLIENT_API_PREFIX}/v1/{blueprint_api_endpoint}`
-   **Example:** `POST /api/v1/jobs/simple_flow`
-   **Description:** Creates and starts a new instance (Job) of the specified blueprint.
-   **Request Body:**
    ```json
    {
      "initial_data": { ... },
      "webhook_url": "https://callback.url/webhook",
      "dispatch_timeout": 60,
      "result_timeout": 300
    }
    ```
    *   `initial_data` (object, optional): Initial data for the job.
    *   `webhook_url` (string, optional): URL to receive asynchronous notifications.
    *   `dispatch_timeout` (integer, optional): Maximum time in seconds a task can wait in queue.
    *   `result_timeout` (integer, optional): Absolute deadline for the job.
-   **Response (`202 Accepted`):** `{"status": "accepted", "job_id": "..."}`

### Get Job Status

-   **Endpoint:** `GET /api/v1/jobs/{job_id}`
-   **Description:** Returns the current state of the specified job.
-   **Query Parameters:**
    *   `fields` (string, optional): Comma-separated list of fields to return (e.g., `status,current_state`).
-   **Response (`200 OK`):** JSON object with `Job` state.

### Get S3 Upload URL

-   **Endpoint:** `GET /api/v1/jobs/{job_id}/files/upload`
-   **Description:** Generates a temporary S3 presigned URL for uploading a file directly.
-   **Query Parameters:**
    *   `filename` (string, required): Name of the file.
    *   `expires_in` (integer, optional): Link validity in seconds.
-   **Response (`200 OK`):** `{"url": "...", "expires_in": 3600, "method": "PUT"}`

### Upload File (Direct Streaming)

-   **Endpoint:** `PUT /api/v1/jobs/{job_id}/files/content/{filename}`
-   **Description:** Uploads a file directly to S3 via Orchestrator streaming proxy.
-   **Response (`200 OK`):** `{"status": "uploaded", "s3_uri": "..."}`

### Download File (Stable Link)

-   **Endpoint:** `GET /api/v1/jobs/{job_id}/files/download/{filename}`
-   **Description:** A stable link that redirects to a fresh S3 presigned URL.
-   **Response (`302 Found`):** Redirects to the S3 URL.

### Cancel Running Task

- **Endpoint**: `POST /api/v1/jobs/{job_id}/cancel`
- **Description**: Initiates cancellation of a task being executed by a worker.
- **Response (`200 OK`):** `{"status": "cancellation_request_sent"}`

### Get Job History

-   **Endpoint:** `GET /api/v1/jobs/{job_id}/history`
-   **Description:** Returns the full event history for the specified job.
-   **Response (`200 OK`):** Array of event objects.

### Get Blueprint Graph

-   **Endpoint:** `GET /api/v1/blueprints/{blueprint_name}/graph`
-   **Description:** Returns the blueprint structure in DOT format.
-   **Response (`200 OK`):** Text response in `text/vnd.graphviz` format.

### Get Active Workers List

-   **Endpoint:** `GET /api/v1/workers`
-   **Description:** Returns a list of all currently active workers.
-   **Response (`200 OK`):** Array of worker objects.

### Get Dashboard Data

-   **Endpoint:** `GET /api/v1/dashboard`
-   **Description:** Returns aggregated statistics about the system state.
-   **Response (`200 OK`):** JSON object with statistics.

### Get Skill Catalog (Marketplace)

-   **Endpoint:** `GET /api/v1/workers/catalog`
-   **Description:** Returns an aggregated catalog of unique skills. Result is cached for 10 seconds.
-   **Response (`200 OK`):** JSON object with skills.

---

## 3. Internal Endpoints for Workers (`/_worker`)

These endpoints are used by workers to register, receive tasks, and submit results. Requires `X-Worker-Token` header.

### Register Worker

-   **Endpoint:** `POST /_worker/workers/register`
-   **Description:** Registers a worker in the system.
-   **Request Body:** 
    ```json
    {
      "worker_id": "worker-123",
      "supported_skills": [
        {"name": "transcribe", "input_schema": {...}, "output_schema": {...}}
      ],
      "capabilities": {"gpu": true, "vram_gb": 16}
    }
    ```
-   **Response (`200 OK`):** `{"status": "registered"}`

### Heartbeat / Status Update

-   **Endpoint:** `PATCH /_worker/workers/{worker_id}`
-   **Description:** Confirms activity and updates state. Supports Jitter to distribute load.
-   **Response (`200 OK`):** `{"status": "ok", "next_heartbeat_jitter_ms": 1500}`

### Get Next Task (Long-Polling)

-   **Endpoint:** `GET /_worker/workers/{worker_id}/tasks/next`
-   **Description:** Worker requests the next task. Connection is held open if no tasks are available. Uses optimized Task Stealing.
-   **Response (`200 OK`):** JSON object with task data.
-   **Response (`204 No Content`):** Returned on timeout if no new tasks appeared.

### Submit Task Result

-   **Endpoint:** `POST /_worker/tasks/result`
-   **Description:** Worker submits the result of a completed task.
-   **Request Body:**
    ```json
    {
      "job_id": "...",
      "task_id": "...",
      "worker_id": "...",
      "status": "success",
      "data": { ... }
    }
    ```
-   **Response (`200 OK`):** `{"status": "accepted"}`

### Emit Generic Event (Bottom-Up)

-   **Endpoint:** `POST /_worker/events`
-   **Description:** Allows a worker to send signals (progress, alerts).
-   **Response (`200 OK`):** `{"status": "event_accepted"}`

### Establish WebSocket Connection

- **Endpoint**: `GET /_worker/ws/{worker_id}`
- **Description**: Real-time command channel.
- **Protocol**: `WebSocket`
