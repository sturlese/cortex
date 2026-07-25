"""The entity registry — curated identity the automatic canonicalization defers to.

normalize.py merges names mechanically (case, accents, legal suffixes). Real corpora need more:
"Globex" and "GX Industries" may be the same company, and no string rule should ever decide that.
The registry is the human-owned identity file:

    {"entities": {
        "globex": {"name": "Globex", "type": "organization",
                   "aliases": ["Globex Corp", "GX Industries"]}}}

- The graph build consults it FIRST: any mention whose normalized form matches a canonical id or
  one of its aliases joins that entity, whatever normalize.py would have said.
- It is a plain, diffable JSON file — same doctrine as the playbook: memory you can read, edit
  and revert. Humans edit it directly, or approve agent-proposed merges (merges.py) into it.
"""
import json
import os
from dataclasses import dataclass, field

from graph.normalize import normalize

REGISTRY_FILE = "entity-registry.json"


@dataclass
class Registry:
    entities: dict = field(default_factory=dict)   # id -> {name, type, aliases: []}
    by_alias: dict = field(default_factory=dict)   # normalized alias/name/id -> id

    def canonical_id(self, name: str) -> str | None:
        return self.by_alias.get(normalize(name))

    def title(self, canonical: str) -> str | None:
        e = self.entities.get(canonical)
        return e.get("name") if e else None

    def type_of(self, canonical: str) -> str | None:
        e = self.entities.get(canonical)
        return e.get("type") if e else None


def _reindex(reg: Registry) -> Registry:
    """Rebuild by_alias from entities — the one place that mapping is derived. Building it
    incrementally leaves entries pointing at ids that no longer exist once an entity is absorbed."""
    reg.by_alias = {}
    for cid, e in reg.entities.items():
        for alias in (cid, e["name"], *e["aliases"]):
            key = normalize(str(alias))
            if key:
                reg.by_alias[key] = cid
    return reg


def load_registry(path: str | None) -> Registry:
    """Missing path/file -> empty registry (the graph works unregistered); malformed -> error,
    loudly — a broken identity file must never silently degrade to wrong entities."""
    reg = Registry()
    if not path or not os.path.exists(path):
        return reg
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entities = data.get("entities")
    if not isinstance(entities, dict):
        raise ValueError(f"registry {path}: top-level 'entities' object is required")
    for cid, e in entities.items():
        if not isinstance(e, dict) or not e.get("name"):
            raise ValueError(f"registry {path}: entity {cid!r} needs at least a 'name'")
        reg.entities[cid] = {"name": e["name"], "type": e.get("type", "organization"),
                             "aliases": list(e.get("aliases", []))}
    return _reindex(reg)


def save_registry(path: str, reg: Registry) -> None:
    data = {"entities": {cid: {"name": e["name"], "type": e["type"],
                               "aliases": sorted(set(e["aliases"]))}
                         for cid, e in sorted(reg.entities.items())}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def apply_merge(reg: Registry, canonical_id: str, canonical_name: str, entity_type: str,
                absorbed_names: list[str], log=None) -> Registry:
    """Fold `absorbed_names` into `canonical_id` (creating it if new) and resolve any contradiction
    that creates. Pure bookkeeping — the JUDGMENT that these are the same entity happened upstream
    (merges.py + a human). `log` is called with a one-line description of anything this changes
    besides the canonical itself, so a destructive step can never be silent.

    A contradiction here means two entities claiming the same normalized name. Left in place, the
    saved file's meaning depends on cid sort order at load time: `globex` absorbing
    `globex-industries` was reverted outright by the next load, while the other sort direction kept
    the merge but orphaned the curated entity's display name and emitted two nodes for one entity.

    How it is resolved depends on WHAT the merge claimed, because this file is human-owned and
    deleting a record needs more warrant than a single string overlap:

    - the other entity's own NAME or ID is claimed -> the merge is asserting they are the same
      entity, so it is absorbed (its id and names become aliases) and removed.
    - only one of its ALIASES is claimed -> the two are still distinct entities that shared a
      spelling. That alias is retargeted to the canonical and the entity is otherwise left alone.
      Deleting it would fold a curated record — its id, display name, type and every other alias —
      into an unrelated one on the strength of one shared string, which is what an earlier version
      of this function did: merging "Acme Foods" swallowed a distinct "Acme Bank" and left ABG
      resolving to a food company."""
    def _note(msg):
        if log:
            log(msg)

    e = reg.entities.setdefault(canonical_id, {"name": canonical_name, "type": entity_type, "aliases": []})
    for name in absorbed_names:
        if name != e["name"] and name not in e["aliases"]:
            e["aliases"].append(name)
    claimed = {normalize(str(n)) for n in (canonical_id, e["name"], *absorbed_names)} - {""}
    for cid in [c for c in reg.entities if c != canonical_id]:
        other = reg.entities[cid]
        identity = {normalize(str(a)) for a in (cid, other["name"])} - {""}
        if identity & claimed:
            # its id/name too, not only its aliases: normalize keeps hyphens, so the slug
            # `globex-industries` is a DIFFERENT key from the name `globex industries`, and an
            # absorbed entity should keep answering to everything it used to.
            for name in (cid, other["name"], *other["aliases"]):
                if name != e["name"] and name not in e["aliases"]:
                    e["aliases"].append(name)
            del reg.entities[cid]
            _note(f"absorbed entity {cid!r} ({other['name']!r}, type {other['type']!r}) into "
                  f"{canonical_id!r} — the merge claimed its own name/id")
            continue
        contested = [a for a in other["aliases"] if normalize(str(a)) in claimed]
        if contested:
            other["aliases"] = [a for a in other["aliases"] if a not in contested]
            _note(f"alias(es) {contested!r} moved from {cid!r} ({other['name']!r}) to "
                  f"{canonical_id!r}; {cid!r} is otherwise unchanged")
    return _reindex(reg)
