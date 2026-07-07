"""Fake (offline) backend: deterministic output shape, backend dispatch."""
import asyncio

import pytest

from clean import agents
from clean.fake_llm import FakeProcessor, process


def _prompt(filename="Quarterly Report Q1.md", method="text", text="body"):
    return f"filename={filename}\nsource_uri=u\nmethod={method}\n\nEXTRACTED TEXT:\n{text}"


def test_process_basic_shape():
    out = process(_prompt(text="Globex grew 40%. Globex signed Initech. Globex rocks. Initech Initech."))
    assert out.skipped is False
    assert out.extraction_quality == "usable"
    assert out.representation == "full"
    assert out.metadata.title == "Quarterly Report Q1"
    assert out.metadata.type == "report"
    names = {m.name for m in out.metadata.mentions}
    assert names == {"Globex", "Initech"}     # >=3 repetitions each


def test_process_sheet_is_digest_and_dates_detected():
    out = process(_prompt(filename="kpis 2026-03.csv", method="sheet", text="| a | b |"))
    assert out.representation == "digest"
    assert out.metadata.date == "2026-03-01"


def test_process_empty_text_skips():
    out = process(_prompt(text="   "))
    assert out.skipped is True


def test_deterministic():
    p = _prompt(text="Globex Globex Globex")
    assert process(p) == process(p)


def test_fake_processor_run_mimics_agent():
    r = asyncio.run(FakeProcessor().run(_prompt()))
    assert r.output.metadata.title
    assert r.usage.input_tokens == 0
    assert (r.usage.details or {}).get("reasoning_tokens", 0) == 0


def test_build_agent_dispatch(monkeypatch):
    monkeypatch.setenv("CLEAN_LLM", "fake")
    assert isinstance(agents.build_agent(), FakeProcessor)
    monkeypatch.setenv("CLEAN_LLM", "fake-flawed")
    agent = agents.build_agent()
    assert isinstance(agent, FakeProcessor) and agent.flawed is True
    monkeypatch.setenv("CLEAN_LLM", "nonsense")
    with pytest.raises(RuntimeError, match="CLEAN_LLM"):
        agents.build_agent()


def test_flawed_backend_hallucinates_once_then_behaves():
    proc = FakeProcessor(flawed=True)
    prompt = _prompt(filename="Quarterly Report Q1.md", text="Revenue was $1.2M.")
    first = asyncio.run(proc.run(prompt)).output
    assert "99.9M" in first.body_markdown            # the seeded invention
    retry = asyncio.run(proc.run(prompt + "\n\nA previous attempt produced this body:\n---\nx\n---\n"
                                          "DETERMINISTIC VERIFIER: ...")).output
    assert "99.9M" not in retry.body_markdown        # behaves on the judge's retry
    assert "DETERMINISTIC VERIFIER" not in retry.body_markdown   # feedback never leaks into pages
    other = asyncio.run(proc.run(_prompt(filename="notes.md", text="plain"))).output
    assert "99.9M" not in other.body_markdown        # only the targeted doc is corrupted


def test_judge_loop_end_to_end_no_mocks(tmp_path, monkeypatch):
    """The whole control loop with the real fake backend: seeded hallucination -> verifier
    catches it -> one retry -> verified page. Zero mocks, zero network."""
    import asyncio as aio

    from clean.worker import process_one
    monkeypatch.setenv("CLEAN_LLM", "fake-flawed")
    p = tmp_path / "Quarterly Report Q1.md"
    p.write_text("Globex revenue was $1.2M in Q1 2026, up 40% QoQ.")
    doc = {"fileId": "F1", "path": str(p),
           "entry": {"name": p.name, "drivePath": f"/X/{p.name}", "sourceUri": "local://q"}}
    res = aio.run(process_one(doc, agents.build_agent(), str(tmp_path), str(tmp_path / "brain")))
    assert res["retried"] is True
    assert res["verification"] == "verified"
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "99.9M" not in page and "$1.2M" in page
    assert res["agent_trace"] == ["verifier-retry"]
