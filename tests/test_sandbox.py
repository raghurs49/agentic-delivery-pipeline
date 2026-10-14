from adp.sandbox import run_pytest

SOL = "def add(a, b):\n    return a + b\n"
T_OK = "from solution import add\n\ndef test_a():\n    assert add(1, 2) == 3\n\ndef test_b():\n    assert add(0, 0) == 0\n"
T_FAIL = "from solution import add\n\ndef test_a():\n    assert add(1, 2) == 4\n\ndef test_b():\n    assert add(0, 0) == 0\n"


def test_passing_tests():
    r = run_pytest({"solution.py": SOL, "test_solution.py": T_OK})
    assert r.passed and r.n_passed == 2 and r.n_failed == 0 and not r.timed_out


def test_failing_tests_report_counts_and_output():
    r = run_pytest({"solution.py": SOL, "test_solution.py": T_FAIL})
    assert not r.passed and r.n_passed == 1 and r.n_failed == 1
    assert "assert" in r.output


def test_import_error_is_a_failure_not_a_pass():
    r = run_pytest({"solution.py": "x = (", "test_solution.py": T_OK})
    assert not r.passed


def test_infinite_loop_is_killed_by_timeout():
    sol = "def add(a, b):\n    while True:\n        pass\n"
    r = run_pytest({"solution.py": sol, "test_solution.py": T_OK}, timeout_s=3)
    assert r.timed_out and not r.passed and r.duration_s < 10


def test_unsafe_filenames_are_refused():
    r = run_pytest({"../evil.py": "x=1"})
    assert not r.passed and "unsafe" in r.output
    r = run_pytest({"/tmp/evil.py": "x=1"})
    assert not r.passed


def test_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "hunter2")
    t = "import os\nfrom solution import f\n\ndef test_a():\n    assert f() is None\n    assert os.environ.get('SUPER_SECRET_TOKEN') is None\n\ndef test_b():\n    assert True\n"
    r = run_pytest({"solution.py": "def f():\n    return None\n", "test_solution.py": t})
    assert r.passed, r.output


def test_output_is_deterministic_and_free_of_temp_paths_and_timings():
    sol = "def add(a, b):\n    return a - b\n"
    r1 = run_pytest({"solution.py": sol, "test_solution.py": T_OK})
    r2 = run_pytest({"solution.py": sol, "test_solution.py": T_OK})
    assert not r1.passed
    assert r1.output == r2.output                      # identical across runs (different temp dirs, different timings)
    assert "adp-sandbox-" not in r1.output and "/var/folders" not in r1.output and "/tmp/" not in r1.output
    assert "in <t>s" in r1.output


def test_scrub_replaces_paths_timings_and_addresses():
    from adp.sandbox import scrub
    raw = "File /tmp/adp-sandbox-abc/solution.py line 3 <function f at 0x10a2b3c40>\n1 failed, 2 passed in 0.12s"
    out = scrub(raw, "/tmp/adp-sandbox-abc")
    assert out == "File <sandbox>/solution.py line 3 <function f at 0x...>\n1 failed, 2 passed in <t>s"


def test_scrub_handles_macos_style_symlinked_tmpdir_in_both_spellings(monkeypatch):
    """/var/x and /private/var/x are the same directory on macOS; both spellings must scrub to the same text."""
    import os
    from adp import sandbox
    monkeypatch.setattr(os.path, "realpath", lambda p: "/private" + p if p.startswith("/var/") else p)
    for raw in ("File /private/var/x/solution.py line 3", "File /var/x/solution.py line 3"):
        assert sandbox.scrub(raw, "/var/x") == "File <sandbox>/solution.py line 3"
