"""Data layer: JSONL round-trip, atomic writes, provenance and freshness."""
import json
import os

from corpus.artifacts import is_fresh, read_json, read_jsonl, sha256_file, write_json, write_jsonl, write_provenance
from corpus.schemas import FileRecord


def _fr(path="./U/a.pdf"):
    return FileRecord(path=path, size=1, mtime=0.0, md5="m")


def test_jsonl_roundtrip(tmp_path):
    out = str(tmp_path / "files.jsonl")
    n = write_jsonl(out, [_fr("./U/a.pdf"), _fr("./U/b.pdf")])
    assert n == 2
    back = read_jsonl(out, FileRecord)
    assert [r.path for r in back] == ["./U/a.pdf", "./U/b.pdf"]


def test_write_jsonl_empty(tmp_path):
    out = str(tmp_path / "empty.jsonl")
    assert write_jsonl(out, []) == 0
    assert open(out).read() == ""
    assert read_jsonl(out, FileRecord) == []


def test_write_json_model_and_plain(tmp_path):
    p1 = str(tmp_path / "model.json")
    write_json(p1, _fr())
    assert read_json(p1)["path"] == "./U/a.pdf"
    p2 = str(tmp_path / "plain.json")
    write_json(p2, {"a": 1})
    assert read_json(p2) == {"a": 1}


def test_no_hidden_tmp_left_behind(tmp_path):
    write_json(str(tmp_path / "x.json"), {"a": 1})
    assert all(not f.startswith(".") for f in os.listdir(tmp_path))


def test_provenance_and_is_fresh(tmp_path):
    inp = tmp_path / "input.jsonl"
    inp.write_text("{}\n")
    art = str(tmp_path / "artifact.jsonl")
    write_jsonl(art, [_fr()])
    write_provenance(art, "stage@1", [str(inp)], 1)

    meta = json.loads(open(art + ".meta.json").read())
    assert meta["produced_by"] == "stage@1"
    assert meta["n_records"] == 1
    assert meta["inputs"][0]["sha256"] == sha256_file(str(inp))

    assert is_fresh(art, [str(inp)])
    inp.write_text("changed\n")
    assert not is_fresh(art, [str(inp)])            # input changed
    assert not is_fresh(str(tmp_path / "nope"), [str(inp)])   # artifact missing


def test_is_fresh_corrupted_meta(tmp_path):
    art = str(tmp_path / "a.jsonl")
    write_jsonl(art, [])
    open(art + ".meta.json", "w").write("{broken")
    assert not is_fresh(art, [])
