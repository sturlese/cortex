"""State + idempotency: which files were processed, keyed by source file id and content hash.

The state is a plain JSON dict — deliberately untyped (ADR 001), but its shape IS a contract:
five modules read and mutate it in place (main, worker via lastResult, versions, dossiers, ops),
so the schema lives here, in the one module every consumer already imports:

    {"version": 1,
     "files": {<fileId>: {
         "name", "mimeType", "localPath", "sourceUri",   # copied from the connector's inventory
         "rawHash",                                      # sha256 of the raw file (idempotency key)
         "status",         # processed | error | requeued | duplicate | deleted
         "updatedAt",      # UTC iso timestamp of the last transition
         "error",          # status=error: message (truncated)
         "requeueReason",  # status=requeued: the supervisor's reason (ops.requeue_impl)
         "duplicateOf",    # status=duplicate: the canonical file id serving this content
         "claims",         # ops claim checks: {checked, unsupported[], contradicted[]}
         "lastResult": {   # worker.process_one's returned dict, verbatim; notably:
             "path", "title", "entity", "unit", "as_of", "acl", "verification",
             "skipped", "representation", "extraction_quality", "usage",
             "supersedes", "superseded_by",    # written by the version phase, not the worker
         }}},
     "dossiers": {<entitySlug>: {"hash", "path", "updatedAt"}}}   # dossiers.build_dossiers

Renaming a key here means updating every consumer above — grep before you touch it."""
import hashlib
import json
import os
import tempfile

STATE_FILE = "clean-state.json"


def _read_json(path, fallback=None):
    """Tolerant read for ANOTHER stage's artifact (fetch's inventory): unreadable or malformed ->
    fallback. clean never writes that file, so degrading to "no inventory" is safe and
    self-healing. Our own state file gets the stricter load_state below instead."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
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
    """Reset ONLY on content corruption, and say so (parity with fetch's load_state): a malformed,
    non-UTF-8 or non-dict clean-state.json is logged and re-initialized. An access error (OSError)
    propagates instead of resetting — an unreadable-but-intact state treated as empty would silently
    reprocess the whole corpus AND overwrite the file at the first processed document."""
    fresh = {"version": 1, "files": {}}
    try:
        with open(os.path.join(state_dir, STATE_FILE), encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return fresh
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"[clean] {STATE_FILE} corrupted, re-initializing", flush=True)
        return fresh
    if not isinstance(state, dict):
        print(f"[clean] {STATE_FILE} is not an object, re-initializing", flush=True)
        return fresh
    state.setdefault("files", {})   # a hand-edited state without "files" must not KeyError the pass
    return state


def save_state(state_dir, state):
    write_json_atomic(os.path.join(state_dir, STATE_FILE), state)


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


