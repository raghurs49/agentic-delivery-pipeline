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
