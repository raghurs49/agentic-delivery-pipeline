"""Tests for the tests: every hidden suite must accept its reference solution and reject broken ones."""
import pytest

from adp.evals import load_tasks, score_hidden

TASKS = load_tasks()


def test_there_are_enough_tasks():
    assert len(TASKS) >= 12 and len({t.id for t in TASKS}) == len(TASKS)


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_reference_passes_hidden_tests(task):
    assert score_hidden(task, task.reference), f"reference solution fails hidden tests for {task.id}"


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_empty_stub_fails_hidden_tests(task):
    assert not score_hidden(task, "def _unused():\n    return None\n")


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_requirement_names_the_symbol_under_test(task):
    # the requirement must name the function/class the hidden tests import
    import re
    names = re.findall(r"from solution import (\w+)", task.hidden_tests)
    assert names and all(n in task.requirement for n in names)
