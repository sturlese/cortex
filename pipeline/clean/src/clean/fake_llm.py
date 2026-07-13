"""Offline LLM backends (CLEAN_LLM=fake) — for demos and tests ONLY.

Not models: small deterministic heuristics that mimic each agent's output *shape* so the whole
pipeline can run end to end with zero API keys and zero network (see examples/). The pages and
facts they produce are structurally valid but crude — never feed them to a real brain.
"""
import re
import types

from clean.schemas import FactObservation, FactsOutput, Mention, PageMetadata, ProcessorOutput

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

_DEMO_MISATTRIBUTION = (
    "Headline: ARR reached 512000 in 2026-01. "
    "(a real figure deliberately tied to the wrong month by the fake-flawed demo backend)\n\n"
)


class FakeProcessor:
    """Drop-in for the PydanticAI agent: async .run(prompt, ...) -> object with .output/.usage.
    Ignores tools/deps/budgets — its body is a verbatim slice of the source, so pages verify.

    `flawed=True` (CLEAN_LLM=fake-flawed) simulates a misbehaving model ONCE per defect class,
    deterministically, and behaves on the judge's retry — so the demo shows the verifier catching
    each defect and the generator-judge loop correcting it, with zero API keys and zero randomness:
    - "quarterly report" docs get two INVENTED figures prepended (presence check);
    - "kpi" docs get a real figure tied to the WRONG month (period-anchoring check)."""

    def __init__(self, flawed: bool = False):
        self.flawed = flawed

    async def run(self, prompt: str, *, deps=None, usage_limits=None):
        out = process(prompt)
        is_retry = "DETERMINISTIC VERIFIER" in prompt
        if self.flawed and not is_retry and not out.skipped:
            filename, _, _ = _parse_prompt(prompt)
            if "quarterly report" in filename.lower():
                out = out.model_copy(update={"body_markdown": _DEMO_HALLUCINATION + (out.body_markdown or "")})
            elif "kpi" in filename.lower():
                out = out.model_copy(update={"body_markdown": _DEMO_MISATTRIBUTION + (out.body_markdown or "")})
        return types.SimpleNamespace(output=out, usage=_Usage())


def _guess_unit(header: str) -> str | None:
    low = header.lower()
    for token, unit in (("usd", "usd"), ("eur", "eur"), ("gbp", "gbp"), ("pct", "%"), ("%", "%")):
        if token in low:
            return unit
    return None


def facts_from_grid(sheets: dict, flawed: bool = False) -> FactsOutput:
    """Deterministic grid mapper: row 1 = headers; a first-column cell that parses as a period
    becomes the row's period, otherwise its dimension; every numeric cell in the other columns
    becomes an observation named after its header. Crude, but shaped exactly like the real
    agent's output — which is the point.

    `flawed=True` prepends one observation whose value does NOT match its cell, so demos/evals
    can watch the deterministic validator drop it (the grid decides, not the model)."""
    from clean.entity import slugify
    from clean.facts import _num
    from clean.verify import parse_period

    obs: list[FactObservation] = []
    for name, rows in sheets.items():
        if len(rows) < 2:
            continue
        headers = [str(h).strip() for h in rows[0]]
        if flawed and len(headers) > 1:
            obs.append(FactObservation(
                metric="seeded-bad-value", metric_raw=headers[1] or "col2",
                value_raw="999999", sheet=name, row=2, col=2))
        for i, row in enumerate(rows[1:], start=2):
            first = str(row[0]).strip() if row else ""
            period = parse_period(first)
            dimension = None if period or not first else first
            for j, cell in enumerate(row[1:], start=2):
                if j - 1 >= len(headers):
                    break
                header = headers[j - 1]
                if not header or _num(str(cell)) is None:
                    continue
                obs.append(FactObservation(
                    metric=slugify(header) or "metric", metric_raw=header,
                    value_raw=str(cell).strip(), unit=_guess_unit(header),
                    period=period, dimension=dimension, sheet=name, row=i, col=j))
    return FactsOutput(observations=obs, reason="fake backend: header-row grid heuristic")


class FakeFactsProcessor:
    """Drop-in for the facts agent: async .run(prompt, deps=GridContext, ...) -> .output/.usage.
    Reads the grid from deps (the same grid the validator re-reads)."""

    def __init__(self, flawed: bool = False):
        self.flawed = flawed

    async def run(self, prompt: str, *, deps=None, usage_limits=None):
        out = facts_from_grid(deps.sheets if deps else {}, flawed=self.flawed)
        return types.SimpleNamespace(output=out, usage=_Usage())


# prose figures worth faking: currency-marked or magnitude-suffixed numbers only ("10k-stop"
# style hyphenated qualifiers are excluded — they are units of description, not metric values)
_PROSE_FIGURE = re.compile(r"[€$£]\s?\d[\d.,]*\s?(?:bn|[kKmMbB])?|\b\d[\d.,]*\s?(?:bn|[kKmMbB])\b(?!-)")
_PROSE_STOP = {"the", "a", "an", "for", "of", "in", "on", "was", "is", "our", "this", "that"}


def prose_facts_from_text(filename: str, text: str, flawed: bool = False):
    """Deterministic prose heuristic: one observation per currency/magnitude figure in the first
    chunk, quoted with its whole sentence; the metric name = the sentence's first content words.
    Crude but valid — label and value are inside the quote by construction.

    `flawed=True` prepends one observation whose quote is NOT in the document, so demos/evals can
    watch the quote validator drop it."""
    from clean.schemas import ProseFact, ProseFactsOutput
    from clean.verify import parse_period

    obs: list[ProseFact] = []
    if flawed and "quarterly report" in filename.lower():
        obs.append(ProseFact(metric="seeded-prose-fact", metric_raw="invented context",
                             value_raw="999999", quote="invented context says 999999 here"))
    for sentence in re.split(r"(?<=[.!?])\s+", text[:4000]):
        m = _PROSE_FIGURE.search(sentence)
        if not m:
            continue
        words = [w for w in re.findall(r"[A-Za-z]+", sentence) if w.lower() not in _PROSE_STOP]
        if len(words) < 2:
            continue
        label = " ".join(words[:2])
        quote = re.sub(r"\s+", " ", sentence).strip()[:300]
        obs.append(ProseFact(
            metric="-".join(w.lower() for w in words[:2]), metric_raw=label,
            value_raw=m.group(0).lstrip("€$£ ").strip(),
            unit="usd" if "$" in m.group(0) else None,
            period=parse_period(quote), quote=quote))
        if len(obs) >= 8:
            break
    return ProseFactsOutput(observations=obs, reason="fake backend: sentence-figure heuristic")


class FakeProseFactsProcessor:
    """Drop-in for the prose-facts agent (no deps; the text travels in the prompt)."""

    def __init__(self, flawed: bool = False):
        self.flawed = flawed

    async def run(self, prompt: str, *, deps=None, usage_limits=None):
        filename = ""
        text = prompt
        if prompt.startswith("filename="):
            head, _, text = prompt.partition("\n\nDOCUMENT TEXT:\n")
            filename = head.split("=", 1)[1]
        out = prose_facts_from_text(filename, text, flawed=self.flawed)
        return types.SimpleNamespace(output=out, usage=_Usage())
