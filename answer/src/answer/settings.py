"""Runtime configuration — constructed once at the entrypoint, passed down explicitly.
Same ground rule as the pipeline packages: modules never read the environment at import time."""
import os
from dataclasses import dataclass


def _labels(value: str) -> tuple:
    """The audience labels in a comma-separated scope, stripped, blanks dropped."""
    return tuple(a.strip() for a in value.split(",") if a.strip())


@dataclass(frozen=True)
class Settings:
    brain_md_dir: str = "/data/brain-md"       # the corpus (read-only; single writer: clean)
    facts_dir: str = "/data/brain-facts"       # the facts store (read-only)
    state_dir: str = "/data/state"             # the index lives here (fully regenerable)
    llm: str = "openai"                        # 'openai' | 'fake' (offline synthesis)
    model: str = "gpt-5.4"
    reasoning_effort: str = "medium"
    bearer_token: str = ""                     # optional static token for the http transport
    # this deployment's ACL scope. None = unrestricted (open corpus); a tuple = exactly those
    # labels, and an EMPTY tuple is an empty scope (open content only), NOT the absence of one —
    # the same distinction index.visible draws between acl='' and acl=None.
    audiences: tuple | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        aud = os.environ.get("ANSWER_AUDIENCES", "").strip()
        if aud and not _labels(aud):
            # Set but yielding zero labels (",", " , ") — almost always a template rendering an empty
            # list. Collapsing that to "no scope" served the whole corpus to a client the operator
            # believed was scoped. ADR 010: a malformed access-control config errors loudly, because
            # silently-open is the one failure mode it must not have.
            raise RuntimeError(f"invalid ANSWER_AUDIENCES: {aud!r} (no audience labels; "
                               "unset it for an unrestricted instance)")
        return cls(
            brain_md_dir=os.environ.get("BRAIN_MD_DIR", cls.brain_md_dir),
            facts_dir=os.environ.get("BRAIN_FACTS_DIR", cls.facts_dir),
            state_dir=os.environ.get("ANSWER_STATE_DIR", cls.state_dir),
            llm=os.environ.get("ANSWER_LLM", cls.llm).lower(),
            model=os.environ.get("ANSWER_MODEL", cls.model),
            reasoning_effort=os.environ.get("ANSWER_REASONING_EFFORT", cls.reasoning_effort),
            bearer_token=os.environ.get("ANSWER_BEARER_TOKEN", ""),
            # audiences are a DEPLOYMENT property: one server instance = one ACL scope
            # (multi-tenant = one instance per audience set, like gbrain's per-client sources)
            audiences=_labels(aud) if aud else None,
        )
