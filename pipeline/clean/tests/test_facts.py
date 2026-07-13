"""Facts layer: the agent proposes, the grid decides — validation, store, fake backend, wiring."""
import asyncio
import json

from clean import factstore
from clean.facts import (
    MAX_READ_ROWS_CALLS,
    GridContext,
    _num,
    read_rows_impl,
    render_rows,
    validate_observations,
)
from clean.fake_llm import FakeFactsProcessor, facts_from_grid
from clean.schemas import FactObservation, FactsOutput

KPI_ROWS = [
    ["month", "active_users", "arr_usd", "nps"],
    ["2026-01", "1250", "480000", "41"],
    ["2026-02", "1310", "495000", "44"],
    ["2026-03", "1400", "512000", "47"],
]


def _obs(**kw):
    d = dict(metric="arr-usd", metric_raw="arr_usd", value_raw="480000",
             period="2026-01", sheet="Sheet1", row=2, col=3)
    d.update(kw)
    return FactObservation(**d)


def _ctx(rows=None, filename="KPI metrics 2026.csv"):
    return GridContext(sheets={"Sheet1": rows or KPI_ROWS}, filename=filename)


# ── deterministic validation: the grid decides ───────────────────────────────
def test_valid_observation_kept():
    ctx = _ctx()
    kept = validate_observations(FactsOutput(observations=[_obs()], reason="t"), ctx)
    assert len(kept) == 1 and not ctx.rejected


def test_value_not_in_cell_rejected():
    ctx = _ctx()
    kept = validate_observations(FactsOutput(observations=[_obs(value_raw="999999")], reason="t"), ctx)
    assert kept == []
    assert ctx.rejected == [("arr-usd", "value-not-in-cell")]


def test_numeric_equivalence_accepted():
    """'5000.0' (xls float rendering) must match a '5000' claim and vice versa — equality is by
    value, not by string."""
    rows = [["metric", "q1"], ["revenue", "5000.0"]]
    ctx = GridContext(sheets={"S": rows}, filename="f.xls")
    obs = _obs(metric="revenue", metric_raw="revenue", value_raw="5000",
               period=None, sheet="S", row=2, col=2)
    assert validate_observations(FactsOutput(observations=[obs], reason="t"), ctx)


def test_label_must_appear_in_row_or_column():
    ctx = _ctx()
    obs = _obs(metric_raw="made-up-label")
    assert validate_observations(FactsOutput(observations=[obs], reason="t"), ctx) == []
    assert ctx.rejected == [("arr-usd", "label-not-found")]


def test_period_must_be_readable_from_grid_or_filename():
    ctx = _ctx()
    bad = _obs(period="2027-05")
    assert validate_observations(FactsOutput(observations=[bad], reason="t"), ctx) == []
    assert ctx.rejected == [("arr-usd", "period-not-found")]
    # a yearly figure whose period lives only in the FILENAME is legitimate
    rows = [["metric", "value"], ["revenue", "5000"]]
    ctx2 = GridContext(sheets={"S": rows}, filename="Annual report 2026.xlsx")
    ok = _obs(metric_raw="revenue", value_raw="5000", period="2026", sheet="S", row=2, col=2)
    assert validate_observations(FactsOutput(observations=[ok], reason="t"), ctx2)


def test_bad_coordinates_and_duplicate_cell_rejected():
    ctx = _ctx()
    out = FactsOutput(observations=[
        _obs(row=99), _obs(sheet="Nope"), _obs(), _obs(metric="arr-dup")], reason="t")
    kept = validate_observations(out, ctx)
    assert len(kept) == 1                       # the two bad ones dropped, the dup collapsed
    reasons = [r for _m, r in ctx.rejected]
    assert reasons == ["bad-coordinates", "bad-coordinates", "duplicate-cell"]


def test_num_parses_common_forms():
    assert _num("480000") == 480000
    assert _num("$1.2M") == 1_200_000
    assert _num("1.200.000") == 1_200_000
    assert _num("1,5") == 1.5
    assert _num("40 %") == 40
    assert _num("n/a") is None
    assert _num("") is None


def test_read_rows_budget_and_paging():
    rows = [[str(i)] for i in range(100)]
    ctx = GridContext(sheets={"S": rows})
    out = read_rows_impl(ctx, "S", 41)
    assert "r41:" in out
    assert "unknown sheet" in read_rows_impl(ctx, "X", 1)
    assert "out of range" in read_rows_impl(ctx, "S", 999)
    ctx.read_rows_calls = MAX_READ_ROWS_CALLS
    assert "budget exhausted" in read_rows_impl(ctx, "S", 1)


def test_render_rows_numbers_are_one_based():
    out = render_rows("S", KPI_ROWS, limit=2)
    assert "r1: c1='month'" in out
    assert "r2: c1='2026-01'" in out
    assert "read_rows('S', 3)" in out           # truncation points at the next row


# ── fake backend: shape + seeded flaw ────────────────────────────────────────
def test_facts_from_grid_maps_kpi_sheet():
    out = facts_from_grid({"Sheet1": KPI_ROWS})
    assert len(out.observations) == 9           # 3 rows x 3 metric columns
    by_key = {(o.metric, o.period): o for o in out.observations}
    arr = by_key[("arr-usd", "2026-03")]
    assert arr.value_raw == "512000" and arr.unit == "usd" and arr.row == 4 and arr.col == 3
    assert by_key[("active-users", "2026-01")].value_raw == "1250"


