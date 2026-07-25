"""The full answering loop: exact metrics, current-truth preference, refusal, the judge retry."""
import asyncio
import types

import answer.service as service_mod
from answer.synthesize import AnswerOutput, Citation


def _ask(service, q):
    return asyncio.run(service.ask(q))


def test_metric_question_gets_exact_verified_answer(service):
    res = _ask(service, "what is the arr-usd for initech in 2026-03?")
    assert res["refused"] is False
    assert "512000" in res["answer"]
    assert res["verification"]["verdict"] == "verified"
    assert res["citations"][0]["path"] == "entities/initech/kpi.md"
    assert "source local-kpi!Sheet1!" in res["answer"]      # provenance travels with the number


def test_conflicting_metric_prefers_current_version(service):
    """1.2M lives on the superseded draft, 1.3M on the FINAL page: current truth must win."""
    res = _ask(service, "what is the revenue impact for globex?")
    assert "1.3M" in res["answer"] and "1.2M" not in res["answer"]
    assert res["citations"][0]["path"] == "entities/globex/q1-report-final.md"
    assert res["verification"]["verdict"] == "verified"


def test_superseded_rows_do_not_starve_current_truth(corpus):
    """current_metric_rows capped at 100 and dropped superseded rows AFTERWARDS, then
    `current or rows` fell back to the stale ones — so with enough superseded rows sorting ahead it
    returned 100 rows of an OLD figure while the current one sat in the store, unreached. That is
    worse than the ACL starvation it mirrors: confidently wrong numbers rather than none."""
    from tests.conftest import add_fact, write_page

    from answer.service import AnswerService

    write_page(corpus.brain_md_dir, "entities/zeta/old.md",
               {"title": "zeta arr old", "entity": "zeta", "superseded_by": "drive:NEW",
                "verification": "verified"}, "zeta arr body")
    write_page(corpus.brain_md_dir, "entities/zeta/new.md",
               {"title": "zeta arr new", "entity": "zeta", "verification": "verified"}, "zeta arr body")
    for i in range(120):                      # 'a…' source_refs sort before the current 'z…' ones
        add_fact(corpus.facts_dir, file_id=f"S{i}", page_path="entities/zeta/old.md", entity="zeta",
                 metric="zeta-arr", metric_raw="ARR", value_raw="1.2M", value_num=1200000.0,
                 unit="usd", period="2026-01", source_ref=f"a{i:03d}")
    for i in range(3):
        add_fact(corpus.facts_dir, file_id=f"C{i}", page_path="entities/zeta/new.md", entity="zeta",
                 metric="zeta-arr", metric_raw="ARR", value_raw="9.9M", value_num=9900000.0,
                 unit="usd", period="2026-01", source_ref=f"z{i:03d}")
    svc = AnswerService(corpus)

    rows = svc.current_metric_rows("zeta-arr", "zeta")
    assert {r["value_raw"] for r in rows} == {"9.9M"}          # today: {'1.2M'}, 100 stale rows
    assert not any(r["from_superseded_page"] for r in rows)
    assert "9.9M" in svc.metrics_text("zeta-arr", "zeta")      # today: 30 stale lines, no 9.9M

    # the fallback still works: when NOTHING current exists, the stale rows are served and flagged
    only_stale = svc.current_metric_rows("zeta-arr", "zeta", period="2025-01")
    assert only_stale == []                                     # no rows at all for that period
    for i in range(3):
        add_fact(corpus.facts_dir, file_id=f"S9{i}", page_path="entities/zeta/old.md", entity="zeta",
                 metric="only-old", metric_raw="Old", value_raw="0.5M", value_num=500000.0,
                 unit="usd", period="2026-01", source_ref=f"q{i}")
    fallback = svc.current_metric_rows("only-old", "zeta")
    assert {r["value_raw"] for r in fallback} == {"0.5M"}
    assert all(r["from_superseded_page"] for r in fallback)
    assert "SUPERSEDED" in svc.metrics_text("only-old", "zeta")


def test_prose_question_cites_top_page(service):
    res = _ask(service, "what are the roadmap themes?")
    assert res["refused"] is False
    assert res["citations"]
    assert res["verification"]["verdict"] == "verified"


