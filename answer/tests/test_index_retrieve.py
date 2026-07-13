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


def test_search_survives_fts_syntax_in_query(service):
    assert isinstance(service.search('globex "revenue (ARR)" AND NOT*'), list)


def test_search_no_results(service):
    assert service.search("zebra unicorn nonsense") == []
