"""Optional config from corpus_config.toml: profiles (sample/full/...) and defaults.
CLI flags ALWAYS win. The config is explicit (it is not a silent default)."""
from __future__ import annotations

import os
import tomllib


def load_config(path: str | None) -> dict:
    """Loads the TOML. {} when there is no path or it doesn't exist (the CLI will still require
    the paths -> fail fast)."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def profile_value(config: dict, profile: str | None, key: str):
    """Value of [profile.<profile>].<key>; else [defaults].<key>; else None."""
    if profile:
        prof = (config.get("profile") or {}).get(profile) or {}
        if key in prof:
            return prof[key]
    return (config.get("defaults") or {}).get(key)
