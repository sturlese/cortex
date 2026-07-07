"""Pydantic (v2) schemas for every data-layer artifact. Validation on read and write.

`inventory.json` is DELIBERATELY minimal: only the fields the clean stage actually reads.
Anything else (classType/fingerprint/parentIds) would be accidental complexity in corpus output.
"""
from __future__ import annotations

from pydantic import BaseModel


class FileRecord(BaseModel):
    """One entry of files.jsonl (output of enumerate-files)."""
    path: str          # relative to the corpus, "./" prefix
    size: int
    mtime: float
    md5: str


class ClassRecord(BaseModel):
    """One entry of classification.jsonl (output of classify-files)."""
    path: str
    type: str
    verdict: str       # IN | MAYBE | OUT
    unit: str          # org unit = top-level folder (optionally mapped via taxonomy org_units)
    size: int


class ManifestRecord(BaseModel):
    """One entry of manifest_full.jsonl / manifest.jsonl. `hash` optional (files without md5)."""
    path: str
    type: str
    verdict: str
    unit: str
    hash: str | None = None
    size: int


class InventoryEntry(BaseModel):
    """inventory.json entry (value; the key is the source file id). MINIMAL: only what clean reads."""
    name: str
    localPath: str
    drivePath: str
    sourceUri: str
    orgUnit: str | None = None
    mimeType: str | None = None


class InputRef(BaseModel):
    name: str
    sha256: str


class Provenance(BaseModel):
    """Sidecar <artifact>.meta.json — traceability + idempotency via input sha256s."""
    schema_version: int = 1
    produced_by: str
    inputs: list[InputRef] = []
    created_at: str
    n_records: int
