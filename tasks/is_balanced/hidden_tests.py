import pytest
from solution import is_balanced


@pytest.mark.parametrize("s", ["", "()", "([{}])", "a(b)c", "{[()()]}", "no brackets at all"])
def test_balanced(s):
    assert is_balanced(s) is True


@pytest.mark.parametrize("s", ["(]", "((", ")(", "{[}]", "(()", "]"])
def test_unbalanced(s):
    assert is_balanced(s) is False
