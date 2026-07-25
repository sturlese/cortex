"""Index lifecycle + contract-aware ranking."""
import os

from tests.conftest import write_page

from answer import index, retrieve


def test_refresh_adds_updates_removes(corpus):
    conn = index.connect(corpus.state_dir)
    stats = index.refresh(conn, corpus.brain_md_dir)
    assert stats["added"] == 4 and stats["total"] == 4

    # unchanged -> no work
    assert index.refresh(conn, corpus.brain_md_dir)["added"] == 0

    # update: page rewritten
    write_page(corpus.brain_md_dir, "entities/initech/kpi.md",
               {"type": "report", "title": "KPI metrics 2026 v2", "entity": "initech"}, "new body")
    stats = index.refresh(conn, corpus.brain_md_dir)
    assert stats["updated"] == 1
    assert index.get_page(conn, "entities/initech/kpi.md")["title"] == "KPI metrics 2026 v2"

    # removal propagates
    os.remove(os.path.join(corpus.brain_md_dir, "units/product/roadmap.md"))
    stats = index.refresh(conn, corpus.brain_md_dir)
    assert stats["removed"] == 1 and stats["total"] == 3


def test_index_parses_contract_fields(service):
    page = service.get_page("entities/globex/q1-report.md")
    assert page["entity"] == "globex"
    assert page["as_of"] == "2026-Q1"
    assert page["superseded_by"] == "local-new"
    assert page["verification"] == "verified"
    kpi = service.get_page("entities/initech/kpi.md")
    assert kpi["detail_in_source"] == 1


