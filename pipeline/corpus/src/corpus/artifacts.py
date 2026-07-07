"""Data layer: typed read/write of JSON/JSONL artifacts, ATOMIC writes, provenance sidecar and
idempotency via input sha256s. Visible names (no leading dot)."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile
from collections.abc import Iterable

from pydantic import BaseModel

from corpus.schemas import InputRef, Provenance


def _atomic_write_bytes(path: str, data: bytes) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="tmp-")  # visible, no leading dot
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)  # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_jsonl(path: str, records: Iterable[BaseModel]) -> int:
    """One JSON record per line (stream-able, diff-able). Returns the count written."""
    parts = [r.model_dump_json() for r in records]
    data = ("\n".join(parts) + ("\n" if parts else "")).encode("utf-8")
    _atomic_write_bytes(path, data)
    return len(parts)


def read_jsonl[T: BaseModel](path: str, model: type[T]) -> list[T]:
    out: list[T] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(model.model_validate_json(line))
    return out


def write_json(path: str, obj) -> None:
    if isinstance(obj, BaseModel):
        data = obj.model_dump_json(indent=2).encode("utf-8")
    else:
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(path, data)


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def write_provenance(artifact_path: str, produced_by: str, inputs: list[str], n_records: int) -> None:
    """Writes <artifact>.meta.json with the sha256 of each input (for idempotency)."""
    refs = [InputRef(name=os.path.basename(p), sha256=sha256_file(p)) for p in inputs if os.path.exists(p)]
    prov = Provenance(produced_by=produced_by, inputs=refs, created_at=_utcnow_iso(), n_records=n_records)
    write_json(artifact_path + ".meta.json", prov)


def is_fresh(artifact_path: str, inputs: list[str]) -> bool:
    """True if the artifact exists and its inputs' sha256s match the recorded provenance.
    Lets a stage be skipped when already done (idempotency). False on any doubt."""
    meta = artifact_path + ".meta.json"
    if not (os.path.exists(artifact_path) and os.path.exists(meta)):
        return False
    try:
        prov = Provenance.model_validate(read_json(meta))
    except Exception:  # noqa: BLE001
        return False
    recorded = {r.name: r.sha256 for r in prov.inputs}
    return all(os.path.exists(p) and recorded.get(os.path.basename(p)) == sha256_file(p) for p in inputs)
