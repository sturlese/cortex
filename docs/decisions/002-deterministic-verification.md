# ADR 002 — Verify faithfulness with deterministic cross-checks, not a reviewer LLM

**Status:** accepted · 2026-07

## Context

The pipeline's core promise is *zero invention*: every figure on a page must be quotable from the
source document. Until now that promise lived only in the prompt, and the single quality signal
(`extraction_quality`) was **self-reported by the same model that wrote the page**. An earlier
reviewer LLM was retired because it rubber-stamped: a second model judging the first adds cost and
latency without an independent ground truth.

## Decision

Enforce the promise with **pure code** (`clean/src/verify.py`): after the agent writes the body,
every numeric token in it is traced back to the extracted source text (plus filename/path, where
dates legitimately come from). The LLM writes; code verifies.

Key properties:

- **Generous matching → high-precision flags.** Each token expands into all plausible
  interpretations (decimal comma vs point, ambiguous grouping — `1.200` is both `1200` and `1.2` —
  magnitude suffixes `1.2M`, currency symbols, percent spacing, space-grouped thousands). A body
  figure is verified if any interpretation matches any source interpretation. Reformatting can
  never trigger a flag; a flag means "this figure is very likely not in the source".
- **Weak signals are skipped.** Bare single digits (list markers, "the 3 initiatives") are not
  checked; repeated tokens count once.
- **Three-level verdict**, mechanical and documented: `verified` (0 misses) · `partial` (1 miss,
  or ≤25% of the page's figures) · `failed` (≥2 and >25%). Failed pages get a warning banner;
  all pages carry `verification` in frontmatter and in the pipeline state, so MCP clients can
  rank by trust and operators can triage (`VERIFY FAILED` log lines, `verify_*` pass stats).
- **Mentions are advisory.** Names not literally found in the source are listed
  (`unverified_mentions`) but never affect the verdict — entity phrasing varies legitimately.

## Limits (deliberate)

Recall is best-effort: rephrased or semantic claims (wrong attribution, inverted trend words) are
out of scope for deterministic checking — the absence of flags is not proof of faithfulness. A
derived figure ("up 40%" computed from two source numbers) *is* flagged: by design, pages should
quote, not compute. If semantic verification is ever needed, it belongs in a separate, sampled
LLM-judge stage — not in the hot path.

## Alternatives rejected

- **Reviewer LLM per page** — already tried; rubber-stamped, doubled cost, non-deterministic.
- **Embedding-similarity checks** — fuzzy, threshold-tuning burden, still no ground truth.
- **Blocking failed pages** — a false flag would silently drop real knowledge; annotating keeps
  recall and pushes the trust decision to the consumer, who has the signal.

## Amendment — period anchoring (2026-07-13)

The original check verified **presence**: a figure matching *anywhere* in the source passed. That
made the green badge weaker than readers assume — "ARR was 512000 in January" verified even when
the source ties 512000 to March. A misattributed figure is as wrong as an invented one.

`verify.py` now runs a second deterministic check: for each body figure whose own line asserts a
period (ISO date, `YYYY-MM`, quarter, capitalized month name, bare year), every source occurrence
of that value is inspected. The figure anchors if at least one occurrence's line carries **no
period signal** (absence is never a contradiction — headers, filenames and prose layouts stay
safe) or a **compatible** one (coarser never contradicts finer: `2026` vs `2026-03` is fine, `Q1
2026` vs `2026-02` is fine). Only when every occurrence explicitly contradicts the page does the
figure land in `unanchored_numbers` — same design as presence: flags you can trust, best-effort
recall. The window is the occurrence's own line, deliberately: in tables, adjacent rows carry
adjacent periods, and any wider window would anchor a wrong-row figure to its neighbor.

The verdict now ranges over problems = unverified ∪ unanchored (same thresholds), unanchored
figures also fire the generator-judge retry, and each verified figure records the source span
that anchored it (state-only telemetry). The demo/eval backend seeds one misattribution
alongside the invention; the golden scorecard requires both to be caught and corrected.
