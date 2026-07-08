# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dmitrii Gagarin aka madgagarin

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

import avtomatika
from avtomatika import F, FastF
from avtomatika.blueprint import Blueprint, ConditionalHandler


class Ctx:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_handler(condition: FastF, state: str = "s") -> ConditionalHandler:
    return ConditionalHandler(MagicMock(), state, lambda: None, condition)


class TestPublicApi:
    def test_F_is_importable_from_avtomatika(self):
        assert avtomatika.F is F

    def test_FastF_is_importable_from_avtomatika(self):
        assert avtomatika.FastF is FastF

    def test_F_is_FastF_instance(self):
        assert isinstance(F, FastF)

    def test_FastF_in_all(self):
        assert "F" in avtomatika.__all__
        assert "FastF" in avtomatika.__all__

    def test_custom_filter_is_fastf(self):
        f = F.some.attr == "value"
        assert isinstance(f, FastF)


class TestConditionalHandlerPositive:
    def test_simple_attr_match(self):
        h = make_handler(F.status == "done")
        assert h.evaluate(Ctx(status="done")) is True

    def test_dict_field_match(self):
        h = make_handler(F.data["key"] == 42)
        assert h.evaluate(Ctx(data={"key": 42})) is True

    def test_nested_attr_match(self):
        h = make_handler(F.result.code == 200)
        inner = Ctx(code=200)
        assert h.evaluate(Ctx(result=inner)) is True

    def test_and_condition(self):
        h = make_handler((F.x == 1) & (F.y == 2))
        assert h.evaluate(Ctx(x=1, y=2)) is True

    def test_or_condition_first_branch(self):
        h = make_handler((F.status == "ok") | (F.status == "done"))
        assert h.evaluate(Ctx(status="ok")) is True

    def test_or_condition_second_branch(self):
        h = make_handler((F.status == "ok") | (F.status == "done"))
        assert h.evaluate(Ctx(status="done")) is True

    def test_not_condition(self):
        h = make_handler(~(F.status == "error"))
        assert h.evaluate(Ctx(status="ok")) is True

    def test_gt_condition(self):
        h = make_handler(F.score > 80)
        assert h.evaluate(Ctx(score=90)) is True

    def test_gte_condition(self):
        h = make_handler(F.score >= 80)
        assert h.evaluate(Ctx(score=80)) is True

    def test_lt_condition(self):
        h = make_handler(F.retries < 3)
        assert h.evaluate(Ctx(retries=2)) is True

    def test_lte_condition(self):
        h = make_handler(F.retries <= 3)
        assert h.evaluate(Ctx(retries=3)) is True

    def test_ne_condition(self):
        h = make_handler(F.status != "error")
        assert h.evaluate(Ctx(status="ok")) is True

    def test_in_operator(self):
        h = make_handler(F.role.in_(["admin", "moderator"]))
        assert h.evaluate(Ctx(role="admin")) is True

    def test_not_in_operator(self):
        h = make_handler(F.role.not_in(["banned", "guest"]))
        assert h.evaluate(Ctx(role="admin")) is True

    def test_regexp_match(self):
        h = make_handler(F.text.regexp(r"done$"))
        assert h.evaluate(Ctx(text="task done")) is True

    def test_func_callback(self):
        h = make_handler(F.value.func(lambda v: v % 2 == 0))
        assert h.evaluate(Ctx(value=4)) is True

    def test_cast_and_compare(self):
        h = make_handler(F.raw.cast(int) == 42)
        assert h.evaluate(Ctx(raw="42")) is True

    def test_len_condition(self):
        h = make_handler(F.items.len() > 0)
        assert h.evaluate(Ctx(items=[1, 2, 3])) is True

    def test_any_on_list(self):
        h = make_handler((F.nums[...] > 5).any())
        assert h.evaluate(Ctx(nums=[1, 3, 7])) is True

    def test_all_on_list(self):
        h = make_handler((F.flags[...] == True).all())  # noqa: E712
        assert h.evaluate(Ctx(flags=[True, True, True])) is True

    def test_method_call_on_attr(self):
        h = make_handler(F.text.startswith("hello"))
        assert h.evaluate(Ctx(text="hello world")) is True

    def test_arithmetic_expression(self):
        h = make_handler((F.x + F.y) == 10)
        assert h.evaluate(Ctx(x=3, y=7)) is True

    def test_is_none(self):
        h = make_handler(F.result.is_(None))
        assert h.evaluate(Ctx(result=None)) is True

    def test_is_not_none(self):
        h = make_handler(F.result.is_not(None))
        assert h.evaluate(Ctx(result=42)) is True

    def test_deeply_nested_chain(self):
        h = make_handler(F.a.b.c.d == "deep")
        level3 = Ctx(d="deep")
        level2 = Ctx(c=level3)
        level1 = Ctx(b=level2)
        assert h.evaluate(Ctx(a=level1)) is True


