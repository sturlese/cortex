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


def test_dedup_keeps_the_in_copy_not_a_maybe_duplicate():
    """An IN document duplicated into a blander folder (fallback 'other'/MAYBE) must survive dedup
    as IN, not be represented by the MAYBE copy that a later trim would drop."""
    records = [
        _cr("./Archive/q1.pdf", verdict="MAYBE", typ="other"),   # would win on path alone
        _cr("./Sales/Quarterly Report Q1.pdf", verdict="IN", typ="reports"),
    ]
    md5 = {r.path: "same-hash" for r in records}
    kept = curate(records, md5)
    assert len(kept) == 1
    assert kept[0].verdict == "IN" and kept[0].type == "reports"


def test_empty_files_are_not_deduped_together():
    """Distinct 0-byte files share the empty md5; they must not collapse into one entry."""
    records = [_cr("./U1/a.md", size=0), _cr("./U2/b.md", size=0)]
    md5 = {r.path: "d41d8cd98f00b204e9800998ecf8e400" for r in records}
    kept = curate(records, md5)
    assert {r.path for r in kept} == {"./U1/a.md", "./U2/b.md"}
