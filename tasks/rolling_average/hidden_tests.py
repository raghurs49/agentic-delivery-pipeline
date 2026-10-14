import pytest
from solution import rolling_average


def test_basic():
    assert rolling_average([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]


def test_window_one_returns_values():
    assert rolling_average([4, 8], 1) == [4.0, 8.0]


def test_window_larger_than_data():
    assert rolling_average([1, 2], 5) == []
    assert rolling_average([], 2) == []


def test_rounding():
    assert rolling_average([1, 1, 2], 3) == [1.33]
    assert rolling_average([1, 2, 2], 2) == [1.5, 2.0]


@pytest.mark.parametrize("w", [0, -2])
def test_bad_window(w):
    with pytest.raises(ValueError):
        rolling_average([1, 2, 3], w)
