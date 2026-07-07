"""Frontmatter parsing, doc rewriting with wikilinks, and entity node rendering."""
import re

import yaml

from graph.normalize import normalize


def split_frontmatter(text: str):
    """(frontmatter dict, body). If it doesn't parse, frontmatter = {} and body = full text."""
    if text.startswith("---"):
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
        if m:
            try:
                return (yaml.safe_load(m.group(1)) or {}), m.group(2)
            except Exception:
                return {}, text
    return {}, text


def page_mentions(text: str):
    """List of (name, type) from the frontmatter `mentions` (extracted by the clean stage's LLM)."""
    fm, _ = split_frontmatter(text)
    ms = fm.get("mentions") if isinstance(fm, dict) else None
    out = []
    if isinstance(ms, list):
        for m in ms:
            if isinstance(m, dict) and m.get("name"):
                out.append((m["name"], m.get("type") or "other"))
    return out


def rewrite_doc(text: str, entities: dict) -> str:
    """Appends a '## Related entities' section with [[wikilinks]] to the entities that survive
    the filter. Does NOT touch the rest of the body. No duplicate links."""
    links, seen = [], set()
    for name, _ in page_mentions(text):
        key = normalize(name)
        if not key or key not in entities or key in seen:
            continue
        seen.add(key)
        links.append(f"- [[{entities[key]['slug']}|{name}]]")
    out = text.rstrip() + "\n"
    if links:
        out += "\n## Related entities\n\n" + "\n".join(links) + "\n"
    return out


def _y(s: str) -> str:
    return '"' + s.replace('"', "'") + '"' if re.search(r'[:#\[\]{}",]', s) else s


def render_node(entity: dict) -> str:
    """Entity node page (stub): frontmatter type/title/aliases + minimal body."""
    fm = ["---", f"type: {entity['type']}", f"title: {_y(entity['title'])}"]
    aliases = [a for a in entity["aliases"] if a != entity["title"]]
    if aliases:
        fm.append("aliases: [" + ", ".join(_y(a) for a in aliases[:8]) + "]")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {entity['title']}\n\nEntity of type {entity['type']}.\n"
