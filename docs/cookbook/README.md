**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/README.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/README.md)

# Cookbook: Recipes for Orchestrator Library

This cookbook provides a collection of recipes for building workflows (blueprints) with the Avtomatika Orchestrator.

> **Production Note:** The examples below use `print()` for simplicity and clarity. In a production environment, you should use the standard Python `logging` module. The Orchestrator automatically configures structured JSON logging, and using `logging.info(...)` ensures your logs are properly formatted and captured.

## Table of Contents

### **Recipe 1: Creating a Simple Linear Pipeline**

**Task:** Create a pipeline that sequentially executes three steps: A -> B -> C.
```python
from orchestrator.blueprint import StateMachineBlueprint

simple_pipeline = StateMachineBlueprint(
    name="simple_linear_flow",
    api_version="v1",
    api_endpoint="jobs/simple_flow"
)

@simple_pipeline.handler_for("start", is_start=True)
async def start_handler(context, actions):
    actions.transition_to("step_A")

@simple_pipeline.handler_for("step_A")
async def handler_A(context, actions):
    actions.dispatch_task(
        task_type="simple_task",
        params=context.initial_data,
        transitions={"success": "step_B", "failure": "failed"}
    )

@simple_pipeline.handler_for("step_B")
async def handler_B(context, actions):
    actions.transition_to("finished")

@simple_pipeline.handler_for("finished", is_end=True)
async def finished_handler(context, actions):
    print(f"Pipeline {context.job_id} completed successfully.")

@simple_pipeline.handler_for("failed", is_end=True)
async def failed_handler(context, actions):
    print(f"Pipeline {context.job_id} failed.")
```

### **Recipe 2: Implementing "Human-in-the-Loop" (Moderation)**

**Task:** Pause the pipeline after `generate_data` step and wait for approval from a moderator.
```python
from orchestrator.blueprint import StateMachineBlueprint

moderation_pipeline = StateMachineBlueprint(
    name="moderation_flow",
    api_version="v1",
    api_endpoint="jobs/moderation_flow"
)

@moderation_pipeline.handler_for("generate_data", is_start=True)
async def generate_data_handler(context, actions):
    actions.dispatch_task(
        task_type="data_generation",
        params=context.initial_data,
        transitions={"success": "awaiting_approval", "failure": "failed"}
    )


@moderation_pipeline.handler_for("process_approved_data", is_end=True)
async def process_approved_handler(context, actions):
    actions.transition_to("finished")

@moderation_pipeline.handler_for("rejected_by_moderator", is_end=True)
async def process_rejected_handler(context, actions):
    print(f"Job {context.job_id} was rejected by moderator.")

@moderation_pipeline.handler_for("failed", is_end=True)
async def moderation_failed_handler(context, actions):
    print(f"Job {context.job_id} failed.")

@moderation_pipeline.handler_for("awaiting_approval")
async def await_approval_handler(context, actions):
    actions.await_human_approval(
        integration="telegram",
        message=f"Approval required for Job {context.job_id}",
        transitions={
            "approved": "process_approved_data",
            "rejected": "rejected_by_moderator"
        }
    )

@moderation_pipeline.handler_for("process_approved_data")
async def process_approved_handler(context, actions):
    actions.transition_to("finished")
```

### **Recipe 3: Parallel Execution and Result Aggregation**

**Task:** Run multiple independent tasks (e.g., processing different parts of one file) simultaneously and, after waiting for all to complete, collect their results into a single summary.

**Concept:**
Parallelism is achieved by calling `actions.dispatch_task()` multiple times in one handler.
1.  **Launch:** In a regular handler, call `dispatch_task` for each parallel task. **Key requirement:** all these calls must point to the same state in `transitions`. This state will be the aggregation point.
2.  **Aggregation:** The handler for the aggregator state is marked with the special decorator `@blueprint.aggregator_for(...)`. The Orchestrator will not call this handler until **all** tasks leading to this state are completed.
3.  **Accessing Results:** Inside the aggregator handler, results of all parallel tasks are available in `context.aggregation_results`. This is a dictionary where the key is `task_id` and the value is the result returned by the worker.

```python
from orchestrator.blueprint import StateMachineBlueprint
import logging

logger = logging.getLogger(__name__)

parallel_bp = StateMachineBlueprint(
    name="parallel_flow_example",
    api_version="v1",
    api_endpoint="/jobs/parallel_example"
)

@parallel_bp.handler_for("start", is_start=True)
async def start_parallel_tasks(context, actions):
    """
    This handler starts two tasks that will run in parallel.
    Both tasks upon successful completion will transition the process to 'aggregate_results' state.
    """
    logger.info(f"Job {context.job_id}: Starting parallel tasks.")

    # Start task A
    actions.dispatch_task(
        task_type="task_A",
        params={"input": "data_for_A"},
        transitions={"success": "aggregate_results", "failure": "failed"}
    )

    # Start task B
    actions.dispatch_task(
        task_type="task_B",
        params={"input": "data_for_B"},
        transitions={"success": "aggregate_results", "failure": "failed"}
    )

@parallel_bp.aggregator_for("aggregate_results")
async def aggregate_results_handler(context, actions):
    """
    This handler will be called only after both task_A and task_B complete successfully.
    """
    logger.info(f"Job {context.job_id}: Aggregating results.")

    processed_results = {}
    # context.aggregation_results contains results of both tasks
    for task_id, result in context.aggregation_results.items():
        if result.get("status") == "success":
            processed_results[task_id] = result.get("data")

    context.state_history["aggregated_data"] = processed_results
    logger.info(f"Job {context.job_id}: Aggregated data: {processed_results}")
    actions.transition_to("end")

@parallel_bp.handler_for("end", is_end=True)
async def end_flow(context, actions):
    final_data = context.state_history.get("aggregated_data")
    logger.info(f"Job {context.job_id}: Process complete. Final data: {final_data}")

@parallel_bp.handler_for("failed", is_end=True)
async def failed_handler(context, actions):
    logger.error(f"Job {context.job_id} failed.")

```
*Note: If at least one of the parallel tasks fails (transitions to `failed` state), the aggregator handler will not be called, and the entire `Job` will immediately transition to `failed` state.*

