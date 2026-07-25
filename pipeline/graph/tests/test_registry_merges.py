"""Entity identity: the registry overrides mechanics; merges are judged, then human-approved."""
import asyncio
import json

import pytest

from graph.build import build_graph
from graph.entities import build_entities
from graph.merges import (
    FakeMergeJudge,
    candidate_pairs,
    cli,
    collect_groups,
    propose,
)
from graph.registry import Registry, apply_merge, load_registry, save_registry


def _registry_file(tmp_path, entities):
    path = tmp_path / "entity-registry.json"
    path.write_text(json.dumps({"entities": entities}))
    return str(path)


# ── the registry ─────────────────────────────────────────────────────────────
def test_load_registry_builds_alias_map(tmp_path):
    path = _registry_file(tmp_path, {
        "globex": {"name": "Globex", "type": "organization",
                   "aliases": ["Globex Corp", "GX Industries"]}})
    reg = load_registry(path)
    assert reg.canonical_id("GLOBEX CORP") == "globex"        # normalized alias
    assert reg.canonical_id("gx industries") == "globex"
    assert reg.canonical_id("Globex, S.L.") == "globex"       # legal suffix stripped by normalize
    assert reg.canonical_id("Initech") is None
    assert reg.title("globex") == "Globex"


def test_load_registry_missing_is_empty_malformed_is_loud(tmp_path):
    assert load_registry(None).entities == {}
    assert load_registry(str(tmp_path / "nope.json")).entities == {}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"entities": {"x": {}}}))
    with pytest.raises(ValueError, match="needs at least a 'name'"):
        load_registry(str(bad))


def test_registry_merges_aliases_across_normalize_boundaries(tmp_path):
    """'GX Industries' would never merge with 'Globex' mechanically — the registry decides."""
    path = _registry_file(tmp_path, {
        "globex": {"name": "Globex", "type": "organization", "aliases": ["GX Industries"]}})
    reg = load_registry(path)
    mentions = [("Globex", "organization", 2), ("GX Industries", "company", 2)]
    ents = build_entities(mentions, min_mentions=2, registry=reg)
    assert list(ents) == ["globex"]
    e = ents["globex"]
    assert e["mentions"] == 4                      # both alias groups pooled
    assert e["title"] == "Globex"                  # registry name wins
    assert e["type"] == "organization"             # registry type wins over per-mention majority
    assert set(e["aliases"]) == {"Globex", "GX Industries"}


def test_build_entities_without_registry_unchanged():
    ents = build_entities([("Initech", "company", 2), ("INITECH, S.L.", "company", 1)])
    assert list(ents) == ["initech"]
    assert ents["initech"]["mentions"] == 3


def test_apply_merge_and_save_roundtrip(tmp_path):
    reg = Registry()
    apply_merge(reg, "globex", "Globex", "organization", ["Globex Corp", "GX Industries"])
    path = str(tmp_path / "entity-registry.json")
    save_registry(path, reg)
    again = load_registry(path)
    assert again.canonical_id("gx industries") == "globex"
    # idempotent re-apply doesn't duplicate aliases
    apply_merge(again, "globex", "Globex", "organization", ["Globex Corp"])
    assert again.entities["globex"]["aliases"].count("Globex Corp") == 1


def test_apply_merge_absorbs_an_already_registered_entity(tmp_path):
    """A merge that absorbs names belonging to an EXISTING entity left that entity in place, so the
    saved file had two entities each claiming the same alias. load_registry resolves that by
    last-writer-wins over cid order, so whether the human's approved merge survived depended on how
    the two ids happened to sort — here it is reverted outright."""
    path = str(tmp_path / "entity-registry.json")
    json.dump({"entities": {"globex-industries": {"name": "Globex Industries",
                                                  "type": "organization", "aliases": ["Globex"]}}},
              open(path, "w"))
    reg = load_registry(path)
    apply_merge(reg, "globex", "Globex", "organization", ["Globex", "Globex Industries"])

    # IN MEMORY first: `merges approve` applies every chosen proposal against one registry before
    # saving once, so a later proposal sees this state. An absorbed id must not still resolve to the
    # entity that was just deleted, or the next apply_merge reasons about a phantom.
    assert reg.canonical_id("globex-industries") == "globex"
    assert reg.entities.get("globex-industries") is None

    save_registry(path, reg)
    again = load_registry(path)

    assert list(again.entities) == ["globex"]                     # today: both, contradicting
    assert again.canonical_id("Globex") == "globex"               # today: "globex-industries"
    assert again.canonical_id("Globex Industries") == "globex"
    assert "Globex Industries" in again.entities["globex"]["aliases"]
    # the absorbed entity keeps answering to everything it used to, including its old ID: normalize
    # keeps hyphens, so the slug is a different key from the display name and would otherwise be lost
    assert again.canonical_id("globex-industries") == "globex"


