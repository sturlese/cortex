"""Time semantics: provable as_of, version candidates, judge plumbing, supersedes application."""
import asyncio

from clean.verify import provable_as_of
from clean.versions import (
    FakeVersionJudge,
    annotate_page,
    build_pair_prompt,
    candidate_pairs,
    content_similarity,
    detect_versions,
    name_similarity,
)


# ── as_of: the evidence decides the granularity ──────────────────────────────
def test_as_of_full_date_only_when_literal():
    assert provable_as_of("2026-02-14", "notes from 2026-02-14 meeting") == "2026-02-14"
    # date not literal, but a compatible year-month signal exists -> month granularity
    assert provable_as_of("2026-02-14", "the February 2026 meeting") == "2026-02"


def test_as_of_downgrades_to_provable_granularity():
    assert provable_as_of("2026-03-01", "report for 2026-03") == "2026-03"
    assert provable_as_of("2026-02-01", "covers Q1 2026") == "2026-Q1"     # month inside the quarter
    assert provable_as_of("2026-03-01", "annual summary 2026") == "2026"
    assert provable_as_of("2026-03-01", "no dates anywhere") is None


def test_as_of_rejects_unprovable_or_malformed():
    assert provable_as_of("2027-01-01", "everything says 2026") is None
    assert provable_as_of("not-a-date", "2026") is None
    assert provable_as_of(None, "2026") is None


# ── deterministic candidates ─────────────────────────────────────────────────
def _state(**overrides):
    files = {
        "A": {"status": "processed", "name": "Quarterly Report Q1 2026.md",
              "localPath": "a.md",
              "lastResult": {"path": "entities/g/a.md", "entity": "globex", "as_of": "2026-Q1"}},
        "B": {"status": "processed", "name": "Quarterly Report Q1 2026 FINAL.md",
              "localPath": "b.md",
              "lastResult": {"path": "entities/g/b.md", "entity": "globex", "as_of": "2026-Q1"}},
        "C": {"status": "processed", "name": "meeting notes 2026-02-14.txt",
              "localPath": "c.md",
              "lastResult": {"path": "entities/g/c.md", "entity": "globex"}},
        "D": {"status": "processed", "name": "Quarterly Report Q1 2026.md",
              "localPath": "d.md",
              "lastResult": {"path": "entities/other/d.md", "entity": "other"}},
    }
    files.update(overrides)
    return {"files": files}


def test_candidate_pairs_group_and_name_gated():
    pairs = candidate_pairs(_state(), touched={"B"})
    assert pairs == [("A", "B")]     # same entity + near-identical stripped names; C dissimilar; D other entity


def test_candidate_pairs_skip_untouched_and_already_linked():
    assert candidate_pairs(_state(), touched=set()) == []
    st = _state()
    st["files"]["A"]["lastResult"]["superseded_by"] = "B"
    assert candidate_pairs(st, touched={"B"}) == []


def test_name_and_content_similarity():
    assert name_similarity("Quarterly Report Q1 2026.md", "Quarterly Report Q1 2026 FINAL.md") > 0.95
    assert name_similarity("Quarterly Report Q1 2026.md", "meeting notes.txt") < 0.5
    assert content_similarity("same text body here", "same text body here!") > 0.9


# ── the fake judge: markers, then recency, else refuse ───────────────────────
def test_fake_judge_marker_beats_no_marker():
    prompt = build_pair_prompt("Report.md", "2026-Q1", "text", "Report FINAL.md", "2026-Q1", "text")
    v = asyncio.run(FakeVersionJudge().run(prompt)).output
    assert v.same_document is True and v.current == "b"


def test_fake_judge_recency_when_no_markers():
    prompt = build_pair_prompt("Report.md", "2026-01", "text", "Report.md", "2026-03", "text")
    v = asyncio.run(FakeVersionJudge().run(prompt)).output
    assert v.same_document is True and v.current == "b"


def test_fake_judge_refuses_without_signal():
    prompt = build_pair_prompt("Report.md", "", "text", "Report.md", "", "text")
    v = asyncio.run(FakeVersionJudge().run(prompt)).output
    assert v.same_document is False


# ── application: state + pages ───────────────────────────────────────────────
def _page(tmp_path, rel, extra=""):
    p = tmp_path / "brain" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntitle: T\n{extra}---\n\n# T\n\nbody\n")
    return p


def test_annotate_page_idempotent(tmp_path):
    _page(tmp_path, "entities/g/a.md")
    assert annotate_page(str(tmp_path / "brain"), "entities/g/a.md", "superseded_by", '"X"')
    assert annotate_page(str(tmp_path / "brain"), "entities/g/a.md", "superseded_by", '"Y"')
    text = (tmp_path / "brain" / "entities/g/a.md").read_text()
    assert text.count("superseded_by:") == 1
    assert 'superseded_by: "Y"' in text
    assert not annotate_page(str(tmp_path / "brain"), "entities/g/missing.md", "x", "y")


def test_detect_versions_links_state_and_pages(tmp_path):
    st = _state()
    (tmp_path / "a.md").write_text("Quarterly review. Revenue was $1.2M ARR, up 40% QoQ.")
    (tmp_path / "b.md").write_text("Quarterly review. Revenue was $1.3M ARR, up 45% QoQ.")
    (tmp_path / "c.md").write_text("meeting notes")
    (tmp_path / "d.md").write_text("something else entirely about other topics")
    _page(tmp_path, "entities/g/a.md")
    _page(tmp_path, "entities/g/b.md")
    stats = asyncio.run(detect_versions(st, {"B"}, str(tmp_path), str(tmp_path / "brain"),
                                        judge=FakeVersionJudge(), log=lambda *_: None))
    assert stats == {"versions_checked": 1, "versions_linked": 1}
    assert st["files"]["A"]["lastResult"]["superseded_by"] == "B"
    assert st["files"]["B"]["lastResult"]["supersedes"] == "A"
    assert 'superseded_by: "B"' in (tmp_path / "brain" / "entities/g/a.md").read_text()
    assert 'supersedes: "A"' in (tmp_path / "brain" / "entities/g/b.md").read_text()
    # second run: the pair is linked, nothing to do
    stats2 = asyncio.run(detect_versions(st, {"B"}, str(tmp_path), str(tmp_path / "brain"),
                                         judge=FakeVersionJudge(), log=lambda *_: None))
    assert stats2 == {}


def test_detect_versions_content_gate_blocks_dissimilar(tmp_path):
    st = _state()
    (tmp_path / "a.md").write_text("Totally different content about product roadmaps and hiring.")
    (tmp_path / "b.md").write_text("Quarterly review. Revenue was $1.3M ARR, up 45% QoQ.")
    _page(tmp_path, "entities/g/a.md")
    _page(tmp_path, "entities/g/b.md")
    stats = asyncio.run(detect_versions(st, {"B"}, str(tmp_path), str(tmp_path / "brain"),
                                        judge=FakeVersionJudge(), log=lambda *_: None))
    assert stats == {}
    assert "superseded_by" not in st["files"]["A"]["lastResult"]
