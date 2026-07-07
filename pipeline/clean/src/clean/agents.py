"""The processor (the brain): an agentic worker with bounded autonomy.

One agent per document run. The prompt carries the deterministic extraction; the agent may use
two tools when — and only when — they change the outcome (pull more text, re-read a mangled PDF
with vision OCR). Hard budgets cap the worst case; the happy path stays a single request. After
the run, the deterministic verifier (verify.py) judges the page and can trigger one corrective
retry — the generator-judge loop with a judge that cannot hallucinate.
"""
import asyncio
import os
from typing import Any, Protocol

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from clean.schemas import ProcessorOutput
from clean.tools import DocContext
from clean.tools import ocr as ocr_impl
from clean.tools import read_more as read_more_impl

PROCESSOR_MODEL = os.environ.get("CLEAN_MODEL", "gpt-5.4")
DEFAULT_REASONING_EFFORT = "medium"
_VALID_EFFORTS = ("minimal", "low", "medium", "high")

# Hard per-attempt budget: a clean document costs exactly 1 request; tools cost extra requests.
# This caps the worst case without ever touching the happy path's cost.
RUN_LIMITS = UsageLimits(request_limit=6, tool_calls_limit=4)


class Processor(Protocol):
    """Port implemented by every LLM backend (the PydanticAI agent, fake_llm.FakeProcessor, or
    your own). `run` returns an object with `.output` (ProcessorOutput) and `.usage`."""

    async def run(self, prompt: str, *, deps: Any = None, usage_limits: Any = None) -> Any: ...


def build_model(model_name: str):
    """Model + settings for the agents.

    Two forms of CLEAN_MODEL:
    - bare name ("gpt-5.4"): OpenAI Responses API with an EXPLICIT reasoning effort
      (never the API's implicit default). Requires OPENAI_API_KEY.
    - provider-prefixed pydantic-ai string ("anthropic:claude-sonnet-4-5",
      "google-gla:gemini-2.5-pro", ...): resolved by pydantic-ai; the provider reads its own
      env key. Provider-specific tuning is yours to add — the agents don't care.
    """
    if ":" in model_name:
        return model_name, None
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required (set it in the environment / .env)")
    effort = os.environ.get("CLEAN_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
    if effort not in _VALID_EFFORTS:
        raise RuntimeError(f"invalid CLEAN_REASONING_EFFORT: {effort!r} (use one of {_VALID_EFFORTS})")
    model = OpenAIResponsesModel(model_name, provider=OpenAIProvider(api_key=key))
    return model, OpenAIResponsesModelSettings(openai_reasoning_effort=effort)


PROCESSOR_SYS = """You are an analyst building a company knowledge base. You receive the
(deterministically) EXTRACTED TEXT of a document from the company's shared drive and produce the
knowledge-base page for it. Reason like a human, not like a script. Zero invention.

You have two tools — use them only when they change the outcome:
- read_more(): the prompt may show only the first part of the extraction. Call this when content
  you need for a faithful page was cut off mid-document.
- ocr(): re-reads the original PDF with a vision model. Call it ONCE when the extracted text is
  clearly mangled or missing (scanned PDF, mojibake, broken ordering, near-empty) — then write the
  page from the OCR transcription and judge extraction_quality on THAT text.

1) extraction_quality:
   - `usable`: the content is there, even if NOISY (email print headers, footers, page numbers,
     mid-sentence line breaks). Noise != loss.
   - `manual_review`: even with the tools you could not obtain usable content (lost or mangled
     beyond repair, purely visual content the OCR also missed, key tables/charts absent).

2) representation (depends on the CLASS of document, not on quality):
   - `full`: prose/narrative (emails, memos, status updates, letters, reports, decks with real text).
     Clean transcription preserving EVERYTHING.
   - `digest`: numbers-heavy (financial models, KPI exports, large spreadsheets). The value is the
     headline figures + structure, NOT the whole grid (that lives in the original file).
   - `minimal`: raw data without narrative, or a low-value doc -> pointer (title + type + source link).

3) Numbers: "meaningful figure YES, massive grid NO". In `full`, all of them (faithful transcription).
   In `digest`, the headline ones, QUOTED EXACTLY from the text (never estimate a number).

4) metadata: human title, kebab-case type, content date (YYYY-MM-DD or omit), 3-8 tags, mentions
   (unresolved entities; the graph stage links them — do NOT write wikilinks), tier (1 by default).

5) body_markdown according to representation. In `full`, strip the extraction chrome and structure
   with `##`, keeping every fact/figure. In `digest`, give the headline figures + structure (not the
   grid). Do NOT start with an H1 of the title, do NOT use [[wikilinks]], do NOT use `---` lines, and
   do NOT add the source link (the system appends it automatically).

If the document is pure administrative noise (empty, system file, valueless junk), set
`skipped=true` and leave the rest empty."""


def build_agent(playbook: str = "") -> "Processor":
    """CLEAN_LLM backend: 'openai' (default) or 'fake' (offline demo/testing heuristic).
    `playbook` is the supervisor-distilled memory, appended as advisory context (playbook.py)."""
    backend = os.environ.get("CLEAN_LLM", "openai").lower()
    if backend in ("fake", "fake-flawed"):
        from clean.fake_llm import FakeProcessor
        return FakeProcessor(flawed=backend == "fake-flawed")
    if backend != "openai":
        raise RuntimeError(f"invalid CLEAN_LLM: {backend!r} (use 'openai', 'fake' or 'fake-flawed')")
    from clean.playbook import compose_instructions
    model, settings = build_model(PROCESSOR_MODEL)
    agent = Agent(model, output_type=ProcessorOutput, instructions=compose_instructions(PROCESSOR_SYS, playbook),
                  model_settings=settings, deps_type=DocContext)

    @agent.tool
    async def read_more(ctx: RunContext[DocContext]) -> str:
        """Return the next chunk of the extracted text (use when the excerpt was truncated)."""
        return read_more_impl(ctx.deps)

    @agent.tool
    async def ocr(ctx: RunContext[DocContext]) -> str:
        """Re-read the original PDF with a vision OCR model. Use ONCE, only when the extracted
        text is clearly mangled or missing."""
        return await asyncio.to_thread(ocr_impl, ctx.deps)

    return agent
