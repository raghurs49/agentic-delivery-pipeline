import pytest
from solution import roman_to_int


@pytest.mark.parametrize("s,n", [("III", 3), ("IV", 4), ("IX", 9), ("LVIII", 58), ("MCMXCIV", 1994), ("MMXXIV", 2024), ("XL", 40), ("CD", 400)])
def test_values(s, n):
    assert roman_to_int(s) == n


@pytest.mark.parametrize("bad", ["", "ABC", "iv", "X1", "I V"])
def test_invalid(bad):
    with pytest.raises(ValueError):
        roman_to_int(bad)
