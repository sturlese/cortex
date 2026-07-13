# clean — the agentic worker

`pipeline/clean/src/clean/`. Turns the raw mirror into Markdown knowledge-base pages. The design is
**bounded agency inside the document, determinism outside**: hashes, state, layout and the page
contract are pure code; the judgment lives in one agent per document that can use tools when — and
only when — they change the outcome.

## Flow per document

```
raw file ─▶ deterministic converter ─▶ agentic processor ─▶ deterministic verifier ─▶ page on disk
            (pdftotext / openpyxl /     (judges + writes;      (traces every figure     (frontmatter
             python-docx / gotenberg)    tools: read_more,      to the source; can       contract, entity-
                                         ocr; hard budgets)     trigger 1 retry)         derived folder)
```

1. **Extract** (`converters.py`): routed by extension. PDFs via `pdftotext -layout`; sheets
   (xlsx/xls/csv/tsv) become a compact per-tab profile (dimensions + 25 sample rows — the full
   grid stays in the source); docx via python-docx; legacy Office and `.ods` via the Gotenberg
   sidecar (→ PDF → text); plain text as-is.
2. **Resolve the entity** (`entity.py`): from the source *path*, not the LLM. Conventions are
   configurable (JSON via `CLEAN_CONVENTIONS`); defaults recognize `Portfolio|Clients|Projects/<N>.
   <Name>[ - status]` (tracked) and `Pipeline|Dealflow/<Stage>/<Name>` (prospect). Two passes: a
   high-confidence catalog over the whole inventory, then a recovery pass that matches known slugs
   under non-standard folders.
3. **Process** (`agents.py`, `tools.py`, `schemas.py`): one PydanticAI agent per document decides
   `extraction_quality`, `representation` (`full` | `digest` | `minimal`), metadata and writes
   `body_markdown` — structured outputs, zero parsing fragility. The prompt carries the first 16k
   chars of the extraction; the agent has two tools:
   - `read_more()` — pulls the next chunk when content was cut off mid-document (max 2 calls);
   - `ocr()` — re-reads the original PDF with a vision model when the deterministic text is
     mangled (scan, mojibake, near-empty). One shot; failures degrade to a message so the agent
     can mark the page `manual_review` instead of crashing.

   A clean document costs exactly **1 request**; hard budgets (`RUN_LIMITS`: 6 requests, 4 tool
   calls per attempt) cap the worst case. `manual_review` now means "even with the tools, no
   usable content".
4. **Verify** (`verify.py`): the trust layer, two deterministic checks. **Presence**: every
   numeric token in the body is traced back to what the agent could see — the full extraction
   plus the OCR transcription when it escalated — with generous matching (separators, magnitude
   suffixes, currency, percent). **Period anchoring**: a figure the page ties to a date/month/
   quarter must be compatible with the period the source's own line gives it — a real figure
   attributed to the wrong month is flagged (`unanchored_numbers`), which presence alone cannot
   see. If the verdict is `failed` — or any figure is unanchored — the **generator-judge loop**
   fires: one corrective retry whose prompt carries the verifier's findings; the retry wins only
   if it measurably improves. Verdict, problem figures, per-figure source spans and provenance
   land in the frontmatter, the state and the pass stats. See
   [ADR 002](../decisions/002-deterministic-verification.md) (incl. the 2026-07 amendment) and
   [ADR 003](../decisions/003-bounded-agency-worker.md).
5. **Write** (`page.py`): frontmatter + body under `entities/<slug>/`, `prospects/<slug>/`,
   `units/<unit>/` or `general/`, named `slug(filename)-<sha1(id)[:6]>.md` (stable + unique).
   Pages produced through OCR carry `extraction_method: vision` + `ocr_model` (auditable).
   Contract: [brain-page-contract.md](brain-page-contract.md).
6. **Facts** (`facts.py`): a second bounded agent maps sheet grids — and, quote-anchored, prose
   documents — to typed metric observations; a deterministic validator re-reads every claimed
   cell/quote and only literal matches land in the facts store (`brain-facts/`: SQLite + JSONL,
   `source_ref` per number). The agent judges, the source decides — see [facts.md](facts.md)
   and [ADR 005](../decisions/005-facts-layer.md).
7. **Time** (`versions.py` + `verify.provable_as_of`): every page carries `as_of` — the content's
   validity time at the finest granularity the evidence proves (LLM proposes, code verifies,
   entity path period as fallback). After the pass, near-duplicate revisions are detected
   (deterministic name+content gates → a bounded version-judge agent → `supersedes:` /
   `superseded_by:` frontmatter + state). See [ADR 006](../decisions/006-time-semantics.md).
