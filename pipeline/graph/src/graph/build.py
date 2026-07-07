"""Orchestrates build-graph: read brain-md, build the entity set, write brain-md-graphed."""
import os
from collections import Counter

from graph.entities import build_entities
from graph.pages import page_mentions, render_node, rewrite_doc


def _walk_md(root: str):
    for d, _, files in os.walk(root):
        if os.sep + ".git" in d:
            continue
        for fn in files:
            if fn.endswith(".md"):
                yield os.path.join(d, fn)


def build_graph(in_dir: str, out_dir: str, min_mentions: int = 2) -> dict:
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
        [(n, t, c) for (n, t), c in mention_counts.items()], min_mentions=min_mentions
    )

    # pass 2: write docs (with wikilinks) + entity node pages
    for rel, text in docs:
        p = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(rewrite_doc(text, entities))
    for e in entities.values():
        p = os.path.join(out_dir, e["slug"] + ".md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(render_node(e))

    return {
        "docs": len(docs),
        "entities": len(entities),
        "mentions_raw": sum(mention_counts.values()),
        "by_type": dict(Counter(e["type"] for e in entities.values())),
    }