class TestConditionalHandlerNegative:
    def test_simple_attr_mismatch(self):
        h = make_handler(F.status == "done")
        assert h.evaluate(Ctx(status="pending")) is False

    def test_and_one_branch_fails(self):
        h = make_handler((F.x == 1) & (F.y == 2))
        assert h.evaluate(Ctx(x=1, y=99)) is False

    def test_and_both_branches_fail(self):
        h = make_handler((F.x == 1) & (F.y == 2))
        assert h.evaluate(Ctx(x=0, y=0)) is False

    def test_or_both_branches_fail(self):
        h = make_handler((F.status == "ok") | (F.status == "done"))
        assert h.evaluate(Ctx(status="error")) is False

    def test_not_negation_fails(self):
        h = make_handler(~(F.status == "error"))
        assert h.evaluate(Ctx(status="error")) is False

    def test_gt_boundary_fail(self):
        h = make_handler(F.score > 80)
        assert h.evaluate(Ctx(score=80)) is False

    def test_in_not_present(self):
        h = make_handler(F.role.in_(["admin"]))
        assert h.evaluate(Ctx(role="guest")) is False

    def test_regexp_no_match(self):
        h = make_handler(F.text.regexp(r"^error"))
        assert h.evaluate(Ctx(text="task done")) is False

    def test_len_zero_fails_gt(self):
        h = make_handler(F.items.len() > 0)
        assert h.evaluate(Ctx(items=[])) is False

    def test_all_on_list_one_false(self):
        h = make_handler((F.flags[...] == True).all())  # noqa: E712
        assert h.evaluate(Ctx(flags=[True, False, True])) is False

    def test_any_on_empty_list(self):
        h = make_handler((F.nums[...] > 5).any())
        assert h.evaluate(Ctx(nums=[])) is False

    def test_method_call_mismatch(self):
        h = make_handler(F.text.startswith("error"))
        assert h.evaluate(Ctx(text="hello world")) is False

    def test_cast_wrong_value(self):
        h = make_handler(F.raw.cast(int) == 99)
        assert h.evaluate(Ctx(raw="42")) is False


class TestConditionalHandlerBoundary:
    def test_missing_top_level_attr_returns_false(self):
        h = make_handler(F.nonexistent == "value")
        ctx = Ctx()
        assert h.evaluate(ctx) is False

    def test_missing_nested_attr_returns_false(self):
        h = make_handler(F.a.b.c == "value")
        assert h.evaluate(Ctx(a=None)) is False

    def test_none_context_returns_false(self):
        h = make_handler(F.status == "done")
        assert h.evaluate(None) is False

    def test_dict_missing_key_returns_false(self):
        h = make_handler(F.data["missing"] == 1)
        assert h.evaluate(Ctx(data={})) is False

    def test_type_error_in_comparison_returns_false(self):
        h = make_handler(F.value > 5)
        assert h.evaluate(Ctx(value=[1, 2, 3])) is False

    def test_none_value_eq_none(self):
        h = make_handler(F.absent == None)  # noqa: E711
        assert h.evaluate(Ctx()) is True

    def test_none_value_ne_concrete(self):
        h = make_handler(F.absent == 42)
        assert h.evaluate(Ctx()) is False

    def test_empty_string_match(self):
        h = make_handler(F.text == "")
        assert h.evaluate(Ctx(text="")) is True

    def test_zero_value_match(self):
        h = make_handler(F.count == 0)
        assert h.evaluate(Ctx(count=0)) is True

    def test_false_value_match(self):
        h = make_handler(F.flag == False)  # noqa: E712
        assert h.evaluate(Ctx(flag=False)) is True

    def test_condition_is_reusable(self):
        h = make_handler(F.n > 5)
        assert h.evaluate(Ctx(n=10)) is True
        assert h.evaluate(Ctx(n=1)) is False
        assert h.evaluate(Ctx(n=10)) is True

    def test_deepcopy_of_filter_works(self):
        original = F.status == "done"
        copied = deepcopy(original)
        h = make_handler(copied)
        assert h.evaluate(Ctx(status="done")) is True
        assert h.evaluate(Ctx(status="fail")) is False

    def test_method_on_none_attr_returns_false(self):
        h = make_handler(F.text.upper() == "HELLO")
        assert h.evaluate(Ctx(text=None)) is False

    def test_division_by_zero_does_not_raise(self):
        h = make_handler((F.x / F.y) == 0)
        assert h.evaluate(Ctx(x=1, y=0)) is False

    def test_any_on_none_list_returns_false(self):
        h = make_handler((F.items[...] == 1).any())
        assert h.evaluate(Ctx(items=None)) is False

    def test_complex_nested_dict_condition(self):
        h = make_handler((F.meta["retry_count"] < 5) & (F.meta["status"] == "running"))
        assert h.evaluate(Ctx(meta={"retry_count": 3, "status": "running"})) is True
        assert h.evaluate(Ctx(meta={"retry_count": 5, "status": "running"})) is False
        assert h.evaluate(Ctx(meta={"retry_count": 3, "status": "done"})) is False

    def test_regexp_in_middle_of_string(self):
        h = make_handler(F.text.regexp(r"world"))
        assert h.evaluate(Ctx(text="hello world")) is True
        assert h.evaluate(Ctx(text="hello")) is False

    def test_attr_named_like_reserved_method_via_attr_(self):
        h = make_handler(F.attr_("resolve") == "sentinel")
        assert h.evaluate(Ctx(resolve="sentinel")) is True
        assert h.evaluate(Ctx(resolve="other")) is False


