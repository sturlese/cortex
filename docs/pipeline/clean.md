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
   (xlsx/xls/csv/tsv/ods) become a compact per-tab profile (dimensions + 25 sample rows — the full
   grid stays in the source); docx via python-docx; legacy Office via the Gotenberg sidecar
   (→ PDF → text); plain text as-is.
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
4. **Verify** (`verify.py`): the trust layer. Every numeric token in the body is traced back to
   what the agent could see — the full extraction plus the OCR transcription when it escalated —
   with generous, deterministic matching (separators, magnitude suffixes, currency, percent).
   If the verdict is `failed`, the **generator-judge loop** fires: one corrective retry whose
   prompt carries the verifier's findings ("these figures are not in the source — fix or drop
   them"); the retry wins only if it improves. Verdict, unverified figures and provenance land in
   the frontmatter, the state and the pass stats. See
   [ADR 002](../decisions/002-deterministic-verification.md) and
   [ADR 003](../decisions/003-bounded-agency-worker.md).
5. **Write** (`page.py`): frontmatter + body under `entities/<slug>/`, `prospects/<slug>/`,
   `units/<unit>/` or `general/`, named `slug(filename)-<sha1(id)[:6]>.md` (stable + unique).
   Pages produced through OCR carry `extraction_method: vision` + `ocr_model` (auditable).
   Contract: [brain-page-contract.md](brain-page-contract.md).

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
  plus `VERIFY FAILED` log lines for triage; each result carries an `agent_trace`
  (`["read_more x1", "ocr", "verifier-retry"]`) — every autonomous decision is recorded in state.
- Before each pass the processor loads the **playbook** — the supervisor-distilled, size-capped
  advisory memory ([ops.md](ops.md)); `CLEAN_TOKEN_BUDGET` gives the pass a hard spend ceiling
  with the same clean-abort semantics as the rate-limit breaker.
- `--once` single pass; default loops every `CLEAN_INTERVAL_SECONDS`.

## ENV

| Var | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | — | required; fail-fast if missing |
| `CLEAN_LLM` | `openai` | `openai` · `fake` (offline) · `fake-flawed` (offline + one seeded hallucination, for demos/evals of the judge loop) |
| `CLEAN_MODEL` | `gpt-5.4` | bare = OpenAI Responses; or any provider-prefixed pydantic-ai string (`anthropic:claude-sonnet-4-5`, `google-gla:gemini-2.5-pro`, ...) |
| `CLEAN_REASONING_EFFORT` | `medium` | `minimal`\|`low`\|`medium`\|`high` |
| `CLEAN_DRY_RUN` | `true` | safe no-op until explicitly `false` |
| `CLEAN_MAX_CONCURRENT` | `4` | parallel docs |
| `CLEAN_MAX_DOCS` | `0` | bound a run (0 = unlimited) |
| `CLEAN_INTERVAL_SECONDS` | `300` | loop cadence |
| `CLEAN_CONVENTIONS` | built-ins | path to a JSON overriding entity conventions |
| `CLEAN_TOKEN_BUDGET` | `0` | hard per-pass token ceiling (0 = uncapped); pass pauses cleanly when hit |
| `CLEAN_PLAYBOOK` | `on` | `off` disables injecting the supervisor-distilled playbook |
| `CLEAN_TRACE` | — | `logfire` = OpenTelemetry tracing of every agent run (optional dep) |
| `GEMINI_API_KEY` | — | enables the agent's `ocr()` tool; without it the tool degrades gracefully |
| `VISION_MODEL` | `gemini-3-flash-preview` | OCR model behind the tool |
| `GOTENBERG_URL` | `http://gotenberg:3000` | Office converter sidecar |
