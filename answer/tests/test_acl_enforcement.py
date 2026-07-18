"""ACL enforcement: out-of-scope pages and facts simply don't exist for a scoped client."""
import asyncio
import dataclasses

from tests.conftest import add_fact, write_page

from answer.index import visible
from answer.service import AnswerService


def test_visible_rule():
    assert visible(None, {"eng"}) is True             # no ACL -> open
    assert visible("sales", None) is True             # unrestricted client sees everything
    assert visible("sales,leadership", {"sales"}) is True
    assert visible("sales", {"eng"}) is False
    # an EMPTY acl is not "no ACL": it is a deliberately empty intersection (dossier whose
    # members share no audience) — restricted to nobody below unrestricted clients, exactly
    # like the pipeline's visible([], audiences). It used to be served OPEN.
    assert visible("", {"eng"}) is False
    assert visible("", None) is True


def test_empty_acl_page_is_hidden_from_scoped_clients(corpus):
    """Regression: a page carrying `acl: []` (e.g. a dossier over members with disjoint
    audiences) must not be visible to any scoped client — but stays visible unrestricted."""
    write_page(corpus.brain_md_dir, "entities/acme/dossier.md",
               {"type": "dossier", "title": "Acme dossier", "entity": "acme",
                "verification": "verified", "acl": "[]"},
               "Cross-audience rollup for Acme. Contract value 900000 usd.")
    scoped = _scoped(corpus, "eng")
    assert scoped.get_page("entities/acme/dossier.md") is None
    assert not any(h["path"] == "entities/acme/dossier.md" for h in scoped.search("acme dossier"))
    assert _scoped(corpus).get_page("entities/acme/dossier.md") is not None


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


def test_pre_fix_index_reencodes_empty_acl_as_open(tmp_path):
    """Old indexes stored '' for pages with no acl; under the fixed encoding '' means
    "restricted to nobody", so connect() must re-encode legacy rows to NULL (their observed
    behavior) exactly once, without touching rows written after the migration."""
    from answer import index
    state = str(tmp_path / "state")
    conn = index.connect(state)
    conn.execute("INSERT INTO pages (path, acl) VALUES ('legacy.md', '')")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    conn = index.connect(state)                      # migration fires
    assert conn.execute("SELECT acl FROM pages WHERE path='legacy.md'").fetchone()["acl"] is None
    conn.execute("INSERT INTO pages (path, acl) VALUES ('empty.md', '')")
    conn.commit()
    conn.close()
    conn = index.connect(state)                      # migration must NOT fire again
    assert conn.execute("SELECT acl FROM pages WHERE path='empty.md'").fetchone()["acl"] == ""


def test_settings_parse_audiences(monkeypatch):
    from answer.settings import Settings
    monkeypatch.setenv("ANSWER_AUDIENCES", "sales, leadership")
    assert Settings.from_env().audiences == ("sales", "leadership")
    monkeypatch.setenv("ANSWER_AUDIENCES", "")
    assert Settings.from_env().audiences is None
