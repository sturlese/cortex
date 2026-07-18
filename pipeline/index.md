# pipeline — the ingestion stack

Narrative doc: [`docs/pipeline/README.md`](../docs/pipeline/README.md) · system view:
[`docs/architecture.md`](../docs/architecture.md). This file routes you to the right package.

## Purpose

The write half of cortex: sources become verified pages, facts and dossiers. One Docker stack,
five Python packages, no database and no `DATABASE_URL` — the pipeline never sees the serving
side (the airgap).

## Key entry points

| Package | Does | Map | LLM? |
|---|---|---|---|
| [`fetch/`](fetch/index.md) | Drive → `raw/` (incremental mirror, deletions propagate) | `src/drive_fetch.py` | no |
| [`slack/`](slack/index.md) | Slack export → `raw/` (same contract) | `src/slackexport/sync.py` | no |
| [`corpus/`](corpus/index.md) | local corpus → curated `inventory.json` | `src/corpus/cli.py` | no |
| [`clean/`](clean/index.md) | `raw/` → `brain-md/` + `brain-facts/` + `brain-dossiers/` | `src/clean/main.py` | **yes** (bounded, verified) |
| [`graph/`](graph/index.md) | `brain-md/` → `brain-md-graphed/` | `src/graph/cli.py` | optional, human-gated |

The supervisor (`clean/src/clean/ops.py`) runs as a separate compose profile:
`docker compose --profile ops run --rm ops`.

## Data flow

```
fetch ─┐
       ├─▶ raw/ + raw/_state.json ─▶ clean ─▶ brain-md/ ─▶ graph ─▶ brain-md-graphed/
slack ─┘   (the source contract)      │  └─▶ brain-facts/ · brain-dossiers/
                                      └─▶ telemetry ─▶ ops ─▶ ops-report.md + playbook (human-gated)
```

`corpus/` is an offline side path producing a curated inventory from a local copy.

## Use these

- **The source contract** (`raw/_state.json`) is the seam: any new connector emits it and the
  whole downstream runs unchanged ([ADR 011](../docs/decisions/011-source-contract.md)).
- **`clean/src/clean/settings.py`** is the pattern for configuration everywhere: frozen dataclass,
  built at the entrypoint, never read from the environment at import time.
- **Deterministic verifiers** (`clean/verify.py`, `clean/facts.py` validators) are the trust layer;
  every agent in the stack is judged by one.
- **Atomic writes** (`state.write_json_atomic`, `fsutil.write_text_atomic`, `fetch.write_atomic`)
  everywhere state is persisted.

## Avoid / anti-patterns

- Do not give any stage a second writer: `raw/` ← fetch|slack, `brain-md/` ← clean,
  `brain-facts/` ← clean, `brain-md-graphed/` ← graph. Enforce with read-only mounts.
- Do not add database credentials or serving concerns to this stack — that breaks the airgap.
- Do not let an agent make a decision pure code can make (paths, hashes, dedup, deletions,
  entity resolution) ([ADR 003](../docs/decisions/003-bounded-agency-worker.md)).
- Do not cross-import between packages; they are independent, separately tested units that
  communicate through artifacts on disk.
- Do not couple to Drive specifics outside `fetch/`.

## Data & contracts

- Source inventory: `raw/_state.json` (see [`fetch/index.md`](fetch/index.md)).
- Page frontmatter: [`docs/pipeline/brain-page-contract.md`](../docs/pipeline/brain-page-contract.md).
- Facts store: `facts.db` + `facts.jsonl` (owned by `clean/factstore.py`).
- Per-stage state: `_state.json` (fetch), `clean-state.json` (clean); graph and corpus hold none.

## Tests

Per package, each with its own venv and pytest config (coverage gate 75%). From the repo root:
`make test` runs every suite; `make lint` runs ruff. Cross-package contract parity, seeded-defect
catch rates and the Slack connector proof are scored in [`../evals/`](../evals/) and
[`../benchmark/`](../benchmark/).

Note: CI runs **bare** `pytest`, so a package whose tests import shared helpers needs
`pythonpath = ["src", "."]` in its `pyproject.toml` (see `clean/pyproject.toml`).

## Common tasks

| Task | Start at |
|---|---|
| Add a source | [`slack/index.md`](slack/index.md) — copy its shape, match `_state.json` |
| Change what a page contains | `clean/src/clean/page.py` + the page contract doc |
| Change trust rules | `clean/src/clean/verify.py` |
| Add/adjust a fact type | `clean/src/clean/facts.py` (validator first) |
| Tune supervisor behaviour | `clean/src/clean/ops.py` |
| Deploy / operate | [`docs/operations/deploy.md`](../docs/operations/deploy.md), [`runbook.md`](../docs/operations/runbook.md) |

## Notes

Everything is idempotent and resumable: stages key off content hashes, state writes are atomic,
and both circuit breakers (persistent 429, token budget) abort a pass leaving pending work
pending rather than marking it failed.
