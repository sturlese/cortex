"""The deterministic answer verifier + the facts read path."""
from answer import metrics
from answer.numbers import unverified_figures
from answer.synthesize import AnswerOutput, Citation
from answer.verify_answer import feedback, verify


def test_unverified_figures_matching_is_generous():
    ev = "ARR was 1,200,000 EUR in Q1 2026 (about 40 %)."
    assert unverified_figures("Revenue reached 1.2M, up 40%, in 2026.", ev) == []
    assert unverified_figures("Margin was 77%.", ev) == ["77%"]
    assert unverified_figures("the 3 initiatives", "no digits") == []      # bare digit skipped


def _pages(**pages):
    return lambda path: pages.get(path)


def test_verify_citations_and_figures():
    out = AnswerOutput(answer_markdown="Revenue was 1.3M.",
                       citations=[Citation(path="p.md", quote="Revenue was 1.3M")])
    get_page = _pages(**{"p.md": {"title": "T", "body": "Quarterly. Revenue   was 1.3M this year."}})
    v = verify(out, "tool said: Revenue was 1.3M", get_page, read_paths={"p.md"})
    assert v == {"verdict": "verified", "unverified_figures": [], "citation_problems": []}


def test_verify_flags_unsurfaced_page_and_missing_quote():
    out = AnswerOutput(answer_markdown="Fine.",
                       citations=[Citation(path="ghost.md", quote="x"),
                                  Citation(path="p.md", quote="never said this")])
    get_page = _pages(**{"p.md": {"title": "T", "body": "actual body"}})
    v = verify(out, "evidence", get_page, read_paths={"p.md"})
    assert v["verdict"] == "failed"
    assert any("never surfaced" in p for p in v["citation_problems"])
    assert any("quote not found" in p for p in v["citation_problems"])


def test_verify_requires_citations_for_substantive_answers():
    out = AnswerOutput(answer_markdown="Something substantive.", citations=[])
    v = verify(out, "evidence", _pages(), read_paths=set())
    assert v["citation_problems"] == ["answer carries no citations"]
    assert v["verdict"] == "partial"


def test_refusal_is_vacuously_verified():
    out = AnswerOutput(refused=True, reason="not in the brain")
    assert verify(out, "", _pages(), set())["verdict"] == "verified"


def test_feedback_carries_both_problem_classes():
    out = AnswerOutput(answer_markdown="Revenue 9.9M.", citations=[])
    v = {"unverified_figures": ["9.9M"], "citation_problems": ["answer carries no citations"]}
    fb = feedback("q?", out, v)
    assert "DETERMINISTIC VERIFIER" in fb and "9.9M" in fb and "citations" in fb


# ── the facts read path ──────────────────────────────────────────────────────
def test_query_metrics_filters_and_prefix(corpus):
    rows = metrics.query_metrics(corpus.facts_dir, metric="arr-usd", entity="initech", period="2026")
    assert [r["value_raw"] for r in rows] == ["480000", "495000", "512000"]
    assert metrics.query_metrics(corpus.facts_dir, metric="arr-usd", period="2026-02")[0]["value_raw"] == "495000"
    assert metrics.query_metrics(corpus.facts_dir, metric="nope") == []
    assert metrics.query_metrics("/nonexistent") == []


def test_known_metrics(corpus):
    assert metrics.known_metrics(corpus.facts_dir) == ["active-users", "arr-usd", "revenue-impact"]
    assert metrics.known_metrics(corpus.facts_dir, entity="globex") == ["revenue-impact"]
    assert metrics.known_metrics("/nonexistent") == []


def test_read_paths_tolerate_a_pre_acl_facts_db(tmp_path):
    """`acl` is an additive migration clean applies on its WRITE path; this package only reads
    facts.db, so a store clean has not re-touched since the ACL feature has no such column. Both
    read paths must degrade to "no acl -> open" (what query_metrics has always done), not raise."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "facts.db"))
    conn.executescript(
        "CREATE TABLE observations (file_id TEXT NOT NULL, page_path TEXT, entity TEXT,"
        " org_unit TEXT, metric TEXT NOT NULL, metric_raw TEXT NOT NULL, value_raw TEXT NOT NULL,"
        " value_num REAL, unit TEXT, period TEXT, dimension TEXT, source_ref TEXT NOT NULL,"
        " extracted_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 1);")
    conn.execute("INSERT INTO observations VALUES ('F','p','acme','u','arr-usd','ARR','1',1.0,"
                 "'usd','2026','d','r','t',1)")
    conn.commit()
    conn.close()

    assert metrics.known_metrics(str(tmp_path)) == ["arr-usd"]                     # must not raise
    assert metrics.known_metrics(str(tmp_path), audiences={"eng"}) == ["arr-usd"]  # no acl -> open
    assert len(metrics.query_metrics(str(tmp_path), audiences={"eng"})) == 1       # same rule


def test_annotate_superseded():
    rows = [{"page_path": "a.md"}, {"page_path": "b.md"}, {"page_path": None}]
    out = metrics.annotate_superseded(rows, {"a.md"})
    assert [r["from_superseded_page"] for r in out] == [True, False, False]
