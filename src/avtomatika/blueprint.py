from logging import getLogger
from operator import eq, ge, gt, le, lt, ne
from re import compile as re_compile
from typing import Any, Callable, NamedTuple

from .datastore import AsyncDictStore

logger = getLogger(__name__)

# Simple parser for expressions like "context.area.field operator value"
# The order of operators is important: >= and <= must come before > and <
CONDITION_REGEX = re_compile(
    r"context\.(?P<area>\w+)\.(?P<field>\w+)\s*(?P<op>>=|<=|==|!=|>|<)\s*(?P<value>.*)",
)

OPERATORS = {
    "==": eq,
    "!=": ne,
    ">": gt,
    "<": lt,
    ">=": ge,
    "<=": le,
}


class Condition(NamedTuple):
    area: str
    field: str
    op: Callable
    value: Any


def _parse_condition(condition_str: str) -> Condition:
    match = CONDITION_REGEX.match(condition_str.strip())
    if not match:
        raise ValueError(f"Invalid condition string format: {condition_str}")

    parts = match.groupdict()
    op_str = parts["op"]
    op_func = OPERATORS.get(op_str)
    if not op_func:
        raise ValueError(f"Unsupported operator: {op_str}")

    value_str = parts["value"].strip().strip("'\"")
    value: Any
    try:
        value = int(value_str)
    except ValueError:
        try:
            value = float(value_str)
        except ValueError:
            value = value_str

    return Condition(area=parts["area"], field=parts["field"], op=op_func, value=value)


class ConditionalHandler:
    def __init__(self, blueprint: "StateMachineBlueprint", state: str, func: Callable, condition_str: str):
        self.blueprint = blueprint
        self.state = state
        self.func = func
        self.condition = _parse_condition(condition_str)

    def evaluate(self, context: Any) -> bool:
        try:
            context_area = getattr(context, self.condition.area)
            actual_value = context_area[self.condition.field]
            result = self.condition.op(actual_value, self.condition.value)
            return bool(result)
        except (AttributeError, KeyError):
            return False


class HandlerDecorator:
    def __init__(
        self,
        blueprint: "StateMachineBlueprint",
        state: str,
        is_start: bool = False,
        is_end: bool = False,
    ):
        self._blueprint = blueprint
        self._state = state
        self._is_start = is_start
        self._is_end = is_end

    def __call__(self, func: Callable) -> Callable:
        if self._state in self._blueprint.handlers:
            raise ValueError(f"Default handler for state '{self._state}' is already registered.")
        self._blueprint.handlers[self._state] = func

        if self._is_start:
            if self._blueprint.start_state is not None:
                raise ValueError(
                    f"Blueprint '{self._blueprint.name}' already has a start state: '{self._blueprint.start_state}'."
                )
            self._blueprint.start_state = self._state

        if self._is_end:
            self._blueprint.end_states.add(self._state)

        return func

    def when(self, condition_str: str) -> Callable:
        def decorator(func: Callable) -> Callable:
            if self._state not in self._blueprint.handlers:
                self._blueprint.handlers[self._state] = lambda: None  # Placeholder

            handler = ConditionalHandler(self._blueprint, self._state, func, condition_str)
            self._blueprint.conditional_handlers.append(handler)
            return func

        return decorator


