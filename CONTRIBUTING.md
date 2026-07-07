# Contributing

Thanks for taking a look. cortex is a blueprint — the most valuable contributions are the ones
that keep it small, deterministic and easy to adapt.

## Ground rules

- **Code wins over docs.** If they disagree, fix the doc in the same PR.
- **Deterministic by default.** New logic should be pure code unless an LLM genuinely earns its
  cost; config over code for anything corpus-specific (see `taxonomy.json`, `CLEAN_CONVENTIONS`).
- **No secrets, ever.** `.env` files are gitignored; examples use placeholders.
- **Configuration is constructed at the entrypoint** (`Settings.from_env()` / `Config.from_env()`)
  and passed down explicitly — modules never read the environment at import time, and tests build
  config objects instead of monkeypatching globals.
- **Single writer per artifact** (fetch → `raw/`, clean → `brain-md/`, graph → its derived layer).

## Dev loop

```bash
make demo    # end-to-end over the example corpus, no API keys
make test    # all four package suites (pytest, coverage gate 75%)
make lint    # ruff (config in ruff.toml)
```

Each package under `pipeline/` is self-contained: `pip install -r requirements.txt && pytest`
inside it. CI runs the same three things on every PR.

## Pull requests

- Keep them focused; include tests for behavior changes (the 75% gate is enforced per package).
- Match the local style: small modules, comments only for non-obvious constraints.
- If you change the page frontmatter, update `docs/pipeline/brain-page-contract.md` — it's an API.
