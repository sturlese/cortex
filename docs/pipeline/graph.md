# graph — entity graph layer

`pipeline/graph/src/graph/`. Derives an entity graph from the pages' frontmatter `mentions`.
The build is deterministic, no LLM, fully regenerable — it never modifies clean's `brain-md`
(single-writer); it writes a derived copy to `brain-md-graphed`.

## How it works

1. **Collect** every `mentions:` entry (name + type) across all pages.
2. **Canonicalize** names: the **entity registry** first (`registry.py` — curated identity:
   "GX Industries" → Globex, whatever string rules say), then `normalize.py` mechanics (strip
   accents, case, punctuation and legal suffixes, so "Initech", "INITECH, S.L." and "Initech
   Inc." merge). Noise (initials, <3 chars) is dropped.
3. **Filter** by `--min-mentions` (default 2) — one-off mentions rarely deserve a node.
4. **Write**:
   - every doc, with a `## Related entities` section of `[[wikilinks]]` appended (body untouched),
   - one stub node page per entity at `entities/<type>/<slug>.md` (title + aliases frontmatter;
     registered entities carry their curated name/type).

## Entity identity (registry + judged merges)

`--registry entity-registry.json` points the build at the curated identity file. To grow it,
an agent proposes and a **human approves** ([ADR 008](../decisions/008-entity-registry.md)):

```bash
CLEAN_LLM=fake python -m graph.merges propose --in brain-md/ --registry entity-registry.json
python -m graph.merges list    --registry entity-registry.json
python -m graph.merges approve --registry entity-registry.json    # or reject / --index N
```

Deterministic candidates (similar/contained normalized keys) → merge-judge agent (refuses when
unsure; the offline fake merges only on token containment) → pending file → your call.

## Run

```bash
# in the compose stack (profile "graph"):
docker compose --profile graph run --rm graph

# or locally:
cd pipeline/graph && PYTHONPATH=src python -m graph.cli \
  --in /path/to/brain-md --out /path/to/brain-md-graphed --min-mentions 2
```

Output is deterministic for a given input: safe to delete and rebuild anytime. The input is the
pages' **content** — not the order the filesystem happens to yield them in, so re-running the build
over an unchanged corpus cannot move anything. Every choice that could tie is decided by a stated
rule: the entity's type and its aliases by most-mentioned, then by **codepoint** order (not locale
collation, so for equal-length names it prefers the more-ASCII spelling); its title by
non-ALL-CAPS → shortest → most-mentioned → codepoint.

One caveat, since it is the same class of surprise: slugs are assigned in key order, and two
different keys can slugify identically (`Foo & Bar` and `Foo + Bar` both give `foo-bar`), with the
second taking a `-2` suffix. So adding an entity whose key sorts earlier *can* move an existing
one's node path — not because of walk order, but because the disambiguation depends on the set of
keys. Rare, and the build rewrites every link consistently, but it is not "nothing ever moves".
