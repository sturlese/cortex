# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-18

The "referent memory layer" release: the pipeline grows a verified numeric facts
store, provable time, per-entity dossiers and audience ACLs — and a new serving
half (`answer/`) that turns "trust the pages" into "trust the answers". Plus a
hardening pass: a security fix in ACL enforcement, and cross-package contract
parity proven in CI.

### Added
- Trust layer v2: period-anchored verification — a real figure attributed to the
  wrong month is now caught, not green-lit (#12)
- Learning-loop hardening: human-approval gate for the supervisor's playbook,
  prompt-injection fencing of audited document content, O(n) state saves (#13)
- The facts layer: an agent maps each spreadsheet grid to typed observations; a
  deterministic validator re-reads every claimed cell — verified numbers land in
  `brain-facts/` (SQLite + diffable JSONL) (#14)
- Structured claim checks: sampled semantic judge, each paragraph anchored to its
  best-overlap source window and ruled supported/unsupported/contradicted (#15)
- Prose facts: quote-anchored numeric observations from text documents (#16)
- Time as a first-class dimension: provable `as_of` on every page and a
  near-duplicate version judge producing an explicit `supersedes` chain (#17)
- The answer server (`answer/`): MCP server with contract-enforcing retrieval,
  exact facts, and an answering agent judged by a deterministic answer verifier —
  refusal is a first-class outcome (#18)
- Golden Q&A evals: exactness, freshness, refusal and retrieval measured end to
  end on every push (#19)
- Entity identity: curated registry plus agent-proposed merges, human-approved (#20)
- Dossiers: distilled, verified per-entity rollups in `brain-dossiers/`,
  regenerated only when members change, judged by the page verifier (#21)
- Audience ACLs: derived deterministically from the source path, carried by
  pages/facts/dossiers, enforced at the answer (#22)
- Slack connector: a workspace export becomes pipeline inventory (stdlib-only);
  the whole downstream runs unchanged — the ADR 011 source-contract proof (#23)
- The cortex benchmark: a public ground-truth corpus + 12-dimension floor that
  scores the whole system (#24)
- Contract-parity evals: CI now proves the hand-mirrored ACL and facts-query
  implementations in `clean` and `answer` agree (golden scorecard 22 → 24) (#32)

### Fixed
- Slack connector: thread replies whose parent lay outside the export month were
  silently dropped (#26)
- Answer retrieval: BM25 base relevance was nullified by clamping negative FTS5
  scores, flattening the lexical signal (#27)
- Graph merge judge: honor the full `CLEAN_MODEL` contract — provider-prefixed
  values (e.g. `anthropic:...`) worked in clean but broke in graph; a `CLEAN_LLM`
  typo silently selected the offline fake (#32)

### Security
- An empty ACL (`acl: []` — a dossier whose members share no audience, documented
  as "restricted to nobody") was served OPEN to scoped clients by the answer
  index. Empty and absent ACLs are now encoded distinctly, with a one-shot index
  migration; audience labels that would break the CSV round-trip (commas, blanks)
  are rejected at config load (#30)

### Changed
- clean's internal architecture: one LLM backend dispatch (`llm.build_processor`)
  instead of seven hand-mirrored copies, a dependency-free numeric leaf (removing
  a storage→agent layering inversion and an import cycle), one atomic-write
  primitive, one page-deletion primitive (#29)
- Fail-fast backend validation everywhere: an invalid `CLEAN_LLM` or `ANSWER_LLM`
  now raises instead of silently picking a backend; the ops report is written
  atomically; the pipeline state schema is documented at its single source (#32)

## [0.1.0] - 2026-07-09

### Added
- Initial public release: deterministic Drive mirror (`fetch`), agentic cleaning
  with deterministic verification (`clean`), reproducible curation (`corpus`),
  derived entity graph (`graph`), gbrain deploy wrapper, offline demo and golden
  evals.
