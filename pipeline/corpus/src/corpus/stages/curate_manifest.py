"""Stage curate-manifest: classification.jsonl (+ md5 from files.jsonl) -> manifest_full.jsonl.

Selects IN+MAYBE, dedups EXACT duplicates by md5, and picks a canonical copy per hash group:
non-version-junk first (no "copy"/"(2)"/"draft" markers), then shallower path, then lexicographic.
"""
from __future__ import annotations

import collections
import os
import re

from corpus.artifacts import read_jsonl, write_jsonl, write_provenance
from corpus.schemas import ClassRecord, FileRecord, ManifestRecord

VERSION_MARKERS = re.compile(r"(copy|\(\d+\)|_old|_v\d+\b|draft|unsigned)", re.I)


def _canon_key(path: str):
    return (
        1 if VERSION_MARKERS.search(path) else 0,
        path.count("/"),
        path,
    )


def curate(class_records, md5_by_path: dict[str, str]) -> list[ManifestRecord]:
    """IN+MAYBE -> exact dedup by hash -> canonical pick. No hash -> kept as-is. Sorted by path."""
    selected = [r for r in class_records if r.verdict in ("IN", "MAYBE")]

    groups: dict[str, list[ManifestRecord]] = collections.defaultdict(list)
    no_hash: list[ManifestRecord] = []
    for r in selected:
        h = md5_by_path.get(r.path)
        rec = ManifestRecord(path=r.path, type=r.type, verdict=r.verdict, unit=r.unit, hash=h, size=r.size)
        if h:
            groups[h].append(rec)
        else:
            no_hash.append(rec)

    kept: list[ManifestRecord] = []
    for grp in groups.values():
        grp.sort(key=lambda r: _canon_key(r.path))
        kept.append(grp[0])
    kept.extend(no_hash)
    kept.sort(key=lambda r: r.path)
    return kept


def run_stage(workdir: str) -> int:
    cls_path = os.path.join(workdir, "classification.jsonl")
    files_path = os.path.join(workdir, "files.jsonl")
    class_records = read_jsonl(cls_path, ClassRecord)
    md5_by_path = {fr.path: fr.md5 for fr in read_jsonl(files_path, FileRecord)}
    kept = curate(class_records, md5_by_path)
    out = os.path.join(workdir, "manifest_full.jsonl")
    write_jsonl(out, kept)
    write_provenance(out, "curate-manifest@2", [cls_path, files_path], len(kept))
    return len(kept)
