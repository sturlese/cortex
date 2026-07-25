"""Stage enumerate-files: walks --corpus (READ-ONLY) -> files.jsonl {path, size, mtime, md5}.

Pure Python: os.walk + hashlib (no shell find/md5 -> minimal system coupling). `path` is relative
to the corpus with a './' prefix. Deterministic order (dirs and names sorted).

Raises PathError (CLI exit 2, no artifact written) if any path could not be read, listing all of
them. That failure is load-bearing, not defensive: absence from files.jsonl is what tells clean a
document was deleted, so a path this stage cannot read must never be reported as an absent one.
"""
from __future__ import annotations

import hashlib
import os
import stat

from corpus.artifacts import write_jsonl, write_provenance
from corpus.paths import PathError
from corpus.schemas import FileRecord

_MAX_REPORTED = 50   # unreadable paths named in the error before it collapses to a count


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _utf8_safe(rel: str) -> bool:
    """False for a name os.walk decoded with surrogateescape (non-UTF8 bytes on Linux). Such a name
    would crash the JSON/pydantic serializer AFTER the whole corpus is walked and hashed."""
    try:
        rel.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def enumerate_files(corpus_dir: str) -> list[FileRecord]:
    out: list[FileRecord] = []
    # kept apart so the report can lead with the directories: one directory entry can stand for
    # thousands of lost documents, and the cap must not truncate it away in favour of 50 files.
    bad_dirs: list[str] = []
    bad_files: list[str] = []

    def _walk_error(e: OSError) -> None:
        # os.walk defaults to onerror=None, i.e. it SWALLOWS a failed listdir and yields nothing for
        # that directory. An unreadable directory therefore made every document beneath it vanish
        # from files.jsonl at once — the same silent-deletion hazard as an unreadable file, except a
        # whole org unit's worth of pages and facts instead of one document's.
        #
        # Fatal even for ENOENT, unlike the per-file case below, and the asymmetry is deliberate.
        # For a file we can prove the file itself vanished by checking that its parent survived. For
        # a directory there is no such check: "removed" and "renamed" are indistinguishable from
        # here, and guessing "removed" when it was renamed reports every document beneath it as
        # absent, which deletes them. A needless block is recoverable; that is not.
        bad_dirs.append(f"{os.path.relpath(e.filename, corpus_dir)}/: {e}")

    for root, dirs, names in os.walk(corpus_dir, onerror=_walk_error):
        dirs.sort()  # deterministic walk
        for name in sorted(names):
            full = os.path.join(root, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, corpus_dir)
            if not _utf8_safe(rel):
                print(f"[enumerate-files] skipping non-UTF8 filename: {rel!r}", flush=True)
                continue
            try:
                st = os.stat(full)
                if not stat.S_ISREG(st.st_mode):
                    continue          # directory, fifo, socket — os.path.isfile's old job
                digest = _md5(full)
            except FileNotFoundError as e:
                if not os.path.isdir(root):
                    # The path is gone because an ANCESTOR moved, not because this file did. The
                    # documents are alive under the new name and this walk will never see them: the
                    # new name was not in the parent listing os.walk already took. Reporting them
                    # absent would delete live pages and facts — one rename, a whole folder gone —
                    # so this is the unreadable case, not the vanished one.
                    bad_files.append(f"{rel}: {e} (an ancestor directory moved during the walk)")
                    continue
                # The parent survived, so the file itself is gone and absence is the TRUTH about this
                # path: recording it as absent is accurate rather than a lie, and must NOT be fatal.
                # taxonomy.json's system-noise list (.crdownload, .download, .tmp) says the project
                # expects in-flight sync artifacts inside a corpus, and vanishing is what they do.
                print(f"[enumerate-files] vanished during the walk: {rel!r}", flush=True)
                continue
            except OSError as e:
                # Collect and keep walking, then fail — do NOT skip. Absence from files.jsonl means
                # "this document is gone" all the way down: it drops out of inventory.json, and
                # clean's classify_pending then emits reason="deleted" and calls remove_page +
                # delete_facts. So skipping an unreadable file would let a TRANSIENT read error
                # silently destroy that document's page and its numbers. Verified end to end.
                #
                # Failing is therefore the safe direction, and it is what this stage already did.
                # What it did badly was fail on the FIRST bad file, so an operator fixing a corpus
                # with several of them learned about one per full re-walk. Now one pass reports all.
                bad_files.append(f"{rel}: {e}")
                continue
            out.append(FileRecord(path="./" + rel, size=st.st_size, mtime=st.st_mtime, md5=digest))
    unreadable = bad_dirs + bad_files      # directories first: see the cap below
    if unreadable:
        listing = "\n".join(f"  - {u}" for u in unreadable[:_MAX_REPORTED])
        if len(unreadable) > _MAX_REPORTED:
            # this goes to stderr through cli.py; an unreadable mount root can mean tens of
            # thousands of paths, and a multi-megabyte error message is not a usable one.
            listing += f"\n  ... and {len(unreadable) - _MAX_REPORTED} more"
        raise PathError(
            f"{len(unreadable)} path(s) in the corpus could not be enumerated:\n{listing}\n"
            "Fix the permissions, or re-run once the corpus has stopped changing. enumerate refuses "
            "to report a path it could not read as absent, because absence is what tells clean to "
            "delete the page and its facts.")
    return out


def run_stage(corpus_dir: str, workdir: str) -> int:
    files = enumerate_files(corpus_dir)
    out = os.path.join(workdir, "files.jsonl")
    write_jsonl(out, files)
    write_provenance(out, "enumerate-files@1", [], len(files))
    return len(files)
