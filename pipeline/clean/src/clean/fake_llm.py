"""Offline LLM backend (CLEAN_LLM=fake) — for demos and tests ONLY.

Not a model: a small deterministic heuristic that mimics the processor's output *shape* so the
whole pipeline can run end to end with zero API keys and zero network (see examples/). The pages
it writes are structurally valid but crude — never feed them to a real brain.
"""
import re
import types

from clean.schemas import Mention, PageMetadata, ProcessorOutput

_DATE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?\b")
_CAP_WORD = re.compile(r"\b([A-Z][a-zA-Z]{3,})\b")
# common sentence-leading words that are not entities
_STOPWORDS = {
    "This", "That", "These", "Those", "There", "Then", "When", "Where", "What", "While",
    "With", "From", "Into", "Over", "Under", "After", "Before", "About", "Please", "Thanks",
    "Team", "Notes", "Meeting", "Report", "Quarterly", "Monthly", "Annual", "Summary",
    "Revenue", "Total", "Status", "Update", "Draft", "Agenda", "Action", "Next", "Overview",
}

_TYPE_HINTS = (
    ("minutes", "meeting-notes"), ("meeting notes", "meeting-notes"),
    ("report", "report"), ("kpi", "report"), ("metrics", "report"),
    ("deck", "presentation"), ("pitch", "presentation"),
    ("roadmap", "product-doc"), ("rfc", "product-doc"), ("spec", "product-doc"),
    ("strategy", "memo"), ("memo", "memo"), ("nda", "contract"), ("contract", "contract"),
    ("invoice", "other"),
)


class _Usage:
    """Mirrors the attributes worker.py reads from pydantic-ai's usage object."""
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    details: dict = {}


def _parse_prompt(prompt: str) -> tuple[str, str, str]:
    """Recovers (filename, method, text) from the prompt worker.py builds."""
    filename = method = ""
    for line in prompt.splitlines()[:5]:
        if line.startswith("filename="):
            filename = line.split("=", 1)[1]
        elif line.startswith("method="):
            method = line.split("=", 1)[1]
    text = prompt.split("EXTRACTED TEXT", 1)[-1]
    text = text.split(":\n", 1)[-1]
    text = text.split("\n\nA previous attempt produced this body:")[0]   # strip judge feedback
    return filename, method, text


def _doc_type(filename: str) -> str:
    low = filename.lower()
    for hint, typ in _TYPE_HINTS:
        if hint in low:
            return typ
    return "note"


def _mentions(text: str) -> list[Mention]:
    """Capitalized words repeated >=3 times — a crude but deterministic entity guess."""
    counts: dict[str, int] = {}
    for m in _CAP_WORD.finditer(text):
        w = m.group(1)
        if w not in _STOPWORDS:
            counts[w] = counts.get(w, 0) + 1
    names = sorted(w for w, c in counts.items() if c >= 3)
    return [Mention(name=w, type="organization") for w in names[:8]]


def process(prompt: str) -> ProcessorOutput:
    filename, method, text = _parse_prompt(prompt)
    body = text.strip()
    if not body:
        return ProcessorOutput(skipped=True, reason="fake backend: empty extraction")

    stem = filename.rsplit("/", 1)[-1]
    title = (stem.rsplit(".", 1)[0] if "." in stem else stem) or "Untitled"
    date_m = _DATE.search(f"{filename} {text[:2000]}")
    date = None
    if date_m:
        date = f"{date_m.group(1)}-{date_m.group(2)}-{date_m.group(3) or '01'}"
    typ = _doc_type(filename)
    representation = "digest" if method == "sheet" else "full"

    return ProcessorOutput(
        skipped=False,
        extraction_quality="usable",
        representation=representation,
        metadata=PageMetadata(
            title=title, type=typ, date=date,
            tags=["demo", typ, representation],
            mentions=_mentions(text), tier=1,
        ),
        body_markdown=body[:8000],
        reason="fake backend: deterministic heuristic output (demo/testing only)",
    )


_DEMO_HALLUCINATION = (
    "Revenue soared to $99.9M with a 77% margin. "
    "(two figures deliberately invented by the fake-flawed demo backend)\n\n"
)


class FakeProcessor:
    """Drop-in for the PydanticAI agent: async .run(prompt, ...) -> object with .output/.usage.
    Ignores tools/deps/budgets — its body is a verbatim slice of the source, so pages verify.

    `flawed=True` (CLEAN_LLM=fake-flawed) simulates a hallucinating model ONCE, deterministically:
    it prepends two invented figures to any "quarterly report" document on the FIRST attempt, and
    behaves on the judge's retry — so the demo shows the verifier catching the invention and the
    generator-judge loop correcting it, with zero API keys and zero randomness."""

    def __init__(self, flawed: bool = False):
        self.flawed = flawed

    async def run(self, prompt: str, *, deps=None, usage_limits=None):
        out = process(prompt)
        is_retry = "DETERMINISTIC VERIFIER" in prompt
        if self.flawed and not is_retry and not out.skipped:
            filename, _, _ = _parse_prompt(prompt)
            if "quarterly report" in filename.lower():
                out = out.model_copy(update={"body_markdown": _DEMO_HALLUCINATION + (out.body_markdown or "")})
        return types.SimpleNamespace(output=out, usage=_Usage())
