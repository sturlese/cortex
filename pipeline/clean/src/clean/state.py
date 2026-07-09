"""State + idempotency: which files were processed, keyed by source file id and content hash."""
import hashlib
import json
import os
import tempfile


def _read_json(path, fallback=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def write_json_atomic(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state(state_dir) -> dict:
    fallback = {"version": 1, "files": {}}
    return _read_json(os.path.join(state_dir, "clean-state.json"), fallback) or fallback


def save_state(state_dir, state):
    write_json_atomic(os.path.join(state_dir, "clean-state.json"), state)


def load_inventory(raw_dir):
    inv = _read_json(os.path.join(raw_dir, "_state.json"))
    files = inv.get("files") if isinstance(inv, dict) else None
    return files if isinstance(files, dict) else None


def classify_pending(inventory, state, raw_dir):
    """Pending = new / changed (hash) / previous error / requeued / restored-after-delete /
    orphaned-duplicate (its canonical is gone) / deleted."""
    pending, seen = [], set()
    for file_id, entry in inventory.items():
        seen.add(file_id)
        local = entry.get("localPath")
        path = os.path.join(raw_dir, local) if local else None
        if not path or not os.path.exists(path):
            continue
        raw_hash = file_sha256(path)
        prev = state["files"].get(file_id)
        # A file whose source reappeared after a delete (its page was removed) must be regenerated
        # even if the bytes are unchanged; a duplicate whose canonical left the inventory is now the
        # only holder of that content and must get its own page.
        orphaned_dup = prev and prev.get("status") == "duplicate" and prev.get("duplicateOf") not in inventory
        if (not prev or prev.get("rawHash") != raw_hash
                or prev.get("status") in ("error", "requeued", "deleted")
                or orphaned_dup):
            pending.append({"fileId": file_id, "entry": entry, "rawHash": raw_hash,
                            "path": path, "reason": "changed" if prev else "new"})
    for file_id, f in state["files"].items():
        if file_id not in seen and f.get("status") != "deleted":
            pending.append({"fileId": file_id, "entry": None, "rawHash": None, "path": None, "reason": "deleted"})
    return pending


