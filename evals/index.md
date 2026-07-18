# evals — the golden scorecard

Narrative doc: [`README.md`](README.md) (what each dimension means and why). This file is the
code map.

## Purpose

Answers *"does the **system** produce the quality we promised"* — as opposed to the unit tests,
which answer *"does the code do what the code says"*. Runs the entire pipeline plus the answer
layer over the fictional corpus in [`../examples/demo-corpus`](../examples/demo-corpus) and scores
the result against a golden set. Fully offline and deterministic (content-derived ids, fake
backends), so every target is exact and any drift is a real regression. **CI runs it on every push.**

## Key entry points

| Entry | File |
|---|---|
| `make eval` → the harness | `run-evals.sh` → `run_evals.py` (`main`) |
| expected artifacts | `golden.json` (taxonomy, manifest, pages, facts, versions, graph) |
| expected answers | `qa_golden.json` (4 end-to-end Q&A cases) |
| ACL fixture | `acl-config.json` |
| output | `out/scorecard.md` (+ the full run tree under `out/`) |

`run_evals.py` puts `pipeline/{clean,graph,corpus,slack}/src` and `answer/src` on `sys.path`, so
it is the one harness that imports *both* sides of every cross-package contract.

## The scorecard: 24 metrics

Emitted in `main()` in run order — each `eval_*` function contributes a fixed number:

| Function | Metrics |
|---|---|
| `eval_curation` | 2 — taxonomy type+verdict, dedup + allowlist |
| `eval_clean_and_trust` | 6 — pass completes, placement paths, frontmatter contract, seeded hallucination caught, seeded misattribution caught, zero false positives |
| `eval_facts` | 3 — store contents, exact value+period spot-checks, seeded bad sheet+prose facts rejected |
| `eval_versions` | 1 — supersedes chain in state *and* on both pages |
| `eval_dossiers` | 1 — verified rollup carrying current truth |
| `eval_ops_claims` | 2 — supervision completes, claim judge raises zero false alarms |
| `eval_graph` | 1 — canonical entity nodes |
| `eval_answers` | 4 — one per `qa_golden.json` case (exactness, freshness, refusal, retrieval) |
| `eval_acl` | 1 — sales answered, eng refused *and* the page absent from its search |
| `eval_contract_parity` | 2 — ACL visibility parity, facts read-path parity |
| `eval_slack_connector` | 1 — export → verified page through the unchanged pipeline |

**A live-model run scores 23, not 24**: with `CLEAN_LLM` set to anything other than `fake-flawed`
there is nothing seeded to catch, so `eval_clean_and_trust` swaps its two seeded-defect metrics
for one ("no unresolved verification failures"). Placement, curation, graph and contract metrics
stay exact either way.

## Use these

- `metric(name, result, passed)` — the only way to record a result; `main()` renders and gates on
  `RESULTS`. A new check is a new `metric(...)` call, not a new output format.
- `golden.json` / `qa_golden.json` — expectations are **data**. Add cases there before adding code.
- `eval_contract_parity` — the doctrine check for [ADR 001](../docs/decisions/001-no-ddd-refactor.md):
  the packages deliberately share no code, so the hand-mirrored halves of each cross-package
  contract (clean's `visible(list)` vs answer's `visible(csv)`; `query_facts` vs `query_metrics`)
  are proven to agree here. Any new duplicated contract belongs in this function.
- `_jsonl(path)` — reading stage artifacts.

## Avoid / anti-patterns

- Do not weaken a target to make the scorecard green — the whole point is that drift is visible.
  Offline targets are exact by construction; a failure is a regression or an intentional change
  that must be reflected in `golden.json`.
- Do not add assertions in `eval_*` functions; they must record a metric and keep going so the
  full scorecard always renders.
- Do not depend on filesystem ordering or wall-clock time — determinism is what makes exact
  targets legitimate.
- Do not point this at production data: the golden set is tied to the demo corpus. Extend it with
  your own corpus and expectations first (see the README's closing note).
- Do not duplicate unit-test coverage here; evals score *system* outcomes, not function behaviour.
- Do not let a new cross-package contract ship without a parity probe.

## Data & contracts

Consumes the demo corpus and produces a full run tree under `out/` (`work/`, `raw/`, `brain-md/`,
`facts/`, `state/`, `graphed/`, `dossiers/`, `slack-*`, `answer-state*`), plus `out/scorecard.md`.
`out/` is wiped at the start of every run. Exit code is non-zero if any metric fails, which is
what gates CI.

## Tests

The harness has no unit tests of its own — it *is* the test, at system level. Package-level suites
live next to each package (421 tests total: fetch 29, clean 248, corpus 48, graph 38, slack 13,
answer 42, benchmark 3). Capability against a generated ground-truth corpus is scored separately
by [`../benchmark/`](../benchmark/index.md).

## Common tasks

| Task | Touch |
|---|---|
| Corpus changed → placement/curation drift | `golden.json` (`taxonomy`, `manifest`, `pages`) |
| New golden question | `qa_golden.json` (the metric count rises with it) |
| New scored dimension | a new `eval_*` function + its call in `main()` + a golden entry |
| New cross-package contract | a probe in `eval_contract_parity` |
| Score a real model | `CLEAN_LLM=openai OPENAI_API_KEY=... evals/.venv/bin/python evals/run_evals.py` |

## Notes

The seeded defects are the point: the `fake-flawed` backend deliberately invents two figures in
one document, ties a real figure to the wrong month in another, and plants one bad sheet
observation plus one bad prose quote. Four failure modes, four deterministic catchers — if any of
those metrics goes green *for the wrong reason* (e.g. the backend stops seeding), the
zero-false-positive metrics are what keep the suite honest.
