import pytest
from solution import chunk_text


def test_with_overlap():
    assert chunk_text("abcdefghij", 4, 1) == ["abcd", "defg", "ghij"]
    assert chunk_text("abcdefgh", 4, 2) == ["abcd", "cdef", "efgh"]


def test_no_overlap():
    assert chunk_text("abcdef", 3) == ["abc", "def"]
    assert chunk_text("abcdefg", 3) == ["abc", "def", "g"]


def test_short_text_is_one_chunk():
    assert chunk_text("abcde", 10) == ["abcde"]


def test_empty():
    assert chunk_text("", 3) == []


@pytest.mark.parametrize("args", [("abc", 0), ("abc", 3, 3), ("abc", 3, 4), ("abc", 3, -1)])
def test_invalid(args):
    with pytest.raises(ValueError):
        chunk_text(*args)
