# ADR 001 — No DDD refactor; keep functional core / imperative shell

**Status:** accepted · 2026-07

## Context

With the codebase stabilized (~2k statements across four small Python packages), we evaluated
restructuring it along DDD lines: typed domain entities instead of dicts, repositories for state,
application services per use case, explicit bounded contexts.

## Decision

Don't. The codebase already follows the pattern that fits it — **functional core, imperative
shell** — and the classic DDD preconditions are absent:

- **No invariant-rich domain.** The stages are content transformations (mirror → text → page →
  graph) keyed by content hashes. There are no long-lived mutable aggregates whose consistency
  rules need guarding — the closest thing, clean's state file, is a flat idempotency ledger.
- **The bounded contexts already exist as packages.** fetch / clean / corpus / graph share no code
  and talk through files with explicit schemas (`_state.json`, the page frontmatter contract,
  `inventory.json`). Repositories and anti-corruption layers would formalize boundaries that are
  already physical.
- **Blueprint audience.** This repo is meant to be read and forked. A newcomer can follow
  `main.py → worker.py → page.py` top-to-bottom today; aggregates and service layers would
  roughly 1.5× the code for the same behavior and raise the bar for contributors.
- **The seams that matter are already ports.** The LLM backend is swappable (`CLEAN_LLM`:
  PydanticAI agent / offline fake — formalized as the `Processor` protocol in `agents.py`);
  converters are routed by a table; entity conventions and the corpus taxonomy are data, not code.

## What we did instead

- Formalized the one real port with `typing.Protocol` (`agents.Processor`) so alternative backends
  have an explicit contract.
- Kept domain purity where it pays: `entity.py`, `page.py`, `normalize.py`, `entities.py`, and all
  corpus stages are pure functions over values; I/O lives at the edges (`main.py`, `run_stage`,
  `build_graph`, `cli`s).

## Revisit when

- A second source type (Notion, Slack, S3) forces a shared ingestion abstraction, or
- clean's state grows real business rules (versioning, review workflows, approvals), or
- any package needs a database instead of files.

At that point, introduce typed domain objects at the boundary that hurts — not everywhere.
