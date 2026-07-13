# ADR 009 — Dossiers: distilled, verified knowledge per entity

**Status:** accepted · 2026-07-13

## Context

The brain answered from documents; a *second brain* also needs the rollup. "What's the current
state of Globex?" should not require a client to assemble N pages, resolve which report is
current and cross-check figures — that assembly is itself knowledge, it goes stale the moment a
member document changes, and nobody hand-maintains it.

## Decision

A per-entity dossier layer (`brain-dossiers/`, single writer: clean), regenerated as a bounded
post-pass phase (`clean/dossiers.py`) with the standard doctrine split:

- **Deterministic scope**: the member set is the state's processed pages for the entity; a hash
  over (file id, path, supersedes-state) gates regeneration — unchanged entities cost nothing,
  and an entity whose last page disappears loses its dossier (deletions propagate here too).
- **An agent writes** (judgment): bounded tools — `read_page` (member pages only, fenced) and
  `query_facts` (the entity's verified numbers, superseded rows flagged) — every tool result
  recorded as the run's evidence. Instructed to prefer current documents and to present
  superseded material as history.
- **The page verifier judges** (pure code): `verify.verify_page` traces every figure in the
  dossier back to the run's evidence; `failed` earns one corrective retry; the verdict lands in
  the dossier's frontmatter. A dossier is held to the page standard — a rollup that invents is
  worse than no rollup.

## Consequences

- "Ask about Acme" gets a curated, current, cited answer surface — consumable by the answer
  server or gbrain like any Markdown corpus (`docker volume create brain-dossiers`).
- Conflicting figures across versions resolve visibly: the dossier carries the current value,
  and notes the superseded document as history.
- Cost: one bounded agent run per *changed* entity per pass; `CLEAN_DOSSIERS=off` disables.

## Alternatives rejected

- **A separate dossier package** — clean already owns every LLM write and the verifier;
  a fourth artifact (after pages, facts, version annotations) with the same single-writer
  doctrine beats a new package re-importing half of clean.
- **Dossiers at query time** (answer server synthesizes on demand) — right for ad-hoc
  questions, wrong for the standing rollup: no persistence, no diffable history, cost per ask.
- **Hand-maintained entity pages** — they rot; the whole point is regeneration on change.
