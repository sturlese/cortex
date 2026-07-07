"""Agent/model construction: fail-fast on missing key or invalid effort, defaults."""
import pytest
from pydantic_ai.models.openai import OpenAIResponsesModel

from clean import agents


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("OPENAI_API_KEY", "CLEAN_REASONING_EFFORT"):
        monkeypatch.delenv(k, raising=False)


def test_build_model_requires_api_key():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        agents.build_model("gpt-5.4")


def test_build_model_default_effort_is_medium(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    model, settings = agents.build_model("gpt-5.4")
    assert isinstance(model, OpenAIResponsesModel)
    assert settings["openai_reasoning_effort"] == "medium"


def test_build_model_effort_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    monkeypatch.setenv("CLEAN_REASONING_EFFORT", "minimal")
    _, settings = agents.build_model("gpt-5.4")
    assert settings["openai_reasoning_effort"] == "minimal"


def test_build_model_invalid_effort_fails(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    monkeypatch.setenv("CLEAN_REASONING_EFFORT", "ultra")
    with pytest.raises(RuntimeError, match="CLEAN_REASONING_EFFORT"):
        agents.build_model("gpt-5.4")


def test_build_model_provider_prefixed_passthrough(monkeypatch):
    """A provider-prefixed pydantic-ai string bypasses the OpenAI path (and its key requirement)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model, settings = agents.build_model("anthropic:claude-sonnet-4-5")
    assert model == "anthropic:claude-sonnet-4-5"
    assert settings is None


def test_build_agent_wires_structured_output(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    agent = agents.build_agent()
    assert agent is not None


def test_agent_tools_wired_end_to_end_offline(monkeypatch, tmp_path):
    """Real Agent + real tools, offline: TestModel exercises every registered tool once with the
    per-document deps, then emits a schema-conforming ProcessorOutput."""
    import asyncio

    from pydantic_ai.models.test import TestModel

    from clean.schemas import ProcessorOutput
    from clean.tools import DocContext

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    agent = agents.build_agent()
    deps = DocContext(path=str(tmp_path / "x.txt"), full_text="words " * 6000, shown=1000)
    r = asyncio.run(agent.run("process this document", deps=deps,
                              model=TestModel(), usage_limits=agents.RUN_LIMITS))
    assert isinstance(r.output, ProcessorOutput)
    assert deps.read_more_calls == 1          # read_more actually ran against the deps
    assert deps.ocr_used is False             # ocr refused: not a PDF (graceful message, no crash)
