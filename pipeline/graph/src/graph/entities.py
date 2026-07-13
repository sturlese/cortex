"""Builds the canonical entity set from mentions: dedup + noise filter + unique slugs.
Input: (name, type, count) tuples. Output: dict norm_key -> entity.
A registry (registry.py) overrides the mechanical grouping: aliases a human (or an approved
merge) declared equivalent join their canonical entity whatever normalize() would say."""
from collections import Counter, defaultdict

from graph.normalize import is_noise, normalize, slugify


def _best_title(names: Counter) -> str:
    """Canonical title: prefer non-ALL-CAPS, then shorter (fewer suffixes), then most frequent."""
    return sorted(names, key=lambda n: (n.isupper(), len(n), -names[n]))[0]


def build_entities(mention_counts, min_mentions: int = 2, registry=None) -> dict:
    groups = defaultdict(lambda: {"names": Counter(), "types": Counter(), "total": 0})
    for name, typ, count in mention_counts:
        canonical = registry.canonical_id(name) if registry else None
        key = canonical or normalize(name)
        if not key or (canonical is None and is_noise(key)):
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
        registered = registry.entities.get(key) if registry else None
        typ = (registered or {}).get("type") or g["types"].most_common(1)[0][0]
        base = f"entities/{typ}/{slugify(key)}"
        slug, i = base, 2
        while slug in used:          # disambiguate slug collisions
            slug, i = f"{base}-{i}", i + 1
        used.add(slug)
        entities[key] = {
            "slug": slug,
            "title": (registered or {}).get("name") or _best_title(g["names"]),
            "type": typ,
            "aliases": list(g["names"]), "mentions": g["total"],
        }
    return entities
