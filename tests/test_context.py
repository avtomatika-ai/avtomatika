# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin


from itertools import permutations

import pytest
from src.avtomatika.context import ActionFactory


def test_action_factory_go_to():
    actions = ActionFactory("job-1")
    actions.go_to("next_state")
    assert actions.next_state == "next_state"


def test_action_factory_dispatch_task():
    actions = ActionFactory("job-1")
    actions.dispatch_task("test_task", {}, {})
    assert actions.task_to_dispatch is not None


def test_action_factory_run_blueprint():
    actions = ActionFactory("job-1")
    actions.run_blueprint("child_bp", {}, {})
    assert actions.sub_blueprint_to_run is not None


def test_action_factory_dispatch_parallel():
    actions = ActionFactory("job-1")
    actions.dispatch_parallel([{}], "agg_state")
    assert actions.parallel_tasks_to_dispatch is not None


ACTIONS_TO_TEST = [
    ("go_to", ("next_state",)),
    ("dispatch_task", ("test_task", {}, {})),
    ("run_blueprint", ("child_bp", {}, {})),
    ("dispatch_parallel", ([{}], "agg_state")),
]

ACTION_PAIRS = list(permutations(ACTIONS_TO_TEST, 2))


@pytest.mark.parametrize("action1_data, action2_data", ACTION_PAIRS)
def test_action_factory_multiple_actions_error(action1_data, action2_data):
    """
    Checks that any two activities in the same ActionFactory
    call RuntimeError.
    """
    action1_name, action1_args = action1_data
    action2_name, action2_args = action2_data
    actions = ActionFactory("job-1")
    try:
        action1_method = getattr(actions, action1_name)
    except AttributeError:
        pytest.fail(f"Method {action1_name} not found in ActionFactory")
    action1_method(*action1_args)
    try:
        action2_method = getattr(actions, action2_name)
    except AttributeError:
        pytest.fail(f"Method {action2_name} not found in ActionFactory")
    with pytest.raises(RuntimeError, match="Cannot set multiple actions in the same step"):
        action2_method(*action2_args)
