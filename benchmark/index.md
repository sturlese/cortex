# benchmark — the cortex benchmark

Narrative doc: [`README.md`](README.md) (the dimensions and the two tiers). This file is the code map.

## Purpose

Where the evals guard the golden demo, the benchmark measures **capability**: it generates a
synthetic company drive *with ground truth* — every planted figure, duplicate, revision, ACL scope
and unanswerable probe recorded at generation time — runs the whole system over it, and scores
what came out against what went in. It is the bar to clear if you build a company brain.

## Key entry points

| Entry | File |
|---|---|
| `make benchmark` | `src/benchmark/run.py` (`main`, `run`, `score`) |
| corpus + ground truth generation | `src/benchmark/generate.py` (`generate`) |
| output | `out/benchmark-report.md` + `out/report.json` |

## Module map

- `generate.py` — builds the corpus and returns ground truth in one pass. Fixtures at the top are
  the tuning surface: `CLIENTS`, `PROSPECTS`, `REVISED` (which clients get a FINAL revision with a
  corrected ARR), `MONTHS`. Helpers: `_kpis` (the planted grid values), `_quarterly` (the prose
  documents), `_slug`.
- `run.py` — orchestrates the full system over the generated corpus and scores each dimension via
  `score(dimension, value, threshold, detail)`; `run(out_dir, gate)` decides whether thresholds
  gate the exit code.

## Scored dimensions

`curation`, `placement`, `trust`, `facts-captured` / `facts-wrong`, `versions`, `dossiers`,
`graph`, `qa-exact` / `qa-freshness` / `qa-refusal`, `acl` — see the README table for what each
one asserts against ground truth.

## Use these

- `generate()` returns the ground truth — never re-derive expectations by reading the produced
  artifacts, or the benchmark would grade the system against itself.
- `score(...)` — the single recording path; the report and the gate both read from it.
- The fixture constants in `generate.py` — grow the corpus there, not by hand-writing documents.

## Avoid / anti-patterns

- **Never score an output against another output.** Ground truth comes from generation only; that
  independence is the entire value of this harness.
- Do not lower a floor threshold to make CI pass — the offline floor is deterministic, so a drop
  is a real capability regression.
- Do not gate on the model tier: real backends vary, so thresholds there inform, they do not fail
  the build (`gate` in `run()`).
- Do not let `facts-wrong` be anything but zero on the floor — a stored wrong value is the one
  defect the whole trust layer exists to prevent.
- Do not remove the model-tier probes (e.g. the Spanish memo with dot-grouped, decimal-comma
  figures) just because the offline heuristic skips them by design — they exist to differentiate
  real models.

## Data & contracts

Consumes nothing external: the corpus is generated. Produces `out/benchmark-report.md` and
`out/report.json`. Runs the real `clean`, `graph` and `answer` packages, so it transitively
depends on the page contract
([`../docs/pipeline/brain-page-contract.md`](../docs/pipeline/brain-page-contract.md)) and the
facts store schema.

## Tests

`tests/test_benchmark.py` — 3 tests covering the harness itself (generation and scoring
mechanics). Run from this directory: `pytest -q`. The benchmark is included in the repo suite
(421 tests total across seven packages).

## Common tasks

| Task | Touch |
|---|---|
| Bigger / richer corpus | the fixture constants and builders in `generate.py` |
| New scored dimension | a `score(...)` call in `run.py` + the ground truth it needs from `generate` |
| Adjust the offline floor | the thresholds in `run.py` (justify any downward move) |
| Score a real model | `CLEAN_LLM=openai OPENAI_API_KEY=... python benchmark/src/benchmark/run.py` |

## Notes

The benchmark has already paid for itself: its refusal probe caught the answer layer serving
another entity's data for a metric question about an unknown entity — fixed, with a regression
test added in the same change. Unanswerable probes are the cheapest way to find hallucination.
