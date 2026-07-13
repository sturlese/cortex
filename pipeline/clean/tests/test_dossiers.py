"""Dossiers: deterministic staleness, bounded tools, verified output, lifecycle."""
import asyncio
import dataclasses

from clean import factstore
from clean.dossiers import (
    DossierContext,
    FakeDossierWriter,
    build_dossiers,
    member_hash,
    members_of,
    query_facts_impl,
    read_page_impl,
)
from clean.settings import Settings


def _state():
    return {"version": 1, "files": {
        "A": {"status": "processed", "name": "Quarterly Report Q1 2026.md",
              "lastResult": {"path": "entities/globex/q1.md", "entity": "globex",
                             "title": "Quarterly Report Q1 2026", "as_of": "2026-Q1",
                             "superseded_by": "B"}},
        "B": {"status": "processed", "name": "Quarterly Report Q1 2026 FINAL.md",
              "lastResult": {"path": "entities/globex/q1-final.md", "entity": "globex",
                             "title": "Quarterly Report Q1 2026 FINAL", "as_of": "2026-Q1",
                             "supersedes": "A"}},
        "C": {"status": "processed", "name": "notes.md",
              "lastResult": {"path": "units/product/notes.md", "unit": "Product"}},
        "D": {"status": "processed", "name": "junk.md", "lastResult": {"skipped": True, "entity": "globex"}},
    }}


def _cfg(tmp_path, **kw):
    d = dict(brain_md_dir=str(tmp_path / "brain"), state_dir=str(tmp_path / "state"),
             facts_dir=str(tmp_path / "facts"), dossiers_dir=str(tmp_path / "dossiers"),
             dry_run=False)
    d.update(kw)
    return Settings(**d)


def _seed_pages_and_facts(tmp_path):
    (tmp_path / "brain" / "entities" / "globex").mkdir(parents=True)
    (tmp_path / "brain" / "entities" / "globex" / "q1.md").write_text(
        "---\nt: x\n---\n\n# Q1\n\nRevenue impact was $1.2M ARR.\n")
    (tmp_path / "brain" / "entities" / "globex" / "q1-final.md").write_text(
        "---\nt: x\n---\n\n# Q1 FINAL\n\nRevenue impact was $1.3M ARR.\n")
    from clean.facts import sheet_rows_for_store
    from clean.schemas import FactObservation
    factstore.replace_facts(str(tmp_path / "facts"), "A", sheet_rows_for_store("A", [
        FactObservation(metric="revenue-impact", metric_raw="Revenue impact", value_raw="1.2M",
                        unit="usd", sheet="text", row=1, col=1)]),
        page_path="entities/globex/q1.md", entity="globex", org_unit=None, extracted_at="t")
    factstore.replace_facts(str(tmp_path / "facts"), "B", sheet_rows_for_store("B", [
        FactObservation(metric="revenue-impact", metric_raw="Revenue impact", value_raw="1.3M",
                        unit="usd", sheet="text", row=1, col=1)]),
        page_path="entities/globex/q1-final.md", entity="globex", org_unit=None, extracted_at="t")


def test_members_and_hash_change_on_supersede():
    st = _state()
    members = members_of(st, "globex")
    assert [m["fileId"] for m in members] == ["A", "B"]        # skipped D excluded, sorted
    h1 = member_hash(members)
    st["files"]["A"]["lastResult"]["superseded_by"] = None
    assert member_hash(members_of(st, "globex")) != h1         # supersede-state is part of staleness


def test_tool_impls_bound_and_fence(tmp_path):
    _seed_pages_and_facts(tmp_path)
    ctx = DossierContext(slug="globex", members=members_of(_state(), "globex"),
                         brain_md_dir=str(tmp_path / "brain"), facts_dir=str(tmp_path / "facts"))
    out = read_page_impl(ctx, "entities/globex/q1.md")
    assert "UNTRUSTED-DATA" in out and "$1.2M" in out
    assert "not one of this entity's pages" in read_page_impl(ctx, "units/product/notes.md")
    ctx.page_reads = 99
    assert "budget exhausted" in read_page_impl(ctx, "entities/globex/q1-final.md")
    facts_txt = query_facts_impl(ctx, "")
    assert "1.3M" in facts_txt
    assert "SUPERSEDED" in facts_txt                            # the draft's row is flagged


