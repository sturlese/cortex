# clean — agentic ingestion workers

Narrative docs: [`docs/pipeline/clean.md`](../../docs/pipeline/clean.md),
[`docs/pipeline/facts.md`](../../docs/pipeline/facts.md),
[`docs/pipeline/ops.md`](../../docs/pipeline/ops.md).
This file is the code map: where things live and which module owns what.

## Purpose

Turns the raw file mirror (`raw/`) into verified `brain-md/` pages, cell-verified facts
(`brain-facts/`) and per-entity dossiers (`brain-dossiers/`). It is the only writer of all three
artifacts. Every LLM output is judged by pure code before it is persisted.

## Key entry points

| Entry | File | Notes |
|---|---|---|
| `clean --once` / loop | `src/clean/main.py` (`cli`, `main`, `run_once`) | pass orchestration, dedup, deletions, circuit breakers, post-pass phases |
| per-document pipeline | `src/clean/worker.py` (`process_one`) | extract → agent → verify → retry → page → facts |
| supervisor | `src/clean/ops.py` (`main`, `build_ops_agent`) | run via the compose `ops` profile |
| playbook CLI | `src/clean/playbook.py` (`cli`) | approve/reject the supervisor's pending proposal |
| runtime config | `src/clean/settings.py` (`Settings.from_env`, `resolve_backend`) | all `CLEAN_*` / `*_DIR` env vars |

## Module map

- **Orchestration** — `main.py` (pass loop, `dedup_pending`, token/rate circuit breakers),
  `worker.py` (`process_one`), `state.py` (sha256 idempotency, `classify_pending`, atomic writes).
- **Agents** — `agents.py` (the document processor + its `Processor` protocol), `facts.py`
  (grid + prose facts agents *and* their deterministic validators), `versions.py` (supersedes
  lineage judge), `dossiers.py` (per-entity rollup writer), `claims.py` (structured claim judge
  used by ops), `ops.py` (supervisor + its tools).
- **Trust layer (pure code)** — `verify.py` (`verify_page`, `provable_as_of`, `parse_period`),
  `numeric.py` (`parse_num`). Nothing here may call an LLM.
- **Model plumbing** — `llm.py` (`build_model`, `build_processor` — the single fake/real dispatch
  every agent builder goes through), `fake_llm.py` (offline backends, incl. the deliberately
  flawed one), `observability.py` (`CLEAN_TRACE=logfire`).
- **Deterministic spine** — `entity.py` (path-based entity resolution + catalog), `page.py`
  (frontmatter/`brain_path`/`write_page`/`remove_page`), `converters.py` (extraction per
  extension, incl. `vision_extract`), `factstore.py` (SQLite + JSONL), `acl.py`, `fsutil.py`,
  `schemas.py` (all Pydantic contracts), `tools.py` (`read_more`, `ocr` — the worker's 2 tools).

## Use these

- `settings.Settings` — never read `os.environ` inside a module; config is constructed at the
  entrypoint and passed down. `resolve_backend()` is the only reader of `CLEAN_LLM`.
- `llm.build_processor(...)` — the single place that chooses fake vs. OpenAI. New agents go
  through it and get the offline path for free.
- `verify.verify_page(...)` — the page trust verdict. Do not write a second verifier.
- `numeric.parse_num` / `verify._interpretations` — number canonicalization already handles
  separators, currency and magnitude suffixes.
- `entity.resolve_entity` / `build_catalog` — entity ownership comes from the path, never the LLM.
- `state.write_json_atomic`, `fsutil.write_text_atomic` — all persistence is crash-safe.
- `factstore.replace_facts` / `delete_facts` / `export_jsonl` — the only writes to `brain-facts`.
- `schemas.py` models — extend these rather than passing dicts between stages.

## Avoid / anti-patterns

- Do not let an agent decide entity, path, slug, dedup or deletion — that spine is pure code.
- Do not persist an LLM figure that the deterministic validator did not confirm: facts enter the
  store only via `facts.validate_observations` / `validate_prose_observations`.
- Do not read env vars at import time, and do not monkeypatch env in tests — construct `Settings`.
- Do not add a per-agent fake branch; extend `fake_llm.py` and dispatch through `llm.py`.
- Do not widen agent budgets ad hoc: `RUN_LIMITS` in `worker.py` and the ops caps are the
  contract ([ADR 003](../../docs/decisions/003-bounded-agency-worker.md)).
- Do not write page frontmatter by hand — `page.build_page` owns the contract
  ([`docs/pipeline/brain-page-contract.md`](../../docs/pipeline/brain-page-contract.md)).
- Do not let the supervisor's playbook go live unreviewed; `CLEAN_PLAYBOOK_AUTOAPPROVE` exists for
  tests, not for production.

## Data & contracts

- `src/clean/schemas.py` — `ProcessorOutput`, `PageMetadata`, `Verification`, `Mention`,
  `FactObservation`/`FactsOutput`, `ProseFact`/`ProseFactsOutput`, `OpsReport`.
- Page frontmatter contract: [`docs/pipeline/brain-page-contract.md`](../../docs/pipeline/brain-page-contract.md).
- State: `clean-state.json` (per-file hash, status, `lastResult`) via `state.py`.
- Facts store: `facts.db` + `facts.jsonl` in `BRAIN_FACTS_DIR`, one `source_ref` per number.
- Inputs: `raw/_state.json` written by `fetch` or `slack` — the source contract
  ([ADR 011](../../docs/decisions/011-source-contract.md)).

## Tests

`tests/` — one file per module (`test_worker`, `test_verify`, `test_facts`, `test_versions`,
`test_dossiers`, `test_ops`, `test_claims`, `test_playbook`, `test_acl`, `test_entity`,
`test_page`, `test_state`, `test_main`, `test_converters`, `test_tools`, `test_agents`,
`test_llm`, `test_settings`, `test_fake_llm`, `test_fsutil`). The real agents are exercised
offline against their real tools via the fake backends. Run from this directory: `pytest -q`
(coverage gate 75%; `pythonpath = ["src", "."]` matters — CI runs bare `pytest`).

## Common tasks

| Task | Touch |
|---|---|
| New frontmatter field | `schemas.py` + `page.build_page` + the page contract doc |
| New extraction format | `converters.py` (`method_for_ext`, `extract`) |
| Change what counts as verified | `verify.py` (+ `test_verify.py`) |
| New fact source/shape | `facts.py` validator first, then the agent prompt |
| New agent | `llm.build_processor` + a fake in `fake_llm.py` + a deterministic judge |
| Tune entity conventions | `entity.py` + its conventions JSON |
| New supervisor action | `ops.py` tool impl + cap + `render_report` |

## Notes

- A clean document costs exactly one model request; a verification failure buys one corrective
  retry, and the retry only wins if `worker._quality` improves.
- Circuit breakers (persistent 429, `CLEAN_TOKEN_BUDGET`) abort the pass *without* marking docs as
  errors — they stay pending and resume on relaunch.
- The version and dossier phases run post-pass and only over documents touched in that pass.
- Deletions propagate end to end: source gone → page removed → facts removed.
- `CLEAN_DRY_RUN` defaults to `true`; the container idles until it is explicitly `false`.
