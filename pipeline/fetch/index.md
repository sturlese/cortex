# fetch — deterministic Drive mirror

Narrative doc: [`docs/pipeline/fetch.md`](../../docs/pipeline/fetch.md). This file is the code map.

## Purpose

Mirrors a Google Drive folder tree into `raw/` incrementally, with deletions propagating. No LLM,
no interpretation: it produces the inventory contract every downstream stage keys off.

## Key entry points

`src/drive_fetch.py` — a single stdlib-plus-`gog` module:

| Function | Role |
|---|---|
| `main(argv)` | CLI entrypoint |
| `sync_once(cfg, folder_id)` | one full incremental pass |
| `inventory(cfg, folder_id)` / `resolve_folder_id(cfg)` | remote enumeration |
| `build_lineage(cfg, items, root_id)` | reconstructs `drivePath` from parent chains |
| `download_file` / `remove_local` | content writes and deletion propagation |
| `load_state` / `save_state` / `write_atomic` | the fingerprint manifest |
| `Config` | runtime configuration |

## Use these

- `fingerprint(d)` — the change-detection key. Re-download decisions must go through it, never
  through timestamps alone.
- `write_atomic` — every state write is crash-safe; do not use plain `open(...).write`.
- `gog(cfg, *args)` — the single subprocess wrapper around the `gog` CLI (raises `GogError`).
- `_field(d, *names)` — tolerant field access across API response shapes.
- `content_name` / `sidecar_name` / `ext_for` — local naming rules, including the sidecar
  collision guard (`_clobbered_by_sidecar`, `merge_sidecar_lineage`).

## Avoid / anti-patterns

- Do not add an LLM, classification or content parsing here — this stage only mirrors bytes.
- Do not call the `gog` CLI directly; go through `gog()` so errors and JSON parsing stay uniform.
- Do not skip deletion handling: a file gone remotely must be removed locally *and* in state, or
  stale pages linger downstream forever.
- Do not mutate `raw/` from any other package — `fetch` (or `slack`) is its single writer.
- Do not store credentials in code or state; auth is the `gog` keyring's job.

## Data & contracts

Writes `raw/<mirrored tree>` plus `raw/_state.json`: the per-file fingerprint manifest that
doubles as the **inventory contract** consumed by `clean` (`fileId`, `name`, `mimeType`,
`localPath`, `sourceUri`, `drivePath`, `orgUnit`). Any alternative source connector must emit the
same shape — see [ADR 011](../../docs/decisions/011-source-contract.md) and
[`../slack/index.md`](../slack/index.md).

## Tests

`tests/test_drive_fetch.py` — the `gog` boundary is faked; lineage, fingerprinting, naming
collisions and deletion propagation are covered offline. Run from this directory: `pytest -q`.

## Common tasks

| Task | Touch |
|---|---|
| New export format / MIME mapping | `ext_for`, `content_name` |
| Change re-download rules | `fingerprint` |
| Path/lineage issues | `build_lineage`, `_path_join` |
| Auth or CLI failures | `gog`, `GogError`, and the runbook |

## Notes

Idempotent and resumable: kill it mid-pass and relaunch — state is written atomically, and
unchanged files are never re-downloaded. Operational gotchas live in
[`docs/operations/runbook.md`](../../docs/operations/runbook.md).
