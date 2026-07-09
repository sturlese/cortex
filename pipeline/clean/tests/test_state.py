"""State, idempotency and pending classification."""
import json
import os

from clean.state import (
    classify_pending,
    file_sha256,
    load_inventory,
    load_state,
    save_state,
    write_json_atomic,
)


def _touch(dirpath, name, content="x"):
    p = os.path.join(dirpath, name)
    with open(p, "w") as f:
        f.write(content)
    return p


def test_state_roundtrip(tmp_path):
    state = load_state(str(tmp_path))
    assert state == {"version": 1, "files": {}}
    state["files"]["A"] = {"status": "processed"}
    save_state(str(tmp_path), state)
    assert load_state(str(tmp_path))["files"]["A"]["status"] == "processed"


def test_write_json_atomic_creates_parents(tmp_path):
    p = tmp_path / "nested" / "x.json"
    write_json_atomic(str(p), {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}


def test_file_sha256_changes_with_content(tmp_path):
    a = _touch(str(tmp_path), "a", "one")
    b = _touch(str(tmp_path), "b", "two")
    assert file_sha256(a) != file_sha256(b)


def test_load_inventory_missing_and_malformed(tmp_path):
    assert load_inventory(str(tmp_path)) is None
    _touch(str(tmp_path), "_state.json", "not json")
    assert load_inventory(str(tmp_path)) is None
    _touch(str(tmp_path), "_state.json", json.dumps({"files": {"A": {"name": "a"}}}))
    assert load_inventory(str(tmp_path)) == {"A": {"name": "a"}}


def test_classify_pending_new_changed_error_deleted(tmp_path):
    raw = str(tmp_path)
    _touch(raw, "a.pdf", "v1")
    _touch(raw, "b.pdf", "v2")
    _touch(raw, "c.pdf", "v3")
    inventory = {
        "A": {"localPath": "a.pdf"},            # new
        "B": {"localPath": "b.pdf"},            # unchanged
        "C": {"localPath": "c.pdf"},            # previous error -> retry
        "M": {"localPath": "missing.pdf"},      # file not on disk -> ignored
    }
    state = {"files": {
        "B": {"rawHash": file_sha256(os.path.join(raw, "b.pdf")), "status": "processed"},
        "C": {"rawHash": file_sha256(os.path.join(raw, "c.pdf")), "status": "error"},
        "G": {"status": "processed"},           # gone from inventory -> deleted
        "H": {"status": "deleted"},             # already deleted -> not re-queued
    }}
    pending = classify_pending(inventory, state, raw)
    by_id = {p["fileId"]: p["reason"] for p in pending}
    assert by_id == {"A": "new", "C": "changed", "G": "deleted"}


def test_classify_pending_reprocesses_restored_after_delete(tmp_path):
    """A file deleted (its page removed) that reappears with the SAME bytes must be regenerated."""
    raw = str(tmp_path)
    _touch(raw, "a.pdf", "v1")
    h = file_sha256(os.path.join(raw, "a.pdf"))
    inventory = {"A": {"localPath": "a.pdf"}}
    state = {"files": {"A": {"rawHash": h, "status": "deleted"}}}
    pending = classify_pending(inventory, state, raw)
    assert [p["fileId"] for p in pending] == ["A"]


def test_classify_pending_promotes_orphaned_duplicate(tmp_path):
    """When the canonical of a duplicate leaves the inventory, the duplicate must be reprocessed
    so the content it still holds gets its own page (otherwise it is lost forever)."""
    raw = str(tmp_path)
    _touch(raw, "b.pdf", "shared")
    h = file_sha256(os.path.join(raw, "b.pdf"))
    inventory = {"B": {"localPath": "b.pdf"}}          # canonical "A" is gone from Drive
    state = {"files": {"B": {"rawHash": h, "status": "duplicate", "duplicateOf": "A"}}}
    pending = classify_pending(inventory, state, raw)
    assert [p["fileId"] for p in pending] == ["B"]
    # but while the canonical is still present, the duplicate stays deduped (no churn)
    inventory["A"] = {"localPath": "a.pdf"}
    assert classify_pending(inventory, state, raw) == []  # B stays a duplicate, A missing on disk

