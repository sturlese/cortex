"""Runtime configuration — constructed once at the entrypoint, passed down explicitly.

Modules never read the environment at import time: `Settings.from_env()` is called by the CLI
(or by tests/evals, which construct Settings directly — no monkeypatching, no env juggling).
Frozen: configuration is data, not shared mutable state.
"""
import os
from dataclasses import dataclass

_VALID_BACKENDS = ("openai", "fake", "fake-flawed")


def resolve_backend() -> str:
    """Read + validate CLEAN_LLM once, at call time (never at import). Returns one of
    'openai' | 'fake' | 'fake-flawed'.

    Single source of truth for backend selection across every agent in the package: an unknown
    value raises here so a typo fails fast instead of silently falling through to the real
    OpenAI path. Consumed by llm.build_processor — the one fake/real dispatch every agent
    builder goes through; it stays in this dependency-light module so anything can read the
    backend without pulling in pydantic-ai."""
    backend = os.environ.get("CLEAN_LLM", "openai").lower()
    if backend not in _VALID_BACKENDS:
        raise RuntimeError(f"invalid CLEAN_LLM: {backend!r} (use 'openai', 'fake' or 'fake-flawed')")
    return backend


@dataclass(frozen=True)
class Settings:
    raw_dir: str = "/data/raw"
    brain_md_dir: str = "/data/brain-md"
    state_dir: str = "/data/state"
    interval: int = 300        # loop cadence (seconds)
    max_concurrent: int = 4    # parallel documents
    max_docs: int = 0          # 0 = unlimited; >0 = bounded trial run
    dry_run: bool = True       # safe no-op until explicitly disabled
    token_budget: int = 0      # 0 = uncapped; else hard per-pass ceiling (in+out tokens)
    playbook_autoapprove: bool = False   # true = supervisor playbook writes go live WITHOUT a human
    facts: bool = True                   # extract typed numeric facts from sheets (facts.py)
    facts_prose: bool = True             # ...and from prose documents (quote-anchored)
    versions: bool = True                # detect near-duplicate versions -> supersedes chain
    dossiers: bool = True                # regenerate per-entity dossiers when members change
    facts_dir: str = "/data/brain-facts"  # facts store (facts.db + facts.jsonl); single writer: clean
    dossiers_dir: str = "/data/brain-dossiers"  # dossier layer; single writer: clean
    acl_path: str = ""                   # CLEAN_ACL: audience-mapping JSON (empty = open corpus)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            raw_dir=os.environ.get("RAW_DIR", cls.raw_dir),
            brain_md_dir=os.environ.get("BRAIN_MD_DIR", cls.brain_md_dir),
            state_dir=os.environ.get("CLEAN_STATE_DIR", cls.state_dir),
            interval=int(os.environ.get("CLEAN_INTERVAL_SECONDS", cls.interval)),
            max_concurrent=int(os.environ.get("CLEAN_MAX_CONCURRENT", cls.max_concurrent)),
            max_docs=int(os.environ.get("CLEAN_MAX_DOCS", cls.max_docs)),
            dry_run=os.environ.get("CLEAN_DRY_RUN", "true").lower() != "false",
            token_budget=int(os.environ.get("CLEAN_TOKEN_BUDGET", cls.token_budget)),
            playbook_autoapprove=os.environ.get("CLEAN_PLAYBOOK_AUTOAPPROVE", "false").lower() == "true",
            facts=os.environ.get("CLEAN_FACTS", "on").lower() != "off",
            facts_prose=os.environ.get("CLEAN_FACTS_PROSE", "on").lower() != "off",
            versions=os.environ.get("CLEAN_VERSIONS", "on").lower() != "off",
            dossiers=os.environ.get("CLEAN_DOSSIERS", "on").lower() != "off",
            dossiers_dir=os.environ.get("BRAIN_DOSSIERS_DIR", cls.dossiers_dir),
            acl_path=os.environ.get("CLEAN_ACL", ""),
            facts_dir=os.environ.get("BRAIN_FACTS_DIR", cls.facts_dir),
        )
