# answer — the serving half with the pipeline's guarantees

`answer/`. A first-party MCP server over `brain-md` + `brain-facts` that **enforces the page
contract server-side**. Design record: [ADR 007](decisions/007-answer-layer.md).

```
question ─▶ answering agent ─▶ deterministic ANSWER verifier ─▶ answer + verdict
            (tools: search /     (every figure traced to this      (verified | partial | failed,
             read_page /          run's tool evidence; every        citations, retried flag)
             query_metrics)       citation quoted verbatim;
                                  1 corrective retry)
```

## The four tools (MCP)

| Tool | What it does |
|---|---|
| `ask_brain(question)` | the full loop above; refuses when the brain doesn't contain the answer |
| `search_brain(query)` | contract-aware ranked hits: superseded/failed/manual_review demoted, entity/period/freshness boosted — each hit lists the factors applied |
| `query_metrics(metric, entity, period)` | exact values from the verified facts store, with the source cell/quote reference; rows from superseded pages are flagged |
| `read_page(path)` | one page, trust signals first, body fenced as untrusted data |

## Run

```bash
# local MCP client (Claude Desktop, IDEs): stdio
BRAIN_MD_DIR=... BRAIN_FACTS_DIR=... ANSWER_STATE_DIR=... \
  PYTHONPATH=answer/src python -m answer.mcp_server

# deployment: the compose stack (reads the shared volumes; front it with your ingress)
cd answer && docker compose up -d
```

## ENV

| Var | Default | Meaning |
|---|---|---|
| `BRAIN_MD_DIR` | `/data/brain-md` | the corpus (read-only) |
| `BRAIN_FACTS_DIR` | `/data/brain-facts` | the facts store (read-only) |
| `ANSWER_STATE_DIR` | `/data/state` | the FTS index (fully regenerable) |
| `ANSWER_LLM` | `openai` | `fake` = offline deterministic answering (demo/CI) |
| `ANSWER_MODEL` | `gpt-5.4` | synthesizer model (`OPENAI_API_KEY` required for `openai`) |
| `ANSWER_BEARER_TOKEN` | — | when set, the HTTP transport requires `Authorization: Bearer <token>` |

## Trust model

- Two read-only mounts in; one regenerable index out. No database, no Drive access, no keys
  unless the real model is enabled — the airgap doctrine extended to serving.
- The verifier's evidence corpus is **what the tools returned this run**: an invented figure
  cannot be laundered by a lucky match elsewhere in the corpus.
- Refusals are first-class and vacuously verified: no evidence, no answer, no hallucination.

## gbrain vs answer

Both serve the same `brain-md` volume and neither writes it. [gbrain](gbrain/README.md) brings
embeddings + pgvector (semantic recall, multi-user OAuth); `answer` brings the guarantees
(contract-aware ranking, exact facts, verified answers). Run either or both.
