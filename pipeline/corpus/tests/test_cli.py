"""CLI: fail-fast on missing paths, full build-manifest run over a tiny corpus."""
import json

from corpus import cli


def test_missing_required_path_fails_fast(capsys):
    rc = cli.main(["enumerate-files", "--workdir", "/tmp/w-x"])
    assert rc == 2
    assert "--corpus is required" in capsys.readouterr().err


def test_corpus_must_exist(capsys, tmp_path):
    rc = cli.main(["enumerate-files", "--corpus", "/nonexistent", "--workdir", str(tmp_path)])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_build_manifest_end_to_end(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    (corpus / "Sales").mkdir(parents=True)
    (corpus / "Sales" / "Quarterly Report Q1.pdf").write_text("q1")
    (corpus / "Sales" / "Quarterly Report Q1 (copy).pdf").write_text("q1")   # exact dup
    (corpus / "Sales" / "photo.jpg").write_text("img")                       # trimmed
    (corpus / "Sales" / "NDA.pdf").write_text("legal")                       # OUT
    work = tmp_path / "work"

    rc = cli.main(["build-manifest", "--corpus", str(corpus), "--workdir", str(work)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "build-manifest: enumerate=4 classify=4" in out

    manifest = [json.loads(line) for line in (work / "manifest.jsonl").read_text().splitlines()]
    assert [m["path"] for m in manifest] == ["./Sales/Quarterly Report Q1.pdf"]

    rc = cli.main(["build-inventory", "--workdir", str(work)])
    assert rc == 0
    inv = json.loads((work / "inventory.json").read_text())
    assert len(inv["files"]) == 1

