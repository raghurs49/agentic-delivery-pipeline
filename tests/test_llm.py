import json
import threading

import pytest

from adp.llm import FakeProvider, RecordingProvider, ReplayProvider, prompt_key


def _provider(path, text):
    return RecordingProvider(FakeProvider(lambda stage, system, user, idx: text), str(path))


def test_record_then_replay_roundtrip(tmp_path):
    path = tmp_path / "rec.json"
    rec = _provider(path, "hello")
    r = rec.generate(stage="spec", model="m", system="S", user="U")
    assert r.text == "hello"
    data = json.loads(path.read_text())
    entry = data[prompt_key("spec", "m", "S", "U")]
    assert entry["system"] == "S" and entry["user"] == "U" and entry["text"] == "hello"
    assert ReplayProvider(str(path)).generate(stage="spec", model="m", system="S", user="U").text == "hello"


def test_replay_names_the_missing_recording(tmp_path):
    path = tmp_path / "rec.json"
    _provider(path, "x").generate(stage="spec", model="m", system="S", user="U")
    with pytest.raises(KeyError, match="no recording for stage=spec"):
        ReplayProvider(str(path)).generate(stage="spec", model="m", system="S", user="CHANGED")


def test_two_recorders_on_the_same_file_do_not_lose_entries(tmp_path):
    """Regression: an unlocked read-modify-write let one process overwrite another's recordings."""
    path = tmp_path / "rec.json"
    a, b = _provider(path, "A"), _provider(path, "B")

    def work(p, tag):
        for i in range(25):
            p.generate(stage="s", model="m", system="S", user=f"{tag}{i}")

    ts = [threading.Thread(target=work, args=(a, "a")), threading.Thread(target=work, args=(b, "b"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(json.loads(path.read_text())) == 50


def test_recorded_keys_are_independent_of_seed_and_include_stage_attempt():
    assert prompt_key("spec", "m", "S", "U") != prompt_key("spec#1", "m", "S", "U")
    assert prompt_key("spec", "m", "S", "U") != prompt_key("spec", "m2", "S", "U")


def test_replay_tolerates_different_pytest_wording_in_repair_prompts_but_not_other_changes(tmp_path):
    from adp.agents import IMPL_SPEC, get_variant, implement_user
    from adp.models import Spec

    v, spec = get_variant("v2-spec-driven"), Spec(summary="s", signature="def f()", acceptance_criteria=["a"])
    mac = implement_user(v, "REQ", spec, "def f():\n    return 1\n", "E assert 1 == 2   (python 3.12 wording)")
    linux = implement_user(v, "REQ", spec, "def f():\n    return 1\n", "E assert 1 == 2\nE   ^^^ (python 3.10 wording)")
    assert mac != linux
    path = tmp_path / "rec.json"
    RecordingProvider(FakeProvider(lambda *a: "FIXED"), str(path)).generate(stage="implement1", model="m", system=IMPL_SPEC, user=mac)
    rp = ReplayProvider(str(path))
    assert rp.generate(stage="implement1", model="m", system=IMPL_SPEC, user=linux).text == "FIXED"
    # a different previous attempt (or any other prompt change) must still fail the replay
    changed = implement_user(v, "REQ", spec, "def f():\n    return 2\n", "E assert 1 == 2")
    with pytest.raises(KeyError):
        rp.generate(stage="implement1", model="m", system=IMPL_SPEC, user=changed)
    with pytest.raises(KeyError):
        rp.generate(stage="implement2", model="m", system=IMPL_SPEC, user=linux)