### **Recipe 4: Configuring Worker for Multiple Orchestrators**

**Task:** Ensure high availability and/or load balancing by configuring a single Worker to connect to multiple Orchestrator instances.

**Concept:**
`worker_sdk` supports two modes for working with multiple Orchestrators, configured via environment variables. The worker will automatically register and send heartbeats to all Orchestrators in the list.

-   `FAILOVER` (default): Worker polls Orchestrators in the order they appear in configuration. If the main Orchestrator becomes unavailable, Worker automatically switches to the next one in the list.
-   `ROUND_ROBIN`: Worker polls each Orchestrator in the list sequentially, allowing load distribution.

**How to configure:**
1.  **`ORCHESTRATORS_CONFIG`**: Instead of `ORCHESTRATOR_URL`, use this variable to pass a JSON string describing all available Orchestrators.
2.  **`MULTI_ORCHESTRATOR_MODE`**: Set value to `FAILOVER` or `ROUND_ROBIN`.

#### **Example 1: Configuration for High Availability (Failover)**

```bash
# Worker will poll 'orchestrator-main'.
# If it goes down, Worker automatically switches to 'orchestrator-backup'.
export ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-main:8080"},
    {"url": "http://orchestrator-backup:8080"}
]'

export MULTI_ORCHESTRATOR_MODE="FAILOVER"

# Run worker
python -m your_worker_module
```

#### **Example 2: Configuration for Load Balancing (Round Robin)**
```bash
# Worker will poll 'orchestrator-1' and 'orchestrator-2' sequentially.
export ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-1:8080"},
    {"url": "http://orchestrator-2:8080"}
]'

export MULTI_ORCHESTRATOR_MODE="ROUND_ROBIN"

# Run worker
python -m your_worker_module
```
*Note: This configuration is done exclusively on the Worker side and is completely transparent to the Orchestrator. Each Orchestrator sees this Worker as a normal, registered executor.*

### **Recipe 5: Conditional Routing with .when()**

```python
from orchestrator.blueprint import StateMachineBlueprint

multilingual_pipeline = StateMachineBlueprint(
    name="multilingual_flow",
    api_version="v1",
    api_endpoint="jobs/multilingual_flow"
)

@multilingual_pipeline.handler_for("start", is_start=True)
async def start_multilingual(context, actions):
    # This step just passes control further, where conditional logic will trigger
    actions.transition_to("process_text")

@multilingual_pipeline.handler_for("process_text").when("context.initial_data.language == 'en'")
async def process_english_text(context, actions):
    actions.dispatch_task(task_type="process_en", params=context.initial_data, transitions={"success": "finished"})

@multilingual_pipeline.handler_for("process_text").when("context.initial_data.language == 'de'")
async def process_german_text(context, actions):
    actions.dispatch_task(task_type="process_de", params=context.initial_data, transitions={"success": "finished"})

@multilingual_pipeline.handler_for("finished", is_end=True)
async def multilingual_finished(context, actions):
    print(f"Job {context.job_id} finished processing.")
```

### **Recipe 6: Choosing Task Dispatch Strategy**

**Task:** For a critical task, use not just a random worker, but the least loaded one.

```python
from orchestrator.blueprint import StateMachineBlueprint

critical_pipeline = StateMachineBlueprint(
    name="critical_flow",
    api_version="v1",
    api_endpoint="jobs/critical_flow"
)

@critical_pipeline.handler_for("start", is_start=True)
async def start_critical_task(context, actions):
    actions.dispatch_task(
        task_type="critical_computation",
        params=context.initial_data,
        # Specify worker selection strategy
        dispatch_strategy="least_connections",
        transitions={"success": "finished"}
    )

@critical_pipeline.handler_for("finished", is_end=True)
async def critical_finished(context, actions):
    print(f"Critical task {context.job_id} finished.")
```
*Note: Available strategies: `default`, `round_robin`, `least_connections`.*

### **Recipe 7: Managing Task Priority**

**Task:** System has normal tasks and urgent ones that must be executed first, even if they arrived later.

**Solution:** The `dispatch_task` method accepts a `priority` parameter, which is a float. Higher value means higher priority.

```python
from orchestrator.blueprint import StateMachineBlueprint

priority_pipeline = StateMachineBlueprint(
    name="priority_flow",
    api_version="v1",
    api_endpoint="jobs/priority_flow"
)

@priority_pipeline.handler_for("start", is_start=True)
async def start_priority_task(context, actions):
    # Assign different priority depending on input data
    is_urgent = context.initial_data.get("is_urgent", False)

    actions.dispatch_task(
        task_type="computation",
        params=context.initial_data,
        # Urgent tasks get priority 10, others - 0
        priority=10.0 if is_urgent else 0.0,
        transitions={"success": "finished"}
    )

@priority_pipeline.handler_for("finished", is_end=True)
async def priority_finished(context, actions):
    print(f"Task {context.job_id} finished.")

```
*Note: If multiple tasks have the same priority, they will be executed in arrival order (FIFO) within that priority.*


