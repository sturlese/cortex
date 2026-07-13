# ADR 005 — A typed facts layer: the agent judges, the grid decides

**Status:** accepted · 2026-07-13

## Context

The page contract was honest about numbers but didn't *hold* them: a spreadsheet became a digest
with 25 sample rows and `detail_in_source: true` — a pointer, not the data. Exact-figure
questions ("Initech's ARR in March?") depended on an MCP client deciding to open the original
file and read a grid. For a knowledge base whose promise is numeric precision, delegating the
numbers to client behavior was the weakest link. And prose pages were forbidden from computing
("quote, don't compute" — ADR 002), so derived questions (QoQ growth) had no honest path at all.

## Decision

Extract **typed numeric observations** from spreadsheet grids into a queryable, auditable facts
store — with the same trust doctrine as the page pipeline, applied at cell granularity:

- **The agent judges** (`facts.py`): a second bounded PydanticAI agent per sheet document reads
  the numbered grid (paging with a budgeted `read_rows` tool) and proposes observations:
  `{metric, metric_raw, value_raw, unit, period, dimension, sheet, row, col}`. Table orientation,
  header detection, metric naming and period attribution are genuinely fuzzy — exactly the work
  an LLM should do, and exactly the work a regex should not.
- **The grid decides** (pure code): a deterministic validator re-reads the same parsed grid and
  keeps an observation only if (1) `value_raw` is literally the claimed cell's value (string or
  numeric equality), (2) `metric_raw` appears in the value's row or column, and (3) the claimed
  `period` is readable from a cell in that row/column, the sheet name, or the filename.
  Everything else is dropped and counted (`facts_rejected`, `FACTS REJECTED` log lines). A
  hallucinated number **cannot** enter the store — the model has no authority over values.
- **The store** (`factstore.py`): SQLite (`facts.db`) for exact lookups (metric/entity/period,
  with year-prefix matching) plus a sorted `facts.jsonl` export per pass — the same
  "plain file you can diff" doctrine as the playbook. Every row carries
  `source_ref = fileId!sheet!RnCm`: any number traces to its cell.
- **Lifecycle = the page's lifecycle**: single writer (clean), replace-on-reprocess keyed by
  file id, deletion propagation (source removed / doc skipped / doc deduped ⇒ its facts go too).

Entity and org-unit attribution reuse the deterministic path resolution — the LLM never assigns
ownership of a number.

## Consequences

- Exact-figure questions get exact answers with provenance, without opening source files; the
  answer layer can *compute with provenance* on top of verified atoms instead of prohibiting
  arithmetic.
- Conflicting values for the same (entity, metric, period) become **detectable** — they are
  distinct rows with distinct `source_ref`s, the raw material for time/versioning semantics.
- Cost: one extra bounded agent run per *sheet* document (sheets are a small fraction of a
  corpus); `CLEAN_FACTS=off` removes it entirely.
- The demo/eval backend seeds one observation whose value does not match its cell; the golden
  scorecard requires the validator to reject it and the honest observations to land intact.

## Alternatives rejected

- **Deterministic header heuristics only** — free, but wrong exactly where it matters (merged
  headers, pivoted layouts, multi-level periods); a silent mis-mapping is worse than a dropped
  one. The heuristic survives as the *fake* backend, which is its honest scope.
- **Facts from the page digest** — the digest is a summary; extraction must read the grid.
- **A vector store of numbers** — numbers need exact lookup and joins, not similarity.
