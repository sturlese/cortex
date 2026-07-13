"""Runtime configuration — constructed once at the entrypoint, passed down explicitly.
Same ground rule as the pipeline packages: modules never read the environment at import time."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    brain_md_dir: str = "/data/brain-md"       # the corpus (read-only; single writer: clean)
    facts_dir: str = "/data/brain-facts"       # the facts store (read-only)
    state_dir: str = "/data/state"             # the index lives here (fully regenerable)
    llm: str = "openai"                        # 'openai' | 'fake' (offline synthesis)
    model: str = "gpt-5.4"
    reasoning_effort: str = "medium"
    bearer_token: str = ""                     # optional static token for the http transport

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            brain_md_dir=os.environ.get("BRAIN_MD_DIR", cls.brain_md_dir),
            facts_dir=os.environ.get("BRAIN_FACTS_DIR", cls.facts_dir),
            state_dir=os.environ.get("ANSWER_STATE_DIR", cls.state_dir),
            llm=os.environ.get("ANSWER_LLM", cls.llm).lower(),
            model=os.environ.get("ANSWER_MODEL", cls.model),
            reasoning_effort=os.environ.get("ANSWER_REASONING_EFFORT", cls.reasoning_effort),
            bearer_token=os.environ.get("ANSWER_BEARER_TOKEN", ""),
        )
