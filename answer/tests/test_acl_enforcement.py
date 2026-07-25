"""ACL enforcement: out-of-scope pages and facts simply don't exist for a scoped client."""
import asyncio
import dataclasses
import inspect
import os
import re

from tests.conftest import add_fact, write_page

from answer import index, mcp_server, metrics, retrieve
from answer.index import visible, visible_sql
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
    # `or None` on purpose here and nowhere else: _scoped() with no labels means "unrestricted" for
    # readability in these tests. Production code must test the scope with `is None` — see
    # test_an_explicitly_empty_scope_is_empty_not_unrestricted for why () is not None.
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


def test_a_legacy_index_re_derives_both_acl_encodings(tmp_path, corpus):
    """Both ACL migrations, end to end, because the value a migration leaves behind is provisional:
    it is the state AFTER the following refresh that gets served.

    v1 (#30): old indexes stored '' for a page with NO acl, and '' now means restricted-to-nobody,
    so such a page must end up open again. v2 (this fix): a row indexed before refresh could read a
    non-list `acl:` holds NULL — open — and refresh only re-reads a page whose mtime/size changed,
    so the migration has to invalidate the cached stat or those rows stay open for the life of the
    index. It closes them meanwhile (unknown is not open), and refresh then derives the truth."""
    write_page(corpus.brain_md_dir, "general/scalar.md", {"title": "s", "acl": "finance"}, "secret body")
    write_page(corpus.brain_md_dir, "general/noacl.md", {"title": "n"}, "open body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    # rewind to a pre-migration index: v1's legacy encoding for no-acl, v2's for an unreadable one
    conn.execute("UPDATE pages SET acl = '' WHERE path = 'general/noacl.md'")
    conn.execute("UPDATE pages SET acl = NULL WHERE path = 'general/scalar.md'")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    conn = index.connect(corpus.state_dir)                     # both migrations fire
    assert conn.execute("SELECT acl FROM pages WHERE path='general/scalar.md'").fetchone()["acl"] == ""
    index.refresh(conn, corpus.brain_md_dir)                   # ...then the truth is re-derived
    assert index.get_page(conn, "general/noacl.md")["acl"] is None        # v1: open again
    assert index.get_page(conn, "general/scalar.md")["acl"] == ""         # v2: nobody scoped
    assert not index.visible(index.get_page(conn, "general/scalar.md")["acl"], {"eng"})

    # neither migration may fire again: a row written afterwards keeps its value
    conn.execute("INSERT INTO pages (path, acl) VALUES ('written-later.md', '')")
    conn.commit()
    conn.close()
    conn = index.connect(corpus.state_dir)
    assert conn.execute("SELECT acl FROM pages WHERE path='written-later.md'").fetchone()["acl"] == ""
    assert conn.execute("SELECT mtime FROM pages WHERE path='general/noacl.md'").fetchone()["mtime"] > 0


def test_an_acl_the_index_cannot_read_is_not_open(corpus):
    """`refresh` honoured `acl` only when it was a YAML *list*; every other shape fell through to
    NULL, i.e. "this page carries no ACL" — open. So the intuitive single-label form `acl: finance`
    served a restricted page to everyone. The rule is already written down in
    brain-page-contract.md: only a page with genuinely NO `acl` key is open; an `acl` you cannot
    read is an `acl`, so it means restricted-to-nobody."""
    shapes = {"scalar": "finance", "quoted": '"finance"', "blank": "''", "null": "null",
              "mapping": "{finance: true}", "empty-list": "[]"}
    for name, value in shapes.items():
        write_page(corpus.brain_md_dir, f"general/{name}.md",
                   {"title": name, "acl": value}, "confidential payroll body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    for name in shapes:
        acl = index.get_page(conn, f"general/{name}.md")["acl"]
        assert acl is not None, name                       # NULL would mean "open to everyone"
        assert not index.visible(acl, {"eng"}), name
        assert index.visible(acl, None), name              # unrestricted still sees it: discoverable
    # a page that genuinely carries no acl key is still open — that is the one open case
    write_page(corpus.brain_md_dir, "general/noacl.md", {"title": "x"}, "open body")
    index.refresh(conn, corpus.brain_md_dir)
    assert index.get_page(conn, "general/noacl.md")["acl"] is None


def test_an_acl_in_a_block_we_could_not_read_is_not_open(corpus):
    """Recognising the block must not require the CLOSING `---`. It did, so a page carrying
    `acl: [finance]` inside a block that is unclosed, truncated mid-write, or ended with YAML's
    `...` was indexed as "no acl" — open to everyone. A BOM or a leading blank line before the
    opener did the same. The BOM case parses correctly now (a BOM is encoding noise, not content);
    the rest cannot be parsed at all, so they resolve to restricted-to-nobody."""
    unreadable = {
        "unclosed": "---\ntitle: p\nacl: [finance]\nbody\n",
        "truncated": "---\ntitle: p\nacl: [fin",
        "dots": "---\ntitle: p\nacl: [finance]\n...\nbody\n",
        "leadblank": "\n---\ntitle: p\nacl: [finance]\n---\nbody\n",
    }
    for name, text in unreadable.items():
        with open(os.path.join(corpus.brain_md_dir, f"{name}.md"), "w", encoding="utf-8") as f:
            f.write(text)
    with open(os.path.join(corpus.brain_md_dir, "bom.md"), "w", encoding="utf-8") as f:
        f.write("﻿---\ntitle: p\nacl: [finance]\n---\nbody\n")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    for name in unreadable:
        acl = index.get_page(conn, f"{name}.md")["acl"]
        assert acl == "", name                             # NULL here would be open to everyone
        assert not index.visible(acl, {"eng"}), name
        assert index.visible(acl, None), name
    # the BOM is stripped, so this page's real ACL is read rather than merely being closed
    assert index.get_page(conn, "bom.md")["acl"] == "finance"
    assert index.visible("finance", {"finance"}) and not index.visible("finance", {"eng"})


def test_a_non_string_label_is_not_coerced_into_a_grantable_one(corpus):
    """`str(label)` would RENAME rather than reject, and a renamed label is grantable. YAML 1.1
    reads `acl: [010]` as the int 8 and `acl: [12:30]` as 750, so coercing indexed audiences "8"
    and "750" that nobody granted — the widening the label check exists to prevent. It also
    collided `[~]` with `["None"]`."""
    shapes = {"octal": ("[010]", "8"), "sexagesimal": ("[12:30]", "750"),
              "hex": ("[0x1f]", "31"), "nil": ("[~]", "None"), "nested": ("[[a]]", "['a']")}
    for name, (value, _coerced) in shapes.items():
        write_page(corpus.brain_md_dir, f"general/{name}.md", {"title": name, "acl": value}, "body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    for name, (_, coerced) in shapes.items():
        acl = index.get_page(conn, f"general/{name}.md")["acl"]
        assert acl == "", name                             # unreadable, not renamed
        assert not index.visible(acl, {coerced}), f"{name}: audience {coerced!r} was never granted"


def test_a_comma_inside_a_label_cannot_widen_access(corpus):
    """Labels are CSV-serialised into one column, so `clean.acl._check_labels` rejects a comma
    inside a label — "the exact silent-corruption failure mode an access-control config must not
    have". The index re-derives the same encoding from page text and applied no such check, so
    `acl: ["finance,eng"]` joined to "finance,eng" and `visible` split it back into TWO audiences,
    granting eng. A label the encoding cannot represent makes the whole ACL unreadable."""
    write_page(corpus.brain_md_dir, "general/csvlabel.md",
               {"title": "x", "acl": '["finance,eng"]'}, "confidential payroll body")
    write_page(corpus.brain_md_dir, "general/blanklabel.md",
               {"title": "x", "acl": '["finance", "  "]'}, "confidential payroll body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    for name in ("csvlabel", "blanklabel"):
        acl = index.get_page(conn, f"general/{name}.md")["acl"]
        assert not index.visible(acl, {"eng"}), name
        assert not index.visible(acl, {"finance"}), name   # unreadable, so nobody scoped — not eng, not finance
        assert index.visible(acl, None), name
    # a well-formed multi-label acl is unaffected
    write_page(corpus.brain_md_dir, "general/ok.md",
               {"title": "x", "acl": "[finance, leadership]"}, "body")
    index.refresh(conn, corpus.brain_md_dir)
    ok = index.get_page(conn, "general/ok.md")["acl"]
    assert index.visible(ok, {"leadership"}) and not index.visible(ok, {"eng"})


def test_visible_sql_agrees_with_visible(tmp_path):
    """index.visible_sql is a second FORM of the one visibility rule, not a second rule — so the two
    are proven identical over a truth table rather than trusted to stay in step. This repo has spent
    five bugs on hand-mirrored ACL logic; a SQL copy only earns its place with this test.

    Includes labels containing LIKE metacharacters (`%`, `_`, `\\`): unescaped, `%` would match
    audiences nobody granted, which is the widening the predicate exists to prevent."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (acl TEXT)")
    acls = [None, "", "finance", "finance,leadership", "eng", "fin", "financex",
            "100%", "a_b", "back\\slash", "%", "_"]
    conn.executemany("INSERT INTO t VALUES (?)", [(a,) for a in acls])
    conn.commit()

    scopes = [None, set(), {"finance"}, {"eng"}, {"finance", "eng"}, {"leadership"},
              {"fin"}, {"100%"}, {"a_b"}, {"back\\slash"}, {"%"}, {"_"}, {"nothing"}]
    for audiences in scopes:
        sql, params = visible_sql("acl", audiences)
        rows = conn.execute(f"SELECT acl FROM t WHERE {sql}", params).fetchall()
        by_sql = sorted((r[0] for r in rows), key=lambda x: (x is None, x))
        by_py = sorted((a for a in acls if visible(a, audiences)), key=lambda x: (x is None, x))
        assert by_sql == by_py, (audiences, by_sql, by_py)


def test_out_of_scope_rows_do_not_starve_a_scoped_client(corpus):
    """The row cap was applied by SQL and the ACL filter in Python afterwards, so invisible rows
    consumed slots in the cap. "An invisible page simply isn't there" (retrieve.py's own comment) —
    a row that isn't there must not occupy a candidate slot. With enough out-of-scope rows sorting
    ahead, a scoped client got ZERO results while open rows it is entitled to existed: a wrong
    answer rather than a truncated one, and one that only afflicts scoped clients."""
    for i in range(60):                       # 'acme' sorts before 'initech', so these come first
        add_fact(corpus.facts_dir, file_id=f"F{i}", page_path="p", entity="acme", metric="arr-usd",
                 metric_raw="ARR", value_raw=str(i), value_num=float(i), unit="usd",
                 period=f"2026-{i % 12 + 1:02d}", source_ref=f"r{i}", acl="finance")
    open_rows = ["480000", "495000", "512000"]
    for i, v in enumerate(open_rows):
        add_fact(corpus.facts_dir, file_id=f"O{i}", page_path="q", entity="zenith", metric="arr-usd",
                 metric_raw="ARR", value_raw=v, value_num=float(v), unit="usd", period="2026-01",
                 source_ref=f"o{i}", acl=None)
    eng = metrics.query_metrics(corpus.facts_dir, "arr-usd", entity="zenith", audiences={"eng"})
    assert [r["value_raw"] for r in eng] == open_rows        # today: []
    assert len(metrics.query_metrics(corpus.facts_dir, "arr-usd", audiences={"eng"})) >= 3
    # the cap still caps, counted in rows the client may actually see
    assert len(metrics.query_metrics(corpus.facts_dir, "arr-usd", limit=2, audiences=None)) == 2
    assert len(metrics.query_metrics(corpus.facts_dir, "arr-usd", limit=2, audiences={"eng"})) == 2
    # ...with SQL's LIMIT semantics, which the old query had for free: 0 = none, negative =
    # unlimited. A `len(out) >= limit` break would have returned ONE row for both.
    assert metrics.query_metrics(corpus.facts_dir, "arr-usd", limit=0) == []
    unlimited = metrics.query_metrics(corpus.facts_dir, "arr-usd", limit=-1)
    assert len(unlimited) > len(metrics.query_metrics(corpus.facts_dir, "arr-usd", limit=50))


def test_out_of_scope_pages_do_not_starve_a_scoped_search(corpus):
    """Same defect on the page side: the FTS candidate pool was capped before the ACL filter, so
    out-of-scope pages crowded an open one out of the pool entirely."""
    for i in range(45):
        write_page(corpus.brain_md_dir, f"entities/acme/w{i}.md",
                   {"title": f"widget {i}", "acl": "[finance]"}, "widget detail body")
    write_page(corpus.brain_md_dir, "general/widget-open.md", {"title": "widget open"},
               "widget detail body open")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    hits = retrieve.search(conn, "widget detail", audiences={"eng"})
    assert any(h["path"] == "general/widget-open.md" for h in hits)      # today: no hits at all
    assert all(not h["path"].startswith("entities/acme/") for h in hits)
    assert len(retrieve.search(conn, "widget detail", audiences=None)) > 0


def test_settings_parse_audiences(monkeypatch):
    from answer.settings import Settings
    monkeypatch.setenv("ANSWER_AUDIENCES", "sales, leadership")
    assert Settings.from_env().audiences == ("sales", "leadership")
    monkeypatch.setenv("ANSWER_AUDIENCES", "")
    assert Settings.from_env().audiences is None                  # documented: unset = open corpus


def test_a_scope_that_parses_to_no_labels_is_refused(monkeypatch):
    """A set value that yields zero labels must NOT collapse to "no scope". It used to: `tuple(...)
    or None` turned () into None, so ANSWER_AUDIENCES="," served the WHOLE corpus while the operator
    believed the instance was scoped — one template rendering an empty list is enough. ADR 010 states
    the rule for the write side: "A malformed config errors loudly: silently-open is the one failure
    mode an access-control file must not have." The read side gets it too."""
    import pytest

    from answer.settings import Settings
    for hostile in [",", ",,", " , ", "\t,\t", ",  ,", ", ,"]:
        monkeypatch.setenv("ANSWER_AUDIENCES", hostile)
        with pytest.raises(RuntimeError, match="ANSWER_AUDIENCES"):
            Settings.from_env()
    # An unset — or purely blank, which is indistinguishable from unset — scope stays the documented
    # open corpus. What must fail is a value that LOOKS like it carries labels but carries none.
    for open_corpus in [None, "", "   ", "\t"]:
        monkeypatch.delenv("ANSWER_AUDIENCES", raising=False)
        if open_corpus is not None:
            monkeypatch.setenv("ANSWER_AUDIENCES", open_corpus)
        assert Settings.from_env().audiences is None, repr(open_corpus)
    monkeypatch.setenv("ANSWER_AUDIENCES", " ,finance,")      # stray separators still parse
    assert Settings.from_env().audiences == ("finance",)


def test_an_explicitly_empty_scope_is_empty_not_unrestricted(corpus):
    """audiences=() is a client holding NO labels — it may see open content and nothing else. It
    must never mean "unrestricted", the way `if settings.audiences` used to read it: an empty scope
    is not the absence of a scope, exactly as index.visible('') is not index.visible(None)."""
    _restricted_corpus(corpus)
    empty = AnswerService(dataclasses.replace(corpus, audiences=()))
    assert empty.audiences == set()                                   # not None
    assert [n for n, probe in _LEAK_PROBES.items() if probe(empty)] == []   # sees no labeled content
    assert empty.get_page("entities/initech/kpi.md") is not None      # unlabeled -> still open
    assert empty.query_metrics("arr-usd")

    # ...and the converse must NOT drift: None is unrestricted, and collapsing it into an empty
    # scope would take every open deployment dark while still satisfying the assertions above.
    unrestricted = AnswerService(dataclasses.replace(corpus, audiences=None))
    assert unrestricted.audiences is None
    assert [n for n, probe in _LEAK_PROBES.items() if not probe(unrestricted)] == []
