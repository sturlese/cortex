"""Stage enumerate-files: walks --corpus (READ-ONLY) -> files.jsonl {path, size, mtime, md5}.

Pure Python: os.walk + hashlib (no shell find/md5 -> minimal system coupling). `path` is relative
to the corpus with a './' prefix. Deterministic order (dirs and names sorted).
"""
from __future__ import annotations

import hashlib
import os

from corpus.artifacts import write_jsonl, write_provenance
from corpus.schemas import FileRecord


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def enumerate_files(corpus_dir: str) -> list[FileRecord]:
    out: list[FileRecord] = []
    for root, dirs, names in os.walk(corpus_dir):
        dirs.sort()  # deterministic walk
        for name in sorted(names):
            full = os.path.join(root, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, corpus_dir)
            st = os.stat(full)
            out.append(FileRecord(path="./" + rel, size=st.st_size, mtime=st.st_mtime, md5=_md5(full)))
    return out


def run_stage(corpus_dir: str, workdir: str) -> int:
    files = enumerate_files(corpus_dir)
    out = os.path.join(workdir, "files.jsonl")
    write_jsonl(out, files)
    write_provenance(out, "enumerate-files@1", [], len(files))
    return len(files)
