# ADR 003 — Bounded agency inside the document, determinism outside

**Status:** accepted · 2026-07

## Context

The original clean stage was a fixed pipeline per document: one converter picked by extension →
one LLM call with a fixed prompt → page. Adaptivity existed, but as *operator choreography*: docs
whose extraction came out mangled were marked `manual_review`, and a **second full run** re-OCR'd
them (`--escalate-vision`), pre-filtered by a dedicated curation stage (`corpus select-vision`)
plus a selection file wired through an env var. Roughly 200 lines of orchestration, a second
operational mode, and hours of latency — to simulate a decision the model can make in-context:
*"this extraction is garbage; let me look at the PDF with vision."*

Separately, the deterministic verifier (ADR 002) produced a trust verdict that nothing consumed:
a `failed` page was annotated and left as-is.

## Decision

Make the **per-document unit of work an agentic loop with tools and hard budgets**, and keep the
orchestration spine deterministic (hashes, state, dedup, deletions, layout, contract).

- **Tools** (`tools.py`): `read_more()` — pull extraction text beyond the 16k prompt window
  (max 2 calls); `ocr()` — re-read the original PDF with a vision model (one shot; failures return
  a message, never an exception, so the agent degrades to `manual_review` instead of crashing).
- **Budgets** (`RUN_LIMITS`): 6 model requests / 4 tool calls per attempt, enforced by the
  framework. A clean document still costs exactly **one** request — agency is free until used.
- **Generator-judge loop**: after the run, the deterministic verifier judges the page; on
  `failed`, one corrective retry carries the findings back ("these figures are not in the source —
  fix or drop them"). The retry wins only if it measurably improves. The judge is pure code, so
  the loop cannot hallucinate its way to green.
- **Deleted, not deprecated**: the second-run escalation (`--escalate-vision`,
  `VISION_SELECTION`, `classify_manual_review`) and the `corpus select-vision` stage are gone.
  Escalation now happens per document, at first contact, inside the run.

## Consequences

- **Latency**: a scanned PDF becomes a faithful page in one pass, not two runs apart.
- **Cost**: happy path unchanged (1 request/doc); OCR is paid only when the agent judges the
  deterministic text unusable; retries only on verifier failure. Observability:
  `ocr_docs`/`verify_retries` pass stats and per-page `extraction_method: vision` provenance.
- **Determinism**: byte-level reproducibility per document is gone (it already was, softly — LLM
  output varies); what remains guaranteed is the *contract*: layout, frontmatter schema, verdicts,
  idempotency and deletions are still pure code. Trade accepted: robustness over replayability.
- **Near-duplicate OCR spend**: the old select-vision deduped recurring reports by (unit, period)
  before paying OCR. Exact-hash dedup covers identical files; two *different scans* of the same
  report will now both be OCR'd. Accepted: rare, cheap relative to the deleted complexity.

## What stays non-agentic, deliberately

fetch (a mirror needs no opinions), corpus (auditable rules beat an LLM for classifying 10k
paths), graph (pure), and the orchestration loop itself. Agency is a scalpel here, not a lifestyle.
