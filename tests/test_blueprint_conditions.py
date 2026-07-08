# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin
from unittest.mock import MagicMock

import pytest
from fast_filter import F
from src.avtomatika.blueprint import Blueprint, ConditionalHandler

# ---------------------------------------------------------------------------
# ConditionalHandler with FastF
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_blueprint():
    return MagicMock()


@pytest.fixture
def conditional_handler(mock_blueprint):
    """Handler that passes when initial_data.status == 'completed'."""
    condition = F.initial_data["status"] == "completed"
    return ConditionalHandler(mock_blueprint, "start", lambda: None, condition)


def test_conditional_handler_evaluate_true(conditional_handler):
    context = MagicMock()
    context.initial_data = {"status": "completed"}
    assert conditional_handler.evaluate(context) is True


def test_conditional_handler_evaluate_false(conditional_handler):
    context = MagicMock()
    context.initial_data = {"status": "pending"}
    assert conditional_handler.evaluate(context) is False


def test_conditional_handler_evaluate_missing_area(conditional_handler):
    """Missing attribute must return False gracefully, not raise."""
    context = MagicMock(spec=[])  # no attributes at all
    assert conditional_handler.evaluate(context) is False


def test_conditional_handler_evaluate_missing_field(conditional_handler):
    context = MagicMock()
    context.initial_data = {}
    assert conditional_handler.evaluate(context) is False


def test_conditional_handler_evaluate_complex_condition(mock_blueprint):
    """FastF allows arbitrary expressions impossible with old string syntax."""
    condition = (F.data["score"] >= 80) & (F.data["active"] == True)  # noqa: E712
    handler = ConditionalHandler(mock_blueprint, "check", lambda: None, condition)

    context_ok = MagicMock()
    context_ok.data = {"score": 90, "active": True}
    assert handler.evaluate(context_ok) is True

    context_fail = MagicMock()
    context_fail.data = {"score": 70, "active": True}
    assert handler.evaluate(context_fail) is False


# ---------------------------------------------------------------------------
# Blueprint.handler with direct FastF condition
# ---------------------------------------------------------------------------


@pytest.fixture
def blueprint():
    return Blueprint("test_bp")


def test_handler_decorator_condition_with_fastf(blueprint):
    @blueprint.handler("start", F.initial_data["status"] == "completed")
    def handler(context, actions):
        pass

    assert len(blueprint.conditional_handlers) == 1
    assert blueprint.conditional_handlers[0].state == "start"


def test_handler_decorator_condition_evaluates_correctly(blueprint):
    @blueprint.handler("start", F.result["ok"] == True)  # noqa: E712
    def handler(context, actions):
        pass

    ctx_true = MagicMock()
    ctx_true.result = {"ok": True}
    assert blueprint.conditional_handlers[0].evaluate(ctx_true) is True

    ctx_false = MagicMock()
    ctx_false.result = {"ok": False}
    assert blueprint.conditional_handlers[0].evaluate(ctx_false) is False


def test_handler_decorator_condition_invert(blueprint):
    @blueprint.handler("start", ~(F.error["code"] == 404))
    def handler(context, actions):
        pass

    ctx = MagicMock()
    ctx.error = {"code": 404}
    assert blueprint.conditional_handlers[0].evaluate(ctx) is False

    ctx2 = MagicMock()
    ctx2.error = {"code": 500}
    assert blueprint.conditional_handlers[0].evaluate(ctx2) is True


# ---------------------------------------------------------------------------
# Other Blueprint decorator tests (unchanged logic)
# ---------------------------------------------------------------------------


def test_handler_decorator_duplicate_handler(blueprint):
    @blueprint.handler("start")
    def handler1(context, actions):
        pass

    with pytest.raises(ValueError, match="Default handler for state 'start' is already registered."):

        @blueprint.handler("start")
        def handler2(context, actions):
            pass


def test_handler_decorator_duplicate_start_state(blueprint):
    @blueprint.handler("start", is_start=True)
    def handler1(context, actions):
        pass

    with pytest.raises(ValueError, match="Blueprint 'test_bp' already has a start state: 'start'."):

        @blueprint.handler("another_start", is_start=True)
        def handler2(context, actions):
            pass


def test_add_data_store_duplicate(blueprint):
    blueprint.add_data_store("my_store", {})
    with pytest.raises(ValueError, match="Data store with name 'my_store' already exists."):
        blueprint.add_data_store("my_store", {})


def test_aggregator_duplicate(blueprint):
    @blueprint.aggregator("start")
    def aggregator1(context, actions):
        pass

    with pytest.raises(ValueError, match="Aggregator for state 'start' is already registered."):

        @blueprint.aggregator("start")
        def aggregator2(context, actions):
            pass


def test_validate_no_start_state(blueprint):
    with pytest.raises(ValueError, match="Blueprint 'test_bp' must have exactly one start state."):
        blueprint.validate()


def test_find_handler_no_handler(blueprint):
    with pytest.raises(
        ValueError, match="No suitable handler found for state 'start' in blueprint 'test_bp' for the given context."
    ):
        blueprint.find_handler("start", MagicMock())


def test_render_graph_with_filename(blueprint, tmp_path):
    @blueprint.handler("start", is_start=True)
    def handler(context, actions):
        actions.go_to("end")

    @blueprint.handler("end", is_end=True)
    def end_handler(context, actions):
        pass

    filename = tmp_path / "graph"
    blueprint.render_graph(output_filename=str(filename))
    assert (tmp_path / "graph.png").exists()


def test_render_graph_no_filename(blueprint):
    @blueprint.handler("start", is_start=True)
    def handler(context, actions):
        actions.go_to("end")

    @blueprint.handler("end", is_end=True)
    def end_handler(context, actions):
        pass

    source = blueprint.render_graph()
    assert "digraph" in source


# ---------------------------------------------------------------------------
# Direct condition arguments tests
# ---------------------------------------------------------------------------


def test_handler_decorator_direct_condition_with_state_name(blueprint):
    @blueprint.handler("start", F.initial_data["status"] == "completed")
    def handler(context, actions):
        pass

    assert len(blueprint.conditional_handlers) == 1
    assert blueprint.conditional_handlers[0].state == "start"

    ctx_true = MagicMock()
    ctx_true.result = {}
    ctx_true.initial_data = {"status": "completed"}
    assert blueprint.conditional_handlers[0].evaluate(ctx_true) is True


def test_handler_decorator_direct_condition_inferred_name(blueprint):
    @blueprint.handler(F.initial_data["status"] == "completed")
    def my_custom_state(context, actions):
        pass

    assert len(blueprint.conditional_handlers) == 1
    assert blueprint.conditional_handlers[0].state == "my_custom_state"

    ctx_true = MagicMock()
    ctx_true.initial_data = {"status": "completed"}
    assert blueprint.conditional_handlers[0].evaluate(ctx_true) is True


def test_handler_decorator_direct_condition_keyword_arg(blueprint):
    @blueprint.handler(condition=F.initial_data["status"] == "completed")
    def my_custom_state(context, actions):
        pass

    assert len(blueprint.conditional_handlers) == 1
    assert blueprint.conditional_handlers[0].state == "my_custom_state"


def test_handler_decorator_direct_condition_with_is_start(blueprint):
    @blueprint.handler("start", F.initial_data["status"] == "completed", is_start=True)
    def handler(context, actions):
        pass

    assert len(blueprint.conditional_handlers) == 1
    assert blueprint.start_state == "start"
