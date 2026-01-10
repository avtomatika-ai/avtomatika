from operator import eq, ge, gt, le, lt, ne
from re import compile as re_compile
from typing import Any, Callable, NamedTuple

from .datastore import AsyncDictStore

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
    def __init__(self, blueprint, state: str, func: Callable, condition_str: str):
        self.blueprint = blueprint
        self.state = state
        self.func = func
        self.condition = _parse_condition(condition_str)

    def evaluate(self, context: Any) -> bool:
        try:
            context_area = getattr(context, self.condition.area)
            actual_value = context_area[self.condition.field]
            return self.condition.op(actual_value, self.condition.value)
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
        data_stores: Any = None,
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
        self.data_stores: dict[str, AsyncDictStore] = data_stores if data_stores is not None else {}
        self.handlers: dict[str, Callable] = {}
        self.aggregator_handlers: dict[str, Callable] = {}
        self.conditional_handlers: list[ConditionalHandler] = []
        self.start_state: str | None = None
        self.end_states: set[str] = set()

    def add_data_store(self, name: str, initial_data: dict[str, Any]):
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

    def validate(self):
        """Validates that the blueprint is configured correctly."""
        if self.start_state is None:
            raise ValueError(f"Blueprint '{self.name}' must have exactly one start state.")

    def find_handler(self, state: str, context: Any) -> Callable:
        for handler in self.conditional_handlers:
            if handler.state == state and handler.evaluate(context):
                return handler.func
        if default_handler := self.handlers.get(state):
            return default_handler
        raise ValueError(
            f"No suitable handler found for state '{state}' in blueprint '{self.name}' for the given context.",
        )

    def render_graph(self, output_filename: str | None = None, output_format: str = "png"):
        import ast
        import inspect
        import logging
        import textwrap

        from graphviz import Digraph  # type: ignore[import]

        logger = logging.getLogger(__name__)

        dot = Digraph(comment=f"State Machine for {self.name}")
        dot.attr("node", shape="box", style="rounded")
        all_handlers = list(self.handlers.items()) + [(ch.state, ch.func) for ch in self.conditional_handlers]
        states = set(self.handlers.keys())
        for handler_state, handler_func in all_handlers:
            try:
                source = textwrap.dedent(inspect.getsource(handler_func))
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(
                        node.func,
                        ast.Attribute,
                    ):
                        if node.func.attr == "transition_to" and node.args and isinstance(node.args[0], ast.Constant):
                            target_state = str(node.args[0].value)
                            states.add(target_state)
                            dot.edge(handler_state, target_state, label="transition")
                        elif node.func.attr == "dispatch_task":
                            for keyword in node.keywords:
                                if keyword.arg == "transitions" and isinstance(
                                    keyword.value,
                                    ast.Dict,
                                ):
                                    for key_node, value_node in zip(
                                        keyword.value.keys,
                                        keyword.value.values,
                                        strict=False,
                                    ):
                                        if isinstance(
                                            key_node,
                                            ast.Constant,
                                        ) and isinstance(value_node, ast.Constant):
                                            key = str(key_node.value)
                                            value = str(value_node.value)
                                            states.add(value)
                                            dot.edge(
                                                handler_state,
                                                value,
                                                label=f"on {key}",
                                            )
            except (TypeError, OSError) as e:
                logger.warning(
                    f"Could not parse handler '{handler_func.__name__}' for state '{handler_state}'. "
                    f"Graph may be incomplete. Error: {e}"
                )
        for state in states:
            dot.node(state, state)

        if not output_filename:
            return dot.source
        dot.render(output_filename, format=output_format, cleanup=True)
        print(f"Graph rendered to {output_filename}.{output_format}")
        return None
