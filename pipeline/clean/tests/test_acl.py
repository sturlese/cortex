"""ACL resolution: config validation, first-match rules, dossier intersection, wiring."""
import asyncio
import json

import pytest

from clean.acl import dossier_acl, load_acl_config, resolve_acl, visible


def _config(tmp_path, cfg):
    p = tmp_path / "acl.json"
    p.write_text(json.dumps(cfg))
    return str(p)


def test_load_acl_config_none_and_validation(tmp_path):
    assert load_acl_config(None) is None
    ok = load_acl_config(_config(tmp_path, {
        "default": ["all"],
        "rules": [{"unit": "Finance", "audiences": ["finance"]}]}))
    assert ok["default"] == ["all"]
    with pytest.raises(ValueError, match="non-empty 'audiences'"):
        load_acl_config(_config(tmp_path, {"rules": [{"unit": "X", "audiences": []}]}))
    with pytest.raises(ValueError, match="rule needs one of"):
        load_acl_config(_config(tmp_path, {"rules": [{"audiences": ["a"]}]}))
    with pytest.raises(ValueError, match="'default' must be"):
        load_acl_config(_config(tmp_path, {"default": [], "rules": []}))


def test_resolve_acl_first_match_wins(tmp_path):
    cfg = load_acl_config(_config(tmp_path, {
        "default": ["all"],
        "rules": [
            {"path_contains": "board", "audiences": ["leadership"]},
            {"unit": "Clients", "audiences": ["sales", "leadership"]},
            {"entity_kind": "prospect", "audiences": ["sales"]},
        ]}))
    assert resolve_acl(cfg, "/X/Clients/board minutes.pdf", "Clients", None) == ["leadership"]
    assert resolve_acl(cfg, "/X/Clients/1. Acme/report.pdf", "Clients", "tracked") == ["sales", "leadership"]
    assert resolve_acl(cfg, "/X/Pipeline/Evaluating/Hooli/deck.pdf", "Pipeline", "prospect") == ["sales"]
    assert resolve_acl(cfg, "/X/Product/roadmap.md", "Product", None) == ["all"]
    assert resolve_acl(None, "/anything", "Clients", None) is None      # ACLs off -> no field


def test_dossier_acl_is_intersection():
    assert dossier_acl([["sales", "leadership"], ["finance", "leadership"]]) == ["leadership"]
    assert dossier_acl([["sales"], None]) == ["sales"]                  # None members don't restrict
    assert dossier_acl([None, None]) is None                            # open members -> open dossier
    assert dossier_acl([["sales"], ["finance"]]) == []                  # disjoint -> restricted, never open


def test_visible_rule():
    assert visible(None, {"eng"}) is True                               # unlabeled page: open
    assert visible(["sales"], None) is True                             # unrestricted client
    assert visible(["sales"], {"sales", "eng"}) is True
    assert visible(["sales"], {"eng"}) is False
    assert visible([], {"eng"}) is False                                # empty acl: nobody scoped sees it


def test_worker_stamps_pages_facts_and_result(tmp_path):
    from tests.test_worker import FakeProcessor, _output

    from clean import factstore
    from clean.fake_llm import FakeProseFactsProcessor
    from clean.worker import process_one

    cfg = load_acl_config(_config(tmp_path, {
        "default": ["all"], "rules": [{"unit": "Clients", "audiences": ["sales"]}]}))
    doc_file = tmp_path / "Quarterly Report Q1 2026.md"
    doc_file.write_text("Revenue impact for Globex was $1.2M ARR this quarter.")
    doc = {"fileId": "FA", "path": str(doc_file),
           "entry": {"name": doc_file.name, "drivePath": "/X/Clients/1. Globex/q.md",
                     "orgUnit": "Clients", "sourceUri": "local://q"}}
    res = asyncio.run(process_one(doc, FakeProcessor(_output(body_markdown="no figures here")),
                                  str(tmp_path), str(tmp_path / "brain"),
                                  prose_facts_processor=FakeProseFactsProcessor(),
                                  facts_dir=str(tmp_path / "facts"), acl_config=cfg))
    assert res["acl"] == ["sales"]
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "acl: [sales]" in page
    rows = factstore.query_facts(str(tmp_path / "facts"))
    assert rows and all(r["acl"] == "sales" for r in rows)              # numbers inherit the audience


def test_dossier_page_carries_intersection(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEAN_LLM", "fake")
    from clean.dossiers import build_dossiers
    from clean.settings import Settings

    st = {"version": 1, "files": {
        "A": {"status": "processed", "lastResult": {
            "path": "entities/g/a.md", "entity": "globex", "title": "A", "acl": ["sales", "leadership"]}},
        "B": {"status": "processed", "lastResult": {
            "path": "entities/g/b.md", "entity": "globex", "title": "B", "acl": ["leadership"]}},
    }}
    (tmp_path / "brain" / "entities" / "g").mkdir(parents=True)
    for n in ("a", "b"):
        (tmp_path / "brain" / "entities" / "g" / f"{n}.md").write_text("---\nt: x\n---\n\n# T\n\nbody\n")
    cfg = Settings(brain_md_dir=str(tmp_path / "brain"), state_dir=str(tmp_path / "state"),
                   facts_dir=str(tmp_path / "facts"), dossiers_dir=str(tmp_path / "dossiers"),
                   dry_run=False)
    asyncio.run(build_dossiers(cfg, st, touched={"A", "B"}, log=lambda *_: None))
    page = (tmp_path / "dossiers" / "globex.md").read_text()
    assert "acl: [leadership]" in page                                  # intersection, never union
