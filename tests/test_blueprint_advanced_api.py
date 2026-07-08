from unittest.mock import MagicMock

import pytest
from fast_filter import F

from avtomatika.blueprint import Blueprint


def test_combine_condition_and_default_handler():
    """Verifies that condition and a default handler can be used together for the same state."""
    bp = Blueprint("test")

    @bp.handler("process", F.initial_data["x"] == 1)
    async def process_special(actions):
        pass

    @bp.handler("process")
    async def process_default(actions):
        pass

    assert len(bp.conditional_handlers) == 1
    assert bp.handlers["process"] == process_default


def test_combine_condition_and_default_handler_reverse_order():
    """Verifies that condition and a default handler work regardless of definition order."""
    bp = Blueprint("test")

    @bp.handler("process")
    async def process_default(actions):
        pass

    @bp.handler("process", F.initial_data["x"] == 1)
    async def process_special(actions):
        pass

    assert len(bp.conditional_handlers) == 1
    assert bp.handlers["process"] == process_default


def test_no_handler_matches_error_message():
    """Verifies the error message when all conditions fail and no default handler exists."""
    bp = Blueprint("test")

    @bp.handler("start", F.initial_data["x"] == 1)
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

    transitions, _ = bp._get_all_transitions()
    assert transitions["start"] == {"middle"}
    assert transitions["middle"] == {"end"}


def test_multiple_conditions():
    """Verifies that multiple conditions can be registered for the same state."""
    bp = Blueprint("test")

    @bp.handler("step", F.initial_data["v"] == 1)
    async def handler1(actions):
        return 1

    @bp.handler("step", F.initial_data["v"] == 2)
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


def test_find_handler_aggregator_fallback():
    """Verifies that find_handler finds aggregators if no regular handler exists."""
    bp = Blueprint("test_bp")

    @bp.aggregator("collect")
    async def collect(aggregation_results, actions):
        pass

    handler = bp.find_handler("collect", context=None)
    assert handler == collect


def test_find_handler_priority():
    """Verifies that regular handlers have priority over aggregators."""
    bp = Blueprint("test_bp")

    @bp.handler("both")
    async def regular(actions):
        pass

    @bp.aggregator("both")
    async def aggregator(aggregation_results, actions):
        pass

    handler = bp.find_handler("both", context=None)
    assert handler == regular


def test_blueprint_get_contract():
    """Verifies that get_contract returns all relevant blueprint metadata."""
    bp = Blueprint(name="test_bp", api_endpoint="/test", api_version="v2")

    @bp.handler("start", is_start=True)
    async def start(actions):
        actions.go_to("end")

    @bp.handler("end", is_end=True)
    async def end():
        pass

    @bp.aggregator("agg")
    async def agg(aggregation_results, actions):
        pass

    bp.event("my_event", {"type": "object"})

    contract = bp.get_contract()

    assert contract["name"] == "test_bp"
    assert contract["api_endpoint"] == "/test"
    assert contract["api_version"] == "v2"
    assert contract["start_state"] == "start"
    assert "end" in contract["end_states"]
    assert "start" in contract["handlers"]
    assert "end" in contract["handlers"]
    assert "agg" in contract["aggregators"]
    assert "my_event" in contract["events_schema"]
    assert isinstance(contract["conditional_handlers"], list)
