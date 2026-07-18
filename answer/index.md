# answer — the serving half (verified answers over MCP)

Narrative doc: [`docs/answer.md`](../docs/answer.md) · design rationale:
[ADR 007](../docs/decisions/007-answer-layer.md). This file is the code map.

## Purpose

Serves the pipeline's guarantees to MCP clients: contract-enforcing retrieval over `brain-md`,
exact numbers from `brain-facts`, and an answering agent judged by a **deterministic answer
verifier** before anything leaves the server. Read-only: it never writes the corpus.

## Key entry points

| Entry | File | Notes |
|---|---|---|
| MCP server | `src/answer/mcp_server.py` (`main`, `build_mcp`) | `python -m answer.mcp_server [--transport http --port 3141]` |
| serving core | `src/answer/service.py` (`AnswerService`) | transport-agnostic; all contract enforcement lives here |
| runtime config | `src/answer/settings.py` (`Settings.from_env`) | `ANSWER_*`, `BRAIN_MD_DIR`, `BRAIN_FACTS_DIR` |

MCP tools exposed: `ask_brain`, `search_brain`, `query_metrics`, `read_page`.

## Module map

- `index.py` — SQLite FTS5 index over `brain-md` (`connect`, `refresh`, `get_page`,
  `superseded_paths`, `visible`, `split_frontmatter`). Fully regenerable.
- `retrieve.py` — `search()`: BM25 plus explainable contract adjustments (superseded / failed /
  manual-review / partial penalties; entity, period and freshness boosts). Constants at the top
  of the file are the tuning surface.
- `metrics.py` — exact facts lookups (`query_metrics`, `known_metrics`, `annotate_superseded`).
- `synthesize.py` — the answering agent: `AnswerOutput`/`Citation` schemas, `SynthesisContext`
  (tracks `read_paths` + evidence), `ANSWER_LIMITS`, `build_synthesizer`, `FakeSynthesizer`.
- `verify_answer.py` — the deterministic judge: `verify()` (figures traced to this run's
  evidence), `check_citations()` (quotes must appear verbatim in a page actually read),
  `feedback()` for the corrective retry.
- `numbers.py` — number canonicalization for the verifier (`interpretations`, `number_pool`,
  `unverified_figures`).
- `service.py` — wires the above into `search` / `query_metrics` / `read_page` / `ask`.

## Use these

- `AnswerService` for any new transport or client — do not reimplement enforcement in an adapter.
- `service.get_page` / `index.visible` — the ACL scope (`ANSWER_AUDIENCES`) must filter **every**
  read path; out-of-scope pages must look non-existent, not forbidden.
- `service.current_metric_rows` — current-truth preference over superseded rows.
- `service.match_metric` — tolerant metric-id matching ("the ARR (usd)" → `arr-usd`).
- `numbers.unverified_figures` — the shared figure check; do not write a second number parser.

## Avoid / anti-patterns

- Do not return an answer that skipped `verify_answer.verify` — the verdict ships with the answer.
- Do not put ranking or ACL logic in `mcp_server.py`; it is a thin skin over the service.
- Do not let page bodies reach the model unfenced: `service.page_text` wraps them in
  `<<<UNTRUSTED-DATA … >>>`. Content is data, never instructions.
- Do not answer numeric questions from page prose when the facts store has the metric — that is
  what `query_metrics` and `detail_in_source` exist for.
- Do not make refusal a failure mode: `refused` is a first-class, correct outcome.
- Do not write to `brain-md` / `brain-facts` from this package (they are read-only mounts).

## Data & contracts

- Consumes the page frontmatter contract
  ([`docs/pipeline/brain-page-contract.md`](../docs/pipeline/brain-page-contract.md)) — the
  `superseded_by`, `verification`, `acl`, `as_of`, `detail_in_source` fields drive ranking.
- Consumes `facts.db` (schema owned by `clean/factstore.py`) — parity is covered by the evals.
- Produces `answer-index.db` in `ANSWER_STATE_DIR` (regenerable; delete to rebuild).
- Answer payload shape: see `AnswerService.ask` (`refused`, `answer`, `citations`, `confidence`,
  `verification`, `retried`).

## Tests

`tests/` — `test_index_retrieve.py` (indexing + ranking), `test_verify_and_metrics.py` (the
answer verifier + facts queries), `test_service_ask.py` (the full loop with the fake
synthesizer), `test_acl_enforcement.py`, `test_mcp_adapter.py`, shared fixtures in `conftest.py`.
Run from this directory: `pytest -q`. End-to-end golden Q&A lives in [`../evals/`](../evals/)
and [`../benchmark/`](../benchmark/).

## Common tasks

| Task | Touch |
|---|---|
| Re-tune ranking | the penalty/boost constants in `retrieve.py` + `test_index_retrieve.py` |
| New MCP tool | `mcp_server.build_mcp` + a method on `AnswerService` |
| Stricter answer checks | `verify_answer.py` (+ `feedback` so the retry can act on it) |
| Index a new frontmatter field | `index.py` `_SCHEMA` + `refresh` (then delete the index db) |
| Offline testing | `ANSWER_LLM=fake` → `synthesize.FakeSynthesizer` |

## Notes

- One server instance = one ACL scope; multi-tenant means one instance per audience set.
- `ANSWER_BEARER_TOKEN` adds a static-token gate on the HTTP transport only; TLS/ingress is yours.
- `service.refresh()` re-scans the corpus on each `ask`/`search` call — cheap because the index
  keys off file state, but it is the place to look if serving latency grows with corpus size.
