from unittest.mock import MagicMock

import pytest

from avtomatika.blueprint import Blueprint


def test_combine_when_and_default_handler():
    """Verifies that .when() and a default handler can be used together for the same state."""
    bp = Blueprint("test")

    @bp.handler("process").when("context.initial_data.x == 1")
    async def process_special(actions):
        pass

    @bp.handler("process")
    async def process_default(actions):
        pass

    assert len(bp.conditional_handlers) == 1
    assert bp.handlers["process"] == process_default


def test_combine_when_and_default_handler_reverse_order():
    """Verifies that .when() and a default handler work regardless of definition order."""
    bp = Blueprint("test")

    @bp.handler("process")
    async def process_default(actions):
        pass

    @bp.handler("process").when("context.initial_data.x == 1")
    async def process_special(actions):
        pass

    assert len(bp.conditional_handlers) == 1
    assert bp.handlers["process"] == process_default


def test_no_handler_matches_error_message():
    """Verifies the error message when all conditions fail and no default handler exists."""
    bp = Blueprint("test")

    @bp.handler("start").when("context.initial_data.x == 1")
    async def start_cond(actions):
        pass

    context = MagicMock()
    context.initial_data = {"x": 2}

    with pytest.raises(ValueError, match="All conditions failed and no default handler registered"):
        bp.find_handler("start", context)


def test_inferred_name_with_start_end_flags():
    """Verifies that inferred names work correctly with is_start and is_end."""
    bp = Blueprint("test")

    @bp.handler(is_start=True)
    async def start_node(actions):
        actions.go_to("end_node")

    @bp.handler(is_end=True)
    async def end_node():
        pass

    assert bp.start_state == "start_node"
    assert "end_node" in bp.end_states
    assert "start_node" in bp.handlers
    assert "end_node" in bp.handlers


def test_static_analysis_with_inferred_names():
    """Verifies that the static transition analyzer correctly finds go_to calls in inferred handlers."""
    bp = Blueprint("test")

    @bp.handler(is_start=True)
    async def start(actions):
        actions.go_to("middle")

    @bp.handler
    async def middle(actions):
        actions.go_to("end")

    @bp.handler(is_end=True)
    async def end():
        pass

    # validate() triggers _analyze_handlers and validate_integrity which uses _get_all_transitions
    bp.validate()

    transitions = bp._get_all_transitions()
    assert transitions["start"] == {"middle"}
    assert transitions["middle"] == {"end"}


def test_multiple_when_conditions():
    """Verifies that multiple .when() conditions can be registered for the same state."""
    bp = Blueprint("test")

    @bp.handler("step").when("context.initial_data.v == 1")
    async def handler1(actions):
        return 1

    @bp.handler("step").when("context.initial_data.v == 2")
    async def handler2(actions):
        return 2

    context = MagicMock()

    context.initial_data = {"v": 1}
    assert bp.find_handler("step", context) == handler1

    context.initial_data = {"v": 2}
    assert bp.find_handler("step", context) == handler2


def test_aggregator_inferred_name():
    """Verifies inferred naming for aggregators."""
    bp = Blueprint("test")

    @bp.aggregator
    async def my_aggregator(aggregation_results, actions):
        pass

    assert "my_aggregator" in bp.aggregator_handlers
