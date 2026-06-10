**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/creating_blueprint.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/creating_blueprint.md)

# Cookbook: Creating a Blueprint (Pipeline)

Blueprints (`Blueprint`) are the foundation for defining business logic in the system. Each blueprint represents a state machine ("script") that the Orchestrator will execute.

This guide will show how to create a simple yet complete pipeline.

## Step 1: Create a Blueprint File

It is recommended to store blueprints in a separate file, e.g., `my_service/blueprints.py`.

## Step 2: Define the Blueprint

Import `Blueprint` and create an instance.

- `name`: Unique blueprint name.
- `api_version`: API version (e.g., "v1").
- `api_endpoint`: URL where clients will create tasks for this pipeline.

```python
from avtomatika import Blueprint

# Create blueprint instance
order_pipeline = Blueprint(
    name="order_processing_flow",
    api_version="v1",
    api_endpoint="/jobs/process_order"  # URL for task creation
)
```

## Step 3: Define States and Handlers

Each step in your process is a "state" with an attached "handler" function.

-   Decorator `@blueprint.handler("state_name")` binds function to state.
-   **Initial State** must be exactly one, marked with `is_start=True`.
-   **Final States** can be multiple, marked with `is_end=True`.

The handler receives arguments via **Dependency Injection**. You can request:
- `initial_data`: Original job input.
- `actions`: `ActionFactory` object to control the process.
- `state_history`: Full job history.
- `job_id`: Unique job ID.

```python
from avtomatika import ActionFactory

@order_pipeline.handler(is_start=True)
async def start_handler(initial_data: dict, actions: ActionFactory, job_id: str):
    """
    Initial handler. Called when Job is created.
    """
    print(f"Job {job_id}: order processing started.")
    print(f"Input data: {initial_data}")

    # Transition to next step
    actions.go_to("dispatch_to_worker")


@order_pipeline.handler
async def dispatch_to_worker(initial_data: dict, actions: ActionFactory):
    """
    This handler dispatches task to worker.
    """
    # Pause pipeline and wait for worker result
    actions.dispatch_task(
        task_type="check_inventory",  # Task type worker understands
        params={"items": initial_data.get("items")},
        
        # Define where process goes depending on worker response
        transitions={
            "success": "inventory_ok",      # If worker returns status="success"
            "out_of_stock": "inventory_failed", # If worker returns custom status
            "failure": "generic_failure"    # If worker returns status="failure"
        }
    )

@order_pipeline.handler
async def inventory_ok(actions: ActionFactory, state_history: dict):
    """
    Called if worker confirmed item availability.
    """
    # Data returned by worker is available in the history
    print("Items are in stock. Finishing successfully.")
    actions.go_to("finished_successfully")


@order_pipeline.handler(is_end=True)
async def inventory_failed(actions: ActionFactory):
    """
    Final state if items out of stock.
    """
    print("Cannot process order, items out of stock.")


@order_pipeline.handler(is_end=True)
async def generic_failure(actions: ActionFactory):
    """
    Final state for generic failures.
    """
    print("Processing error occurred.")


@order_pipeline.handler(is_end=True)
async def finished_successfully(actions: ActionFactory):
    """
    Final state on success.
    """
    print("Order processed successfully!")

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
