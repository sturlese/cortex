"""curate-manifest: IN+MAYBE selection, exact dedup by md5, canonical pick."""
from corpus.schemas import ClassRecord
from corpus.stages.curate_manifest import _canon_key, curate


def _cr(path, verdict="IN", typ="reports", unit="U", size=1):
    return ClassRecord(path=path, type=typ, verdict=verdict, unit=unit, size=size)


def test_out_records_dropped():
    kept = curate([_cr("./U/a.pdf", "IN"), _cr("./U/b.pdf", "OUT"), _cr("./U/c.pdf", "MAYBE")],
                  {"./U/a.pdf": "h1", "./U/b.pdf": "h2", "./U/c.pdf": "h3"})
    assert [r.path for r in kept] == ["./U/a.pdf", "./U/c.pdf"]


def test_exact_dedup_picks_canonical():
    records = [_cr("./U/deep/nested/report.pdf"), _cr("./U/report.pdf"), _cr("./U/report (copy).pdf")]
    md5 = {r.path: "same-hash" for r in records}
    kept = curate(records, md5)
    assert len(kept) == 1
    assert kept[0].path == "./U/report.pdf"     # no version markers, shallowest


def test_version_markers_penalized():
    assert _canon_key("./U/report draft.pdf")[0] == 1
    assert _canon_key("./U/report (2).pdf")[0] == 1
    assert _canon_key("./U/report_old.pdf")[0] == 1
    assert _canon_key("./U/report.pdf")[0] == 0


def test_no_hash_records_kept():
    kept = curate([_cr("./U/a.pdf"), _cr("./U/b.pdf")], {"./U/a.pdf": "h1"})
    assert {r.path for r in kept} == {"./U/a.pdf", "./U/b.pdf"}
    assert next(r for r in kept if r.path == "./U/b.pdf").hash is None


def test_output_sorted_by_path():
    kept = curate([_cr("./U/z.pdf"), _cr("./U/a.pdf")], {"./U/z.pdf": "h1", "./U/a.pdf": "h2"})
    assert [r.path for r in kept] == ["./U/a.pdf", "./U/z.pdf"]