### **Recipe 8: Cost Optimization with `cheapest` and `max_cost`**

**Task:** Dispatch task to the cheapest worker, but only if its cost does not exceed a certain threshold.

```python
from orchestrator.blueprint import StateMachineBlueprint

cost_optimized_pipeline = StateMachineBlueprint(
    name="cost_optimized_flow",
    api_version="v1",
    api_endpoint="jobs/cost_optimized_flow"
)

@cost_optimized_pipeline.handler_for("start", is_start=True)
async def start_cost_optimized_task(context, actions):
    actions.dispatch_task(
        task_type="image_compression",
        params=context.initial_data,
        # Select cheapest worker
        dispatch_strategy="cheapest",
        # But only if its cost ($/sec) is no more than 0.05
        max_cost=0.05,
        transitions={"success": "finished", "failure": "too_expensive"}
    )

@cost_optimized_pipeline.handler_for("finished", is_end=True)
async def cost_optimized_finished(context, actions):
    print("Job finished with cost optimization.")

@cost_optimized_pipeline.handler_for("too_expensive", is_end=True)
async def cost_optimized_failed(context, actions):
    print("Job failed because no workers met the cost criteria.")
```
*Note: `cheapest` strategy uses worker's `cost_per_second` field. If no worker meets `max_cost`, pipeline won't find an executor and will fail.*

### **Recipe 9: Using Data Stores (`data_stores`)**

**Task:** Use shared persistent storage (`data_store`) to exchange data between different states or even different runs of the same pipeline. For example, to implement a counter or cache.

**Concept:**
1.  **Initialization:** When creating a blueprint, you can "attach" one or more data stores to it. Each store is essentially a Redis wrapper providing key-value access.
2.  **Access in Handlers:** Inside any handler of this blueprint, you can access these stores via `context.data_stores`.
3.  **Persistence:** Data in `data_store` persists between handler calls and even between different `job_id` of the same blueprint.

```python
from orchestrator.blueprint import StateMachineBlueprint

# 1. Create blueprint and add data_store named 'request_counter' to it.
#    We can also set initial values.
analytics_bp = StateMachineBlueprint(
    name="analytics_flow",
    api_version="v1",
    api_endpoint="jobs/analytics"
)
analytics_bp.add_data_store("request_counter", {"total_requests": 0})


@analytics_bp.handler_for("start", is_start=True)
async def count_request(context, actions):
    # 2. Access store via context and increment counter.
    #    Operations are atomic as Redis is single-threaded.
    current_count = await context.data_stores.request_counter.get("total_requests")
    new_count = current_count + 1
    await context.data_stores.request_counter.set("total_requests", new_count)

    # Save current counter value to history of this specific job
    context.state_history["request_number"] = new_count

    actions.transition_to("finished")

@analytics_bp.handler_for("finished", is_end=True)
async def show_result(context, actions):
    request_num = context.state_history.get("request_number")
    print(f"Job {context.job_id} processed. Request number {request_num}.")

```

**How it works:**

-   `analytics_bp.add_data_store("request_counter", ...)` creates an `AsyncDictStore` instance that lives as long as the Orchestrator lives.
-   `context.data_stores.request_counter` provides access to this instance. `data_stores` is a dynamic object whose attributes correspond to created store names.
-   Every time you run this pipeline (`/v1/jobs/analytics`), it will increment **the same** counter because `data_store` is bound to blueprint, not specific `job_id`.

### **Recipe 10: Cancelling a Running Task**

**Task:** Run a long-running task (e.g., video processing) and be able to cancel its execution via API.

**Concept:**
System supports hybrid cancellation mechanism working with or without WebSocket.
1.  **Cancellation Request:** You send `POST` request to API endpoint `/api/v1/jobs/{job_id}/cancel`.
2.  **Flag Setting:** Orchestrator immediately sets a flag in Redis signaling cancellation request.
3.  **Push Notification (WebSocket):** If Worker is connected via WebSocket, Orchestrator additionally sends `cancel_task` command for immediate reaction.
4.  **Pull Check (Redis):** If Worker doesn't use WebSocket or connection is temporarily lost, it must periodically check for flag in Redis using `worker.check_for_cancellation(task_id)`.
5.  **Reaction:** Upon receiving cancellation signal (by any method), Worker must interrupt work and return result with `"cancelled"` status.
6.  **Completion:** Pipeline transitions to state specified in `transitions` for `"cancelled"` status.

#### **Step 1: Worker Code**
Worker must periodically call `worker.check_for_cancellation`.

```python
# my_worker.py
@worker.task("long_video_processing")
async def process_video(params: dict, task_id: str, job_id: str) -> dict:
    total_frames = 1000
    for frame in range(total_frames):
        # ... frame processing logic ...
        await asyncio.sleep(0.1) # Simulate work

        # Check for stop every 100 frames
        if frame % 100 == 0:
            if await worker.check_for_cancellation(task_id):
                print(f"Task {task_id} cancelled.")
                return {"status": "cancelled"}

    return {"status": "success"}
```

