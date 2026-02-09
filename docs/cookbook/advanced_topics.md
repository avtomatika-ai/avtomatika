**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/advanced_topics.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/advanced_topics.md)

# Cookbook: Advanced Features

This section describes more complex but powerful system features useful in real-world scenarios.

## 1. Connecting Worker to Multiple Orchestrators

To ensure high availability and load distribution, Worker SDK can connect to multiple Orchestrator instances.

-   **Environment Variable:** `ORCHESTRATORS_CONFIG` (replaces `ORCHESTRATOR_URL`). Contains JSON string with address list.
-   **Operation Mode:** `MULTI_ORCHESTRATOR_MODE`.

### `FAILOVER` Mode (High Availability)

This is the default mode. Worker will work with the first Orchestrator in the list. If it becomes unavailable, Worker automatically switches to the next one.

**Example Configuration:**
```dotenv
# Worker will poll 'main-orchestrator'. If it fails,
# SDK automatically switches to 'backup-orchestrator'.
ORCHESTRATORS_CONFIG='[
    {"url": "http://main-orchestrator:8080"},
    {"url": "http://backup-orchestrator:8080"}
]'

MULTI_ORCHESTRATOR_MODE=FAILOVER
```

### `ROUND_ROBIN` Mode (Load Balancing)

In this mode, Worker will sequentially send task requests to each Orchestrator in the list, distributing load.

**Example Configuration:**
```dotenv
ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-1:8080"},
    {"url": "http://orchestrator-2:8080"}
]'

MULTI_ORCHESTRATOR_MODE=ROUND_ROBIN
```

## 2. Advanced Worker Authentication (`workers.toml`)

Instead of using a single shared `WORKER_TOKEN` for all workers, you can assign an individual token to each. This is more secure as it allows granular access revocation.

**1. Create `workers.toml` file** in Orchestrator root:
```toml
# workers.toml
[worker-video-1]
token = "unique-secret-token-for-video-1"

[worker-audio-5]
token = "another-secret-for-audio-5"
```

**2. Specify path to file** in Orchestrator configuration:
```dotenv
# In Orchestrator .env file
WORKERS_CONFIG_PATH="workers.toml"
```

**3. Configure Worker** to use its ID and individual token:
```dotenv
# In Worker .env file
WORKER_ID="worker-video-1"
AVTOMATIKA_WORKER_TOKEN="unique-secret-token-for-video-1"
```
Orchestrator will always first attempt to authenticate worker by its individual token.

## 3. Handling Large Files (S3 Payload Offloading)

Worker SDK supports large file handling via S3-compatible storage out of the box, avoiding Redis overload.

-   **Automatic Download:** If task parameters contain URI like `s3://...`, SDK automatically downloads file and replaces URI with local path before calling your handler.
-   **Automatic Upload:** If your handler returns local file path, SDK automatically uploads it to S3 and replaces path with new `s3://` URI in final result.

To enable, just configure environment variables in worker:
```dotenv
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=worker-results
WORKER_PAYLOAD_DIR=/tmp/payloads # Directory allowed for upload
```

## 4. Debugging Tips

### Checking Task Status and History

Use `curl` to request state and execution history of a task. Helps understand process step and data content.

```bash
# Specify your client token
TOKEN="your-secret-orchestrator-token"

# Get current task state
curl -H "X-Client-Token: $TOKEN" http://localhost:8080/api/v1/jobs/{job_id}

# Get full task event history (very useful for debugging)
curl -H "X-Client-Token: $TOKEN" http://localhost:8080/api/v1/jobs/{job_id}/history
```

### Visualizing Blueprint Logic

To understand complex pipeline logic, you can get its graph in DOT format and visualize it.

**1. Get DOT graph representation:**
```bash
curl -H "X-Client-Token: $TOKEN" http://localhost:8080/api/v1/blueprints/{blueprint_name}/graph
```

**2. Visualize result:**
Copy received text output and paste into online visualizer, e.g., [Graphviz Online](https://dreampuf.github.io/GraphvizOnline/). You'll immediately see state and transition diagram of your blueprint.