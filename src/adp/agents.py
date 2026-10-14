"""Prompts, prompt variants and output parsing for the four agent roles (spec, implement, tests, review)."""
import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .models import Review, Spec


@dataclass(frozen=True)
class Variant:
    name: str
    spec_style: str          # "simple" | "structured"
    implement_style: str     # "naive" | "spec"
    max_repairs: int         # how many times a failing implementation is sent back with the test output


VARIANTS: Dict[str, Variant] = {
    "v1-naive": Variant("v1-naive", "simple", "naive", 0),
    "v2-spec-driven": Variant("v2-spec-driven", "structured", "spec", 2),
}


def get_variant(name: str) -> Variant:
    try:
        return VARIANTS[name]
    except KeyError:
        raise ValueError(f"unknown variant {name!r}; choose from {sorted(VARIANTS)}")


# ---------- prompts ----------
SPEC_SIMPLE = ("Reply with ONLY a JSON object with the keys summary, signature, acceptance_criteria (list), "
               "edge_cases (list).")
SPEC_STRUCTURED = (
    "You are a senior software engineer turning a requirement into a precise, testable specification.\n"
    "Reply with ONLY a JSON object (no prose, no markdown) with exactly these keys:\n"
    '  "summary": one sentence,\n'
    '  "signature": the exact Python signature with type hints, e.g. "def f(x: int) -> str",\n'
    '  "acceptance_criteria": a list of 3-6 short, independently testable statements,\n'
    '  "edge_cases": a list of 3-6 concrete inputs with the exact expected output or exception.\n'
    "Resolve ambiguity by choosing the most conventional behaviour and stating it explicitly."
)
IMPL_NAIVE = "Write Python code. Reply with only one ```python code block."
IMPL_SPEC = (
    "You are a careful Python engineer. Write the complete contents of solution.py so that it satisfies the specification.\n"
    "Rules: standard library only; never import os, sys, subprocess, socket or similar; no file or network access; "
    "no printing; no input(); handle every listed edge case exactly as specified.\n"
    "Reply with ONLY one ```python code block containing the whole file."
)
TESTS_SYSTEM = (
    "You are a meticulous QA engineer. Write a pytest file for the specification.\n"
    "Import the code under test with `from solution import <name>`. Write at least 4 separate test functions "
    "(one per acceptance criterion or edge case), each with several plain `assert` statements, and use `pytest.raises` for "
    "expected errors. Use real expected values computed by hand, never `assert True`. Do not skip or weaken tests.\n"
    "Reply with ONLY one ```python code block containing the whole file."
)
REVIEW_SYSTEM = (
    "You are a strict code reviewer. Score the implementation from 1 to 5 against the specification "
    "(5 = correct, complete and clear; 3 = acceptable with minor issues; 1 = wrong or unsafe). "
    'Reply with ONLY JSON: {"score": <int 1-5>, "issues": [<short strings>]}.'
)


def spec_system(v: Variant) -> str:
    return SPEC_STRUCTURED if v.spec_style == "structured" else SPEC_SIMPLE


def spec_to_text(spec: Spec) -> str:
    return json.dumps({"summary": spec.summary, "signature": spec.signature,
                       "acceptance_criteria": spec.acceptance_criteria, "edge_cases": spec.edge_cases}, indent=2)


def implement_user(v: Variant, requirement: str, spec: Spec, prev_code: str = "", failures: str = "") -> str:
    if v.implement_style == "naive":
        return requirement
    parts = [f"Requirement:\n{requirement}", f"Specification:\n{spec_to_text(spec)}"]
    if prev_code and failures:
        parts.append(f"Your previous attempt:\n```python\n{prev_code}\n```\nIt failed these tests:\n{failures}\n"
                     "Fix the code. Return the complete corrected file.")
    return "\n\n".join(parts)