#### **Step 2: Blueprint Creation**
Blueprint must have transition for new `cancelled` status.
```python
from orchestrator.blueprint import StateMachineBlueprint

cancellable_pipeline = StateMachineBlueprint(
    name="cancellable_flow",
    api_version="v1",
    api_endpoint="jobs/cancellable"
)

@cancellable_pipeline.handler_for("start", is_start=True)
async def start_long_task(context, actions):
    actions.dispatch_task(
        task_type="long_video_processing",
        params=context.initial_data,
        transitions={
            "success": "finished_successfully",
            "failure": "task_failed",
            "cancelled": "task_cancelled" # New transition
        }
    )

@cancellable_pipeline.handler_for("finished_successfully", is_end=True)
async def success_handler(context, actions):
    print(f"Job {context.job_id} finished successfully.")

@cancellable_pipeline.handler_for("task_failed", is_end=True)
async def failure_handler(context, actions):
    print(f"Job {context.job_id} failed.")

@cancellable_pipeline.handler_for("task_cancelled", is_end=True)
async def cancelled_handler(context, actions):
    print(f"Job {context.job_id} successfully cancelled.")
```

#### **Step 3: Run Job**
```bash
# Run Job and get its ID
curl -X POST http://localhost:8080/api/v1/jobs/cancellable \
-H "Content-Type: application/json" \
-H "X-Client-Token: your-secret-orchestrator-token" \
-d '{"video_url": "..."}'
# Response: {"status": "accepted", "job_id": "YOUR_JOB_ID"}
```

#### **Step 4: Cancel Job**
```bash
# Send cancellation request using received job_id
curl -X POST http://localhost:8080/api/v1/jobs/YOUR_JOB_ID/cancel \
-H "X-Client-Token: your-secret-orchestrator-token"
```
You will see cancellation message in Worker logs, and `Job` in Orchestrator will transition to `task_cancelled` state.

### **Recipe 11: Sending Task Progress**

**Task:** For a long-running task (e.g., model training), regularly report progress to be tracked in UI.

**Concept:**
Like cancellation, this feature works via WebSocket. Worker can send progress events, which Orchestrator saves in `state_history` of the corresponding `Job`.

#### **Step 1: Code in Worker**
Worker must periodically call `worker.send_progress()`.
```python
# Inside your worker file (my_worker.py)
@worker.task("train_model")
async def train_model_handler(params: dict, task_id: str, job_id: str) -> dict:
    for epoch in range(10):
        # ... training logic ...
        await asyncio.sleep(5)
        # Send progress after each epoch
        await worker.send_progress(
            task_id=task_id,
            job_id=job_id,
            progress=(epoch + 1) / 10,
            message=f"Epoch {epoch + 1} completed"
        )
    return {"status": "success"}
```

#### **Step 2: Verification in Orchestrator**
After task execution, you can request status and see `progress_updates` in `state_history`:
```json
{
  "id": "YOUR_JOB_ID",
  "current_state": "finished",
  "state_history": {
    "progress_updates": [
      {"progress": 0.1, "message": "Epoch 1 completed", "timestamp": "..."},
      {"progress": 0.2, "message": "Epoch 2 completed", "timestamp": "..."}
    ]
  }
}
```

### **Recipe 12a: Working with S3 Files in Orchestrator (TaskFiles)**

**Task:** Read a configuration file from S3 inside an Orchestrator handler to decide routing, or write a small result file back to S3.

**Concept:**
When S3 support is enabled in the Orchestrator, you can request the `task_files` argument in your handlers. This object provides helper methods to interact with the job's S3 folder (`jobs/{job_id}/`) without managing connections manually.

**Prerequisite:**
- Orchestrator configured with `S3_ENDPOINT_URL`, etc.
- Dependencies installed: `pip install avtomatika[s3]`

```python
from orchestrator.blueprint import StateMachineBlueprint

s3_ops_bp = StateMachineBlueprint(
    name="s3_ops_flow",
    api_version="v1",
    api_endpoint="jobs/s3_ops"
)

@s3_ops_bp.handler_for("check_config", is_start=True)
async def check_config_handler(context, task_files, actions):
    """
    Downloads 'config.json' from S3 (jobs/{job_id}/config.json),
    reads it, and decides next step.
    """
    if not task_files:
        # S3 might be disabled in config
        actions.transition_to("failed")
        return

    try:
        # read_json automatically downloads file if not present locally
        config = await task_files.read_json("config.json")
    except Exception:
        actions.transition_to("config_missing")
        return

    if config.get("mode") == "fast":
        actions.transition_to("fast_processing")
    else:
        actions.transition_to("deep_processing")

@s3_ops_bp.handler_for("fast_processing")
async def fast_process(context, task_files, actions):
    # ... logic ...
    
    # Write result back to S3
    await task_files.write_text("result.txt", "Done fast.")
    
    # Or upload an entire directory recursively
    # await task_files.download("dataset/") # Downloads s3://.../dataset/ to local
    # await task_files.upload("output_folder") # Uploads local folder to s3://.../output_folder/

    actions.transition_to("finished")
```

### **Recipe 12b: Working with Large Files via S3 (Worker Side)**

**Task:** Process a large video file. Passing it directly in JSON is impractical, so we'll use S3 for data exchange.

**Concept:**
Worker SDK has built-in S3 support. If task parameters (`params`) contain a value starting with `s3://`, SDK automatically downloads file to temp directory and substitutes URI with local path. Similarly, if your handler returns local file path, SDK uploads it to S3 and returns `s3://` URI to Orchestrator.

**Prerequisites:**
- Install `aioboto3` dependency: `pip install orchestrator-worker[s3]`
- Configure environment variables for S3 access:
  ```bash
  export S3_ENDPOINT_URL="http://your-s3-host:9000"
  export S3_ACCESS_KEY_ID="your-access-key"
  export S3_SECRET_ACCESS_KEY="your-secret-key"
  export S3_BUCKET_NAME="my-processing-bucket"
  ```

#### **Step 1: Code in Worker**
Worker doesn't need to know about S3. It just works with local files.

