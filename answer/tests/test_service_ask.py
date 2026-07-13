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
