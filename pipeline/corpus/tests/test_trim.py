"""trim-manifest: non-document extensions and demoted types out."""
from corpus.schemas import ManifestRecord
from corpus.stages.trim_manifest import is_noise, trim

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
