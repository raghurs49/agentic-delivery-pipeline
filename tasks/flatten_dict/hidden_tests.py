from solution import flatten_dict


def test_nested():
    assert flatten_dict({"a": {"b": 1, "c": {"d": 2}}, "e": 3}) == {"a.b": 1, "a.c.d": 2, "e": 3}


def test_custom_separator():
    assert flatten_dict({"a": {"b": {"c": 1}}}, sep="/") == {"a/b/c": 1}


def test_lists_are_kept_whole():
    assert flatten_dict({"a": {"b": [1, 2, {"x": 1}]}}) == {"a.b": [1, 2, {"x": 1}]}


def test_empty_nested_dict_vanishes():
    assert flatten_dict({"a": {}, "b": 1}) == {"b": 1}


def test_empty_input():
    assert flatten_dict({}) == {}


def test_input_not_modified():
    data = {"a": {"b": 1}}
    flatten_dict(data)
    assert data == {"a": {"b": 1}}
