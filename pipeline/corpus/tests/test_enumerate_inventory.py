"""enumerate-files walk determinism + build-inventory mapping."""
import json
import os

import pytest

from corpus.paths import PathError
from corpus.schemas import ManifestRecord
from corpus.stages.build_inventory import _stable_key, build_inventory
from corpus.stages.build_inventory import run_stage as inv_stage
from corpus.stages.enumerate_files import _utf8_safe, enumerate_files
from corpus.stages.enumerate_files import run_stage as enum_stage


def _mk_corpus(tmp_path):
    (tmp_path / "Unit A" / "sub").mkdir(parents=True)
    (tmp_path / "Unit A" / "sub" / "b.pdf").write_text("b")
    (tmp_path / "Unit A" / "a.pdf").write_text("a")
    (tmp_path / "root.txt").write_text("r")
    return str(tmp_path)


def test_enumerate_deterministic_and_hashed(tmp_path):
    corpus = _mk_corpus(tmp_path / "corpus")
    files = enumerate_files(corpus)
    assert [f.path for f in files] == ["./root.txt", "./Unit A/a.pdf", "./Unit A/sub/b.pdf"]
    assert all(len(f.md5) == 32 for f in files)
    assert files[1].size == 1


def test_utf8_safe_detects_surrogate_names():
    """A name os.walk decoded with surrogateescape (non-UTF8 bytes on Linux) must be flagged so
    enumerate skips it instead of crashing the whole stage at serialization time."""
    assert _utf8_safe("café.pdf") is True
    assert _utf8_safe("caf\udce9.pdf") is False   # latin-1 é surrogateescaped by os.fsdecode


def test_enumerate_skips_symlinks(tmp_path):
    corpus = _mk_corpus(tmp_path / "corpus")
    os.symlink(os.path.join(corpus, "root.txt"), os.path.join(corpus, "link.txt"))
    files = enumerate_files(corpus)
    assert "./link.txt" not in [f.path for f in files]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_enumerate_reports_every_unreadable_file_in_one_pass(tmp_path):
    """An unreadable file must abort the stage, and the error must name ALL of them.

    Aborting is not the defect — it is the only safe answer, see the test below. The defect was
    aborting on the FIRST one: `_md5` raised straight out of the loop, so an operator repairing a
    corpus with three bad files learned about them one per full walk-and-hash of the whole corpus.
    Both unreadable files have to appear in one message."""
    corpus = _mk_corpus(tmp_path / "corpus")
    bad = [os.path.join(corpus, "Unit A", "a.pdf"), os.path.join(corpus, "root.txt")]
    for b in bad:
        os.chmod(b, 0o000)
    try:
        with pytest.raises(PathError) as ei:
            enumerate_files(corpus)
    finally:
        for b in bad:
            os.chmod(b, 0o644)

    msg = str(ei.value)
    assert "2 path(s)" in msg
    assert "Unit A/a.pdf" in msg and "root.txt" in msg     # one pass, not one per re-run
    assert "Permission denied" in msg                      # the OS reason, per path


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_enumerate_fails_on_an_unreadable_directory_too(tmp_path):
    """The directory case, which is the same hazard an order of magnitude worse.

    `os.walk` defaults to `onerror=None`, meaning a directory it cannot list is silently skipped and
    yields nothing — no exception anywhere. So an unreadable org-unit folder made EVERY document
    beneath it disappear from files.jsonl in one pass, and by the reasoning in the test above that
    reads downstream as a bulk delete of all their pages and facts. Guarding only the per-file read
    would have left this wide open, and the guard's own error message claims to cover it.

    TWO bad directories, because one cannot tell collecting from re-raising: an `onerror` that raises
    aborts the walk on the first, which is the very one-per-re-walk behaviour this fix removes."""
    corpus = _mk_corpus(tmp_path / "corpus")
    units = [os.path.join(corpus, "Unit A"), os.path.join(corpus, "Unit Z")]
    os.mkdir(units[1])
    open(os.path.join(units[1], "z.pdf"), "w").write("z")
    for u in units:
        os.chmod(u, 0o000)
    try:
        with pytest.raises(PathError) as ei:
            enumerate_files(corpus)
    finally:
        for u in units:
            os.chmod(u, 0o755)
    msg = str(ei.value)
    assert "2 path(s)" in msg                        # both, in one pass
    # exact lines, not `in`: an absolute path would satisfy a substring check, so a directory entry
    # that forgot its relpath could silently mix absolute dirs with relative files in one report.
    listed = [ln.strip()[2:].split(":")[0] for ln in msg.splitlines() if ln.startswith("  - ")]
    assert sorted(listed) == ["Unit A/", "Unit Z/"]
    assert "Permission denied" in msg


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_enumerate_never_reports_an_unreadable_file_as_absent(tmp_path):
    """Why this stage must fail instead of skipping the file — the reason is entirely downstream.

    Absence from files.jsonl does not mean "unreadable", it means "this document is gone": the id
    drops out of inventory.json, and clean's classify_pending then emits reason="deleted", which
    drives remove_page + delete_facts. So skipping would let a TRANSIENT read error (a permission
    blip, an EIO, an unmounted share) silently destroy a document's page and every number extracted
    from it — with no failure anywhere for an operator to notice.

    Between blocking the pipeline and deleting data, blocking is the recoverable one. This test
    pins the direction, so a future 'the pipeline shouldn't stop for one file' change has to
    confront the deletion it would cause."""
    corpus = _mk_corpus(tmp_path / "corpus")
    work = tmp_path / "work"
    work.mkdir()
    bad = os.path.join(corpus, "Unit A", "a.pdf")

    n = enum_stage(corpus, str(work))                       # healthy pass: the file is enumerated
    assert n == 3
    present = {json.loads(ln)["path"] for ln in (work / "files.jsonl").read_text().splitlines() if ln}
    assert "./Unit A/a.pdf" in present

    os.chmod(bad, 0o000)
    try:
        with pytest.raises(PathError):
            enum_stage(corpus, str(work))
    finally:
        os.chmod(bad, 0o644)
    # and the previous artifact is left alone rather than rewritten without the file — a truncated
    # files.jsonl is what would eventually be read as "deleted".
    after = {json.loads(ln)["path"] for ln in (work / "files.jsonl").read_text().splitlines() if ln}
    assert after == present


