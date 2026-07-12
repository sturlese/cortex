"""trim-manifest: non-document extensions and demoted types out."""
import os

from corpus.artifacts import read_json, write_jsonl
from corpus.schemas import ManifestRecord
from corpus.stages.classify_files import default_taxonomy_path
from corpus.stages.trim_manifest import is_noise, run_stage, trim

DEMOTED = {"other", "internal-admin"}


def _mr(path, typ="reports"):
    return ManifestRecord(path=path, type=typ, verdict="IN", unit="U", hash="h", size=1)


def test_non_document_extensions_dropped():
    assert is_noise(_mr("./U/photo.JPG"), DEMOTED)
    assert is_noise(_mr("./U/backup.zip"), DEMOTED)
    assert is_noise(_mr("./U/video.mp4"), DEMOTED)
    assert not is_noise(_mr("./U/report.pdf"), DEMOTED)
    assert not is_noise(_mr("./U/sheet.xlsx"), DEMOTED)


def test_demoted_types_dropped():
    assert is_noise(_mr("./U/whatever.pdf", typ="other"), DEMOTED)
    assert is_noise(_mr("./U/whatever.pdf", typ="internal-admin"), DEMOTED)
    assert not is_noise(_mr("./U/whatever.pdf", typ="reports"), DEMOTED)


def test_trim_preserves_order():
    records = [_mr("./U/a.pdf"), _mr("./U/b.jpg"), _mr("./U/c.pdf", typ="other"), _mr("./U/d.pdf")]
    assert [r.path for r in trim(records, DEMOTED)] == ["./U/a.pdf", "./U/d.pdf"]


def test_run_stage_records_taxonomy_in_provenance(tmp_path):
    """trim's output is driven by the taxonomy's demoted_types, so its provenance must record the
    taxonomy as an input -- parity with curate/classify -- otherwise is_fresh cannot see a
    taxonomy-only change and would skip a needed re-trim."""
    workdir = str(tmp_path)
    write_jsonl(os.path.join(workdir, "manifest_full.jsonl"), [_mr("./U/a.pdf")])
    run_stage(workdir)
    prov = read_json(os.path.join(workdir, "manifest.jsonl.meta.json"))
    names = {ref["name"] for ref in prov["inputs"]}
    assert "manifest_full.jsonl" in names
    assert os.path.basename(default_taxonomy_path()) in names   # taxonomy.json
