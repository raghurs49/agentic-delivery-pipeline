import pytest
from solution import top_k_frequent


def test_basic():
    assert top_k_frequent(["a", "b", "a", "c", "b", "a"], 2) == ["a", "b"]


def test_ties_are_alphabetical():
    assert top_k_frequent(["pear", "apple", "fig", "apple", "pear", "fig"], 2) == ["apple", "fig"]


def test_k_larger_than_distinct():
    assert top_k_frequent(["x", "y", "x"], 10) == ["x", "y"]


def test_empty_and_k_one():
    assert top_k_frequent([], 3) == []
    assert top_k_frequent(["b", "a"], 1) == ["a"]


@pytest.mark.parametrize("k", [0, -1])
def test_bad_k(k):
    with pytest.raises(ValueError):
        top_k_frequent(["a"], k)