def test_enumerate_guards_every_io_error_not_just_permissions(tmp_path, monkeypatch):
    """The catch is `OSError`, not `PermissionError`: permissions are merely the easiest way to
    reproduce this, not the only way a read fails. EIO on a failing disk and ESTALE on an NFS mount
    have to land in the guard too — narrowed to PermissionError they escape as an uncaught traceback,
    losing both the exit-2 contract and the collect-all-then-report behaviour."""
    import errno

    from corpus.stages import enumerate_files as ef

    corpus = _mk_corpus(tmp_path / "corpus")
    for err in (errno.EIO, errno.ESTALE):
        def boom(p, _e=err):
            raise OSError(_e, os.strerror(_e))
        monkeypatch.setattr(ef, "_md5", boom)
        with pytest.raises(PathError) as ei:
            ef.enumerate_files(corpus)
        assert "3 path(s)" in str(ei.value), errno.errorcode[err]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_enumerate_guards_a_stat_failure_not_only_a_read_failure(tmp_path):
    """A path can be LISTED but not statted, and that case used to bypass the guard entirely.

    `os.path.isfile` swallows OSError internally and returns False, so when it ran before the guard
    every stat-level failure was silently skipped and only `_md5`'s open() ever reached the except —
    which is why chmod 000 on a file was caught (permission bits block open, not stat) and nothing
    else was. A directory with mode 0o400 is the no-monkeypatching reproduction: the `r` bit lets
    os.walk list the names, so `onerror` never fires, but without `x` every stat under it fails.

    Flat on purpose. With a subdirectory, `onerror` fires for the subdir and masks the fact that the
    files alongside it were lost — so a nested corpus passes while this one silently enumerated
    nothing, at exit 0, feeding the whole directory into clean's delete path."""
    corpus = tmp_path / "corpus"
    (corpus / "Unit A").mkdir(parents=True)
    (corpus / "root.txt").write_text("root")
    for n in ("a.md", "b.md"):
        (corpus / "Unit A" / n).write_text(n)

    os.chmod(corpus / "Unit A", 0o400)          # readable, NOT traversable
    try:
        with pytest.raises(PathError) as ei:
            enumerate_files(str(corpus))
    finally:
        os.chmod(corpus / "Unit A", 0o755)
    msg = str(ei.value)
    assert "2 path(s)" in msg                   # BOTH files named, not the directory
    assert "Unit A/a.md" in msg and "Unit A/b.md" in msg


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
def test_enumerate_still_skips_non_regular_files(tmp_path):
    """`os.path.isfile` used to reject non-regular files; the explicit S_ISREG check inherits that job
    when the stat moves inside the guard. It is not cosmetic: opening a fifo BLOCKS until a writer
    appears, so without this check `_md5` hangs the stage indefinitely — no error, no timeout, worse
    than either failure mode this fix is about. A device node or socket in a corpus is the same
    shape. Skipped rather than reported, because a fifo is not a document."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "real.md").write_text("body")
    os.mkfifo(corpus / "pipe.md")

    files = enumerate_files(str(corpus))         # must return, not block
    assert [f.path for f in files] == ["./real.md"]


def test_enumerate_treats_a_file_that_vanished_mid_walk_as_absent(tmp_path, monkeypatch, capsys):
    """ENOENT is the one I/O error that must NOT be fatal, and the reason is a real corpus.

    Everything else in the guard means "this path exists but I cannot read it", where reporting it as
    absent would be a lie that deletes a live document. A file os.walk listed and something removed
    before the stat is genuinely gone, so absence is simply true. And it is expected: taxonomy.json's
    system-noise verdict covers `.crdownload`, `.download` and `.tmp`, i.e. the project states that
    in-flight sync artifacts live in corpora — and vanishing is exactly what those do. Fatal here
    would let a half-finished download block the whole pipeline.

    The removal happens while a SIBLING in the same directory is hashed, so the doomed name really
    was returned by the listdir and really does fail at stat — no faked errno."""
    from corpus.stages import enumerate_files as ef

    corpus = tmp_path / "corpus"
    (corpus / "Unit A").mkdir(parents=True)
    (corpus / "Unit A" / "a.md").write_text("a")
    doomed = corpus / "Unit A" / "b.crdownload"
    doomed.write_text("half a download")

    real_md5 = ef._md5

    def md5_then_race(path):
        if path.endswith("a.md"):
            os.remove(doomed)          # after the listdir, before b's stat
        return real_md5(path)

    monkeypatch.setattr(ef, "_md5", md5_then_race)
    files = ef.enumerate_files(str(corpus))     # must NOT raise

    assert [f.path for f in files] == ["./Unit A/a.md"]
    assert "vanished during the walk" in capsys.readouterr().out


def test_enumerate_fails_when_an_ancestor_directory_moves_mid_walk(tmp_path, monkeypatch):
    """The dangerous half of ENOENT, and the reason the carve-out is conditional.

    "The file was listed and is now gone" conflates two different things: this PATH is gone, and this
    DOCUMENT is gone. Rename a parent directory mid-walk and every not-yet-stat'd file under it
    raises ENOENT while the documents sit alive under the new name — which this walk will never
    visit, because the new name was not in the listing os.walk already took. Treating those as absent
    reports live documents as deleted, and clean acts on that: one rename, a whole folder's pages and
    facts gone, at exit 0.

    The discriminator is whether the parent the walk handed us survived. Here it did not."""
    from corpus.stages import enumerate_files as ef

    corpus = tmp_path / "corpus"
    (corpus / "Unit A").mkdir(parents=True)
    for n in ("a.md", "b.md", "c.md", "d.md"):
        (corpus / "Unit A" / n).write_text(n)

    real_md5 = ef._md5

    def md5_then_rename(path):
        if path.endswith("b.md"):                      # after the listdir, before c/d are stat'd
            os.rename(corpus / "Unit A", corpus / "Unit A (2026)")
        return real_md5(path)

    monkeypatch.setattr(ef, "_md5", md5_then_rename)
    with pytest.raises(PathError) as ei:
        ef.enumerate_files(str(corpus))

    msg = str(ei.value)
    # b.md (whose own open() lost the race), plus c.md and d.md which were never reached. All three
    # are alive under the new name and none of them is reported as gone. a.md was already hashed.
    assert "3 path(s)" in msg
    assert "ancestor directory moved" in msg
    for n in ("b.md", "c.md", "d.md"):
        assert n in msg
        assert (corpus / "Unit A (2026)" / n).exists()  # the documents were there the whole time


def test_enumerate_error_leads_with_directories_so_the_cap_cannot_hide_them(tmp_path):
    """The cap's prefix must be blast radius, not walk order.

    One directory entry can stand for thousands of lost documents while a file entry stands for one.
    Ordered by the walk, 50 unreadable files in an early unit truncate away a later unreadable
    DIRECTORY — so the operator fixes 50 files, pays a full re-walk-and-hash, and only then learns
    about the directory. That is the one-per-re-walk pathology this whole fix exists to remove,
    reappearing through the cap."""
    corpus = tmp_path / "corpus"
    (corpus / "Aunit").mkdir(parents=True)
    (corpus / "Zunit" / "sub").mkdir(parents=True)     # nested, so onerror fires for it
    for i in range(60):
        (corpus / "Aunit" / f"f{i:03d}.md").write_text("x")
    (corpus / "Zunit" / "sub" / "hidden.md").write_text("many docs live here")

    os.chmod(corpus / "Aunit", 0o400)                  # 60 files: listable, not statable
    os.chmod(corpus / "Zunit", 0o000)                  # a whole subtree
    try:
        with pytest.raises(PathError) as ei:
            enumerate_files(str(corpus))
    finally:
        for d in ("Aunit", "Zunit"):
            os.chmod(corpus / d, 0o755)
    msg = str(ei.value)
    listed = [ln.strip()[2:] for ln in msg.splitlines() if ln.startswith("  - ")]
    assert listed[0].startswith("Zunit/: ")            # the directory leads, despite sorting last
    assert len(listed) == 50 and "... and 11 more" in msg


def test_enumerate_error_is_capped_and_ordered(tmp_path, monkeypatch):
    """Two properties of the report itself.

    Capped: this string goes to stderr through cli.py, and an unreadable mount root means tens of
    thousands of paths — uncapped the message measured 2.66 MB for 20k files, which is not a usable
    error. Ordered: the walk sorts dirs and names (see the module docstring), so the report is stable
    across runs; without that the same broken corpus produces a different message every time."""
    from corpus.stages import enumerate_files as ef

    corpus = tmp_path / "corpus"
    (corpus / "Unit B").mkdir(parents=True)
    (corpus / "Unit A").mkdir()
    names = [f"f{i:03d}.md" for i in range(60)]
    for n in names:
        (corpus / "Unit B" / n).write_text(n)
    (corpus / "Unit A" / "first.md").write_text("first")

    monkeypatch.setattr(ef, "_md5", lambda p: (_ for _ in ()).throw(OSError(5, "I/O error")))
    with pytest.raises(PathError) as ei:
        ef.enumerate_files(str(corpus))
    msg = str(ei.value)

    assert "61 path(s)" in msg                              # the true count is never truncated
    assert msg.count("\n  - ") == 50                         # ...but the listing is
    assert "... and 11 more" in msg
    # exactly at the cap there is nothing left over, so no "and 0 more" tail
    monkeypatch.setattr(ef, "_MAX_REPORTED", 61)
    with pytest.raises(PathError) as ei2:
        ef.enumerate_files(str(corpus))
    assert "more" not in str(ei2.value) and str(ei2.value).count("\n  - ") == 61
    # sorted walk: "Unit A" before "Unit B", and f000.. in name order within it
    listed = [ln.strip()[2:].split(":")[0] for ln in msg.splitlines() if ln.startswith("  - ")]
    assert listed[0] == "Unit A/first.md"
    assert listed[1:4] == ["Unit B/f000.md", "Unit B/f001.md", "Unit B/f002.md"]


def test_enumerate_only_treats_io_errors_as_unreadable(tmp_path, monkeypatch):
    """The catch is `OSError`, deliberately narrow. Widened to `Exception` it would report a genuine
    bug in the hashing path as an unreadable corpus file, sending the operator to chmod a file whose
    permissions are fine."""
    from corpus.stages import enumerate_files as ef

    corpus = _mk_corpus(tmp_path / "corpus")
    monkeypatch.setattr(ef, "_md5", lambda p: (_ for _ in ()).throw(ValueError("bug in hashing")))
    with pytest.raises(ValueError, match="bug in hashing"):
        ef.enumerate_files(corpus)


def test_enumerate_stage_writes_artifacts(tmp_path):
    corpus = _mk_corpus(tmp_path / "corpus")
    work = tmp_path / "work"
    work.mkdir()
    n = enum_stage(corpus, str(work))
    assert n == 3
    assert (work / "files.jsonl").exists()
    assert (work / "files.jsonl.meta.json").exists()


def _mr(path, unit="Unit A"):
    return ManifestRecord(path=path, type="reports", verdict="IN", unit=unit, hash="h", size=1)


def test_build_inventory_with_and_without_drive_ids():
    manifest = [_mr("./Unit A/a.pdf"), _mr("./Unit A/sub/b.pdf")]
    inv = build_inventory(manifest, {"Unit A/a.pdf": "DRIVE123"})
    files = inv["files"]
    assert files["DRIVE123"]["sourceUri"] == "https://drive.google.com/file/d/DRIVE123/view"
    assert files["DRIVE123"]["orgUnit"] == "Unit A"
    local_key = _stable_key("Unit A/sub/b.pdf")
    assert local_key.startswith("local-")
    assert files[local_key]["sourceUri"] == "local://Unit A/sub/b.pdf"
    assert files[local_key]["name"] == "b.pdf"
    assert files[local_key]["mimeType"] == "application/pdf"


def test_stable_keys_unique_for_same_basename():
    k1 = _stable_key("Unit A/report.pdf")
    k2 = _stable_key("Unit B/report.pdf")
    assert k1 != k2


def test_inventory_stage(tmp_path):
    from corpus.artifacts import write_jsonl
    work = tmp_path / "work"
    work.mkdir()
    write_jsonl(str(work / "manifest.jsonl"), [_mr("./Unit A/a.pdf")])
    n = inv_stage(str(work))
    assert n == 1
    inv = json.loads((work / "inventory.json").read_text())
    assert len(inv["files"]) == 1