```python
# my_video_worker.py
import os
from pathlib import Path

@worker.task("process_video_from_s3")
async def process_video(params: dict, **kwargs) -> dict:
    # SDK already downloaded file and passed local path
    local_video_path = Path(params["video_path"])

    if not local_video_path.exists():
        return {"status": "failure", "error_type": "INVALID_INPUT_ERROR", "error": "Input file not found"}

    # ... video processing logic ...
    # Create output file
    output_path = local_video_path.parent / f"processed_{local_video_path.name}"
    with open(output_path, "w") as f:
        f.write("processed video data")

    # Just return local path. SDK will upload it to S3 itself.
    return {"status": "success", "data": {"processed_video_path": str(output_path)}}
```

#### **Step 2: Blueprint Creation**
Blueprint simply passes S3 URI as parameter.
```python
@s3_pipeline.handler_for("start", is_start=True)
async def start_s3_task(context, actions):
    actions.dispatch_task(
        task_type="process_video_from_s3",
        # Pass S3 URI
        params={"video_path": context.initial_data.get("s3_uri")},
        transitions={"success": "finished"}
    )
```

#### **Step 3: Run Task**
```bash
curl -X POST ... -d '{"s3_uri": "s3://my-bucket/raw_videos/movie.mp4"}'
```
After execution, task `state_history` will contain result with new S3 URI, e.g.: `{"processed_video_path": "s3://my-processing-bucket/processed_movie.mp4"}`.

### **Recipe 13: Retry Logic Management with Error Types**

**Task:** Create a worker distinguishing temporary failures (e.g., external API unavailability) from permanent errors (e.g., invalid input format).

**Concept:**
Orchestrator defaults to retrying any failed task (`TRANSIENT_ERROR`). However, if Worker returns error type `PERMANENT_ERROR` or `INVALID_INPUT_ERROR`, Orchestrator won't waste time retrying.

- `PERMANENT_ERROR`: Task immediately moved to quarantine for manual analysis.
- `INVALID_INPUT_ERROR`: Task immediately marked as `failed`.

#### **Step 1: Code in Worker**
```python
# my_api_worker.py
@worker.task("fetch_external_data")
async def fetch_data(params: dict, **kwargs) -> dict:
    api_key = params.get("api_key")
    if not api_key:
        # Invalid input, pointless to retry
        return {"status": "failure", "error_type": "INVALID_INPUT_ERROR", "error": "API key is missing"}

    try:
        # ... attempt external API call ...
        response = await call_flaky_api(api_key)
        return {"status": "success", "data": response}
    except APITimeoutError:
        # API temporarily unavailable, worth retrying
        return {"status": "failure", "error_type": "TRANSIENT_ERROR", "error": "API timed out"}
    except APIAuthError:
        # API key invalid, permanent problem
        return {"status": "failure", "error_type": "PERMANENT_ERROR", "error": "Invalid API key"}

```

#### **Step 2: Blueprint Creation**
Blueprint can have different branches for different outcomes.
```python
@error_handling_bp.handler_for("start", is_start=True)
async def start_api_call(context, actions):
    actions.dispatch_task(
        task_type="fetch_external_data",
        params=context.initial_data,
        transitions={
            "success": "finished_successfully",
            "failure": "handle_failure" # Common handler for all errors
        }
    )

@error_handling_bp.handler_for("handle_failure", is_end=True)
async def failure_handler(context, actions):
    # Here we can analyze `job_state` to understand if task was quarantined or just failed.
    job_state = await context.storage.get_job_state(context.job_id)
    if job_state.get("status") == "quarantined":
        print(f"Job {context.job_id} quarantined due to a permanent error.")
    else:
        print(f"Job {context.job_id} failed.")
```

### **Recipe 14: Nested Blueprints (Sub-blueprints)**

**Task:** Create a main pipeline that uses another reusable pipeline as a step and gets its result.

**Concept:**
Nested blueprint mechanism allows one pipeline (parent) to run another (child) and wait for its completion. Child blueprint result (success or failure) is automatically saved in `state_history` of parent pipeline. This allows creating complex yet modular and reusable workflows.

```python
from orchestrator.blueprint import StateMachineBlueprint

# 1. First define small, reusable blueprint.
#    It has no API endpoint, can only be run from another blueprint.
text_processing_bp = StateMachineBlueprint(name="text_processor")

@text_processing_bp.handler_for("start", is_start=True)
async def process(context, actions):
    # ... some text processing logic ...
    text = context.initial_data.get("text", "")
    if not text:
        # If input invalid, end blueprint with failure.
        actions.transition_to("failed")
    else:
        # Result can be saved in `state_history` of child blueprint.
        # Although not passed directly, it's available for debugging in history.
        context.state_history["processed_text"] = text.upper()
        actions.transition_to("finished")

@text_processing_bp.handler_for("finished", is_end=True)
async def text_processing_finished(context, actions):
    pass

@text_processing_bp.handler_for("failed", is_end=True)
async def text_processing_failed(context, actions):
    pass


# 2. Now create main pipeline calling it.
main_pipeline = StateMachineBlueprint(
    name="main_flow",
    api_version="v1",
    api_endpoint="jobs/main_flow"
)

@main_pipeline.handler_for("start", is_start=True)
async def parent_start(context, actions):
    actions.transition_to("process_user_text")

@main_pipeline.handler_for("process_user_text")
async def main_handler(context, actions):
    user_text = context.initial_data.get("raw_text", "")
    actions.run_blueprint(
        blueprint_name="text_processor",
        initial_data={"text": user_text},
        transitions={"success": "final_step", "failure": "sub_job_failed"}
    )

@main_pipeline.handler_for("final_step")
async def final_step_handler(context, actions):
    # Child blueprint result saved in `state_history`.
    # Key generated automatically. We can find it by iterating keys.
    sub_job_result = None
    for key, value in context.state_history.items():
        if key.startswith("sub_job_") and "outcome" in value:
            sub_job_result = value
            break

    if sub_job_result:
        print(f"Sub-blueprint finished with outcome: {sub_job_result['outcome']}")
    else:
        print("Sub-blueprint result not found in state history.")

    actions.transition_to("finished")

@main_pipeline.handler_for("sub_job_failed")
async def sub_job_failed_handler(context, actions):
    print("Sub-blueprint failed, handling failure in parent.")
    actions.transition_to("failed")

@main_pipeline.handler_for("finished", is_end=True)
async def main_finished(context, actions):
    pass

@main_pipeline.handler_for("failed", is_end=True)
async def main_failed(context, actions):
    pass
```

