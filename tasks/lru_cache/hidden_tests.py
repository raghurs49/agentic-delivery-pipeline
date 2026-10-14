import pytest
from solution import LRUCache


def test_classic_sequence():
    c = LRUCache(2)
    c.put(1, 1)
    c.put(2, 2)
    assert c.get(1) == 1
    c.put(3, 3)
    assert c.get(2) == -1
    c.put(4, 4)
    assert c.get(1) == -1
    assert c.get(3) == 3
    assert c.get(4) == 4


def test_update_refreshes_recency():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)
    c.put("c", 3)
    assert c.get("b") == -1
    assert c.get("a") == 10


def test_get_missing():
    assert LRUCache(1).get("x") == -1


def test_capacity_one():
    c = LRUCache(1)
    c.put(1, 1)
    c.put(2, 2)
    assert c.get(1) == -1 and c.get(2) == 2


@pytest.mark.parametrize("cap", [0, -3])
def test_bad_capacity(cap):
    with pytest.raises(ValueError):
        LRUCache(cap)
