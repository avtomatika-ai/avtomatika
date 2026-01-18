from typing import Any


class ActionFactory:
    """A factory that provides handlers with methods for process control."""

    def __init__(self, job_id: str):
        self._job_id = job_id
        self._next_state_val: str | None = None
        self._task_to_dispatch_val: dict[str, Any] | None = None
        self._sub_blueprint_to_run_val: dict[str, Any] | None = None
        self._parallel_tasks_to_dispatch_val: dict[str, Any] | None = None

    def _check_for_existing_action(self) -> None:
        """
        Helper to ensure only one action is set.
        Raises RuntimeError if any action value is already set.
        """
        if any(
            [
                self._next_state_val,
                self._task_to_dispatch_val,
                self._sub_blueprint_to_run_val,
                self._parallel_tasks_to_dispatch_val,
            ]
        ):
            raise RuntimeError(
                "Cannot set multiple actions in the same step. "
                "An action (transition, task, blueprint, or parallel) has already been defined."
            )

    @property
    def next_state(self) -> str | None:
        return self._next_state_val

    @property
    def task_to_dispatch(self) -> dict[str, Any] | None:
        return self._task_to_dispatch_val

    @property
    def sub_blueprint_to_run(self) -> dict[str, Any] | None:
        return self._sub_blueprint_to_run_val

    @property
    def parallel_tasks_to_dispatch(self) -> dict[str, Any] | None:
        return self._parallel_tasks_to_dispatch_val

    def dispatch_parallel(self, tasks: list[dict[str, Any]], aggregate_into: str) -> None:
        """
        Dispatches multiple tasks for parallel execution.
        """
        self._check_for_existing_action()
        print(f"Job {self._job_id}: Dispatching {len(tasks)} tasks in parallel, aggregating into '{aggregate_into}'")
        self._parallel_tasks_to_dispatch_val = {
            "tasks": tasks,
            "aggregate_into": aggregate_into,
        }

    def transition_to(self, state: str) -> None:
        """Schedules a transition to a new state."""
        self._check_for_existing_action()
        print(f"Job {self._job_id}: Transitioning to '{state}'")
        self._next_state_val = state

    def dispatch_task(
        self,
        task_type: str,
        params: dict[str, Any],
        transitions: dict[str, str],
        dispatch_strategy: str = "default",
        resource_requirements: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
        max_cost: float | None = None,
        priority: float = 0.0,
    ) -> None:
        """Dispatches a task to a worker for execution."""
        self._check_for_existing_action()
        print(f"Job {self._job_id}: Dispatching task '{task_type}'")
        self._task_to_dispatch_val = {
            "type": task_type,
            "params": params,
            "transitions": transitions,
            "dispatch_strategy": dispatch_strategy,
            "resource_requirements": resource_requirements,
            "timeout_seconds": timeout_seconds,
            "max_cost": max_cost,
            "priority": priority,
        }

    def await_human_approval(
        self,
        integration: str,
        message: str,
        transitions: dict[str, str],
    ) -> None:
        """Pauses the pipeline until an external signal (human approval) is received."""
        self._check_for_existing_action()
        print(f"Job {self._job_id}: Awaiting human approval via {integration}")
        self._task_to_dispatch_val = {
            "type": "human_approval",
            "integration": integration,
            "message": message,
            "transitions": transitions,
        }

    def run_blueprint(
        self,
        blueprint_name: str,
        initial_data: dict[str, Any],
        transitions: dict[str, str],
    ) -> None:
        """Runs a child blueprint and waits for its result."""
        self._check_for_existing_action()
        print(f"Job {self._job_id}: Running sub-blueprint '{blueprint_name}'")
        self._sub_blueprint_to_run_val = {
            "blueprint_name": blueprint_name,
            "initial_data": initial_data,
            "transitions": transitions,
        }