class StateMachineBlueprint:
    def __init__(
        self,
        name: str,
        api_endpoint: str | None = None,
        api_version: str | None = None,
        data_stores: dict[str, Any] | None = None,
    ):
        """Initializes a new blueprint.

        Args:
            name: A unique name for the blueprint.
            api_endpoint: The path for the API endpoint, e.g., "/jobs/my_flow".
            api_version: An optional API version (e.g., "v1"). If not specified,
                         the endpoint will be unversioned.
            data_stores: An optional dictionary of data stores.

        """
        self.name = name
        self.api_endpoint = api_endpoint
        self.api_version = api_version
        self.data_stores: dict[str, AsyncDictStore] = {}
        if data_stores:
            for ds_name, ds_data in data_stores.items():
                if isinstance(ds_data, AsyncDictStore):
                    self.data_stores[ds_name] = ds_data
                else:
                    self.data_stores[ds_name] = AsyncDictStore(ds_data)

        self.handlers: dict[str, Callable] = {}
        self.aggregator_handlers: dict[str, Callable] = {}
        self.conditional_handlers: list[ConditionalHandler] = []
        self.start_state: str | None = None
        self.end_states: set[str] = set()
        self._handler_params: dict[Callable, tuple[str, ...]] = {}

    def add_data_store(self, name: str, initial_data: dict[str, Any]) -> None:
        """Adds a named data store to the blueprint."""
        if name in self.data_stores:
            raise ValueError(f"Data store with name '{name}' already exists.")
        self.data_stores[name] = AsyncDictStore(initial_data)

    def handler_for(self, state: str, is_start: bool = False, is_end: bool = False) -> HandlerDecorator:
        return HandlerDecorator(self, state, is_start=is_start, is_end=is_end)

    def aggregator_for(self, state: str) -> Callable:
        """Decorator for registering an aggregator handler."""

        def decorator(func: Callable) -> Callable:
            if state in self.aggregator_handlers:
                raise ValueError(f"Aggregator for state '{state}' is already registered.")
            self.aggregator_handlers[state] = func
            return func

        return decorator

    def validate(self) -> None:
        """Validates that the blueprint is configured correctly."""
        if self.start_state is None:
            raise ValueError(f"Blueprint '{self.name}' must have exactly one start state.")
        self._analyze_handlers()
        self.validate_integrity()

    def validate_integrity(self) -> None:
        """Checks for dangling transitions and unreachable states."""
        transitions = self._get_all_transitions()
        defined_states = (
            set(self.handlers.keys())
            | set(self.aggregator_handlers.keys())
            | {ch.state for ch in self.conditional_handlers}
        )

        # 1. Check for dangling transitions
        for source_state, targets in transitions.items():
            for target_state in targets:
                if target_state not in defined_states:
                    raise ValueError(
                        f"Blueprint '{self.name}' has a dangling transition: "
                        f"state '{source_state}' leads to non-existent state '{target_state}'."
                    )

        # 2. Check for unreachable states
        if self.start_state:
            reachable = {self.start_state}
            stack = [self.start_state]
            while stack:
                current = stack.pop()
                for target in transitions.get(current, set()):
                    if target not in reachable:
                        reachable.add(target)
                        stack.append(target)

            unreachable = defined_states - reachable
            if unreachable:
                raise ValueError(
                    f"Blueprint '{self.name}' has unreachable states: {', '.join(unreachable)}. "
                    "All states must be reachable from the start state."
                )

    def _get_all_transitions(self) -> dict[str, set[str]]:
        """Parses handler source code to find all possible transitions."""
        import ast
        import inspect
        import textwrap

        transitions: dict[str, set[str]] = {}

        all_handlers = (
            list(self.handlers.items())
            + list(self.aggregator_handlers.items())
            + [(ch.state, ch.func) for ch in self.conditional_handlers]
        )

        for state, func in all_handlers:
            if state not in transitions:
                transitions[state] = set()
            try:
                source = textwrap.dedent(inspect.getsource(func))
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                        continue

                    # Handle actions.transition_to("state")
                    if node.func.attr == "transition_to" and node.args and isinstance(node.args[0], ast.Constant):
                        transitions[state].add(str(node.args[0].value))

                    # Handle actions.dispatch_task(..., transitions={"status": "state"})
                    # Also handles await_human_approval, run_blueprint which use the same 'transitions' kwarg
                    elif node.func.attr in ("dispatch_task", "await_human_approval", "run_blueprint"):
                        for keyword in node.keywords:
                            if keyword.arg == "transitions" and isinstance(keyword.value, ast.Dict):
                                for value_node in keyword.value.values:
                                    if isinstance(value_node, ast.Constant):
                                        transitions[state].add(str(value_node.value))

                    # Handle actions.dispatch_parallel(..., aggregate_into="state")
                    elif node.func.attr == "dispatch_parallel":
                        for keyword in node.keywords:
                            if keyword.arg == "aggregate_into" and isinstance(keyword.value, ast.Constant):
                                transitions[state].add(str(keyword.value.value))

            except (TypeError, OSError, SyntaxError) as e:
                logger.warning(f"Could not parse handler for state '{state}': {e}")

        return transitions

    def _analyze_handlers(self) -> None:
        """Analyzes and caches parameters for all registered handlers."""
        import inspect

        all_funcs = (
            list(self.handlers.values())
            + list(self.aggregator_handlers.values())
            + [ch.func for ch in self.conditional_handlers]
        )

        for func in all_funcs:
            sig = inspect.signature(func)
            self._handler_params[func] = tuple(sig.parameters.keys())

    def get_handler_params(self, func: Callable) -> tuple[str, ...]:
        """Returns the cached parameters for a handler function."""
        return self._handler_params.get(func, ())

    def find_handler(self, state: str, context: Any) -> Callable:
        for handler in self.conditional_handlers:
            if handler.state == state and handler.evaluate(context):
                return handler.func
        if default_handler := self.handlers.get(state):
            return default_handler
        raise ValueError(
            f"No suitable handler found for state '{state}' in blueprint '{self.name}' for the given context.",
        )

    def render_graph(self, output_filename: str | None = None, output_format: str = "png") -> str | None:
        from graphviz import Digraph  # type: ignore[import]

        dot = Digraph(comment=f"State Machine for {self.name}")
        dot.attr("node", shape="box", style="rounded")

        transitions = self._get_all_transitions()
        defined_states = (
            set(self.handlers.keys())
            | set(self.aggregator_handlers.keys())
            | {ch.state for ch in self.conditional_handlers}
        )
        states = defined_states.copy()

        for source, targets in transitions.items():
            for target in targets:
                states.add(target)
                dot.edge(source, target)

        for state in states:
            dot.node(state, state)

        if not output_filename:
            return dot.source
        dot.render(output_filename, format=output_format, cleanup=True)
        logger.info(f"Graph rendered to {output_filename}.{output_format}")
        return None