def test_apply_merge_absorbing_the_other_direction_also_settles(tmp_path):
    """The mirror case, where the absorbed entity's id sorts FIRST: the merge appeared to survive on
    main but still left a self-contradictory file, and the curated entity's own display name became
    orphaned from its alias. Both names must resolve to the approved canonical."""
    path = str(tmp_path / "entity-registry.json")
    json.dump({"entities": {"acme": {"name": "Acme", "type": "organization",
                                     "aliases": ["Acme Holdings"]}}}, open(path, "w"))
    reg = load_registry(path)
    apply_merge(reg, "zeta-acme", "Zeta Acme", "organization", ["Acme", "Acme Holdings"])
    save_registry(path, reg)
    again = load_registry(path)

    assert list(again.entities) == ["zeta-acme"]
    for name in ("Acme", "Acme Holdings", "Zeta Acme"):
        assert again.canonical_id(name) == "zeta-acme", name


def test_apply_merge_leaves_unrelated_entities_alone(tmp_path):
    """Absorption must be driven by the names in the merge, not by proximity: an entity sharing no
    name with the merge keeps its own id, aliases and resolution."""
    path = str(tmp_path / "entity-registry.json")
    json.dump({"entities": {"initech": {"name": "Initech", "type": "company",
                                        "aliases": ["Initech SL"]},
                            "globex-industries": {"name": "Globex Industries",
                                                  "type": "organization", "aliases": ["Globex"]}}},
              open(path, "w"))
    reg = load_registry(path)
    apply_merge(reg, "globex", "Globex", "organization", ["Globex", "Globex Industries"])
    save_registry(path, reg)
    again = load_registry(path)

    assert sorted(again.entities) == ["globex", "initech"]
    assert again.canonical_id("Initech SL") == "initech"
    assert again.entities["initech"]["aliases"] == ["Initech SL"]


def test_apply_merge_does_not_delete_an_entity_that_only_shares_an_alias(tmp_path):
    """The registry is human-owned (ADR 008), so removing a record needs more warrant than one
    shared string. Two genuinely distinct entities can carry the same short alias — merging one must
    retarget that alias, NOT swallow the other's id, display name, type and every other alias.

    A first version of this fix absorbed on any overlap: merging "Acme Foods" deleted a distinct
    "Acme Bank" (type person) and left ABG resolving to a food company."""
    reg = Registry()
    reg.entities["acme-foods"] = {"name": "Acme Foods", "type": "company",
                                  "aliases": ["Acme Food Group"]}
    reg.entities["acme-bank"] = {"name": "Acme Bank", "type": "person", "aliases": ["Acme", "ABG"]}
    notes = []
    apply_merge(reg, "acme-foods", "Acme Foods", "company", ["Acme", "Acme Foods"], log=notes.append)

    assert sorted(reg.entities) == ["acme-bank", "acme-foods"]     # nothing deleted
    assert reg.entities["acme-bank"]["type"] == "person"           # ...and nothing rewritten
    assert reg.canonical_id("ABG") == "acme-bank"
    assert reg.canonical_id("Acme Bank") == "acme-bank"
    assert reg.canonical_id("Acme") == "acme-foods"                # only the contested alias moved
    assert "Acme" not in reg.entities["acme-bank"]["aliases"]
    assert _alias_conflicts(reg) == []                             # and the contradiction is gone
    assert notes and "moved from 'acme-bank'" in notes[0]           # never silently


def test_apply_merge_absorbing_nothing_deletes_nothing(tmp_path):
    """`claimed` includes the canonical's own id and name, so an empty `absorbs` list still overlaps
    anything aliased to that name. A merge that absorbs nothing must not remove a record."""
    reg = Registry()
    reg.entities["acme"] = {"name": "Acme", "type": "company", "aliases": []}
    reg.entities["acme-bank"] = {"name": "Acme Bank", "type": "person", "aliases": ["Acme"]}
    apply_merge(reg, "acme", "Acme", "company", [])
    assert sorted(reg.entities) == ["acme", "acme-bank"]
    # ...but the contradiction must still be resolved: the canonical's OWN name has to count as
    # claimed, or two entities keep claiming it and the load order decides again.
    assert reg.canonical_id("Acme") == "acme"
    assert _alias_conflicts(reg) == []