class TestBlueprintConditionIntegration:
    @pytest.fixture
    def bp(self):
        return Blueprint("test_bp")

    def test_condition_registers_conditional_handler(self, bp):
        @bp.handler("step", F.data["v"] == 1)
        def h(actions):
            pass

        assert len(bp.conditional_handlers) == 1
        assert bp.conditional_handlers[0].state == "step"
        assert bp.conditional_handlers[0].func is h

    def test_condition_find_handler_positive(self, bp):
        @bp.handler("step", F.data["v"] == 1)
        def h(actions):
            pass

        ctx = Ctx(data={"v": 1})
        assert bp.find_handler("step", ctx) is h

    def test_condition_find_handler_falls_through_to_default(self, bp):
        @bp.handler("step", F.data["v"] == 1)
        def conditional(actions):
            pass

        @bp.handler("step")
        def default(actions):
            pass

        ctx = Ctx(data={"v": 99})
        assert bp.find_handler("step", ctx) is default

    def test_condition_multiple_conditions_first_match_wins(self, bp):
        @bp.handler("step", F.v == 1)
        def h1(actions):
            pass

        @bp.handler("step", F.v == 2)
        def h2(actions):
            pass

        assert bp.find_handler("step", Ctx(v=1)) is h1
        assert bp.find_handler("step", Ctx(v=2)) is h2

    def test_condition_no_match_no_default_raises(self, bp):
        @bp.handler("step", F.v == 1)
        def h(actions):
            pass

        with pytest.raises(ValueError, match="All conditions failed"):
            bp.find_handler("step", Ctx(v=99))

    def test_condition_missing_attr_falls_to_default(self, bp):
        @bp.handler("step", F.missing_attr == "x")
        def conditional(actions):
            pass

        @bp.handler("step")
        def default(actions):
            pass

        ctx = Ctx()
        assert bp.find_handler("step", ctx) is default

    def test_condition_complex_condition_and(self, bp):
        @bp.handler("step", (F.score >= 80) & (F.passed == True))  # noqa: E712
        def h(actions):
            pass

        assert bp.find_handler("step", Ctx(score=85, passed=True)) is h

        with pytest.raises(ValueError):
            bp.find_handler("step", Ctx(score=85, passed=False))

    def test_condition_invert_condition(self, bp):
        @bp.handler("step", ~(F.error == "fatal"))
        def h(actions):
            pass

        assert bp.find_handler("step", Ctx(error="minor")) is h

        with pytest.raises(ValueError):
            bp.find_handler("step", Ctx(error="fatal"))

    def test_condition_same_condition_multiple_states(self, bp):
        cond = F.active == True  # noqa: E712

        @bp.handler("a", cond)
        def ha(actions):
            pass

        @bp.handler("b", cond)
        def hb(actions):
            pass

        ctx = Ctx(active=True)
        assert bp.find_handler("a", ctx) is ha
        assert bp.find_handler("b", ctx) is hb


class TestGetContract:
    def test_conditional_handlers_in_contract(self):
        bp = Blueprint("test_bp")

        @bp.handler("step", F.v == 1)
        def h(actions):
            pass

        contract = bp.get_contract()
        assert len(contract["conditional_handlers"]) == 1
        entry = contract["conditional_handlers"][0]
        assert entry["state"] == "step"
        assert "v" in entry["condition"]
        assert isinstance(entry["condition"], str)

    def test_contract_no_conditions_empty_list(self):
        bp = Blueprint("test_bp")

        @bp.handler("step")
        def h(actions):
            pass

        contract = bp.get_contract()
        assert contract["conditional_handlers"] == []
