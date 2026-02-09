**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/creating_blueprint.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/creating_blueprint.md)

# Cookbook: Creating a Blueprint (Pipeline)

Blueprints (`StateMachineBlueprint`) are the foundation for defining business logic in the system. Each blueprint represents a state machine ("script") that the Orchestrator will execute.

This guide will show how to create a simple yet complete pipeline.

## Step 1: Create a Blueprint File

It is recommended to store blueprints in a separate file, e.g., `my_service/blueprints.py`.

## Step 2: Define the Blueprint

Import `StateMachineBlueprint` and create an instance.

- `name`: Unique blueprint name.
- `api_version`: API version (e.g., "v1").
- `api_endpoint`: URL where clients will create tasks for this pipeline.

```python
from avtomatika import StateMachineBlueprint

# Create blueprint instance
order_pipeline = StateMachineBlueprint(
    name="order_processing_flow",
    api_version="v1",
    api_endpoint="/jobs/process_order"  # URL for task creation
)
```

## Step 3: Define States and Handlers

Each step in your process is a "state" with an attached "handler" function.

-   Decorator `@blueprint.handler_for("state_name")` binds function to state.
-   **Initial State** must be exactly one, marked with `is_start=True`.
-   **Final States** can be multiple, marked with `is_end=True`.

Handler receives one argument — `context`, containing all task info and process control methods (`context.actions`).

```python
@order_pipeline.handler_for("start", is_start=True)
async def start_handler(context):
    """
    Initial handler. Called when Job is created.
    """
    print(f"Job {context.job_id}: order processing started.")
    print(f"Input data: {context.initial_data}")

    # Save something to history for next steps
    context.state_history["processed_by"] = "start_handler"

    # Transition to next step
    context.actions.transition_to("dispatch_to_worker")


@order_pipeline.handler_for("dispatch_to_worker")
async def dispatch_handler(context):
    """
    This handler dispatches task to worker.
    """
    print(f"Job {context.job_id}: dispatching 'check_inventory' task to worker.")

    # Pause pipeline and wait for worker result
    context.actions.dispatch_task(
        task_type="check_inventory",  # Task type worker understands
        params={"items": context.initial_data.get("items")},
        
        # Define where process goes depending on worker response
        transitions={
            "success": "inventory_ok",      # If worker returns status="success"
            "out_of_stock": "inventory_failed", # If worker returns custom status
            "failure": "generic_failure"    # If worker returns status="failure"
        }
    )

@order_pipeline.handler_for("inventory_ok")
async def inventory_ok_handler(context):
    """
    Called if worker confirmed item availability.
    """
    # Data returned by worker is available in state_history
    worker_data = context.state_history.get("warehouse_info")
    print(f"Job {context.job_id}: items in stock. Info from worker: {worker_data}")
    
    context.actions.transition_to("finished_successfully")


@order_pipeline.handler_for("inventory_failed", is_end=True)
async def inventory_failed_handler(context):
    """
    Final state if items out of stock.
    """
    print(f"Job {context.job_id}: cannot process order, items out of stock.")


@order_pipeline.handler_for("generic_failure", is_end=True)
async def failed_handler(context):
    """
    Final state for generic failures.
    """
    print(f"Job {context.job_id}: processing error occurred.")


@order_pipeline.handler_for("finished_successfully", is_end=True)
async def finished_handler(context):
    """
    Final state on success.
    """
    print(f"Job {context.job_id}: order processed successfully!")

```

## Step 4: Register the Blueprint

In your application's main file where you run `OrchestratorEngine`, register the created blueprint.

When you call `register_blueprint()`, the engine automatically performs a **integrity validation check**. It ensures that:
1.  The blueprint has exactly one start state.
2.  All transitions lead to existing states (no "dangling" transitions).
3.  All states are reachable from the start state (no "dead code").

If any of these checks fail, a `ValueError` will be raised, preventing the application from starting with a broken configuration.

```python
# main.py
from avtomatika import OrchestratorEngine
from my_service.blueprints import order_pipeline  # Import our blueprint

# ... (storage and config setup)

engine = OrchestratorEngine(storage, config)

# Register blueprint in engine (Triggers validation!)
engine.register_blueprint(order_pipeline)

# Run engine
engine.run()
```

After this, you can create tasks by sending POST requests to `/api/v1/jobs/process_order`.