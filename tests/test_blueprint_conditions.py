# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from unittest.mock import MagicMock

import pytest
from src.avtomatika.blueprint import OPERATORS, Condition, ConditionalHandler, StateMachineBlueprint, _parse_condition


def test_parse_condition_valid():
    condition_str = "context.initial_data.status == 'completed'"
    condition = _parse_condition(condition_str)
    assert isinstance(condition, Condition)
    assert condition.area == "initial_data"
    assert condition.field == "status"
    assert condition.op == OPERATORS["=="]
    assert condition.value == "completed"


def test_parse_condition_invalid_format():
    with pytest.raises(ValueError, match="Invalid condition string format"):
        _parse_condition("invalid condition")


def test_parse_condition_unsupported_operator():
    with pytest.raises(ValueError, match="Invalid condition string format"):
        _parse_condition("context.initial_data.status ** 'completed'")


@pytest.fixture
def mock_blueprint():
    return MagicMock()


@pytest.fixture
def conditional_handler(mock_blueprint):
    return ConditionalHandler(mock_blueprint, "start", lambda: None, "context.initial_data.status == 'completed'")


def test_conditional_handler_evaluate_true(conditional_handler):
    context = MagicMock()
    context.initial_data = {"status": "completed"}
    assert conditional_handler.evaluate(context) is True


def test_conditional_handler_evaluate_false(conditional_handler):
    context = MagicMock()
    context.initial_data = {"status": "pending"}
    assert conditional_handler.evaluate(context) is False


def test_conditional_handler_evaluate_missing_area(conditional_handler):
    context = MagicMock()
    del context.initial_data
    assert conditional_handler.evaluate(context) is False


def test_conditional_handler_evaluate_missing_field(conditional_handler):
    context = MagicMock()
    context.initial_data = {}
    assert conditional_handler.evaluate(context) is False


@pytest.fixture
def blueprint():
    return StateMachineBlueprint("test_bp")


def test_handler_decorator_duplicate_handler(blueprint):
    @blueprint.handler_for("start")
    def handler1(context, actions):
        pass

    with pytest.raises(ValueError, match="Default handler for state 'start' is already registered."):

        @blueprint.handler_for("start")
        def handler2(context, actions):
            pass


def test_handler_decorator_duplicate_start_state(blueprint):
    @blueprint.handler_for("start", is_start=True)
    def handler1(context, actions):
        pass

    with pytest.raises(ValueError, match="Blueprint 'test_bp' already has a start state: 'start'."):

        @blueprint.handler_for("another_start", is_start=True)
        def handler2(context, actions):
            pass


def test_handler_decorator_when(blueprint):
    @blueprint.handler_for("start").when("context.initial_data.status == 'completed'")
    def handler(context, actions):
        pass

    assert len(blueprint.conditional_handlers) == 1
    assert blueprint.conditional_handlers[0].state == "start"


def test_add_data_store_duplicate(blueprint):
    blueprint.add_data_store("my_store", {})
    with pytest.raises(ValueError, match="Data store with name 'my_store' already exists."):
        blueprint.add_data_store("my_store", {})


def test_aggregator_for_duplicate(blueprint):
    @blueprint.aggregator_for("start")
    def aggregator1(context, actions):
        pass

    with pytest.raises(ValueError, match="Aggregator for state 'start' is already registered."):

        @blueprint.aggregator_for("start")
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
    @blueprint.handler_for("start", is_start=True)
    def handler(context, actions):
        actions.transition_to("end")

    @blueprint.handler_for("end", is_end=True)
    def end_handler(context, actions):
        pass

    filename = tmp_path / "graph"
    blueprint.render_graph(output_filename=str(filename))
    assert (tmp_path / "graph.png").exists()


def test_render_graph_no_filename(blueprint):
    @blueprint.handler_for("start", is_start=True)
    def handler(context, actions):
        actions.transition_to("end")

    @blueprint.handler_for("end", is_end=True)
    def end_handler(context, actions):
        pass

    source = blueprint.render_graph()
    assert "digraph" in source
