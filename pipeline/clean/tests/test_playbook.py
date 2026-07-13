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


def test_full_size_playbook_body_is_not_truncated_on_load(tmp_path):
    """A maxed-out playbook must not lose its tail: save reserves room for the provenance stamp so
    load — which re-caps stamp+body to PLAYBOOK_MAX_CHARS — never drops the end of the body."""
    body = save_playbook(str(tmp_path), "H" * (PLAYBOOK_MAX_CHARS * 2))
    loaded = load_playbook(str(tmp_path))
    assert len(loaded) <= PLAYBOOK_MAX_CHARS                 # guardrail still holds
    assert body in loaded and loaded.endswith(body)          # the whole stored body survives, tail included
    assert "distilled by the ops supervisor" in loaded       # stamp still present


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


# ── the human approval gate ──────────────────────────────────────────────────
def test_pending_proposal_is_invisible_until_approved(tmp_path):
    from clean.playbook import approve_pending, save_pending
    save_pending(str(tmp_path), "Treat KPI exports as digests.")
    assert load_playbook(str(tmp_path)) == ""                    # not live yet
    body = approve_pending(str(tmp_path))
    assert body == "Treat KPI exports as digests."
    loaded = load_playbook(str(tmp_path))
    assert "Treat KPI exports as digests." in loaded
    assert "approved by the operator" in loaded                  # approval provenance
    import os

    from clean.playbook import pending_path
    assert not os.path.exists(pending_path(str(tmp_path)))       # proposal consumed


def test_reject_discards_without_touching_live(tmp_path):
    import os

    import pytest

    from clean.playbook import pending_path, reject_pending, save_pending
    save_playbook(str(tmp_path), "existing guidance")
    save_pending(str(tmp_path), "malicious or wrong guidance")
    reject_pending(str(tmp_path))
    assert not os.path.exists(pending_path(str(tmp_path)))
    assert "existing guidance" in load_playbook(str(tmp_path))   # live playbook untouched
    with pytest.raises(FileNotFoundError):
        reject_pending(str(tmp_path))                            # nothing left to reject


def test_approve_recaps_oversized_proposal(tmp_path):
    from clean.playbook import approve_pending, save_pending
    save_pending(str(tmp_path), "Y" * (PLAYBOOK_MAX_CHARS * 2))
    approve_pending(str(tmp_path))
    assert len(load_playbook(str(tmp_path))) <= PLAYBOOK_MAX_CHARS


def test_cli_show_approve_reject(tmp_path, monkeypatch, capsys):
    from clean.playbook import cli, save_pending
    monkeypatch.setenv("CLEAN_STATE_DIR", str(tmp_path))
    assert cli(["approve"]) == 1                                 # nothing pending -> rc 1
    assert "nothing pending" in capsys.readouterr().out
    save_pending(str(tmp_path), "hint")
    assert cli(["show"]) == 0
    out = capsys.readouterr().out
    assert "PENDING proposal" in out and "hint" in out
    assert cli(["approve"]) == 0
    assert "approved" in capsys.readouterr().out
    assert "hint" in load_playbook(str(tmp_path))
    save_pending(str(tmp_path), "bad hint")
    assert cli(["reject"]) == 0
    assert "hint" in load_playbook(str(tmp_path)) and "bad hint" not in load_playbook(str(tmp_path))
