"""slackexport: rendering, users/threads, incremental mirror semantics, the source contract."""
import json
import zipfile

import pytest

from slackexport.sync import collect_months, doc_id, load_export, main, render_month, sync

USERS = [
    {"id": "U1", "name": "alice", "profile": {"display_name": "Alice Smith"}},
    {"id": "U2", "name": "bob", "profile": {"real_name": "Bob Jones", "display_name": ""}},
]


def _export(tmp_path, days=None):
    root = tmp_path / "export"
    (root / "general").mkdir(parents=True)
    (root / "client-acme").mkdir()
    (root / "users.json").write_text(json.dumps(USERS))
    days = days or {
        "general/2026-01-14.json": [
            {"ts": "1768381920.0", "user": "U1", "text": "Kickoff at 10am, ping <@U2>"},
            {"ts": "1768382100.0", "user": "U2", "text": "ack", "thread_ts": "1768381920.0"},
        ],
        "general/2026-01-15.json": [
            {"ts": "1768468500.0", "user": "U2", "text": "Budget approved: $5k for the pilot"},
        ],
        "client-acme/2026-02-01.json": [
            {"ts": "1769937300.0", "user": "U1", "text": "Acme renewal signed"},
        ],
    }
    for rel, msgs in days.items():
        p = root / rel
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(msgs))
    return root


def test_render_month_threads_users_and_mentions():
    msgs = [
        {"ts": "1768382100.0", "user": "U2", "text": "ack", "thread_ts": "1768381920.0"},
        {"ts": "1768381920.0", "user": "U1", "text": "Kickoff, ping <@U2>"},
        {"ts": "1768381000.0", "user": "U9", "text": ""},          # empty -> dropped
    ]
    users = {"U1": "Alice Smith", "U2": "Bob Jones"}
    doc = render_month("general", "2026-01", msgs, users)
    assert doc.startswith("#general — 2026-01 (Slack)")
    assert "Alice Smith: Kickoff, ping @Bob Jones" in doc          # mention resolved
    assert "    ↳ " in doc and doc.index("Kickoff") < doc.index("Bob Jones: ack")  # reply under parent
    assert "U9" not in doc


def test_orphaned_thread_replies_survive():
    """A reply whose parent is not in this month's messages (cross-month thread, or a tombstoned
    root dropped as empty) must be promoted to top level, not silently lost."""
    msgs = [
        {"ts": "1768382100.0", "user": "U2", "text": "orphan reply body", "thread_ts": "1768300000.0"},
        {"ts": "1768300000.0", "user": "U9", "text": "", "thread_ts": "1768300000.0"},  # tombstoned root
    ]
    doc = render_month("general", "2026-01", msgs, {"U2": "Bob Jones"})
    assert "orphan reply body" in doc
    assert "↳ 2026-01-14" in doc                      # promoted, still marked as a reply


def test_orphan_mark_derives_from_the_message_not_ts_lookups():
    """The ↳ mark comes from the message's own foreign thread_ts: a malformed ts-less top-level
    message must not inherit the mark of a ts-less promoted orphan (value-keyed lookups would)."""
    msgs = [
        {"user": "U1", "text": "no-ts root"},                                # malformed: no ts
        {"user": "U2", "text": "no-ts orphan", "thread_ts": "1768300000.0"},  # malformed: no ts
    ]
    doc = render_month("general", "2026-01", msgs, {})
    root_line = next(ln for ln in doc.splitlines() if "no-ts root" in ln)
    orphan_line = next(ln for ln in doc.splitlines() if "no-ts orphan" in ln)
    assert not root_line.startswith("↳")
    assert orphan_line.startswith("↳ ")


def test_cross_month_thread_reply_is_mirrored(tmp_path):
    """Slack files each reply under the day it was POSTED: a February reply to a January thread
    lands in the February bucket without its parent — the February doc must still carry it."""
    root = _export(tmp_path, days={
        "general/2026-01-31.json": [
            {"ts": "1769860800.0", "user": "U1", "text": "January thread root"},
        ],
        "general/2026-02-01.json": [
            {"ts": "1769947200.0", "user": "U2", "text": "February reply to January thread",
             "thread_ts": "1769860800.0"},
        ],
    })
    raw = tmp_path / "raw"
    stats = sync(str(root), str(raw))
    assert stats["written"] == 2                       # both months mirrored
    feb = (raw / "slack/general/2026-02.md").read_text()
    assert "February reply to January thread" in feb


