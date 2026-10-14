import pytest
from solution import prorated_amount


@pytest.mark.parametrize("price,active,dim,expected", [
    (3000, 15, 30, 1500), (1000, 10, 30, 333), (1000, 20, 30, 667), (999, 1, 30, 33),
    (1, 14, 28, 1), (3, 14, 28, 2), (3000, 0, 31, 0), (2999, 31, 31, 2999),
])
def test_amounts(price, active, dim, expected):
    assert prorated_amount(price, active, dim) == expected


@pytest.mark.parametrize("args", [(1000, 5, 27), (1000, 5, 32), (1000, -1, 30), (1000, 31, 30)])
def test_invalid(args):
    with pytest.raises(ValueError):
        prorated_amount(*args)
