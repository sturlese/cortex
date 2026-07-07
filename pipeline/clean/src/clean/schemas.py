"""Structured outputs (Pydantic) — the contract for the agent's decisions."""
from typing import Literal

from pydantic import BaseModel, Field


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
    verified = every figure in the body traces back to the source text; partial = isolated
    misses; failed = the page's figures largely can't be traced (invention or mangled extraction)."""
    verdict: Literal["verified", "partial", "failed"]
    numbers_total: int = 0
    numbers_unverified: list[str] = Field(default_factory=list)
    mentions_unverified: list[str] = Field(default_factory=list, description="advisory only; never affects the verdict")


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