def test_flawed_fake_is_caught_by_the_validator():
    """The seeded bad value must be dropped by validation — proof the grid decides."""
    ctx = _ctx()
    proc = FakeFactsProcessor(flawed=True)
    out = asyncio.run(proc.run("prompt", deps=ctx)).output
    assert any(o.metric == "seeded-bad-value" for o in out.observations)
    kept = validate_observations(out, ctx)
    assert all(o.metric != "seeded-bad-value" for o in kept)
    assert ("seeded-bad-value", "value-not-in-cell") in ctx.rejected
    assert len(kept) == 9                       # the honest observations all survive


# ── the store ────────────────────────────────────────────────────────────────
def test_store_replace_query_delete_roundtrip(tmp_path):
    fdir = str(tmp_path)
    obs = [_obs(), _obs(metric="nps", metric_raw="nps", value_raw="41", col=4, period="2026-01")]
    n = factstore.replace_facts(fdir, "F1", obs, page_path="entities/initech/kpi.md",
                                entity="initech", org_unit="Clients", extracted_at="2026-07-13T00:00:00Z")
    assert n == 2
    rows = factstore.query_facts(fdir, metric="arr-usd", entity="initech")
    assert len(rows) == 1
    r = rows[0]
    assert r["value_num"] == 480000 and r["value_raw"] == "480000"
    assert r["source_ref"] == "F1!Sheet1!R2C3"
    assert r["period"] == "2026-01" and r["verified"] == 1

    # replace is idempotent per document (reprocess overwrites, never duplicates)
    factstore.replace_facts(fdir, "F1", obs[:1], page_path="p", entity="initech",
                            org_unit="Clients", extracted_at="2026-07-13T01:00:00Z")
    assert len(factstore.query_facts(fdir)) == 1

    assert factstore.delete_facts(fdir, "F1") == 1
    assert factstore.query_facts(fdir) == []
    assert factstore.delete_facts(str(tmp_path / "nowhere"), "F1") == 0   # no db, no crash


def test_store_period_prefix_matching(tmp_path):
    fdir = str(tmp_path)
    factstore.replace_facts(fdir, "F1", [_obs()], page_path=None, entity="initech",
                            org_unit=None, extracted_at="t")
    assert factstore.query_facts(fdir, period="2026")          # year matches year-month rows
    assert factstore.query_facts(fdir, period="2026-01")
    assert not factstore.query_facts(fdir, period="2025")


def test_store_jsonl_export_sorted_and_atomic(tmp_path):
    fdir = str(tmp_path)
    factstore.replace_facts(fdir, "F2", [_obs(metric="zzz", metric_raw="arr_usd")],
                            page_path=None, entity="b", org_unit=None, extracted_at="t")
    factstore.replace_facts(fdir, "F1", [_obs()], page_path=None, entity="a",
                            org_unit=None, extracted_at="t")
    n = factstore.export_jsonl(fdir)
    assert n == 2
    lines = [json.loads(line) for line in (tmp_path / "facts.jsonl").read_text().splitlines()]
    assert [r["entity"] for r in lines] == ["a", "b"]          # deterministic order
    assert factstore.export_jsonl(str(tmp_path / "empty")) == 0


# ── worker wiring: sheet doc -> facts in the store ───────────────────────────
def test_process_one_extracts_facts_for_sheets(tmp_path):
    from tests.test_worker import FakeProcessor, _output

    from clean.worker import process_one

    csv = tmp_path / "KPI metrics 2026.csv"
    csv.write_text("month,arr_usd\n2026-01,480000\n2026-02,495000\n")
    doc = {"fileId": "FID9", "path": str(csv),
           "entry": {"name": csv.name, "drivePath": "/X/Clients/2. Initech/KPI metrics 2026.csv",
                     "orgUnit": "Clients", "sourceUri": "local://kpi"}}
    proc = FakeProcessor(_output(representation="digest", body_markdown="digest body"))
    res = asyncio.run(process_one(doc, proc, str(tmp_path), str(tmp_path / "brain"),
                                  facts_processor=FakeFactsProcessor(), facts_dir=str(tmp_path / "facts")))
    assert res["facts"] == {"kept": 2, "rejected": 0}
    rows = factstore.query_facts(str(tmp_path / "facts"), metric="arr-usd")
    assert {r["value_raw"] for r in rows} == {"480000", "495000"}
    assert all(r["entity"] == "initech" for r in rows)         # entity resolved from the path
    assert all(r["org_unit"] == "Clients" for r in rows)


def test_process_one_skipped_doc_clears_stale_facts(tmp_path):
    from tests.test_worker import FakeProcessor

    from clean.schemas import ProcessorOutput
    from clean.worker import process_one

    fdir = str(tmp_path / "facts")
    factstore.replace_facts(fdir, "FID9", [_obs()], page_path=None, entity=None,
                            org_unit=None, extracted_at="t")
    csv = tmp_path / "old.csv"
    csv.write_text("noise")
    doc = {"fileId": "FID9", "path": str(csv), "entry": {"name": "old.csv", "sourceUri": "u"}}
    proc = FakeProcessor(ProcessorOutput(skipped=True, reason="noise"))
    res = asyncio.run(process_one(doc, proc, str(tmp_path), str(tmp_path / "brain"),
                                  facts_processor=FakeFactsProcessor(), facts_dir=fdir))
    assert res["skipped"] is True
    assert factstore.query_facts(fdir) == []                   # stale numbers gone


def test_process_one_without_facts_processor_is_unchanged(tmp_path):
    from tests.test_worker import FakeProcessor, _output

    from clean.worker import process_one

    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n")
    doc = {"fileId": "F0", "path": str(csv), "entry": {"name": "data.csv", "sourceUri": "u"}}
    res = asyncio.run(process_one(doc, FakeProcessor(_output()), str(tmp_path), str(tmp_path / "b")))
    assert "facts" not in res
