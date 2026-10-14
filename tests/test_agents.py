import json

from adp.agents import (code_error, extract_code, extract_json, parse_review, parse_spec, spec_error, with_feedback)


def test_extract_json_plain_fenced_and_prose():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": 1}\n```\nDone') == {"a": 1}
    assert extract_json('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    assert extract_json("no json here") is None
    assert extract_json("{broken") is None


def test_extract_json_accepts_python_literals():
    assert extract_json("{'a': True, 'b': None}") == {"a": True, "b": None}


def test_parse_spec_handles_dict_shaped_edge_cases():
    text = json.dumps({"summary": "s", "signature": "def f(x)", "acceptance_criteria": ["a"],
                       "edge_cases": [{"input": "", "output": True}]})
    spec = parse_spec(text)
    assert spec.edge_cases == ["input='', output=True"]


def test_parse_spec_rejects_missing_keys_and_explains():
    assert parse_spec('{"summary": "s"}') is None
    assert "required keys missing" in spec_error('{"summary": "s"}')
    assert "not a valid JSON" in spec_error("blah")


def test_real_world_broken_json_from_a_small_model_is_rejected_with_a_reason():
    bad = '{"summary": "x", "signature": "def f()", "acceptance_criteria": ["f(") returns False"], "edge_cases": []}'
    assert parse_spec(bad) is None and "valid JSON" in spec_error(bad)


def test_review_parsing_bounds():
    assert parse_review('{"score": 5, "issues": []}').score == 5
    assert parse_review('{"score": 0}') is None and parse_review('{"score": 6}') is None
    assert parse_review('{"score": "high"}') is None


def test_code_extraction_and_feedback_text():
    assert extract_code("```python\nx = 1\n```") == "x = 1\n"
    assert extract_code("just words") == ""
    assert "python code block" in code_error("x")
    fb = with_feedback("ORIGINAL", "reason here")
    assert fb.startswith("ORIGINAL") and "reason here" in fb


def test_symbol_name_from_signature():
    from adp.agents import symbol_name
    assert symbol_name("def add(a: int, b: int) -> int") == "add"
    assert symbol_name("class LRUCache") == "LRUCache"
    assert symbol_name("roman_to_int(s: str) -> int") == "roman_to_int"
    assert symbol_name("") == ""
