# graph — entity graph layer

`pipeline/graph/src/graph/`. Derives an entity graph from the pages' frontmatter `mentions`.
Deterministic, no LLM, fully regenerable — it never modifies clean's `brain-md` (single-writer);
it writes a derived copy to `brain-md-graphed`.

## How it works

1. **Collect** every `mentions:` entry (name + type) across all pages.
2. **Canonicalize** names (`normalize.py`): strip accents, case, punctuation and legal suffixes
   (S.L., Inc, GmbH, Ltd…), so "Initech", "INITECH, S.L." and "Initech Inc." merge into one entity.
   Noise (initials, <3 chars) is dropped.
3. **Filter** by `--min-mentions` (default 2) — one-off mentions rarely deserve a node.
4. **Write**:
   - every doc, with a `## Related entities` section of `[[wikilinks]]` appended (body untouched),
   - one stub node page per entity at `entities/<type>/<slug>.md` (title + aliases frontmatter).

## Run

```bash
# in the compose stack (profile "graph"):
docker compose --profile graph run --rm graph

# or locally:
cd pipeline/graph && PYTHONPATH=src python -m graph.cli \
  --in /path/to/brain-md --out /path/to/brain-md-graphed --min-mentions 2
```

Output is deterministic for a given input: safe to delete and rebuild anytime.
