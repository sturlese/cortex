"""enumerate-files walk determinism + build-inventory mapping."""
import json
import os

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
