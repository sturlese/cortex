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
