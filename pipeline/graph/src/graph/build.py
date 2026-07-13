"""Orchestrates build-graph: read brain-md, build the entity set, write brain-md-graphed."""
import os
from collections import Counter

from graph.entities import build_entities
from graph.pages import page_mentions, render_node, rewrite_doc


def _walk_md(root: str):
    for d, dirs, files in os.walk(root):
        dirs[:] = [sub for sub in dirs if sub != ".git"]  # skip VCS internals (exact name, not a
        # substring: a '.git'-prefixed ancestor of root — e.g. .gitdata/ — must not drop the tree)
        for fn in files:
            if fn.endswith(".md"):
                yield os.path.join(d, fn)


def build_graph(in_dir: str, out_dir: str, min_mentions: int = 2, registry=None) -> dict:
    # pass 1: collect mentions from every doc
    mention_counts = Counter()
    docs = []
    for path in _walk_md(in_dir):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        docs.append((os.path.relpath(path, in_dir), text))
        for name, typ in page_mentions(text):
            mention_counts[(name, typ)] += 1

    entities = build_entities(
        [(n, t, c) for (n, t), c in mention_counts.items()],
        min_mentions=min_mentions, registry=registry,
    )

    # pass 2: write docs (with wikilinks) + entity node pages
    written: set[str] = set()
    for rel, text in docs:
        p = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(rewrite_doc(text, entities, registry=registry))
        written.add(os.path.abspath(p))
    for e in entities.values():
        p = os.path.join(out_dir, e["slug"] + ".md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(render_node(e))
        written.add(os.path.abspath(p))

    # brain-md-graphed is a DERIVED, fully regenerable mirror: drop stale .md left from a previous
    # run (source docs deleted upstream, or entity nodes now below the mention threshold) so
    # deletion propagates end to end and the layer never accumulates orphans.
    removed = 0
    for path in _walk_md(out_dir):
        if os.path.abspath(path) not in written:
            os.remove(path)
            removed += 1

    return {
        "docs": len(docs),
        "entities": len(entities),
        "mentions_raw": sum(mention_counts.values()),
        "by_type": dict(Counter(e["type"] for e in entities.values())),
    }
