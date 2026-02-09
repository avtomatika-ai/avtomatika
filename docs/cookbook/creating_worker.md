**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/creating_worker.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/creating_worker.md)

# Cookbook: Creating a Worker

Workers are independent executors performing actual work. This guide shows how to create a worker using `avtomatika-worker` package.

## Step 1: Install SDK

Ensure SDK is installed in your environment.
```bash
pip install avtomatika-worker
```

## Step 2: Create Worker File

Create Python file (e.g., `my_worker.py`) and import `Worker` class.

```python
import asyncio
from avtomatika_worker import Worker
from avtomatika_worker.typing import TRANSIENT_ERROR

# 1. Initialize Worker class
# Specify unique type for your worker.
worker = Worker(worker_type="inventory-checker")

# 2. Define task handlers using @worker.task decorator
@worker.task("check_inventory")
async def check_inventory_handler(params: dict, **kwargs) -> dict:
    """
    This function called when Orchestrator sends "check_inventory" task.

    - `params` (dict): Task execution parameters.
    - `**kwargs`: Task metadata:
        - `task_id` (str): Unique task ID.
        - `job_id` (str): Parent Job ID.
        - `priority` (float): Task priority.
    """
    print(f"Received params: {params}")
    items = params.get("items", [])

    # Simulate work: checking inventory
    await asyncio.sleep(1)

    if "unavailable_item" in items:
        # Return custom status handled by blueprint
        return {
            "status": "out_of_stock",
            "data": {"missing_item": "unavailable_item"}
        }

    # 3. Return success result
    #    - 'status': "success" or custom status.
    #    - 'data': Dictionary with data added to Job context.
    return {
        "status": "success",
        "data": {"warehouse_info": "All items are available"}
    }

# Example handler for long task with cooperative cancellation
@worker.task("long_running_task")
async def long_task_handler(params: dict, **kwargs) -> dict:
    task_id = kwargs["task_id"]
    print(f"Starting long task {task_id}...")
    
    for i in range(10):
        # Check if Orchestrator requested cancellation
        if await worker.check_for_cancellation(task_id):
            print(f"Cancellation detected for task {task_id}. Stopping...")
            return {"status": "cancelled", "message": "Task was cancelled by user."}
        
        print(f"Step {i+1}/10 done...")
        await asyncio.sleep(2)

    return {"status": "success"}


# 4. Run worker
if __name__ == "__main__":
    worker.run()
```

## Step 3: Connection and Authentication Setup

Create `.env` file in same directory as `my_worker.py` or export variables to environment.

```dotenv
# Unique ID of this worker instance
WORKER_ID=inventory-worker-01

# Address of your Orchestrator
ORCHESTRATOR_URL=http://localhost:8080

# Token for worker authentication. Must match token expected by Orchestrator
# (global or individual).
WORKER_TOKEN=your-secret-worker-token

# (Optional) Enable WebSocket for instant task cancellation
WORKER_ENABLE_WEBSOCKETS=true
```

## Step 4: Launch

Just run your Python file:
```bash
python my_worker.py
```
Worker will automatically connect to Orchestrator, register, and start polling for new tasks.

## Cancellation Mechanisms

SDK provides two cancellation mechanisms:

1.  **WebSocket (Push Model):** If `WORKER_ENABLE_WEBSOCKETS=true`, Orchestrator can send immediate cancellation command. This raises `asyncio.CancelledError` in your handler. This provides fastest reaction, just wrap your code in `try...except asyncio.CancelledError` for cleanup if needed.

2.  **Redis (Pull Model):** Even without WebSocket, you can implement "cooperative" cancellation for very long tasks. SDK provides async function `worker.check_for_cancellation(task_id)`. Periodically call it inside your processing loop. If returns `True`, Orchestrator requested cancellation. Your code should gracefully interrupt and return `cancelled` status. (See `long_running_task` example above).