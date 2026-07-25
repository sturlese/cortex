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


def test_load_acl_config_rejects_labels_that_break_csv_serialization(tmp_path):
    """Audience labels travel as CSV in the facts store and the answer index: a comma inside a
    label would silently split into two audiences at enforcement time; an empty label vanishes.
    Both must fail loudly at config load."""
    with pytest.raises(ValueError, match="invalid audience label"):
        load_acl_config(_config(tmp_path, {
            "rules": [{"unit": "X", "audiences": ["sales,leadership"]}]}))
    # the blank case must report EMPTINESS, not be swallowed by the whitespace check that follows it
    # (which would tell the operator `did you mean ''?`) — so the order of the two checks matters
    with pytest.raises(ValueError, match="must be non-empty"):
        load_acl_config(_config(tmp_path, {"default": ["  "], "rules": []}))


def test_load_acl_config_rejects_labels_a_client_scope_could_never_produce(tmp_path):
    """A whitespace-padded label is silently DEAD, and this is the mirror image of the comma case.

    Enforcement compares labels exactly (`answer.index.visible` splits the CSV on ',' and
    intersects), while a client's scope is split AND stripped (`answer.settings._labels`). So no
    ANSWER_AUDIENCES value can ever yield " finance ": a rule granting it produces a page that
    reaches nobody. Verified end to end before this fix — a rule whose only audience was " finance "
    was invisible to scope {"finance"}, to {"leadership"}, and to {"finance","leadership","all"};
    only an unrestricted client saw it, so the operator who wrote the rule got the opposite of what
    the file said.

    Rejected rather than stripped on purpose: silently normalizing would make this file mean
    something other than what it says, and it is the access-control authority. It also hides the
    typo the padding usually is."""
    for label in (" finance ", "finance ", " finance", "\tfinance", "finance\n"):
        with pytest.raises(ValueError, match="invalid audience label"):
            load_acl_config(_config(tmp_path, {
                "rules": [{"unit": "X", "audiences": [label]}]}))
    with pytest.raises(ValueError, match="could never be granted"):
        load_acl_config(_config(tmp_path, {"default": [" all "], "rules": []}))
    # ...and the suggestion names the label the author meant
    with pytest.raises(ValueError, match="did you mean 'finance'"):
        load_acl_config(_config(tmp_path, {"rules": [{"unit": "X", "audiences": [" finance "]}]}))
    # internal whitespace is legitimate and must still load
    ok = load_acl_config(_config(tmp_path, {
        "default": ["all"], "rules": [{"unit": "X", "audiences": ["board members"]}]}))
    assert ok["rules"][0]["audiences"] == ["board members"]


def test_a_rejected_acl_config_stops_before_any_work(tmp_path, monkeypatch):
    """Validation has to happen at STARTUP, not where the pass happens to need the config.

    `run_once` loads it deep in the work path — after `dedup_pending` has already deleted pages and
    facts rows and saved state. compose runs this worker `restart: unless-stopped`, so validating
    there would turn one bad label into a crash loop that destroys state every lap and processes
    nothing, while an operator editing the ACL file gets no feedback at edit time (with nothing
    pending the same invalid config even reports `pass OK` and exits 0).

    So: refuse in `cli()`, with the message and no traceback."""
    import asyncio

    from clean import main as clean_main

    cfg_path = _config(tmp_path, {"default": ["all"],
                                  "rules": [{"unit": "Finance", "audiences": [" finance "]}]})
    for k, v in {"CLEAN_ACL": cfg_path, "RAW_DIR": str(tmp_path / "raw"),
                 "BRAIN_MD_DIR": str(tmp_path / "brain"),
                 "CLEAN_STATE_DIR": str(tmp_path / "state")}.items():
        monkeypatch.setenv(k, v)
    ran = []
    monkeypatch.setattr(asyncio, "run", lambda *a, **k: ran.append(True))

    with pytest.raises(SystemExit) as ei:
        clean_main.cli(["--once"])
    assert "invalid audience label" in str(ei.value)
    assert "ERROR:" in str(ei.value)
    assert not ran, "the pass must not start with a rejected ACL config"


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