def symbol_name(signature: str) -> str:
    """The function or class under test, taken from the spec signature (e.g. 'def add(a, b)' -> 'add')."""
    m = re.search(r"\b(?:def|class)\s+([A-Za-z_]\w*)", signature or "") or re.match(r"\s*([A-Za-z_]\w*)\s*\(", signature or "")
    return m.group(1) if m else ""


def tests_user(requirement: str, spec: Spec) -> str:
    name = symbol_name(spec.signature)
    hint = f"\n\nThe code under test is `{name}`; import it with `from solution import {name}`." if name else ""
    return f"Requirement:\n{requirement}\n\nSpecification:\n{spec_to_text(spec)}{hint}"


def review_user(spec: Spec, code: str, report_summary: str) -> str:
    return f"Specification:\n{spec_to_text(spec)}\n\nImplementation:\n```python\n{code}\n```\n\nTest result: {report_summary}"


# ---------- parsing ----------
_FENCE = re.compile(r"```(?:python|py|json)?\s*\n(.*?)```", re.S | re.I)


def extract_code(text: str) -> str:
    """Return the first fenced code block; fall back to the whole text if it looks like code."""
    m = _FENCE.search(text or "")
    if m:
        return m.group(1).strip("\n") + "\n"
    body = (text or "").strip()
    return body + "\n" if re.search(r"^\s*(def|class|import|from)\s", body, re.M) else ""


def _loads_lenient(raw: str) -> Optional[Dict[str, Any]]:
    """json first; then Python literal syntax (True/False/None, single quotes) which small models often emit."""
    for loader in (json.loads, ast.literal_eval):
        try:
            obj = loader(raw)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Find the first parseable JSON-like object in the text, tolerating fences and surrounding prose."""
    t = text or ""
    m = _FENCE.search(t)
    if m:
        t = m.group(1)
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            c = t[i]
            depth += (c == "{") - (c == "}")
            if depth == 0:
                obj = _loads_lenient(t[start:i + 1])
                if obj is not None:
                    return obj
                break
        start = t.find("{", start + 1)
    return None


def _item_text(x: Any) -> str:
    if isinstance(x, dict):
        return ", ".join(f"{k}={v!r}" for k, v in x.items())
    return str(x).strip()


def _str_list(v: Any) -> List[str]:
    if isinstance(v, (str, dict)):
        v = [v]
    return [t for t in (_item_text(x) for x in v) if t] if isinstance(v, list) else []


def parse_spec(text: str) -> Optional[Spec]:
    obj = extract_json(text)
    if not obj:
        return None
    spec = Spec(summary=str(obj.get("summary", "")).strip(), signature=str(obj.get("signature", "")).strip(),
                acceptance_criteria=_str_list(obj.get("acceptance_criteria")), edge_cases=_str_list(obj.get("edge_cases")))
    return spec if spec.summary and spec.signature and spec.acceptance_criteria else None


def parse_review(text: str) -> Optional[Review]:
    obj = extract_json(text)
    if not obj:
        return None
    try:
        score = int(round(float(obj.get("score"))))
    except (TypeError, ValueError):
        return None
    if not 1 <= score <= 5:
        return None
    return Review(score=score, issues=_str_list(obj.get("issues"))[:8])


# ---------- failure explanations, fed back to the model on retry ----------
def spec_error(text: str) -> str:
    if extract_json(text) is None:
        return "the reply was not a valid JSON object (check quotes, commas and that you use true/false, not True/False)"
    obj = extract_json(text) or {}
    missing = [k for k in ("summary", "signature", "acceptance_criteria") if not obj.get(k)]
    return f"required keys missing or empty: {', '.join(missing)}" if missing else "the specification could not be used"


def code_error(text: str) -> str:
    return "no ```python code block was found; reply with exactly one fenced python code block and nothing else"


def review_error(text: str) -> str:
    return 'the reply was not JSON of the form {"score": <integer 1-5>, "issues": [..]}'


def with_feedback(user: str, reason: str) -> str:
    return f"{user}\n\nYour previous reply could not be used because {reason}. Reply again and follow the required format exactly."