def test_collect_months_groups_days(tmp_path):
    root = _export(tmp_path)
    months = collect_months(root)
    assert set(months) == {("general", "2026-01"), ("client-acme", "2026-02")}
    assert len(months[("general", "2026-01")]) == 3               # both days pooled


def test_sync_writes_contract_inventory(tmp_path):
    root = _export(tmp_path)
    raw = tmp_path / "raw"
    stats = sync(str(root), str(raw))
    assert stats["written"] == 2 and stats["removed"] == 0
    state = json.loads((raw / "_state.json").read_text())
    fid = doc_id("general", "2026-01")
    entry = state["files"][fid]
    # the source contract, field by field (ADR 011) — what clean consumes, nothing more
    assert entry["localPath"] == "slack/general/2026-01.md"
    assert entry["drivePath"] == "/Slack/general/2026-01.md"
    assert entry["orgUnit"] == "general"
    assert entry["sourceUri"] == "slack://general/2026-01"
    assert entry["mimeType"] == "text/markdown"
    body = (raw / "slack/general/2026-01.md").read_text()
    assert "$5k" in body                                           # figures survive verbatim
    assert "Alice Smith" in body


def test_sync_is_incremental_and_detects_edits(tmp_path):
    root = _export(tmp_path)
    raw = tmp_path / "raw"
    assert sync(str(root), str(raw))["written"] == 2
    again = sync(str(root), str(raw))
    assert again["written"] == 0 and again["unchanged"] == 2       # nothing rewritten

    # an edited message changes the fingerprint -> rewrite
    day = root / "general/2026-01-15.json"
    msgs = json.loads(day.read_text())
    msgs[0]["text"] = "Budget approved: $7k for the pilot"
    day.write_text(json.dumps(msgs))
    assert sync(str(root), str(raw))["written"] == 1
    assert "$7k" in (raw / "slack/general/2026-01.md").read_text()


def test_sync_deletions_propagate(tmp_path):
    root = _export(tmp_path)
    raw = tmp_path / "raw"
    sync(str(root), str(raw))
    (root / "client-acme/2026-02-01.json").unlink()
    stats = sync(str(root), str(raw))
    assert stats["removed"] == 1
    assert not (raw / "slack/client-acme/2026-02.md").exists()
    state = json.loads((raw / "_state.json").read_text())
    assert doc_id("client-acme", "2026-02") not in state["files"]


def test_sync_preserves_foreign_inventory_entries(tmp_path):
    """A shared raw dir may also hold Drive entries — the connector must only manage slack-* ids."""
    root = _export(tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "_state.json").write_text(json.dumps({"files": {
        "1DriveDoc": {"name": "x.pdf", "localPath": "x.pdf"}}}))
    sync(str(root), str(raw))
    state = json.loads((raw / "_state.json").read_text())
    assert "1DriveDoc" in state["files"]                           # untouched
    assert doc_id("general", "2026-01") in state["files"]


def test_zip_input_and_malformed_day(tmp_path):
    root = _export(tmp_path)
    (root / "general" / "2026-01-16.json").write_text("{not json")
    zpath = tmp_path / "export.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for f in root.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(root))
    assert load_export(str(zpath)).is_dir()
    raw = tmp_path / "raw-from-zip"
    stats = sync(str(zpath), str(raw))
    assert stats["written"] == 2                                   # malformed day skipped, rest fine


def test_team_permalink_and_cli(tmp_path, capsys):
    root = _export(tmp_path)
    raw = tmp_path / "raw"
    assert main(["--export", str(root), "--raw", str(raw), "--team", "acme-corp"]) == 0
    assert "written" in capsys.readouterr().out
    state = json.loads((raw / "_state.json").read_text())
    entry = state["files"][doc_id("general", "2026-01")]
    assert entry["sourceUri"] == "https://acme-corp.slack.com/archives/general"
    assert main(["--export", str(tmp_path / "nope"), "--raw", str(raw)]) == 2


def test_empty_month_is_skipped(tmp_path):
    root = _export(tmp_path, days={"general/2026-03-01.json": [{"ts": "1772335500.0", "user": "U1", "text": ""}]})
    stats = sync(str(root), str(tmp_path / "raw"))
    assert stats["written"] == 0


def test_load_export_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_export(str(tmp_path / "missing"))