def test_fake_writer_prefers_current_figures(tmp_path):
    _seed_pages_and_facts(tmp_path)
    ctx = DossierContext(slug="globex", members=members_of(_state(), "globex"),
                         brain_md_dir=str(tmp_path / "brain"), facts_dir=str(tmp_path / "facts"))
    out = asyncio.run(FakeDossierWriter().run("p", deps=ctx)).output
    assert "1.3M" in out.body_markdown and "1.2M" not in out.body_markdown
    assert "*(superseded)*" in out.body_markdown                # history noted, not erased


def test_build_dossiers_writes_verified_page_and_is_incremental(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEAN_LLM", "fake")
    _seed_pages_and_facts(tmp_path)
    st = _state()
    cfg = _cfg(tmp_path)
    stats = asyncio.run(build_dossiers(cfg, st, touched={"A", "B"}, log=lambda *_: None))
    assert stats == {"dossiers_written": 1}
    page = (tmp_path / "dossiers" / "globex.md").read_text()
    assert "type: dossier" in page and "entity: globex" in page
    assert "verification: verified" in page                     # judged like any page
    assert "1.3M" in page and "1.2M" not in page                # current truth
    assert st["dossiers"]["globex"]["hash"]

    # unchanged members -> no rewrite (member-hash gate)
    stats2 = asyncio.run(build_dossiers(cfg, st, touched={"A"}, log=lambda *_: None))
    assert stats2 == {}


def test_build_dossiers_removes_when_entity_empties(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEAN_LLM", "fake")
    _seed_pages_and_facts(tmp_path)
    st = _state()
    cfg = _cfg(tmp_path)
    asyncio.run(build_dossiers(cfg, st, touched={"A", "B"}, log=lambda *_: None))
    for fid in ("A", "B"):
        st["files"][fid]["status"] = "deleted"
    stats = asyncio.run(build_dossiers(cfg, st, touched={"A", "B"}, log=lambda *_: None))
    assert stats == {"dossiers_removed": 1}
    assert not (tmp_path / "dossiers" / "globex.md").exists()
    assert "globex" not in st["dossiers"]


def test_dossier_verifier_retry_on_invented_figure(tmp_path):
    """A writer that invents a figure must fail verification and the retry (clean) must win."""
    from clean.dossiers import _write_one

    class BadThenGood:
        def __init__(self):
            self.calls = 0

        async def run(self, prompt, *, deps=None, usage_limits=None):
            import types

            from clean.dossiers import DossierOutput, query_facts_impl
            self.calls += 1
            query_facts_impl(deps, "")                          # gather real evidence
            if self.calls == 1:
                out = DossierOutput(body_markdown="Revenue soared to 9.9M with 77% margin and 88% NRR.",
                                    reason="bad")
            else:
                assert "DETERMINISTIC VERIFIER" in prompt
                out = DossierOutput(body_markdown="Revenue impact: 1.3M usd.", reason="good")
            usage = types.SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_tokens=0, details={})
            return types.SimpleNamespace(output=out, usage=usage)

    _seed_pages_and_facts(tmp_path)
    cfg = _cfg(tmp_path)
    members = members_of(_state(), "globex")
    page, verdict = asyncio.run(_write_one(BadThenGood(), "globex", members, cfg))
    assert verdict == "verified"
    assert "1.3M" in page and "9.9M" not in page


def test_settings_dossier_flags(monkeypatch):
    monkeypatch.setenv("CLEAN_DOSSIERS", "off")
    monkeypatch.setenv("BRAIN_DOSSIERS_DIR", "/dd")
    cfg = Settings.from_env()
    assert cfg.dossiers is False and cfg.dossiers_dir == "/dd"
    assert dataclasses.replace(cfg, dossiers=True).dossiers is True