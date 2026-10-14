"""Deterministic guardrails applied to agent-generated work. Pure functions, no I/O, fully unit-tested.

These are a first line of defence, not a security boundary: real isolation needs containers / gVisor / a
separate account (see docs/DESIGN.md).
"""
import ast
import difflib
import re
from typing import List

MAX_DIFF_LINES = 200
MAX_FILE_BYTES = 20_000

FORBIDDEN_IMPORTS = {"os", "subprocess", "socket", "requests", "urllib", "http", "ftplib", "smtplib", "shutil",
                     "ctypes", "multiprocessing", "importlib", "pickle", "sys", "builtins", "pty", "signal"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "exit", "quit"}
TEST_TAMPERING = [r"pytest\.skip", r"pytest\.xfail", r"sys\.exit", r"os\._exit", r"conftest", r"monkeypatch",
                  r"unittest\.mock\.patch\(\s*['\"]builtins", r"__class__\s*=", r"assert\s+True\b"]
SECRET_PATTERNS = {
    "aws access key": r"AKIA[0-9A-Z]{16}",
    "private key block": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "bearer/api token": r"(?i)(?:api[_-]?key|secret|token|passwd|password)\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{16,}['\"]",
    "github token": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
}


def check_code(code: str) -> List[str]:
    """Static checks on generated source: syntax, size, forbidden imports/calls, tampering patterns, secrets."""
    out: List[str] = []
    if len(code.encode("utf-8")) > MAX_FILE_BYTES:
        out.append(f"file too large (> {MAX_FILE_BYTES} bytes)")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return out + [f"syntax error: {e.msg} (line {e.lineno})"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    out.append(f"forbidden import: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                out.append(f"forbidden import: {node.module}")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in FORBIDDEN_CALLS:
                out.append(f"forbidden call: {name}()")
    for pat in TEST_TAMPERING:
        if re.search(pat, code):
            out.append(f"test-tampering pattern: {pat}")
    out += check_secrets(code)
    return sorted(set(out))


def check_secrets(text: str) -> List[str]:
    return [f"possible secret: {label}" for label, pat in SECRET_PATTERNS.items() if re.search(pat, text)]


def check_diff_size(old: str, new: str, max_lines: int = MAX_DIFF_LINES) -> List[str]:
    changed = sum(1 for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
                  if l[:1] in "+-" and l[:3] not in ("+++", "---"))
    return [f"diff too large: {changed} changed lines (limit {max_lines})"] if changed > max_lines else []


def check_tests(tests_code: str, min_tests: int = 2, min_asserts: int = 3) -> List[str]:
    """Generated tests must be real tests: parseable, enough test functions, enough assertions, not vacuous."""
    try:
        tree = ast.parse(tests_code)
    except SyntaxError as e:
        return [f"tests: syntax error: {e.msg} (line {e.lineno})"]
    n_tests = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test"))
    n_asserts = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Assert))
    n_raises = len(re.findall(r"pytest\.raises", tests_code))
    out = []
    if n_tests < min_tests:
        out.append(f"tests: only {n_tests} test function(s), need at least {min_tests}")
    if n_asserts + n_raises < min_asserts:
        out.append(f"tests: only {n_asserts + n_raises} assertion(s), need at least {min_asserts}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant) and node.test.value:
            out.append("tests: vacuous assertion (assert <constant>)")
            break
    if "solution" not in tests_code:
        out.append("tests: do not import from the solution module")
    return out
