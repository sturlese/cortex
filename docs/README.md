# Documentation

Start with the [architecture](architecture.md), then dive into the piece you're touching.

| Area | Doc |
|---|---|
| System architecture & data flow | [architecture.md](architecture.md) |
| Ingestion pipeline (overview + operations) | [pipeline/README.md](pipeline/README.md) |
| fetch — Drive mirror | [pipeline/fetch.md](pipeline/fetch.md) |
| clean — LLM cleaning stage | [pipeline/clean.md](pipeline/clean.md) |
| facts — typed numeric layer | [pipeline/facts.md](pipeline/facts.md) |
| graph — entity graph layer | [pipeline/graph.md](pipeline/graph.md) |
| corpus — offline curation | [pipeline/corpus.md](pipeline/corpus.md) |
| ops — the supervisor agent | [pipeline/ops.md](pipeline/ops.md) |
| Page frontmatter contract | [pipeline/brain-page-contract.md](pipeline/brain-page-contract.md) |
| Brain server (gbrain) deploy & setup | [gbrain/README.md](gbrain/README.md) |
| OAuth clients (Claude / ChatGPT / agents) | [gbrain/oauth-clients.md](gbrain/oauth-clients.md) |
| Deploying both stacks | [operations/deploy.md](operations/deploy.md) |
| Runbook (real gotchas + fixes) | [operations/runbook.md](operations/runbook.md) |
| Evals — golden scorecard | [../evals/README.md](../evals/README.md) |
| Design decisions (ADRs) | [decisions/](decisions/001-no-ddd-refactor.md) |

Documentation is validated against the code; when they disagree, **the code wins** — then fix the doc.
