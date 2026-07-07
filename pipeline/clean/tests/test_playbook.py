"""The learned memory: caps, kill switch, advisory composition."""
from clean.playbook import PLAYBOOK_MAX_CHARS, compose_instructions, load_playbook, save_playbook


def test_roundtrip_and_stamp(tmp_path):
    body = save_playbook(str(tmp_path), "  Prefer digest for KPI exports.  ")
    assert body == "Prefer digest for KPI exports."
    loaded = load_playbook(str(tmp_path))
    assert "Prefer digest for KPI exports." in loaded
    assert "distilled by the ops supervisor" in loaded   # provenance stamp survives


def test_size_cap_enforced(tmp_path):
    save_playbook(str(tmp_path), "X" * (PLAYBOOK_MAX_CHARS * 3))
    assert len(load_playbook(str(tmp_path))) <= PLAYBOOK_MAX_CHARS


def test_missing_playbook_is_empty(tmp_path):
    assert load_playbook(str(tmp_path)) == ""


def test_kill_switch(tmp_path, monkeypatch):
    save_playbook(str(tmp_path), "hints")
    monkeypatch.setenv("CLEAN_PLAYBOOK", "off")
    assert load_playbook(str(tmp_path)) == ""


def test_compose_instructions_advisory_framing():
    assert compose_instructions("BASE", "") == "BASE"
    composed = compose_instructions("BASE", "hint one")
    assert composed.startswith("BASE")
    assert "advisory" in composed
    assert "NEVER override" in composed
    assert "hint one" in composed
