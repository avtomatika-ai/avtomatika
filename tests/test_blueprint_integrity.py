import pytest

from avtomatika.blueprint import StateMachineBlueprint


def test_valid_blueprint_integrity():
    """Tests that a correctly defined blueprint passes integrity validation."""
    bp = StateMachineBlueprint("valid_bp")

    @bp.handler_for("start", is_start=True)
    async def start(actions):
        actions.transition_to("next")

    @bp.handler_for("next")
    async def next_handler(actions):
        actions.dispatch_task("task", params={}, transitions={"success": "end"})

    @bp.handler_for("end", is_end=True)
    async def end():
        pass

    # This should not raise any exception
    bp.validate()


def test_dangling_transition():
    """Tests that a transition to a non-existent state raises ValueError."""
    bp = StateMachineBlueprint("dangling_bp")

    @bp.handler_for("start", is_start=True)
    async def start(actions):
        actions.transition_to("non_existent_state")

    with pytest.raises(ValueError) as excinfo:
        bp.validate()

    assert "has a dangling transition" in str(excinfo.value)
    assert "leads to non-existent state 'non_existent_state'" in str(excinfo.value)


def test_unreachable_state():
    """Tests that a state that cannot be reached from 'start' raises ValueError."""
    bp = StateMachineBlueprint("unreachable_bp")

    @bp.handler_for("start", is_start=True)
    async def start(actions):
        actions.transition_to("end")

    @bp.handler_for("end", is_end=True)
    async def end():
        pass

    @bp.handler_for("dead_code")
    async def dead_code():
        pass

    with pytest.raises(ValueError) as excinfo:
        bp.validate()

    assert "has unreachable states: dead_code" in str(excinfo.value)


def test_aggregator_reachability():
    """Tests that aggregator states are correctly identified as reachable."""
    bp = StateMachineBlueprint("aggregator_bp")

    @bp.handler_for("start", is_start=True)
    async def start(actions):
        actions.dispatch_parallel(tasks=[], aggregate_into="aggregator")

    @bp.aggregator_for("aggregator")
    async def agg(actions):
        actions.transition_to("end")

    @bp.handler_for("end", is_end=True)
    async def end():
        pass

    # Should pass
    bp.validate()


def test_complex_transitions_reachability():
    """Tests run_blueprint and await_human_approval transitions."""
    bp = StateMachineBlueprint("complex_bp")

    @bp.handler_for("start", is_start=True)
    async def start(actions):
        actions.run_blueprint("other", initial_data={}, transitions={"success": "step2"})

    @bp.handler_for("step2")
    async def step2(actions):
        actions.await_human_approval("email", "Verify", transitions={"approved": "end"})

    @bp.handler_for("end", is_end=True)
    async def end():
        pass

    # Should pass
    bp.validate()