### **Recipe 15: Blueprint Logic Visualization**

**Task:** Analyze or document complex pipeline by automatically generating its visual scheme.

**Solution:** Each `StateMachineBlueprint` object has built-in `.render_graph()` method using `graphviz` to create state and transition diagram. It analyzes your handlers' code to find `actions.transition_to()` and `actions.dispatch_task()` calls and builds graph based on them.

**Prerequisite:**
**Graphviz** must be installed in your system for this feature to work.
-   **Debian/Ubuntu:** `sudo apt-get install graphviz`
-   **macOS (Homebrew):** `brew install graphviz`
-   **Windows:** Install from official site and add to `PATH`.

**Example:**
```python
from orchestrator.blueprint import StateMachineBlueprint

# Take conditional logic pipeline from other recipe
conditional_pipeline = StateMachineBlueprint(name="conditional_flow")

@conditional_pipeline.handler_for("start", is_start=True)
async def start(context, actions):
    actions.transition_to("process_data")

@conditional_pipeline.handler_for("process_data").when("context.initial_data.type == 'A'")
async def process_a(context, actions):
    actions.dispatch_task(task_type="task_a", transitions={"success": "finished", "failure": "failed"})

@conditional_pipeline.handler_for("process_data").when("context.initial_data.type == 'B'")
async def process_b(context, actions):
    actions.dispatch_task(task_type="task_b", transitions={"success": "finished", "failure": "failed"})

@conditional_pipeline.handler_for("finished", is_end=True)
async def finished(context, actions):
    print("Finished.")

@conditional_pipeline.handler_for("failed", is_end=True)
async def failed(context, actions):
    print("Failed.")

# Now generate its diagram
if __name__ == "__main__":
    # This command creates 'conditional_flow_diagram.png' in current directory
    conditional_pipeline.render_graph("conditional_flow_diagram", format="png")
```
Running this script creates image clearly showing all possible execution paths in this blueprint.

### **Recipe 16: Partial Worker State Update (PATCH)**

**Task:** Worker needs to update only one field in its state (e.g., current load) without sending all metadata (ID, capabilities, cost, etc.).

**Solution:** Orchestrator API supports `PATCH` method for `/_worker/workers/{worker_id}/status` endpoint, perfect for partial updates. Saves traffic and follows REST best practices.

```python
# Example worker code sending only changed data
async def update_load(session, new_load):
    await session.patch(
        "http://orchestrator.host/_worker/workers/my-worker-id/status",
        json={"load": new_load}
    )
```

### **Recipe 17: Quota and Client Configuration Management**

**Task:** Configure different usage limits (quotas) for different clients and use their custom parameters inside blueprints.

**Concept:**
1.  **Configuration:** All client info including token, plan, and quotas is defined in `clients.toml`.
2.  **Load on Startup:** At startup, Orchestrator reads this file and loads quota data into Redis.
3.  **Quota Check:** Special Quota Middleware automatically checks and decrements "attempts" counter for each API request. If attempts exhausted, request rejected with `429 Too Many Requests`.
4.  **Parameter Access:** Inside blueprint handler, you can access all static client parameters (e.g., plan or language list) via `context.client.config`.

#### **Step 1: `clients.toml` Configuration**
```toml
[client_premium]
token = "user_token_vip"
plan = "premium"
monthly_attempts = 100000
languages = ["en", "de", "fr"]

[client_free]
token = "user_token_free"
plan = "free"
monthly_attempts = 100
languages = ["en"]
```

#### **Step 2: Usage in Blueprint**
```python
from orchestrator.blueprint import StateMachineBlueprint

premium_features_bp = StateMachineBlueprint(
    name="premium_flow",
    api_version="v1",
    api_endpoint="jobs/premium_flow"
)

@premium_features_bp.handler_for("start", is_start=True)
async def start_premium_flow(context, actions):
    # Get configuration of client who made request
    client_config = context.client.config

    # Use client parameters for conditional logic
    if client_config.get("plan") == "premium":
        # Use more powerful worker for premium clients
        actions.dispatch_task(
            task_type="high_quality_generation",
            params=context.initial_data,
            transitions={"success": "finished"}
        )
    elif "en" in client_config.get("languages", []):
         # Standard worker for free English-speaking clients
        actions.dispatch_task(
            task_type="standard_quality_generation",
            params=context.initial_data,
            transitions={"success": "finished"}
        )
    else:
        # Error for others
        actions.transition_to("failed", error_message="Language not supported for your plan")

@premium_features_bp.handler_for("finished", is_end=True)
async def premium_finished(context, actions):
    pass

@premium_features_bp.handler_for("failed", is_end=True)
async def premium_failed(context, actions):
    pass
```

