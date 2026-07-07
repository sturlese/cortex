"""Stage trim-manifest: manifest_full.jsonl -> manifest.jsonl (final allowlist, deterministic).

Final rule-based trim:
  1. drop NON-document extensions (images/video/audio/archives/design/junk),
  2. drop low-value types (the taxonomy's `demoted_types`).

Read-only over the corpus; a pure filter over the manifest.
"""
from __future__ import annotations

import os

from corpus.artifacts import read_jsonl, write_jsonl, write_provenance
from corpus.schemas import ManifestRecord
from corpus.stages.classify_files import load_taxonomy

# extensions that are NOT documents -> out (images, video, audio, archives, design, system junk)
NON_DOCUMENT_EXT = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "svg", "eps", "ai", "psd",
    "mp4", "mov", "avi", "mkv", "wmv", "m4v", "mp3", "wav",
    "zip", "7z", "rar", "tar", "gz",
    "textclipping", "php", "ds_store", "localized", "url", "lnk", "crdownload",
}


def _ext(path: str) -> str:
    b = path.rsplit("/", 1)[-1]
    return b.rsplit(".", 1)[-1].lower() if "." in b else ""


def is_noise(rec: ManifestRecord, demoted_types: set[str]) -> bool:
    if _ext(rec.path) in NON_DOCUMENT_EXT:
        return True                         # not a document
    return rec.type in demoted_types        # low-value types out


def trim(records: list[ManifestRecord], demoted_types: set[str]) -> list[ManifestRecord]:
    """Removes noise; preserves order (manifest_full is already sorted by path)."""
    return [r for r in records if not is_noise(r, demoted_types)]


def run_stage(workdir: str, taxonomy_path: str | None = None) -> int:
    full_path = os.path.join(workdir, "manifest_full.jsonl")
    demoted = set(load_taxonomy(taxonomy_path)["demoted_types"])
    kept = trim(read_jsonl(full_path, ManifestRecord), demoted)
    out = os.path.join(workdir, "manifest.jsonl")
    write_jsonl(out, kept)
    write_provenance(out, "trim-manifest@2", [full_path], len(kept))
    return len(kept)
