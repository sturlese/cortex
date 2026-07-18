"""ACL resolution — audience labels derived deterministically from the source path.

Who may see a document is a property of WHERE it lives (the same insight as entity resolution):
"Finance/" is for finance+leadership, "Clients/" for sales, board minutes for leadership. The
mapping is config, not code, and never an LLM decision:

    {"default": ["all"],
     "rules": [
       {"path_prefix": "/Drive/Finance", "audiences": ["finance", "leadership"]},
       {"unit": "Clients", "audiences": ["sales", "leadership"]},
       {"path_contains": "board", "audiences": ["leadership"]}
     ]}

First matching rule wins (ordered, like the corpus taxonomy). The resolved list lands in the
page frontmatter (`acl: [...]`), on every facts row, and on dossiers as the INTERSECTION of
their members' audiences (a rollup must never widen access to what it summarizes). The answer
layer enforces it at query time; pages without `acl` are visible to every client.

Deliberate limitation, documented: this maps *conventions* to audiences. Mirroring live Drive
per-file permissions is a connector concern (the fetch sidecar already persists whatever
metadata Drive returns) and would feed the same field.
"""
import json

_MATCHERS = ("path_prefix", "path_contains", "unit", "entity_kind")


def load_acl_config(path: str | None) -> dict | None:
    """None/missing -> no ACLs (open corpus). Malformed -> loud error: silently open is the one
    failure mode an access-control file must not have."""
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    rules = cfg.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError(f"acl config {path}: 'rules' must be a list")
    for rule in rules:
        if not isinstance(rule.get("audiences"), list) or not rule["audiences"]:
            raise ValueError(f"acl config {path}: every rule needs a non-empty 'audiences' list")
        if not any(k in rule for k in _MATCHERS):
            raise ValueError(f"acl config {path}: rule needs one of {_MATCHERS}: {rule}")
        _check_labels(path, rule["audiences"])
    default = cfg.get("default", ["all"])
    if not isinstance(default, list) or not default:
        raise ValueError(f"acl config {path}: 'default' must be a non-empty list")
    _check_labels(path, default)
    return {"default": [str(a) for a in default],
            "rules": [{k: rule[k] for k in (*_MATCHERS, "audiences") if k in rule} for rule in rules]}


def _check_labels(path: str, audiences: list) -> None:
    """Audience labels are CSV-serialized downstream (facts rows, the answer index): a comma
    inside a label would silently split into two audiences at enforcement time — the exact
    silent-corruption failure mode an access-control config must not have. Empty labels would
    vanish in the same round-trip."""
    for a in audiences:
        s = str(a)
        if "," in s or not s.strip():
            raise ValueError(f"acl config {path}: invalid audience label {s!r} "
                             "(labels must be non-empty and must not contain ',')")


def resolve_acl(config: dict | None, source_path: str, unit: str | None,
                entity_kind: str | None) -> list[str] | None:
    """Audience list for one document, or None when ACLs are off. First matching rule wins."""
    if config is None:
        return None
    low = (source_path or "").lower()
    for rule in config["rules"]:
        if "path_prefix" in rule and low.startswith(str(rule["path_prefix"]).lower()):
            return list(rule["audiences"])
        if "path_contains" in rule and str(rule["path_contains"]).lower() in low:
            return list(rule["audiences"])
        if "unit" in rule and unit and str(rule["unit"]).lower() == str(unit).lower():
            return list(rule["audiences"])
        if "entity_kind" in rule and entity_kind and rule["entity_kind"] == entity_kind:
            return list(rule["audiences"])
    return list(config["default"])


def dossier_acl(member_acls: list) -> list[str] | None:
    """A dossier summarizes all its members: its audience is the INTERSECTION of theirs. Members
    without ACLs don't restrict; all-None -> None (open). An empty intersection means nobody
    below unrestricted clients sees it — restrictive by construction, never silently open."""
    sets = [set(a) for a in member_acls if a is not None]
    if not sets:
        return None
    out = set.intersection(*sets)
    return sorted(out)


def visible(acl: list[str] | None, audiences: set[str] | None) -> bool:
    """The one visibility rule, shared vocabulary with the answer layer: no ACL -> visible to
    all; unrestricted client (None) -> sees everything; otherwise intersect."""
    if acl is None or audiences is None:
        return True
    return bool(set(acl) & audiences)
