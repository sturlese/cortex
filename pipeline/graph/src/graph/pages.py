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
                # Unconditional, because the promise above is unconditional and PyYAML's failures do
                # not share a base class: "2026-02-30" raises ValueError from datetime.date(),
                # `!!bool maybe` a KeyError, `!!timestamp hello` an AttributeError, deep nesting a
                # RecursionError. Uncaught, ONE such page kills the build for the whole corpus in
                # pass 1. Scope is a single safe_load, so no real defect is masked.
                return {}, text
    return {}, text


def page_mentions(text: str):
    """List of (name, type) from the frontmatter `mentions` (extracted by the clean stage's LLM)."""
    fm, _ = split_frontmatter(text)
    ms = fm.get("mentions") if isinstance(fm, dict) else None
    out = []
    if isinstance(ms, list):
        for m in ms:
            if not isinstance(m, dict):
                continue
            name = m.get("name")
            # YAML may parse an unquoted name like `On`/`1984` as a bool/int; str-coerce so it
            # never crashes normalize(), but drop bools (almost always an accidental YAML boolean).
            if name is None or isinstance(name, bool):
                continue
            name = str(name).strip()
            if not name:
                continue
            typ = m.get("type")
            out.append((name, str(typ) if typ else "other"))
    return out


def rewrite_doc(text: str, entities: dict, registry=None) -> str:
    """Appends a '## Related entities' section with [[wikilinks]] to the entities that survive
    the filter. Does NOT touch the rest of the body. No duplicate links."""
    links, seen = [], set()
    for name, _ in page_mentions(text):
        key = (registry.canonical_id(name) if registry else None) or normalize(name)
        if not key or key not in entities or key in seen:
            continue
        seen.add(key)
        links.append(f"- [[{entities[key]['slug']}|{name}]]")
    out = text.rstrip() + "\n"
    if links:
        out += "\n## Related entities\n\n" + "\n".join(links) + "\n"
    return out


_PLAIN_YAML = re.compile(r"[A-Za-z0-9][\w .\-/]*", re.UNICODE)

# Control characters YAML refuses to carry literally even inside a double-quoted scalar (the reader
# rejects them before quoting can help), plus \x85/NEL and the C1 range, which it folds to a space.
# \t \n \r are escaped explicitly below. Hand-mirrored from clean's page._CTRL_YAML.
_CTRL_YAML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _y(v) -> str:
    """Emit a YAML-safe scalar: plain only when it round-trips through the loader, else an escaped
    double-quoted scalar. Node pages must round-trip through yaml.safe_load (the frontmatter is a
    contract)."""
    s = str(v)
    # Plain only when the restricted charset matches AND the loader reads the scalar back as the
    # identical string. The round-trip check catches every YAML 1.1 implicit type -- dates
    # (2001-12-14), hex/binary/underscored ints (0x1F, 0b101, 1_000), bool/null words -- that the
    # old hand-maintained pattern list silently missed, re-typing the entity name on read.
    if s and _PLAIN_YAML.fullmatch(s):
        try:
            if yaml.safe_load(s) == s:
                return s
        except (yaml.YAMLError, ValueError):
            # An invalid date ("0000-00-00", "2026-02-30") matches YAML's timestamp regex but makes
            # datetime.date() raise a bare ValueError; an over-limit int likewise. Either way the
            # scalar is not provably plain-safe -- fall through and quote (which always round-trips).
            pass
    esc = (s.replace("\\", "\\\\").replace('"', '\\"')
           .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))
    esc = _CTRL_YAML.sub(lambda m: f"\\x{ord(m.group()):02x}", esc)
    return f'"{esc}"'


def render_node(entity: dict) -> str:
    """Entity node page (stub): frontmatter type/title/aliases + minimal body."""
    # type through _y like every other scalar: it comes from page_mentions, i.e. a raw string out of
    # some page's frontmatter, NOT a validated Literal — a colon in it would break the node page.
    fm = ["---", f"type: {_y(entity['type'])}", f"title: {_y(entity['title'])}"]
    aliases = [a for a in entity["aliases"] if a != entity["title"]]
    if aliases:
        fm.append("aliases: [" + ", ".join(_y(a) for a in aliases[:8]) + "]")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {entity['title']}\n\nEntity of type {entity['type']}.\n"