def test_apply_merge_does_not_inherit_names_it_did_not_claim(tmp_path):
    """Absorbing an entity wholesale made the canonical inherit that entity's OTHER aliases — which
    a third entity could also claim, recreating the very cid-sort-order contradiction one hop out.
    Retargeting only the contested alias keeps the merge's footprint to what it claimed."""
    reg = Registry()
    reg.entities["bravo-group"] = {"name": "Bravo Group", "type": "organization",
                                   "aliases": ["Shared One", "Bridge Name"]}
    reg.entities["zetera-holdings"] = {"name": "Zetera Holdings", "type": "organization",
                                       "aliases": ["Bridge Name", "Bravo Group"]}
    apply_merge(reg, "bravo", "Bravo", "organization", ["Shared One"])
    path = str(tmp_path / "entity-registry.json")
    save_registry(path, reg)
    again = load_registry(path)

    assert again.canonical_id("Shared One") == "bravo"          # the merge survives the reload
    assert again.entities["bravo"]["aliases"] == ["Shared One"]  # and claimed nothing else


def _alias_conflicts(reg):
    """Normalized keys claimed by more than one entity — the state that makes a registry's meaning
    depend on cid sort order at load time."""
    from graph.normalize import normalize
    owner, dup = {}, set()
    for cid, e in reg.entities.items():
        for a in (cid, e["name"], *e["aliases"]):
            key = normalize(str(a))
            if not key:
                continue
            if key in owner and owner[key] != cid:
                dup.add(key)
            owner.setdefault(key, cid)
    return sorted(dup)


def test_apply_merge_never_leaves_the_registry_self_contradictory(tmp_path):
    """The guarantee: a merge must not leave two entities claiming the same name, because that is
    what makes the file's meaning depend on cid sort order. Absorbing the conflicting entity is what
    delivers it — folding only its names is what did not.

    Note the scope: a conflict a human hand-authored between entities the merge does not touch is
    CARRIED FORWARD, not silently resolved. Resolving someone else's ambiguity is not a merge's
    business; that is a separate concern, recorded on the backlog."""
    reg = Registry()
    for cid, name, aliases in [("globex-industries", "Globex Industries", ["Globex"]),
                               ("initech", "Initech", ["Initech SL"])]:
        reg.entities[cid] = {"name": name, "type": "organization", "aliases": list(aliases)}
    assert _alias_conflicts(reg) == []

    apply_merge(reg, "globex", "Globex", "organization", ["Globex", "Globex Industries"])
    assert _alias_conflicts(reg) == []                     # today: ['globex', 'globex industries']
    assert sorted(reg.entities) == ["globex", "initech"]

    # a pre-existing conflict between two entities the merge does not touch stays exactly as it was
    reg.entities["other"] = {"name": "Initech", "type": "organization", "aliases": []}
    before = _alias_conflicts(reg)
    apply_merge(reg, "globex", "Globex", "organization", ["Globex"])
    assert _alias_conflicts(reg) == before == ["initech"]


def test_build_graph_with_registry_writes_canonical_node(tmp_path):
    brain = tmp_path / "brain"
    (brain / "entities/g").mkdir(parents=True)
    (brain / "entities/g/a.md").write_text(
        "---\nmentions:\n  - { name: Globex, type: organization }\n---\n\n# A\n\nbody\n")
    (brain / "entities/g/b.md").write_text(
        "---\nmentions:\n  - { name: GX Industries, type: company }\n---\n\n# B\n\nbody\n")
    reg = load_registry(_registry_file(tmp_path, {
        "globex": {"name": "Globex", "type": "organization", "aliases": ["GX Industries"]}}))
    stats = build_graph(str(brain), str(tmp_path / "out"), min_mentions=2, registry=reg)
    assert stats["entities"] == 1
    node = (tmp_path / "out/entities/organization/globex.md").read_text()
    assert "title: Globex" in node
    assert "GX Industries" in node                 # alias recorded on the node
    linked = (tmp_path / "out/entities/g/b.md").read_text()
    assert "[[entities/organization/globex|GX Industries]]" in linked


