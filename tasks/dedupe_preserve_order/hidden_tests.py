from solution import dedupe_preserve_order


def test_basic():
    assert dedupe_preserve_order([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_strings_and_tuples():
    assert dedupe_preserve_order(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]
    assert dedupe_preserve_order([(1, 2), (1, 2), (2, 1)]) == [(1, 2), (2, 1)]


def test_empty_and_unique():
    assert dedupe_preserve_order([]) == []
    assert dedupe_preserve_order([1, 2, 3]) == [1, 2, 3]


def test_returns_new_list_and_keeps_input():
    data = [1, 1, 2]
    out = dedupe_preserve_order(data)
    assert data == [1, 1, 2] and out == [1, 2] and out is not data
