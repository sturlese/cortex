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
| slack — second source connector | [pipeline/slack.md](pipeline/slack.md) |
| ops — the supervisor agent | [pipeline/ops.md](pipeline/ops.md) |
| Page frontmatter contract | [pipeline/brain-page-contract.md](pipeline/brain-page-contract.md) |
| Answer server (contract-enforcing MCP) | [answer.md](answer.md) |
| Brain server (gbrain) deploy & setup | [gbrain/README.md](gbrain/README.md) |
| OAuth clients (Claude / ChatGPT / agents) | [gbrain/oauth-clients.md](gbrain/oauth-clients.md) |
| Deploying both stacks | [operations/deploy.md](operations/deploy.md) |
| Runbook (real gotchas + fixes) | [operations/runbook.md](operations/runbook.md) |
| Evals — golden scorecard | [../evals/README.md](../evals/README.md) |
| The cortex benchmark | [../benchmark/README.md](../benchmark/README.md) |
| Design decisions (ADRs) | [decisions/](decisions/001-no-ddd-refactor.md) |

## Module maps (for agents and newcomers)

The docs above explain *how the system works*. The `index.md` next to each package is the
**code map**: entry points, what to reuse, what not to do, where the tests are.

| Package | Map |
|---|---|
| Ingestion stack (routing overview) | [../pipeline/index.md](../pipeline/index.md) |
| clean — workers, facts, versions, dossiers, ops | [../pipeline/clean/index.md](../pipeline/clean/index.md) |
| fetch — Drive mirror | [../pipeline/fetch/index.md](../pipeline/fetch/index.md) |
| slack — second source connector | [../pipeline/slack/index.md](../pipeline/slack/index.md) |
| corpus — offline curation | [../pipeline/corpus/index.md](../pipeline/corpus/index.md) |
| graph — entity graph | [../pipeline/graph/index.md](../pipeline/graph/index.md) |
| answer — the serving half | [../answer/index.md](../answer/index.md) |
| evals — the golden scorecard | [../evals/index.md](../evals/index.md) |
| benchmark — capability vs. ground truth | [../benchmark/index.md](../benchmark/index.md) |

Documentation is validated against the code; when they disagree, **the code wins** — then fix the doc.
