from adp import guardrails as g

GOOD = "def add(a, b):\n    return a + b\n"


def test_clean_code_has_no_violations():
    assert g.check_code(GOOD) == []


def test_syntax_error_is_reported():
    out = g.check_code("def f(:\n    pass\n")
    assert out and out[0].startswith("syntax error")


def test_forbidden_imports_and_calls():
    code = "import os\nimport subprocess as sp\nfrom socket import socket\n\ndef f():\n    eval('1')\n    exec('x=1')\n    return open('x')\n"
    out = g.check_code(code)
    assert "forbidden import: os" in out
    assert "forbidden import: subprocess" in out
    assert "forbidden import: socket" in out
    assert {"forbidden call: eval()", "forbidden call: exec()", "forbidden call: open()"} <= set(out)


def test_import_of_allowed_modules_is_fine():
    assert g.check_code("import re\nimport math\nfrom collections import OrderedDict\n") == []


def test_test_tampering_patterns():
    for bad in ["import pytest\npytest.skip('x')\n", "def f():\n    assert True\n", "x = 1  # conftest\n"]:
        assert any("tampering" in v for v in g.check_code(bad)), bad


def test_secret_detection():
    assert g.check_secrets("key = 'AKIAABCDEFGHIJKLMNOP'")
    assert g.check_secrets('api_key = "abcd1234abcd1234abcd1234"')
    assert g.check_secrets("-----BEGIN RSA PRIVATE KEY-----")
    assert not g.check_secrets("password = input()")


def test_secret_in_code_blocks_it():
    assert any("secret" in v for v in g.check_code("TOKEN = 'ghp_" + "a" * 36 + "'\n"))


def test_diff_size():
    old = "\n".join(f"line{i}" for i in range(10))
    new = "\n".join(f"new{i}" for i in range(10))
    assert g.check_diff_size(old, new, max_lines=100) == []
    assert g.check_diff_size(old, new, max_lines=5)
    assert g.check_diff_size("a\nb", "a\nb") == []


def test_oversized_file():
    assert any("too large" in v for v in g.check_code("x = 1\n" * 5000))


GOOD_TESTS = ("from solution import add\n\ndef test_a():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n\n"
              "def test_b():\n    assert add(-1, 1) == 0\n")


def test_good_tests_pass_quality_check():
    assert g.check_tests(GOOD_TESTS) == []


def test_tests_too_few_or_vacuous():
    assert any("only 1 test" in v for v in g.check_tests("from solution import f\ndef test_x():\n    assert f(1) == 1\n    assert f(2) == 2\n    assert f(3) == 3\n"))
    assert any("assertion" in v for v in g.check_tests("from solution import f\ndef test_a():\n    f(1)\ndef test_b():\n    f(2)\n"))
    assert any("vacuous" in v for v in g.check_tests("from solution import f\ndef test_a():\n    assert 1\n    assert f(1)==1\n    assert f(2)==2\ndef test_b():\n    assert f(3)==3\n"))
    assert any("do not import" in v for v in g.check_tests("def test_a():\n    assert 1 == 1\ndef test_b():\n    assert 2 == 2\n    assert 3 == 3\n"))


def test_tests_with_syntax_error():
    assert g.check_tests("def test_a(:\n")[0].startswith("tests: syntax error")