### **Recipe 18: Best Practices for `is_start` and `is_end` Handlers**

**Task:** Understand what code is best placed in initial and final handlers to create clean and reliable pipelines.

#### **Purpose of `is_start=True` Handler**

Initial handler is "entry gate" of your pipeline. Ideal place for:
1.  **Validation and Data Prep:** Verify all necessary data present and prepare `state_history` for subsequent steps.
2.  **Initial Routing:** Decide first real step based on input data.

**Example:**
```python
from orchestrator.blueprint import StateMachineBlueprint

validation_bp = StateMachineBlueprint(
    name="validation_example",
    api_version="v1",
    api_endpoint="jobs/validation"
)

@validation_bp.handler_for("validate_input", is_start=True)
async def validate_input_handler(context, actions):
    """
    Validates input data and decides where to direct process.
    """
    user_id = context.initial_data.get("user_id")
    document_type = context.initial_data.get("document_type")

    if not user_id or not document_type:
        actions.transition_to("invalid_input_failed")
        return

    # Save validated data to state_history for use in other handlers
    context.state_history["validated_user"] = user_id
    context.state_history["doc_type"] = document_type

    # Routing based on document type
    if document_type == "invoice":
        actions.transition_to("process_invoice")
    else:
        actions.transition_to("process_other_document")

# ... (other handlers) ...

@validation_bp.handler_for("invalid_input_failed", is_end=True)
async def invalid_input_handler(context, actions):
    print(f"Job {context.job_id} failed due to invalid input.")

```

#### **Purpose of `is_end=True` Handlers**

Final handlers are "exit" of your pipeline. They perform final actions and **must not** contain calls to `actions.transition_to()` or `dispatch_task()`.

**Key Uses:**
1.  **Final Logging and Notification:** Log result, send email or Slack message.
2.  **Resource Cleanup:** Delete temporary files created during process.
3.  **Handling Outcomes:** You can have multiple end states for different results (success, failure, rejection, etc.).

**Example:**
```python
from orchestrator.blueprint import StateMachineBlueprint
import os

finalization_bp = StateMachineBlueprint(name="finalization_example")

# ... (intermediate steps leading to different outcomes) ...

@finalization_bp.handler_for("cleanup_and_notify_success", is_end=True)
async def success_handler(context, actions):
    """
    Executed on successful completion.
    """
    temp_file = context.state_history.get("temp_file_path")
    if temp_file and os.path.exists(temp_file):
        os.remove(temp_file)
        print(f"Job {context.job_id}: Temporary file {temp_file} deleted.")

    # send_success_email(context.initial_data.get("user_email"))
    print(f"Job {context.job_id} finished successfully. Notification sent.")


@finalization_bp.handler_for("handle_rejection", is_end=True)
async def rejection_handler(context, actions):
    """
    Executed if process was rejected.
    """
    rejection_reason = context.state_history.get("rejection_reason", "No reason provided")
    print(f"Job {context.job_id} was rejected. Reason: {rejection_reason}")
    # send_rejection_notification(context.initial_data.get("user_email"), rejection_reason)

```
Using `is_start` and `is_end` this way makes your pipelines more structured, reliable, and easier to understand.

### **Recipe 19: Routing Tasks Based on Resource Requirements**

**Task:** Video generation task (`video_montage`) requires powerful GPU. Ensure task is sent to worker equipped with at least NVIDIA T4.

**Solution:** `dispatch_task` method in `ActionFactory` accepts `resource_requirements` parameter, allowing specification of minimum worker resource requirements. Dispatcher automatically filters out workers not meeting these requirements.

```python
from orchestrator.blueprint import StateMachineBlueprint

gpu_intensive_pipeline = StateMachineBlueprint(
    name="gpu_intensive_flow",
    api_version="v1",
    api_endpoint="jobs/gpu_flow"
)

@gpu_intensive_pipeline.handler_for("start")
async def start_gpu_task(context, actions):
    actions.dispatch_task(
        task_type="video_montage",
        params=context.initial_data,
        # Dispatcher looks for worker whose `gpu_info.model` contains "NVIDIA T4"
        # and has required model installed
        resource_requirements={
            "gpu": {
                "model": "NVIDIA T4",
                "vram_gb": 16
            },
            "installed_models": [
                "stable-diffusion-1.5"
            ]
        },
        transitions={"success": "finished", "failure": "failed"}
    )
```

### **Recipe 20: End-to-End Tracing with OpenTelemetry**

**Task:** Trace full execution path of a job, from creation to worker processing and completion.

**Solution:** Orchestrator automatically manages OpenTelemetry trace context. Context created on API request receipt, passed to worker with task, and returned back with result. Allows combining all operations into single trace.

**How it works:**
1.  **Trace Start:** On `POST /api/...` call, Orchestrator creates root span for new job.
2.  **Orchestrator -> Worker:** On `actions.dispatch_task(...)` call, `Dispatcher` automatically injects W3C Trace Context into HTTP request headers to worker.
3.  **Worker:** Worker emulator extracts context from headers and creates child span for task execution duration.
4.  **Worker -> Orchestrator:** On result submission, worker injects its span context into callback request headers.
5.  **Trace End:** Orchestrator receives result, extracts context, and continues trace.

To see result, you need configured OpenTelemetry collector and visualization backend (e.g., Jaeger or Zipkin). In console, you'll only see warning about unconfigured exporter.

---

### **Recipes for History Storage**

This section contains useful code examples for direct database interaction needed when implementing or extending history storage functionality.

#### **Recipe 21: Direct SQLite Interaction**

**Task:** Connect to SQLite database file, create table, and insert data using standard `sqlite3` library.

