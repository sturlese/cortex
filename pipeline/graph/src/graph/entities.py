"""Builds the canonical entity set from mentions: dedup + noise filter + unique slugs.
Input: (name, type, count) tuples. Output: dict norm_key -> entity."""
from collections import Counter, defaultdict

from graph.normalize import is_noise, normalize, slugify


def _best_title(names: Counter) -> str:
    """Canonical title: prefer non-ALL-CAPS, then shorter (fewer suffixes), then most frequent."""
    return sorted(names, key=lambda n: (n.isupper(), len(n), -names[n]))[0]


def build_entities(mention_counts, min_mentions: int = 2) -> dict:
    groups = defaultdict(lambda: {"names": Counter(), "types": Counter(), "total": 0})
    for name, typ, count in mention_counts:
        key = normalize(name)
        if not key or is_noise(key):
            continue
        g = groups[key]
        g["names"][name] += count
        g["types"][typ or "other"] += count
        g["total"] += count

    entities, used = {}, set()
    for key in sorted(groups):  # deterministic order
        g = groups[key]
        if g["total"] < min_mentions:
            continue
        typ = g["types"].most_common(1)[0][0]
        base = f"entities/{typ}/{slugify(key)}"
        slug, i = base, 2
        while slug in used:          # disambiguate slug collisions
            slug, i = f"{base}-{i}", i + 1
        used.add(slug)
        entities[key] = {
            "slug": slug, "title": _best_title(g["names"]), "type": typ,
            "aliases": list(g["names"]), "mentions": g["total"],
        }
    return entities
