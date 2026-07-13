# ADR 008 — Entity identity: a curated registry, agent-proposed merges, human approval

**Status:** accepted · 2026-07-13

## Context

Entity canonicalization was purely mechanical (`normalize.py`: case, accents, legal suffixes).
That merges "Initech" with "INITECH, S.L." — and can never merge "Globex" with "GX Industries",
nor should any string rule try: whether two names denote the same real-world entity is a
judgment call, and a wrong merge corrupts every page that links either name.

## Decision

Identity gets the three-layer treatment the rest of the system uses:

1. **A curated registry** (`entity-registry.json`, `graph/registry.py`): the human-owned
   identity file — canonical id, display name, type, aliases. The graph build consults it FIRST;
   registered aliases join their canonical entity whatever `normalize()` would say, and the
   registered name/type win the node page. Plain, diffable JSON — playbook doctrine: memory you
   can read, edit and revert. Missing file = empty registry (everything keeps working);
   malformed file = loud error (identity must never silently degrade).
2. **Agent-proposed merges** (`graph/merges.py`): deterministic candidates (similar or
   token-contained normalized keys) go to a merge-judge agent that sees each group's observed
   spellings, counts and mention types — and is instructed to refuse when unsure. The offline
   fake merges only on token containment and otherwise refuses: a heuristic must not invent
   identity.
3. **A human approves**: proposals land in `entity-merges-pending.json`;
   `python -m graph.merges approve` folds them into the registry (all, or one by index),
   `reject` discards. Exactly the playbook gate, applied to identity.

The graph *build* stays pure (no LLM) — agency lives only in the opt-in `propose` subcommand.

## Consequences

- "Ask about Acme" works across spellings, abbreviations and renames — with identity decisions
  auditable in one file's git history.
- The registry is shared vocabulary for later layers (dossiers, ACLs, the answer server's
  entity boosts) — one place where "who is who" lives.
- Cost: zero in the steady build; `propose` runs on demand, bounded to 12 judged pairs.

## Alternatives rejected

- **Auto-applying judged merges** — a wrong merge is the identity equivalent of a hallucinated
  figure; it gets the same human gate the playbook got (ADR 004 amendment).
- **Embedding-based clustering** — thresholds, no auditability, and reversibility is exactly
  what identity errors need most.
- **Registry inside clean's conventions** — path-derived ownership (clean) and mention identity
  (graph) are different problems; the file lives with the stage that consumes it.