def test_unparseable_frontmatter_still_indexes_body(tmp_path, corpus):
    write_page(corpus.brain_md_dir, "general/broken.md", {"title": "x: [unclosed"}, "findable needle body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    hits = retrieve.search(conn, "findable needle")
    assert any(h["path"] == "general/broken.md" for h in hits)


def test_invalid_date_frontmatter_does_not_abort_the_refresh(tmp_path, corpus):
    """An impossible-but-timestamp-shaped date raises a bare ValueError out of datetime.date(), not
    a YAMLError. Uncaught, ONE such page aborted the whole refresh — every other page went unindexed
    too. It must degrade to body-only like any other unparseable frontmatter."""
    write_page(corpus.brain_md_dir, "general/baddate.md",
               {"title": "x", "date": "2026-02-30"}, "findable needle body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)                     # must not raise
    hits = retrieve.search(conn, "findable needle")
    assert any(h["path"] == "general/baddate.md" for h in hits)


def test_refresh_survives_every_kind_of_unreadable_frontmatter(tmp_path, corpus):
    """PyYAML's failures share no base class — an impossible date raises ValueError, `!!bool maybe` a
    KeyError, `!!timestamp hello` an AttributeError, deep nesting a RecursionError. Any of them
    escaping aborts the refresh for EVERY page, not just the bad one."""
    for i, bad in enumerate(["2026-02-30", "!!bool maybe", "!!timestamp hello", "[" * 40000]):
        write_page(corpus.brain_md_dir, f"general/bad{i}.md", {"title": "x", "junk": bad}, "junk body")
    write_page(corpus.brain_md_dir, "general/healthy.md", {"title": "ok"}, "findable needle body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)                     # must not raise
    assert any(h["path"] == "general/healthy.md" for h in retrieve.search(conn, "findable needle"))


def test_unreadable_frontmatter_is_not_served_as_open(tmp_path, corpus):
    """Degrading an unparseable page to body-only must not degrade its ACL to "open". Such a page
    carries an audience nobody can read, so it gets '' — restricted to nobody, the same encoding as
    a deliberately empty ACL: an unrestricted operator still sees it (the breakage stays
    discoverable) but no scoped client does. NULL would mean "carries no acl" and serve a page
    written as acl: [finance] to everyone."""
    write_page(corpus.brain_md_dir, "entities/acme/payroll.md",
               {"title": "Payroll", "date": "2026-02-30", "acl": "[finance]"},
               "confidential needle payroll body")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    assert index.get_page(conn, "entities/acme/payroll.md")["acl"] == ""
    assert not index.visible("", {"finance"})                    # unknown audience, so not finance
    assert not index.visible("", {"eng"})
    assert index.visible("", None)                               # unrestricted operator still sees it
    assert not any(h["path"] == "entities/acme/payroll.md"
                   for h in retrieve.search(conn, "confidential needle", audiences={"eng"}))
    # a page that genuinely carries no frontmatter at all stays open — that is not the same case
    write_page(corpus.brain_md_dir, "general/plain.md", {"title": "Plain"}, "open needle body")
    index.refresh(conn, corpus.brain_md_dir)
    assert index.get_page(conn, "general/plain.md")["acl"] is None


def test_superseded_pages_do_not_starve_a_current_truth_search(tmp_path, corpus):
    """`include_superseded=False` was applied AFTER the candidate pool was capped, so superseded
    pages consumed slots and could crowd the current version out of the pool entirely — zero hits
    while a current matching page exists. Same defect shape as the ACL cap-then-filter: a row that
    the caller has excluded must not occupy a candidate slot."""
    for i in range(45):                      # rank ahead by repeating the query terms
        write_page(corpus.brain_md_dir, f"entities/acme/w{i}.md",
                   {"title": f"widget detail {i}", "superseded_by": "drive:NEWER"},
                   "widget detail widget detail widget detail body")
    write_page(corpus.brain_md_dir, "general/widget-current.md", {"title": "notes"},
               "widget detail appears once here")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    hits = retrieve.search(conn, "widget detail", include_superseded=False)
    assert [h["path"] for h in hits] == ["general/widget-current.md"]     # today: []
    # and with superseded included, the pool is unchanged
    assert len(retrieve.search(conn, "widget detail", k=100)) == 40


def test_search_demotes_superseded_and_prefers_current(service):
    hits = service.search("globex quarterly report revenue")
    paths = [h["path"] for h in hits]
    assert paths.index("entities/globex/q1-report-final.md") < paths.index("entities/globex/q1-report.md")
    demoted = next(h for h in hits if h["path"] == "entities/globex/q1-report.md")
    assert "superseded" in demoted["factors"]


def test_search_demotes_failed_verification(service):
    hits = service.search("roadmap SSO routing onboarding")
    top = next(h for h in hits if h["path"] == "units/product/roadmap.md")
    assert "verification-failed" in top["factors"]


def test_search_entity_and_period_boosts(service):
    hits = service.search("initech kpi 2026-01")
    top = hits[0]
    assert top["path"] == "entities/initech/kpi.md"
    assert any(f.startswith("entity:") for f in top["factors"])
    assert "period-match" in top["factors"]


def test_search_ranks_by_bm25_relevance(corpus):
    """FTS5 bm25() is negative for matches (more negative = better): the base score must
    preserve that order — not clamp every match to one floor and fall through to the
    alphabetical path tiebreak."""
    write_page(corpus.brain_md_dir, "general/zzz-strong.md",
               {"type": "note", "title": "strong", "verification": "verified"},
               "widget widget widget widget widget widget report")
    write_page(corpus.brain_md_dir, "general/aaa-weak.md",
               {"type": "note", "title": "weak", "verification": "verified"},
               "one widget among lots of other filler words here padding padding text")
    conn = index.connect(corpus.state_dir)
    index.refresh(conn, corpus.brain_md_dir)
    hits = retrieve.search(conn, "widget")
    paths = [h["path"] for h in hits]
    assert paths.index("general/zzz-strong.md") < paths.index("general/aaa-weak.md")
    strong = next(h for h in hits if h["path"] == "general/zzz-strong.md")
    weak = next(h for h in hits if h["path"] == "general/aaa-weak.md")
    assert strong["score"] < weak["score"]        # ascending = better; no shared floor


def test_search_survives_fts_syntax_in_query(service):
    assert isinstance(service.search('globex "revenue (ARR)" AND NOT*'), list)


def test_search_no_results(service):
    assert service.search("zebra unicorn nonsense") == []
