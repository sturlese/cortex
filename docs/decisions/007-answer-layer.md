# ADR 007 — Own the answer path: contract enforcement moves server-side

**Status:** accepted · 2026-07-13

## Context

The page contract told MCP clients how to behave — "don't quote numbers from failed pages",
"prefer the superseding version", "open the original when `detail_in_source`" — but nothing
enforced it. The serving half was fully delegated to an external engine (gbrain: embeddings +
pgvector) with none of the pipeline's guarantees and no way to add them there. The product's
promise is decided at answer time; that was the one place the doctrine didn't reach.

## Decision

A first-party serving package (`answer/`) that enforces the contract server-side, structured
exactly like the ingestion side — deterministic where trust matters, agentic where judgment pays:

- **Index & retrieval (pure code).** SQLite FTS5 over brain-md, incremental, regenerable.
  Ranking is BM25 plus *explainable, deterministic* contract factors: superseded pages heavily
  demoted, `verification: failed` / `manual_review` demoted, exact entity/period matches
  boosted, fresh `as_of` preferred for "current"-style questions. Every hit carries the factors
  applied to it — "why did this rank here" is always answerable.
- **Exact numbers (pure code).** `query_metrics` reads the facts store (the documented facts.db
  contract; re-implemented read path — ADR 001: packages share no code) and flags rows whose
  page is superseded, so current truth wins conflicts.
- **The answering agent (judgment).** Bounded tools (search / read_page / query_metrics, all
  results fenced as untrusted data), instructed to cite everything and to *refuse* when the
  evidence is insufficient — refusal is a first-class outcome, not a failure.
- **The answer verifier (pure code, the judge).** Before an answer leaves the server: every
  figure must trace to what the tools returned **this run** (not the whole corpus — a lucky
  match elsewhere cannot launder an invented number), every citation must point at a surfaced
  page and quote it verbatim. `failed` earns exactly one corrective retry with the findings;
  the verdict ships with the answer. The generator-judge loop, at query time.
- **Thin MCP skin.** stdio for local clients; streamable HTTP (optional static bearer token)
  behind your own ingress. All enforcement lives in the service, none in the transport.

**gbrain stays.** It remains the documented vector-search alternative over the same brain-md
volume; `answer/` is the reference path where the guarantees matter. Both consume the same
contracts; neither writes them.

## Consequences

- "Trust the pages" becomes "trust the answers": the same machine-checkable verdict
  (`verified`/`partial`/`failed`) that gates pages now rides on every answer.
- Offline determinism end to end: `ANSWER_LLM=fake` answers metric questions from the facts
  store, falls back to retrieval, refuses honestly — demo and CI run the whole serving path
  with zero keys (and the golden Q&A evals of the next stage have a stable target).
- No external database: the index is a regenerable SQLite file; the only inputs are the two
  read-only volumes (brain-md, brain-facts).

## Alternatives rejected

- **Contributing enforcement to gbrain** — external project, different stack; the trust layer
  is this repo's core competence and must live where its contracts do.
- **Prompt-only enforcement** (system prompts on clients) — hope is not a guarantee; clients
  vary and drift.
- **Vector search in-process** — embeddings need keys and a model choice; BM25 + contract
  factors is deterministic, explainable and offline. Semantic recall can layer on top later
  (or via gbrain) without touching the guarantees.
