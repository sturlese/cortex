# slack — second source connector

Narrative doc: [`docs/pipeline/slack.md`](../../docs/pipeline/slack.md) · rationale:
[ADR 011](../../docs/decisions/011-source-contract.md). This file is the code map.

## Purpose

Turns a Slack workspace export into pipeline inventory: one Markdown document per
`(channel, month)`, written into `raw/` with the same `_state.json` contract `fetch` produces —
so the entire downstream (clean, facts, versions, graph, answer) runs unchanged. Offline,
stdlib-only, no LLM, no network. It exists to prove ingestion is a *contract*, not a framework.

## Key entry points

`src/slackexport/sync.py`:

| Function | Role |
|---|---|
| `main(argv)` | CLI entrypoint |
| `sync(export_path, raw_dir, team)` | full pass: export → `raw/` + `_state.json` |
| `collect_months(root)` | groups messages into `(channel, month)` buckets |
| `render_month(...)` | the Markdown rendering of one bucket |
| `load_export` / `load_users` | export unpacking and the user-id → name map |
| `doc_id(channel, month)` | the stable synthetic file id |

## Use these

- `doc_id` — document identity must stay stable across re-syncs, or every page churns.
- `_substitute_mentions` — resolves `<@U123>` to display names via `load_users`.
- `_ts` — the single timestamp parser; Slack's `ts` is a float-as-string.
- `render_month` — the one place that decides document shape; keep rendering out of `sync`.

## Avoid / anti-patterns

- Do not add dependencies: stdlib-only is a deliberate constraint (it is the proof that the source
  contract is cheap to implement).
- Do not invent a different `_state.json` shape — parity with `fetch` is enforced by the evals.
- Do not drop thread replies whose parent falls outside the month window; that was a real bug
  (commit `6a7270e`) and `tests/test_sync.py` guards it.
- Do not call the Slack API here; the input is an export directory/zip, by design.
- Do not write outside `raw_dir` — it is shared with `fetch` and is `clean`'s only input.

## Data & contracts

Emits the same inventory entries as `fetch` (`fileId`, `name`, `mimeType`, `localPath`,
`sourceUri`, `drivePath`, `orgUnit`) into `raw/_state.json`, plus one `.md` per channel-month.
`sourceUri` points back to the Slack permalink where derivable.

## Tests

`tests/test_sync.py` — rendering, month bucketing, mention substitution, thread handling and
contract parity, all offline against a fixture export. Run from this directory: `pytest -q`.
The connector proof is also a scored eval in [`../../evals/`](../../evals/).

## Common tasks

| Task | Touch |
|---|---|
| Change document granularity | `collect_months` + `doc_id` (expect full re-processing) |
| Change message rendering | `render_month` |
| Support a new export layout | `load_export`, `load_users` |
| Add another source connector | copy this package's shape; match `_state.json` exactly |

## Notes

Channel-month granularity is a deliberate trade-off: coarse enough for meaningful pages, fine
enough for the `as_of` and period machinery to work. Changing it re-keys every document.
