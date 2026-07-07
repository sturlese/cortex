# Architecture

cortex is two independent Docker stacks joined by one Markdown corpus.

```
┌──────────────────────── pipeline stack ────────────────────────┐
│                                                                │
│  Google Drive ──▶ fetch ──▶ raw/ ──▶ clean ──▶ brain-md/ ──▶ graph ──▶ brain-md-graphed/
│                 (gog CLI,          (agentic worker:           (entity nodes
│                  no LLM)            tools + verifier judge)    + wikilinks, no LLM)
│                                        ▲    │ telemetry                       │
│                                 playbook    ▼                                 │
│                                 (memory) ◀── ops supervisor ──▶ ops-report.md │
└────────────────────────────────────────────────────────────────┘
                                   │  brain-md volume (the airgap)
┌──────────────────────── gbrain stack ──────────────────────────┐
│                                                                │
│  ingest (loop) ──▶ gbrain serve (MCP, :3131) ◀── autopilot     │
│         │                 ▲                                    │
│     Supabase (pgvector)   │ Tailscale Funnel :443 (public)     │
│                           ▼                                    │
│               MCP clients (Claude, ChatGPT, agents)            │
└────────────────────────────────────────────────────────────────┘
```

## Stages

| Stage | In → Out | Nature | State |
|---|---|---|---|
| fetch | Drive folder → `raw/` + `_state.json` | deterministic | per-file fingerprint manifest |
| clean | `raw/` → `brain-md/` | agentic worker (1 request/doc happy path; bounded tools + 1 judge retry) | `clean-state.json` (sha256 idempotency) |
| ops | telemetry → diagnosis → bounded actions | supervisor agent (≤14 req; requeue ≤20, playbook ≤1500c) | `ops-report.md` + playbook |
| graph | `brain-md/` → `brain-md-graphed/` | deterministic | none (fully regenerable) |
| corpus | local corpus copy → curated `inventory.json` | deterministic | provenance sidecars |
| gbrain | `brain-md/` → Postgres + embeddings → MCP | external engine | Supabase |

## Key decisions

**Deterministic entity resolution.** A shared drive's folder tree encodes ownership ("Portfolio/3.
Acme/...") more reliably than any NER. `clean` resolves the owning entity from the *path* with
configurable conventions (two passes: strict anchors build a catalog, then a recovery pass);
the LLM only reports unresolved `mentions`, which `graph` links afterwards.

**One agent, structured outputs.** clean uses a single "processor" agent (PydanticAI) that judges
extraction quality, picks a representation (`full`/`digest`/`minimal`) and writes the body. A
schema-validated output removes all parsing fragility. Failed docs are marked and retried; a
persistent 429 aborts the pass without burning the backlog.

**Representation over transcription.** Spreadsheets become a compact profile + `detail_in_source:
true`, pointing clients at the live file for exact figures — the knowledge base indexes meaning,
not grids.

**Trust is checked, not assumed.** The LLM writes; pure code verifies: every figure in a generated
body is deterministically traced back to the source text (generous matching over separators,
suffixes and currency formats), and each page carries a machine-readable `verification` verdict.
Self-reported quality is never the only signal ([ADR 002](decisions/002-deterministic-verification.md)).

**Bounded agency inside the document, determinism outside.** The per-document worker is an agent
with two tools — `read_more()` (pull extraction beyond the prompt window) and `ocr()` (re-read a
mangled PDF with vision) — under hard budgets (6 requests / 4 tool calls; a clean doc costs exactly
one request). A failed verification triggers one corrective retry with the verifier's findings as
feedback: the generator-judge loop, with a judge that cannot hallucinate. The orchestration spine
(hashes, dedup, deletions, layout) stays pure code ([ADR 003](decisions/003-bounded-agency-worker.md)).

**A supervisor closes the loop; memory is one auditable page.** A second agent watches the
system, not the documents: it reads code-aggregated telemetry, spot-audits pages against freshly
re-extracted sources (the sampled semantic judge), requeues bounded work, and distills lessons
into a ≤1500-char playbook the workers read next pass — workers → telemetry → supervisor →
playbook → workers. Everything it does is capped, recorded in `ops-report.md`, and reversible
([ADR 004](decisions/004-supervisor-and-memory.md)).

**Airgap between stacks.** The pipeline carries no `DATABASE_URL`; the brain server never touches
Drive. Compromise of either stack does not expose the other. The shared `brain-md` volume has a
single writer (clean).

## Failure modes & recovery

| Failure | Contained by | Recovery |
|---|---|---|
| Model invents a figure | deterministic verifier → judge retry → `verification:` flag + banner | supervisor spot-audit; requeue after a playbook update |
| Garbled extraction (scan, mojibake) | worker escalates to its `ocr()` tool in-run | no key / OCR fails → page flagged `manual_review` |
| Provider rate limit (persistent 429) | circuit breaker aborts the pass; docs stay pending | relaunch — hash idempotency resumes exactly where it stopped |
| Token overspend | `CLEAN_TOKEN_BUDGET` hard ceiling (same clean-abort semantics) | raise budget or relaunch next window |
| Same file uploaded twice | sha256 dedup — one page, `duplicateOf` pointer | automatic |
| File deleted in Drive | deletions propagate end to end (mirror → state → page) | automatic |
| Crash mid-pass | per-doc atomic state writes + content-hash idempotency | relaunch; nothing reprocessed twice |
| Supervisor writes a bad playbook | advisory-only, ≤1500 chars, single writer, kill switch | edit/delete the file; verifier still gates every page |
| Supervisor requeues wrongly | hard cap 20/run, reason recorded in state + report | requeued docs just reprocess; worst case is one wasted pass |

## Output layout (brain-md)

```
brain-md/
  entities/<slug>/     pages owned by a tracked entity (client, project, account…)
  prospects/<slug>/    pages owned by a prospective entity (lead, deal, opportunity…)
  units/<unit>/        pages owned by an org unit (top-level folder) with no entity
  general/             everything else
```

Page naming: `slug(filename)-<sha1(file_id)[:6]>.md` — human-readable, stable across runs, and
collision-free. The full frontmatter contract lives in
[pipeline/brain-page-contract.md](pipeline/brain-page-contract.md).