```python
import sqlite3
import json

# Connect to DB file (created if not exists)
con = sqlite3.connect("history.db")
cur = con.cursor()

# Create table
cur.execute("""
    CREATE TABLE IF NOT EXISTS job_history (
        event_id TEXT PRIMARY KEY,
        job_id TEXT,
        timestamp TEXT,
        data JSON
    )
""")

# Prepare data
event_data = {"status": "completed", "result": "ok"}

# Insert record using json.dumps to convert dict to string
cur.execute(
    "INSERT INTO job_history VALUES (?, ?, ?, ?)",
    ("evt_123", "job_abc", "2024-01-01T12:00:00Z", json.dumps(event_data))
)

# Save changes
con.commit()

# Read data and convert JSON string back to dict
res = cur.execute("SELECT job_id, data FROM job_history WHERE event_id = 'evt_123'")
job_id, raw_data = res.fetchone()
retrieved_data = json.loads(raw_data)

print(f"Job ID: {job_id}, Data: {retrieved_data}")

con.close()
```
*Note: SQLite natively supports JSON data type, simplifying complex nested structure storage.*


#### **Recipe 22: Asynchronous PostgreSQL Interaction**

**Task:** Asynchronously connect to PostgreSQL, create table, and insert data using `asyncpg` library.

**Prerequisite:** `pip install asyncpg`

```python
import asyncio
import asyncpg
import json

async def main():
    # Connect to PostgreSQL
    conn = await asyncpg.connect(user='user', password='password',
                                 database='db', host='127.0.0.1')

    # Create table using native JSONB type for efficiency
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS job_history (
            event_id TEXT PRIMARY KEY,
            job_id TEXT,
            timestamp TIMESTAMPTZ,
            data JSONB
        )
    """)

    # Prepare data
    event_data = {"status": "dispatched", "worker": "worker-007"}

    # Insert record. asyncpg automatically encodes dict to JSONB.
    await conn.execute(
        "INSERT INTO job_history (event_id, job_id, timestamp, data) VALUES ($1, $2, NOW(), $3)",
        "evt_456", "job_xyz", event_data
    )

    # Read data. asyncpg automatically decodes JSONB to dict.
    row = await conn.fetchrow(
        "SELECT job_id, data FROM job_history WHERE event_id = $1",
        "evt_456"
    )
    print(f"Job ID: {row['job_id']}, Data: {row['data']}")

    # Close connection
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
```
*Note: Using `JSONB` in PostgreSQL is preferred over `JSON` as it's stored in binary format and allows creating indexes on keys inside JSON document.*

---

#### **Recipe 23: Enabling and Using Execution History**

**Task:** Enable detailed task execution history recording and retrieve it via API for analysis.

**Step 1: Enabling History Storage**

Execution history is disabled by default. To enable, set `HISTORY_DATABASE_URI` environment variable.

*   **For SQLite:**
    ```bash
    export HISTORY_DATABASE_URI="sqlite:path/to/your_history.db"
    ```

*   **For PostgreSQL:**
    ```bash
    export HISTORY_DATABASE_URI="postgresql://user:password@hostname/dbname"
    ```

After setting this variable, Orchestrator automatically starts recording events to specified database.

**Step 2: Retrieving History via API**

When history is enabled, new endpoint becomes available.

*   **Request:**
    ```bash
    curl http://localhost:8080/api/jobs/{job_id}/history -H "X-Client-Token: your_token"
    ```

*   **Response Example:**
    ```json
    [
        {
            "event_id": "a1b2c3d4-...".
            "job_id": "job_123",
            "timestamp": "2024-08-27T10:00:00.123Z",
            "state": "start",
            "event_type": "state_started",
            "duration_ms": null,
            "context_snapshot": { "... (full job state at start) ..." }
        },
        {
            "event_id": "e5f6g7h8-...".
            "job_id": "job_123",
            "timestamp": "2024-08-27T10:00:01.456Z",
            "state": "start",
            "event_type": "state_finished",
            "duration_ms": 1333,
            "next_state": "processing",
            "context_snapshot": { "... (job state after handler execution) ..." }
        }
    ]
    ```
This endpoint provides full step-by-step timeline of any task for detailed analysis and debugging.

---

### **Recipe 24: Authentication with Individual Token**

**Task:** Enhance security by configuring unique authentication token for each worker instead of using single shared secret.

**Concept:**
System supports hybrid authentication model. Priority given to individual token bound to `worker_id`. If not found, system checks shared `WORKER_TOKEN` for backward compatibility.

#### **Step 1: Orchestrator Configuration**
Define individual tokens in `workers.toml` file in Orchestrator root directory.

```toml
# workers.toml
[worker-001]
token = "super-secret-token-for-worker-1"
# ... other metadata for this worker ...

[worker-002]
token = "another-unique-token-for-worker-2"
```
Orchestrator loads these tokens into Redis at startup.

#### **Step 2: Worker Configuration**
Worker must pass its ID and unique token. Configure environment variables:

```bash
# Unique identifier matching key in workers.toml
export WORKER_ID="worker-001"
# Individual token for this worker
export WORKER_INDIVIDUAL_TOKEN="super-secret-token-for-worker-1"

# Shared WORKER_TOKEN no longer needed if individual one is used
```

#### **Step 3: Launch**
Run Orchestrator and Worker. Worker authenticates using `WORKER_ID` and `WORKER_INDIVIDUAL_TOKEN`. Requests from workers with invalid token or not listed in `workers.toml` (if shared `WORKER_TOKEN` not set) will be rejected with `401 Unauthorized`.

```