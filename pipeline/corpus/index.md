# corpus — reproducible offline curation

Narrative doc: [`docs/pipeline/corpus.md`](../../docs/pipeline/corpus.md). This file is the code map.

## Purpose

Turns a local copy of a shared drive into a curated, deduplicated `inventory.json` the pipeline
can consume. Fully deterministic, offline, no LLM: same corpus in → same artifacts out.

## Key entry points

- `src/corpus/cli.py` (`main`, `build_parser`) — the stage commands: `enumerate`, `classify`,
  `curate`, `trim`, `build-manifest`, `build-inventory`.
- `src/corpus/stages/` — one module per stage, each exposing `run_stage(...)` plus a pure
  function that does the real work (`enumerate_files`, `classify_records`, `curate`, `trim`,
  `build_inventory`).

## Use these

- `artifacts.py` — all artifact I/O: `write_jsonl` / `read_jsonl` (Pydantic-typed),
  `write_json` / `read_json`, `sha256_file`, `write_provenance`, `is_fresh` (staleness check
  against declared inputs). Never hand-roll file writes; these are atomic.
- `schemas.py` — `FileRecord`, `ClassRecord`, `ManifestRecord`, `InventoryEntry`, `Provenance`.
  Stages communicate through these models, not dicts.
- `paths.py` — `require_corpus` / `require_workdir` for validated path handling.
- `config.py` — `load_config`, `profile_value` (profile-aware settings).
- `stages/classify_files.py` — `load_taxonomy`, `classify`, `unit_of`: the rules engine. Taxonomy
  is data (JSON), not code.

## Avoid / anti-patterns

- Do not add classification logic in Python when a taxonomy rule expresses it — the rules engine
  is the extension point.
- Do not introduce an LLM into this package; determinism is the whole point.
- Do not write an artifact without its provenance sidecar (`write_provenance`) — freshness checks
  and the audit trail depend on it.
- Do not bypass `schemas.py` by passing raw dicts between stages.
- Do not assume filesystem order: `enumerate_files` handles md5 dedup and UTF-8 safety
  (`_utf8_safe`) deliberately.

## Data & contracts

Stage artifacts live in the workdir as JSONL/JSON with a provenance sidecar each. The terminal
artifact is `inventory.json` (`InventoryEntry` records with stable keys), which is what `clean`
reads — the same shape `fetch` and `slack` produce
([ADR 011](../../docs/decisions/011-source-contract.md)).

## Tests

`tests/` — `test_enumerate_inventory.py`, `test_classify.py`, `test_curate.py`, `test_trim.py`,
`test_artifacts.py`, `test_schemas.py`, `test_config.py`, `test_paths.py`, `test_cli.py`.
Run from this directory: `pytest -q`.

## Common tasks

| Task | Touch |
|---|---|
| New document category | the taxonomy JSON (`classify_files.default_taxonomy_path`) |
| Change dedup/ranking rules | `stages/curate_manifest.py` (`_record_rank`, `curate`) |
| Change what counts as noise | `stages/trim_manifest.py` (`is_noise`) |
| New stage | a module in `stages/` with `run_stage` + a subcommand in `cli.py` |

## Notes

Stages are independently re-runnable and skip work when `is_fresh` says the inputs have not
changed — the same idempotency principle as the rest of the pipeline.
