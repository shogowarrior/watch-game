"""tests/runner.py: module selection."""
import runner


def test_selectors_accept_paths_and_names():
    assert runner._module("tests/test_game.py") == runner._module("test_game.py") == "test_game"
    assert runner._module("test_game") == "test_game"


def test_select_keeps_known_and_reports_unknown():
    assert runner._select(["test_a", "test_b"], ["tests/test_b.py", "test_x", "test_a"]) == (
        ["test_a", "test_b"], ["test_x"])
