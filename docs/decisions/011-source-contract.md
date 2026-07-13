# ADR 011 — The ingestion abstraction is a contract, not a framework

**Status:** accepted · 2026-07-13

## Context

ADR 001 deferred the "shared ingestion abstraction" until a second source type forced the
question. The Slack connector forced it — and the answer turned out to already exist: clean
never cared where files come from. It reads a raw dir and `_state.json` entries of exactly

```json
{"name": "...", "localPath": "...", "drivePath": "...", "orgUnit": "...",
 "sourceUri": "...", "mimeType": "..."}
```

`fetch` (Drive), `corpus build-inventory` (local exports) and now `slackexport` all emit it.

## Decision

Bless the file contract as THE connector interface — no shared base class, no plugin registry,
no imports between packages. A connector's whole obligation:

1. Mirror source content into files under a raw dir (any format clean's converters read;
   Markdown is the lingua franca for chat-shaped sources).
2. Maintain `_state.json` entries with the six fields above, under stable ids it owns
   (namespaced: `slack-…`, `local-…`, Drive ids), leaving other connectors' entries alone —
   several connectors can share one raw dir.
3. Mirror semantics: content changes re-fingerprint; sources that disappear take their file and
   entry with them (deletions must keep propagating end to end).
4. Encode meaning in the paths it synthesizes: `drivePath` drives entity/unit resolution and
   ACL rules; `orgUnit` is the coarse grouping; dates in names feed `as_of`.

Everything downstream — agentic pages, verification, facts, versions, dossiers, the graph, the
answer server, ACLs — comes for free, **unchanged**. The golden scorecard proves it: a Slack
export flows to a verified page through the pipeline with zero pipeline edits.

## Consequences

- A new connector is a small, dependency-light package plus fixtures (slackexport: stdlib only).
  Notion, email archives, ticket exports: same recipe.
- The contract is testable field by field, and its consumers (clean's `state.py`) never grow
  connector-specific branches.

## Alternatives rejected

- **A shared `Source` base class / plugin system** — couples every connector to a framework
  release cycle for six fields' worth of agreement; ADR 001's no-shared-code doctrine holds.
- **Connectors writing brain-md directly** — would bypass the trust layer; everything must pass
  through clean's verification.
