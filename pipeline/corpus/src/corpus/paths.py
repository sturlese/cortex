"""Path resolution and validation. Fail fast: critical paths are required and must exist,
no silent defaults. The corpus is ALWAYS read-only (never written into)."""
from __future__ import annotations

import os


class PathError(Exception):
    """Missing/incorrect critical path. The CLI turns it into an exit with a clear message."""


def require_corpus(path: str | None) -> str:
    """--corpus: the raw dataset, read-only. Required and must exist."""
    if not path:
        raise PathError("--corpus is required (root directory of the corpus, read-only). No default.")
    if not os.path.isdir(path):
        raise PathError(f"--corpus does not exist or is not a directory: {path}")
    return os.path.abspath(path)


def require_workdir(path: str | None, create: bool = True) -> str:
    """--workdir: where JSON artifacts go. Required. Created if missing (create=True)."""
    if not path:
        raise PathError("--workdir is required (artifacts directory). No default.")
    if create:
        os.makedirs(path, exist_ok=True)
    elif not os.path.isdir(path):
        raise PathError(f"--workdir does not exist: {path}")
    return os.path.abspath(path)


def workdir_file(workdir: str, name: str) -> str:
    return os.path.join(workdir, name)