8. **Dossiers** (`dossiers.py`): per-entity rollups in `brain-dossiers/`, regenerated only when
   the entity's member set changes (deterministic hash gate). An agent writes from bounded tools
   (member pages + the entity's verified facts, superseded material flagged as history); the
   page verifier judges the result and the verdict lands in the dossier's frontmatter. See
   [ADR 009](../decisions/009-dossiers.md).

## Orchestration (`main.py`)

- Pending = new / hash-changed / previous-error / deleted (from `raw/_state.json` vs
  `clean-state.json`). Deleted sources get their page **removed from brain-md** and their state
  entry marked `deleted` — deletions propagate end to end (Drive → raw → brain-md).
- Exact-content dedup: a pending doc whose sha256 matches an already-processed file (or another
  doc in the same pass) is marked `duplicate` with `duplicateOf: <canonical id>` — no LLM call,
  no page. Deterministic: existing pages win; within a pass, lowest file id.
- Concurrency via a semaphore (`CLEAN_MAX_CONCURRENT`); state saved after every doc (atomic).
- Rate limits: individual errors are recorded and retried next pass; a *persistent* 429 trips a
  circuit breaker that aborts the pass leaving remaining docs pending (relaunch to resume).
- Pass stats surface the agency: `ocr_docs`, `verify_retries`, `verify_verified/partial/failed`,
  `verify_unanchored`, `facts_kept`/`facts_rejected`, plus `VERIFY FAILED` / `VERIFY UNANCHORED` /
  `FACTS REJECTED` log lines for triage; each result carries an `agent_trace`
  (`["read_more x1", "ocr", "verifier-retry"]`) — every autonomous decision is recorded in state.
- Before each pass the processor loads the **playbook** — the supervisor-distilled, size-capped
  advisory memory ([ops.md](ops.md)); `CLEAN_TOKEN_BUDGET` gives the pass a hard spend ceiling
  with the same clean-abort semantics as the rate-limit breaker.
- `--once` single pass; default loops every `CLEAN_INTERVAL_SECONDS`.

## ENV

| Var | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | — | required; fail-fast if missing |
| `CLEAN_LLM` | `openai` | `openai` · `fake` (offline) · `fake-flawed` (offline + one seeded hallucination and one seeded period misattribution, for demos/evals of the judge loop) |
| `CLEAN_MODEL` | `gpt-5.4` | bare = OpenAI Responses; or any provider-prefixed pydantic-ai string (`anthropic:claude-sonnet-4-5`, `google-gla:gemini-2.5-pro`, ...) |
| `CLEAN_REASONING_EFFORT` | `medium` | `minimal`\|`low`\|`medium`\|`high` |
| `CLEAN_DRY_RUN` | `true` | safe no-op until explicitly `false` |
| `CLEAN_MAX_CONCURRENT` | `4` | parallel docs |
| `CLEAN_MAX_DOCS` | `0` | bound a run (0 = unlimited) |
| `CLEAN_INTERVAL_SECONDS` | `300` | loop cadence |
| `CLEAN_CONVENTIONS` | built-ins | path to a JSON overriding entity conventions |
| `CLEAN_TOKEN_BUDGET` | `0` | hard per-pass token ceiling (0 = uncapped); pass pauses cleanly when hit |
| `CLEAN_PLAYBOOK` | `on` | `off` disables injecting the supervisor-distilled playbook |
| `CLEAN_FACTS` | `on` | `off` disables the typed numeric facts layer ([facts.md](facts.md)) |
| `CLEAN_FACTS_PROSE` | `on` | `off` disables prose facts (sheets keep working) |
| `CLEAN_VERSIONS` | `on` | `off` disables near-duplicate version detection (the supersedes chain) |
| `CLEAN_DOSSIERS` | `on` | `off` disables per-entity dossier regeneration |
| `BRAIN_DOSSIERS_DIR` | `/data/brain-dossiers` | dossier layer location |
| `CLEAN_ACL` | — | audience-mapping JSON (`acl.py`): path/unit/kind → audiences; empty = open corpus |
| `BRAIN_FACTS_DIR` | `/data/brain-facts` | facts store location (facts.db + facts.jsonl) |
| `CLEAN_TRACE` | — | `logfire` = OpenTelemetry tracing of every agent run (optional dep) |
| `GEMINI_API_KEY` | — | enables the agent's `ocr()` tool; without it the tool degrades gracefully |
| `VISION_MODEL` | `gemini-3-flash-preview` | OCR model behind the tool |
| `GOTENBERG_URL` | `http://gotenberg:3000` | Office converter sidecar |
