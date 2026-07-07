"""Stage build-inventory: manifest.jsonl (+ drive_ids.json) -> inventory.json.

Produces the `_state.json` that clean consumes. MINIMAL: per file id only
{name, localPath, drivePath, orgUnit, sourceUri, mimeType} — what clean actually READS.

drive_ids.json = {relative_path: fileId} (produced by whatever uploaded the corpus to Drive;
external input). Without a fileId -> a stable key derived from the path (deterministic) and a
local:// sourceUri.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re

from corpus.artifacts import read_json, read_jsonl, write_json, write_provenance
from corpus.schemas import InventoryEntry, ManifestRecord


def _rel(path: str) -> str:
    return re.sub(r"^\./", "", path)


def _stable_key(rel: str) -> str:
    return "local-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]


def build_inventory(manifest: list[ManifestRecord], drive_ids: dict[str, str]) -> dict:
    files: dict[str, dict] = {}
    for r in manifest:
        rel = _rel(r.path)
        fid = drive_ids.get(rel) or drive_ids.get(r.path)
        key = fid or _stable_key(rel)
        source_uri = f"https://drive.google.com/file/d/{fid}/view" if fid else f"local://{rel}"
        entry = InventoryEntry(
            name=rel.rsplit("/", 1)[-1],
            localPath=rel,
            drivePath=rel,
            sourceUri=source_uri,
            orgUnit=r.unit,
            mimeType=mimetypes.guess_type(rel)[0],
        )
        files[key] = entry.model_dump(exclude_none=True)
    return {"files": files}


def run_stage(workdir: str, drive_ids_path: str | None = None) -> int:
    manifest = read_jsonl(os.path.join(workdir, "manifest.jsonl"), ManifestRecord)
    drive_ids = read_json(drive_ids_path) if (drive_ids_path and os.path.exists(drive_ids_path)) else {}
    inv = build_inventory(manifest, drive_ids)
    out = os.path.join(workdir, "inventory.json")
    write_json(out, inv)
    inputs = [os.path.join(workdir, "manifest.jsonl")]
    if drive_ids_path and os.path.exists(drive_ids_path):
        inputs.append(drive_ids_path)
    write_provenance(out, "build-inventory@2", inputs, len(inv["files"]))
    return len(inv["files"])
