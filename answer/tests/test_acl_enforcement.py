"""ACL enforcement: out-of-scope pages and facts simply don't exist for a scoped client."""
import asyncio
import dataclasses

from tests.conftest import add_fact, write_page

from answer.index import visible
from answer.service import AnswerService


def test_visible_rule():
    assert visible(None, {"eng"}) is True
    assert visible("", {"eng"}) is True
    assert visible("sales", None) is True
    assert visible("sales,leadership", {"sales"}) is True
    assert visible("sales", {"eng"}) is False


def _scoped(corpus, *audiences):
    return AnswerService(dataclasses.replace(corpus, audiences=audiences or None))


def _restricted_corpus(corpus):
    write_page(corpus.brain_md_dir, "entities/acme/payroll.md",
               {"type": "report", "title": "Acme payroll summary", "entity": "acme",
                "verification": "verified", "acl": "[finance]"},
               "Payroll summary for Acme. Total compensation 750000 usd in 2026.")
    add_fact(corpus.facts_dir, file_id="local-pay", page_path="entities/acme/payroll.md",
             entity="acme", metric="total-compensation", metric_raw="Total compensation",
             value_raw="750000", value_num=750000.0, unit="usd", period="2026",
             source_ref="local-pay!text!30", acl="finance")
    return corpus


def test_search_hides_out_of_scope_pages(corpus):
    _restricted_corpus(corpus)
    finance = _scoped(corpus, "finance")
    eng = _scoped(corpus, "eng")
    assert any(h["path"] == "entities/acme/payroll.md" for h in finance.search("acme payroll"))
    assert not any(h["path"] == "entities/acme/payroll.md" for h in eng.search("acme payroll"))
    # unlabeled pages stay visible to everyone
    assert eng.search("initech kpi")


def test_read_page_denies_out_of_scope(corpus):
    _restricted_corpus(corpus)
    eng = _scoped(corpus, "eng")
    assert eng.get_page("entities/acme/payroll.md") is None
    assert "unknown page" in eng.page_text("entities/acme/payroll.md")
    assert _scoped(corpus, "finance").get_page("entities/acme/payroll.md")


def test_query_metrics_filters_rows_and_entities(corpus):
    _restricted_corpus(corpus)
    finance = _scoped(corpus, "finance")
    eng = _scoped(corpus, "eng")
    assert finance.query_metrics("total-compensation")
    assert eng.query_metrics("total-compensation") == []
    assert eng.query_metrics("arr-usd")                       # unlabeled facts stay open
    assert "acme" in finance.known_entities()
    assert "acme" not in eng.known_entities()                 # existence is scoped too


def test_ask_refuses_out_of_scope_but_answers_in_scope(corpus):
    _restricted_corpus(corpus)
    res = asyncio.run(_scoped(corpus, "eng").ask("what is the total compensation for acme?"))
    assert res["refused"] is True
    res2 = asyncio.run(_scoped(corpus, "finance").ask("what is the total compensation for acme?"))
    assert res2["refused"] is False
    assert "750000" in res2["answer"]
    assert res2["verification"]["verdict"] == "verified"


def test_unrestricted_service_unchanged(service):
    assert asyncio.run(service.ask("what is the arr-usd for initech in 2026-03?"))["refused"] is False


def test_settings_parse_audiences(monkeypatch):
    from answer.settings import Settings
    monkeypatch.setenv("ANSWER_AUDIENCES", "sales, leadership")
    assert Settings.from_env().audiences == ("sales", "leadership")
    monkeypatch.setenv("ANSWER_AUDIENCES", "")
    assert Settings.from_env().audiences is None
