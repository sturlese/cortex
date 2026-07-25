"""ACL enforcement: out-of-scope pages and facts simply don't exist for a scoped client."""
import asyncio
import dataclasses
import inspect
import re

from tests.conftest import add_fact, write_page

from answer import mcp_server, metrics
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


def test_known_metrics_are_scoped(corpus):
    """The metric vocabulary is a read path too, so it must be scoped like every other one
    (answer/index.md, ADR 010: "even entity *existence* is scoped"). It is reached on the
    EMPTY-result path of metrics_text — i.e. exactly when the ACL just hid the rows — so an
    unscoped copy hands the scoped client the two facts the rest of the service hides: that
    `acme` exists and that it has a `total-compensation` figure."""
    _restricted_corpus(corpus)
    finance, eng = _scoped(corpus, "finance"), _scoped(corpus, "eng")

    assert "total-compensation" in metrics.known_metrics(corpus.facts_dir, audiences={"finance"})
    assert "total-compensation" not in metrics.known_metrics(corpus.facts_dir, audiences={"eng"})
    assert "arr-usd" in metrics.known_metrics(corpus.facts_dir, audiences={"eng"})   # open stays open
    assert "total-compensation" in metrics.known_metrics(corpus.facts_dir)           # unrestricted

    # the two service paths that reach it
    assert "total-compensation" not in eng.metrics_text(None, "acme")
    assert "total-compensation" in finance.metrics_text(None, "acme")
    assert eng.match_metric({"total", "compensation"}) is None
    assert finance.match_metric({"total", "compensation"}) == "total-compensation"


# Every public read surface of AnswerService -> how to probe it for the restricted document of
# _restricted_corpus. `refresh` is the one deliberate exclusion: it returns index-maintenance
# counters, not content (see the test below, which pins that reasoning instead of waiving it).
_LEAK_PROBES = {
    "search": lambda s: any(h["path"] == "entities/acme/payroll.md" for h in s.search("acme payroll")),
    # probe for the document, never for words the client itself supplied: these renderings echo the
    # query back ("no results for: acme payroll"), so matching on "payroll" would flag the echo.
    "search_text": lambda s: "entities/acme/payroll.md" in s.search_text("acme payroll")
                             or "750000" in s.search_text("acme payroll"),
    "get_page": lambda s: s.get_page("entities/acme/payroll.md") is not None,
    "page_text": lambda s: "750000" in (s.page_text("entities/acme/payroll.md") or ""),
    "query_metrics": lambda s: bool(s.query_metrics("total-compensation")),
    "current_metric_rows": lambda s: bool(s.current_metric_rows("total-compensation", "acme")),
    "known_entities": lambda s: "acme" in s.known_entities(),
    "match_metric": lambda s: s.match_metric({"total", "compensation"}) is not None,
    "metrics_text": lambda s: "total-compensation" in s.metrics_text(None, "acme")
                              or "750000" in s.metrics_text(None, "acme"),
    "ask": lambda s: "750000" in asyncio.run(s.ask("what is the total compensation for acme?"))["answer"],
}
_NOT_A_CONTENT_SURFACE = {"refresh"}


def test_leak_probes_cover_every_public_service_surface():
    """The checklist above is only worth anything if it is COMPLETE, so completeness is asserted
    rather than assumed. `known_metrics` reached the service without being added to any such list,
    which is exactly how it stayed unscoped — a hardcoded list would have been blind to it too.
    Adding a public method to AnswerService fails here until it is either probed for leaks or
    explicitly classified as not-a-content-surface."""
    public = {n for n, v in inspect.getmembers(AnswerService)
              if not n.startswith("_") and (inspect.isfunction(v) or inspect.iscoroutinefunction(v))}
    assert public == set(_LEAK_PROBES) | _NOT_A_CONTENT_SURFACE, (
        f"unclassified: {public - set(_LEAK_PROBES) - _NOT_A_CONTENT_SURFACE}; "
        f"stale: {set(_LEAK_PROBES) | _NOT_A_CONTENT_SURFACE - public}")


def test_no_public_read_surface_leaks_an_out_of_scope_document(corpus):
    """The scope must hide the same restricted document through every surface at once — and the
    client that holds the label must still reach it through every one of them, so an over-denying
    "fix" cannot pass by hiding everything from everybody."""
    _restricted_corpus(corpus)
    eng, finance = _scoped(corpus, "eng"), _scoped(corpus, "finance")
    assert [n for n, probe in _LEAK_PROBES.items() if probe(eng)] == []
    assert [n for n, probe in _LEAK_PROBES.items() if not probe(finance)] == []


def test_refresh_counts_are_index_maintenance_not_a_content_surface(corpus):
    """The one surface excluded from the leak probes, with its reason pinned. refresh() reports what
    the INDEX did — and the index deliberately holds every page, since one server instance serves
    one scope. Its counters are therefore unscoped by construction, which is only acceptable while
    no transport returns them to a client: mcp_server calls refresh() and discards the result. If
    that ever changes, this becomes a count oracle over document existence and needs scoping."""
    _restricted_corpus(corpus)
    counts = _scoped(corpus, "eng").refresh()
    assert counts["total"] >= len(_scoped(corpus).search(""))          # unscoped by construction
    src = inspect.getsource(mcp_server)
    assert "service.refresh()" in src or "svc.refresh()" in src
    assert not re.search(r"return\s+[a-z_]*\.refresh\(\)", src)        # never handed to a client


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
