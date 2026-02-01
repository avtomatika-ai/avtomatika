# Orchestrator API Reference

This document describes all HTTP endpoints provided by the Orchestrator server. The API is divided into three logical groups: Public, Client, and Internal (for Workers).

## Authentication

-   **Client -> Orchestrator:** All requests to `/api/*` endpoints must contain the `X-Client-Token` header with the client token.
-   **Worker -> Orchestrator:** All requests to `/_worker/*` endpoints must contain the `X-Worker-Token` header with a valid worker token (individual or global).

---

## 1. Public Endpoints (`/_public`)

These endpoints do not require authentication.

### Service Status Check

-   **Endpoint:** `GET /_public/status`
-   **Description:** Returns a simple response confirming that the service is running.
-   **Response (`200 OK`):** `{"status": "ok"}`

### Prometheus Metrics

-   **Endpoint:** `GET /_public/metrics`
-   **Description:** Returns application metrics in a format compatible with Prometheus.
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

## 2. Client Endpoints (`/api`)

These endpoints are designed for external systems that initiate and monitor workflows. Requires `X-Client-Token` header.

### Create New Job

-   **Endpoint:** `POST /api/{api_version}/{blueprint_api_endpoint}`
-   **Example:** `POST /api/v1/jobs/simple_flow`
-   **Description:** Creates and starts a new instance (Job) of the specified blueprint.
-   **Request Body:**
    ```json
    {
      "initial_data": { ... },
      "webhook_url": "https://callback.url/webhook"
    }
    ```
    *   `initial_data` (object, optional): Initial data for the job.
    *   `webhook_url` (string, optional): URL to receive asynchronous notifications about job completion, failure, or quarantine.
-   **Response (`202 Accepted`):** `{"status": "accepted", "job_id": "..."}`

### Get Job Status

-   **Endpoint:** `GET /api/v1/jobs/{job_id}`
-   **Description:** Returns the full current state of the specified job.
-   **Response (`200 OK`):** JSON object with `Job` state.
-   **Response (`404 Not Found`):** If a job with such ID is not found.

### Cancel Running Task

- **Endpoint**: `POST /api/v1/jobs/{job_id}/cancel`
- **Description**: Initiates cancellation of a task being executed by a worker.
- **Response (`200 OK`):** `{"status": "cancellation_request_sent"}` (if via WebSocket) or `{"status": "cancellation_request_accepted"}` (if via Redis flag).

### Get Job History

-   **Endpoint:** `GET /api/v1/jobs/{job_id}/history`
-   **Description:** Returns the full event history for the specified job (if history storage is enabled).
-   **Response (`200 OK`):** Array of event objects.

### Get Blueprint Graph

-   **Endpoint:** `GET /api/v1/blueprints/{blueprint_name}/graph`
-   **Description:** Returns the blueprint structure in DOT format for visualization.
-   **Response (`200 OK`):** Text response in `text/vnd.graphviz` format.

### Get Active Workers List

-   **Endpoint:** `GET /api/v1/workers`
-   **Description:** Returns a list of all currently active workers.
-   **Response (`200 OK`):** Array of objects with worker information.

### Get Dashboard Data

-   **Endpoint:** `GET /api/v1/dashboard`
-   **Description:** Returns aggregated statistics about the system state.
-   **Response (`200 OK`):** JSON object with statistics.

---

## 3. Internal Endpoints for Workers (`/_worker`)

These endpoints are used by workers to register, receive tasks, and submit results. Requires `X-Worker-Token` header.

### Register Worker

-   **Endpoint:** `POST /_worker/workers/register`
-   **Description:** Registers a worker in the system.
-   **Request Body:** JSON object with full worker description (ID, supported tasks, resources, etc.).
    ```json
    {
      "worker_id": "worker-123",
      "supported_tasks": ["video_processing", "audio_transcription"]
    }
    ```
-   **Response (`200 OK`):** `{"status": "registered"}`

### Heartbeat / Status Update

-   **Endpoint:** `PATCH /_worker/workers/{worker_id}`
-   **Description:** Universal endpoint for confirming activity and updating state.
    -   **Empty Body:** Acts as a lightweight "ping", only updates worker TTL.
    -   **Request Body (JSON):** Updates worker data (status, load, available tasks) and updates TTL.
-   **Response (`200 OK`):** `{"status": "ttl_refreshed"}` or JSON with updated worker state.

### Get Next Task (Long-Polling)

-   **Endpoint:** `GET /_worker/workers/{worker_id}/tasks/next`
-   **Description:** Worker requests the next task. Connection is held open if no tasks are available.
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
      "result": {
        "status": "success",
        "data": { "output": "..." },
        "error": null
      }
    }
    ```
-   **Response (`200 OK`):** `{"status": "result_accepted_success"}`

### Establish WebSocket Connection

- **Endpoint**: `GET /_worker/ws/{worker_id}`
- **Description**: Establishes a WebSocket connection to receive real-time commands from the orchestrator (e.g., `cancel_task`).
- **Protocol**: `WebSocket`