from avtomatika.blueprint import Blueprint


def test_inferred_handler_name_no_parens():
    bp = Blueprint("test")

    @bp.handler
    def implicit_state(actions):
        pass

    assert "implicit_state" in bp.handlers


def test_inferred_handler_name_with_parens():
    bp = Blueprint("test")

    @bp.handler(is_start=True)
    def my_start(actions):
        pass

    assert "my_start" in bp.handlers
    assert bp.start_state == "my_start"


def test_explicit_handler_name():
    bp = Blueprint("test")

    @bp.handler("explicit_name")
    def some_function(actions):
        pass

    assert "explicit_name" in bp.handlers
    assert "some_function" not in bp.handlers


def test_inferred_aggregator_name_no_parens():
    bp = Blueprint("test")

    @bp.aggregator
    def implicit_aggregator(actions):
        pass

    assert "implicit_aggregator" in bp.aggregator_handlers


def test_inferred_aggregator_name_with_parens():
    bp = Blueprint("test")

    @bp.aggregator()
    def another_aggregator(actions):
        pass

    assert "another_aggregator" in bp.aggregator_handlers