# ── merge proposals (agent judges, human approves) ───────────────────────────
def _brain_with_aliases(tmp_path):
    brain = tmp_path / "brain"
    (brain / "x").mkdir(parents=True)
    (brain / "x/a.md").write_text(
        "---\nmentions:\n  - { name: Globex, type: organization }\n---\n\n# A\n\nbody\n")
    (brain / "x/b.md").write_text(
        "---\nmentions:\n  - { name: Globex Industries, type: organization }\n---\n\n# B\n\nbody\n")
    (brain / "x/c.md").write_text(
        "---\nmentions:\n  - { name: Initech, type: company }\n---\n\n# C\n\nbody\n")
    return str(brain)


def test_candidate_pairs_containment_and_similarity():
    groups = {"globex": {}, "globex industries": {}, "initech": {}}
    assert candidate_pairs(groups) == [("globex", "globex industries")]


def test_fake_judge_merges_containment_refuses_otherwise():
    prompt = ("GROUP A — normalized key: globex\n  most common spelling: Globex\n\n"
              "GROUP B — normalized key: globex industries\n  most common spelling: Globex Industries\n")
    v = asyncio.run(FakeMergeJudge().run(prompt)).output
    assert v.same_entity is True and v.canonical_name == "Globex Industries"
    prompt2 = ("GROUP A — normalized key: globex foods\n  most common spelling: Globex Foods\n\n"
               "GROUP B — normalized key: globex bank\n  most common spelling: Globex Bank\n")
    assert asyncio.run(FakeMergeJudge().run(prompt2)).output.same_entity is False


def test_propose_and_approve_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEAN_LLM", "fake")
    brain = _brain_with_aliases(tmp_path)
    registry_path = str(tmp_path / "entity-registry.json")

    proposals = asyncio.run(propose(brain, registry_path))
    assert len(proposals) == 1
    assert proposals[0]["canonical_name"] == "Globex Industries"
    assert set(proposals[0]["absorbs"]) == {"Globex", "Globex Industries"}

    # the CLI gate: propose -> list -> approve writes the registry
    assert cli(["propose", "--in", brain, "--registry", registry_path]) == 0
    assert cli(["list", "--registry", registry_path]) == 0
    assert cli(["approve", "--registry", registry_path]) == 0
    reg = load_registry(registry_path)
    assert reg.canonical_id("Globex") == reg.canonical_id("Globex Industries") == "globex-industries"

    # groups collapse on the next collection; nothing pending anymore
    groups = collect_groups(brain, reg)
    assert "globex-industries" in groups and len(groups["globex-industries"]["names"]) == 2
    assert cli(["list", "--registry", registry_path]) == 1


def test_reject_leaves_registry_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEAN_LLM", "fake")
    brain = _brain_with_aliases(tmp_path)
    registry_path = str(tmp_path / "entity-registry.json")
    assert cli(["propose", "--in", brain, "--registry", registry_path]) == 0
    assert cli(["reject", "--registry", registry_path]) == 0
    assert load_registry(registry_path).entities == {}


# ── backend dispatch: the CLEAN_MODEL/CLEAN_LLM contract, mirrored from clean ─
def test_build_merge_judge_invalid_backend_fails_fast(monkeypatch):
    """A CLEAN_LLM typo must raise — never silently pick the fake (the old startswith check
    accepted 'fakee') and never fall through to the real path."""
    from graph.merges import build_merge_judge
    monkeypatch.setenv("CLEAN_LLM", "fakee")
    with pytest.raises(RuntimeError, match="invalid CLEAN_LLM"):
        build_merge_judge()


def test_resolve_model_provider_prefixed_passthrough(monkeypatch):
    """A provider-prefixed pydantic-ai string bypasses the OpenAI path (and its key
    requirement) — the judge must honor the same CLEAN_MODEL syntax clean does."""
    from graph.merges import _resolve_model
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CLEAN_MODEL", "anthropic:claude-sonnet-4-5")
    model, settings = _resolve_model()
    assert model == "anthropic:claude-sonnet-4-5"
    assert settings is None


def test_resolve_model_bare_name_requires_key_and_valid_effort(monkeypatch):
    from graph.merges import _resolve_model
    monkeypatch.setenv("CLEAN_MODEL", "gpt-5.4")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        _resolve_model()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    monkeypatch.setenv("CLEAN_REASONING_EFFORT", "ultra")
    with pytest.raises(RuntimeError, match="CLEAN_REASONING_EFFORT"):
        _resolve_model()
    monkeypatch.setenv("CLEAN_REASONING_EFFORT", "minimal")
    _, settings = _resolve_model()
    assert settings["openai_reasoning_effort"] == "minimal"
