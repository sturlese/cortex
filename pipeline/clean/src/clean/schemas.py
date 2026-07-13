"""Structured outputs (Pydantic) — the contract for the agent's decisions."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Mention(BaseModel):
    name: str = Field(description="canonical name, UNRESOLVED (the graph stage links it)")
    type: Literal["company", "person", "organization", "product", "other"]


class PageMetadata(BaseModel):
    title: str = Field(description="human-readable document title")
    type: str = Field(description=(
        "kebab-case: report, memo, contract, meeting-notes, presentation, spreadsheet-summary, email, note, other"))
    date: str | None = Field(None, description="YYYY-MM-DD of the CONTENT's date, or omit")
    tags: list[str] = Field(default_factory=list, description="3-8, kebab-case, lowercase, based on actual content")
    mentions: list[Mention] = Field(default_factory=list, description="relevant entities, unresolved")
    tier: int = Field(1, description="1 primary source (most docs), 2 second-hand claim, 3 AI-generated")


class Verification(BaseModel):
    """Result of the deterministic faithfulness check (verify.py) — NOT produced by the LLM.
    verified = every figure in the body traces back to the source text AND none is tied to a
    period the source contradicts; partial = isolated problems; failed = the page's figures
    largely can't be trusted (invention, misattribution, or mangled extraction)."""
    verdict: Literal["verified", "partial", "failed"]
    numbers_total: int = 0
    numbers_unverified: list[str] = Field(default_factory=list)
    numbers_unanchored: list[str] = Field(
        default_factory=list,
        description="figures present in the source but asserted for a period every source occurrence contradicts")
    numbers_spans: dict[str, list[int]] = Field(
        default_factory=dict,
        description="first matching source span per verified figure (offsets into extraction+context); state-only")
    mentions_unverified: list[str] = Field(default_factory=list, description="advisory only; never affects the verdict")


class FactObservation(BaseModel):
    """One typed numeric fact proposed by the facts agent from a spreadsheet grid. The agent's
    job is JUDGMENT (which cells are metrics, what they're called, which period they belong to);
    the VALUE is never trusted as-is — a deterministic validator re-reads the grid and drops any
    observation whose value_raw does not match its claimed cell (facts.py)."""
    metric: str = Field(description="canonical kebab-case metric id, e.g. arr-usd, active-users")
    metric_raw: str = Field(description="the label EXACTLY as it appears in the sheet (header/row label)")
    value_raw: str = Field(description="the cell value EXACTLY as shown in the grid — copy, never reformat")
    unit: str | None = Field(None, description="usd, eur, %, users, ... when evident from the label/values")
    period: str | None = Field(None, description="normalized period this value belongs to: YYYY, YYYY-MM or YYYY-QN")
    dimension: str | None = Field(None, description="short qualifier when the row/col is a breakdown (region, product)")
    sheet: str = Field(description="sheet name exactly as given")
    row: int = Field(description="1-based row index of the VALUE cell in the numbered grid")
    col: int = Field(description="1-based column index of the VALUE cell")


class FactsOutput(BaseModel):
    """The facts agent's mapping of one spreadsheet into typed observations."""
    observations: list[FactObservation] = Field(default_factory=list)
    reason: str = Field(description="how the grid was read (orientation, header/period location), briefly")


class OpsReport(BaseModel):
    """The supervisor's structured verdict on the pipeline (ops.py) — rendered to ops-report.md.
    Written for a human operator: findings are observations, actions are what the agent DID
    (bounded tools), recommendations are what it wants a human to decide."""
    health: Literal["green", "yellow", "red"]
    summary: str = Field(description="2-4 sentences: overall state and the one thing that matters most")
    findings: list[str] = Field(default_factory=list, description="concrete observations, most important first")
    actions_taken: list[str] = Field(default_factory=list,
                                     description="what the supervisor did this run (requeues, playbook)")
    recommendations: list[str] = Field(default_factory=list, description="decisions that belong to a human")


class ProcessorOutput(BaseModel):
    """The processor extracts, decides and writes. If the input is pure noise, skipped=True and the rest stays empty."""
    skipped: bool = Field(False, description="True only for pure administrative noise with no knowledge-base value")
    extraction_quality: Literal["usable", "manual_review"] | None = None
    representation: Literal["full", "digest", "minimal"] | None = None
    metadata: PageMetadata | None = None
    body_markdown: str | None = Field(
        None,
        description=(
            "POLISHED body according to representation. full=clean transcription (strip print "
            "chrome/footers/page numbers, keep ALL facts and figures, structure with ##). "
            "digest=narrative + EXACT key figures + note pointing to the original file. "
            "minimal=pointer. NEVER invent. NO title H1, NO [[wikilinks]], NO --- lines."
        ),
    )
    reason: str = Field(description="why these decisions, grounded in concrete evidence from the text")

    @model_validator(mode="after")
    def _page_fields_required_unless_skipped(self):
        """A non-skipped output must carry the fields build_page needs. Enforcing it here makes the
        agent framework RETRY an incomplete generation instead of crashing the worker with an
        AttributeError (which would mark the doc 'error' and re-burn an LLM call every pass)."""
        if not self.skipped and (self.metadata is None
                                 or self.representation is None
                                 or self.extraction_quality is None):
            raise ValueError("non-skipped output requires metadata, representation and extraction_quality")
        return self
