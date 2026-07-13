# facts — the typed numeric layer

`pipeline/clean/src/clean/facts.py` (+ `factstore.py`). Turns spreadsheet grids into verified,
queryable metric observations. Design record: [ADR 005](../decisions/005-facts-layer.md).
Doctrine: **the agent judges, the grid decides** — an LLM maps the table (orientation, headers,
metric names, periods); pure code re-reads every claimed cell and only literal matches enter the
store.

## Flow (inside clean, per sheet document)

```
sheet grid ─▶ facts agent ─▶ deterministic validator ─▶ facts.db + facts.jsonl
(numbered     (observations    (value-at-cell, label,     (SQLite queries +
 rows/cols)    w/ coordinates)  period all literal)        diffable audit)
```

The agent sees each sheet as numbered rows/cells and pages through big grids with a budgeted
`read_rows(sheet, start_row)` tool (max 6 calls, 4 requests). Rejected observations are counted
(`facts_rejected` pass stat, `FACTS REJECTED` log lines) with a reason:
`value-not-in-cell` · `label-not-found` · `period-not-found` · `bad-coordinates` · `duplicate-cell`.

## The store contract

`facts.db`, table `observations` — one row per verified observation:

| Column | Meaning |
|---|---|
| `file_id` | source document id (Drive id or `local-…`) — lifecycle key |
| `page_path` | the brain-md page derived from the same source |
| `entity`, `org_unit` | ownership, resolved **deterministically from the folder path** (never by the LLM) |
| `metric` | canonical kebab-case id chosen by the agent (`arr-usd`, `active-users`) |
| `metric_raw` | the label exactly as written in the sheet |
| `value_raw` | the cell content exactly as extracted — the ground truth |
| `value_num` | best-effort numeric parse of `value_raw` (separators, currency, %, k/M/bn) |
| `unit`, `period`, `dimension` | `usd`/`%`/…; `YYYY`, `YYYY-MM` or `YYYY-QN`; breakdown qualifier |
| `source_ref` | `fileId!sheet!RnCm` — every number traces to its cell |
| `extracted_at`, `verified` | provenance; `verified` is always 1 (unverified never lands) |

`facts.jsonl` is the same data, deterministically sorted, rewritten atomically once per pass —
the human-diffable audit trail.

Query semantics (`factstore.query_facts`): equality on `metric` / `entity`; `period` matches
exactly **or by year prefix** (`2026` finds `2026-03` rows).

## Lifecycle

Single writer: clean. Reprocess replaces the document's rows atomically; a deleted source, a doc
downgraded to noise (`skipped`), or a doc that became an exact duplicate deletes its rows —
deletions propagate exactly like pages.

## ENV

| Var | Default | Meaning |
|---|---|---|
| `CLEAN_FACTS` | `on` | `off` disables the layer entirely |
| `BRAIN_FACTS_DIR` | `/data/brain-facts` | store location (the `brain-facts` volume) |

Offline: `CLEAN_LLM=fake` maps grids with a deterministic header heuristic; `fake-flawed` also
seeds one observation with a wrong value so demos/evals show the validator dropping it.
