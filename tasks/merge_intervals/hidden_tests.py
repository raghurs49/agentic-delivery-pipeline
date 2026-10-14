from solution import merge_intervals


def test_basic():
    assert merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]


def test_touching_merges():
    assert merge_intervals([[1, 3], [3, 5]]) == [[1, 5]]


def test_unsorted_input():
    assert merge_intervals([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]


def test_nested():
    assert merge_intervals([[1, 10], [2, 3], [4, 5]]) == [[1, 10]]


def test_empty_and_single():
    assert merge_intervals([]) == []
    assert merge_intervals([[5, 7]]) == [[5, 7]]


def test_input_not_modified():
    data = [[8, 10], [1, 3], [2, 6]]
    merge_intervals(data)
    assert data == [[8, 10], [1, 3], [2, 6]]