def test_unanswerable_question_refuses(service):
    res = _ask(service, "zebra unicorn parking policy in antarctica?")
    assert res["refused"] is True
    assert res["answer"] == ""
    assert res["verification"]["verdict"] == "verified"     # refusing cleanly is verified behavior


def test_metric_question_about_unknown_entity_refuses(service):
    """A known metric + an unknown entity must never be answered with another entity's data
    (caught by the benchmark's refusal probe)."""
    res = _ask(service, "what is the arr-usd for zenith-corp?")
    assert res["refused"] is True
    # ...while the entity-less form legitimately answers from the store
    res2 = _ask(service, "what is the arr-usd in 2026-03?")
    assert res2["refused"] is False and "512000" in res2["answer"]


def test_ask_retry_fires_and_improves(service, monkeypatch):
    """A first answer with an invented figure and a bogus citation must fail the deterministic
    verifier; the corrective retry (with findings in the prompt) wins only because it improves."""
    class Scripted:
        def __init__(self):
            self.calls = 0

        async def run(self, prompt, *, deps=None, usage_limits=None):
            self.calls += 1
            deps.record(deps.service.search_text("globex quarterly report"))
            deps.record(deps.service.page_text("entities/globex/q1-report-final.md", deps))
            if self.calls == 1:
                out = AnswerOutput(answer_markdown="Revenue was 9.9M with 77% margin.",
                                   citations=[Citation(path="entities/nowhere.md", quote="ghost")])
            else:
                assert "DETERMINISTIC VERIFIER" in prompt   # findings reached the retry
                out = AnswerOutput(
                    answer_markdown="Revenue impact was $1.3M ARR.",
                    citations=[Citation(path="entities/globex/q1-report-final.md",
                                        quote="Revenue impact was $1.3M ARR")])
            usage = types.SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_tokens=0, details={})
            return types.SimpleNamespace(output=out, usage=usage)

    monkeypatch.setattr(service_mod, "build_synthesizer", lambda settings: Scripted())
    res = _ask(service, "globex revenue?")
    assert res["retried"] is True
    assert res["verification"]["verdict"] == "verified"
    assert "1.3M" in res["answer"] and "9.9M" not in res["answer"]


def test_ask_keeps_first_when_retry_worse(service, monkeypatch):
    class Scripted:
        def __init__(self):
            self.calls = 0

        async def run(self, prompt, *, deps=None, usage_limits=None):
            self.calls += 1
            deps.record(deps.service.page_text("entities/globex/q1-report-final.md", deps))
            bad = AnswerOutput(answer_markdown=f"Invented {self.calls * 111}% and {self.calls * 222}%.",
                               citations=[])
            usage = types.SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_tokens=0, details={})
            return types.SimpleNamespace(output=bad, usage=usage)

    monkeypatch.setattr(service_mod, "build_synthesizer", lambda settings: Scripted())
    res = _ask(service, "globex revenue?")
    assert res["retried"] is True
    assert res["verification"]["verdict"] == "failed"
    assert "111%" in res["answer"]                          # the (equally bad) retry did not win


def test_search_text_carries_trust_flags(service):
    listing = service.search_text("globex quarterly report revenue")
    assert "SUPERSEDED" in listing
    listing2 = service.search_text("initech kpi")
    assert "detail_in_source" in listing2


def test_metrics_text_marks_superseded_rows(service):
    txt = service.metrics_text("revenue-impact", "globex", None)
    assert "1.2M" in txt and "SUPERSEDED" in txt
    assert "1.3M" in txt


def test_page_text_fences_body_and_reports_currency(service):
    txt = service.page_text("entities/globex/q1-report.md")
    assert "<<<UNTRUSTED-DATA" in txt
    assert "superseded_by: local-new" in txt
    assert "unknown page" in service.page_text("nope.md")


def test_build_synthesizer_invalid_llm_fails_fast(corpus):
    """An ANSWER_LLM typo must raise — never silently pick the fake (the old startswith check
    accepted 'fakee') and never fall through to the real OpenAI path."""
    import dataclasses

    import pytest

    from answer.synthesize import build_synthesizer
    with pytest.raises(RuntimeError, match="invalid ANSWER_LLM"):
        build_synthesizer(dataclasses.replace(corpus, llm="fakee"))
