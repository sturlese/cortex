"""Document tools — the agent's hands during a run. Bounded, per-document budgets.

DocContext is the per-document state shared between the orchestrator (worker.py) and the tools:
how much of the extraction the model has seen, budgets consumed, and OCR provenance. Tools never
raise into the agent loop: failures come back as plain messages so the agent can degrade
gracefully (e.g. mark the page `manual_review` instead of crashing the document).
"""
from dataclasses import dataclass

from clean.converters import vision_extract

READ_MORE_CHUNK = 16000
MAX_READ_MORE_CALLS = 2   # beyond ~48k chars a page should be a digest anyway
OCR_MAX_CHARS = 60000     # vision transcriptions are long; don't truncate them away


@dataclass
class DocContext:
    path: str                      # local file (the ocr tool re-reads it)
    full_text: str                 # full deterministic extraction
    shown: int                     # chars of full_text already shown to the model
    read_more_calls: int = 0
    ocr_used: bool = False
    ocr_text: str = ""             # OCR transcription — also part of the verification source
    ocr_model: str | None = None   # provenance -> page frontmatter


def read_more(d: DocContext) -> str:
    """Next chunk of the deterministic extraction (when the excerpt was truncated)."""
    if d.shown >= len(d.full_text):
        return "There is no more text — you have already seen the full extraction."
    if d.read_more_calls >= MAX_READ_MORE_CALLS:
        return ("read_more budget exhausted — finalize with what you have "
                "(use representation=digest if the remainder is bulk data).")
    d.read_more_calls += 1
    chunk = d.full_text[d.shown : d.shown + READ_MORE_CHUNK]
    d.shown += len(chunk)
    remaining = len(d.full_text) - d.shown
    tail = f"\n[...{remaining} more chars not shown]" if remaining > 0 else "\n[END OF EXTRACTED TEXT]"
    return chunk + tail


def ocr(d: DocContext) -> str:
    """Vision OCR of the original PDF. One shot per document; failures degrade gracefully."""
    if d.ocr_used:
        return "ocr was already used for this document — work with its output."
    if not d.path.lower().endswith(".pdf"):
        return "ocr is only available for PDF files — judge this document from the extracted text."
    d.ocr_used = True
    try:
        res = vision_extract(d.path)
    except Exception as ex:  # noqa: BLE001 — the agent must keep working without OCR
        return f"ocr failed ({str(ex)[:200]}) — judge extraction quality from the deterministic text."
    d.ocr_text = (res.get("text") or "")[:OCR_MAX_CHARS]
    d.ocr_model = res.get("model")
    return d.ocr_text or "(ocr returned no text)"
