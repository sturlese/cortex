# graph — derived entity graph

Narrative doc: [`docs/pipeline/graph.md`](../../docs/pipeline/graph.md) · registry rationale:
[ADR 008](../../docs/decisions/008-entity-registry.md). This file is the code map.

## Purpose

Derives an entity graph from the `mentions` the pages already carry: writes node pages and
rewrites mentions as wikilinks into `brain-md-graphed/`. Deterministic and fully regenerable —
the graph holds no state of its own. The one optional agent (`merges.py`) proposes canonical
merges for a human to approve; it never edits pages directly.

## Key entry points

- `src/graph/cli.py` (`main`) — build the graph: `brain-md/` → `brain-md-graphed/`.
- `src/graph/build.py` (`build_graph`) — the orchestration.
- `src/graph/merges.py` (`cli`, `build_merge_judge`) — the human-gated alias-merge proposal flow.

## Use these

- `normalize.py` — `normalize`, `slugify`, `is_noise`: the single canonicalization path for entity
  names. Any new comparison must go through it.
- `pages.py` — `split_frontmatter`, `page_mentions`, `rewrite_doc`, `render_node`: page parsing
  and rendering. Do not parse frontmatter ad hoc.
- `registry.py` — `Registry`, `load_registry`, `save_registry`, `apply_merge`: the canonical
  alias store; the only writer of merge decisions.
- `entities.py` — `build_entities(mention_counts, min_mentions, registry)`: mention aggregation
  with the noise floor.

## Avoid / anti-patterns

- Do not write into `brain-md/` from this package — the input is read-only; output goes to
  `brain-md-graphed/`. `clean` is the single writer of `brain-md`.
- Do not apply an agent-proposed merge automatically: proposals land in a pending file next to
  the registry (`merges.pending_path`) and need human approval.
- Do not persist graph state — everything must be reproducible from the pages plus the registry.
- Do not lower `min_mentions` casually; the noise floor is what keeps the graph readable.
- Do not duplicate name normalization (accents, casing, legal suffixes) — `normalize.py` owns it.

## Data & contracts

Reads the `mentions` and entity fields of the page contract
([`docs/pipeline/brain-page-contract.md`](../../docs/pipeline/brain-page-contract.md)). Writes
node pages plus rewritten documents into `brain-md-graphed/`. The registry is a JSON file mapping
aliases to canonical ids; `MergeVerdict` (in `merges.py`) is the agent's proposal schema.

## Tests

`tests/` — `test_build.py`, `test_entities.py`, `test_normalize.py`, `test_pages.py`,
`test_registry_merges.py`, `test_cli.py`. Run from this directory: `pytest -q`.
Graph canonicalization is also scored in [`../../evals/`](../../evals/).

## Common tasks

| Task | Touch |
|---|---|
| Entity names not merging | `normalize.py` first, then the registry |
| Too many junk nodes | `normalize.is_noise` and `min_mentions` in `build_entities` |
| Change node page layout | `pages.render_node` |
| Change wikilink rendering | `pages.rewrite_doc` |

## Notes

The graph is a *derived* view: deleting `brain-md-graphed/` and rebuilding is always safe, and is
the recommended fix for any inconsistency.
