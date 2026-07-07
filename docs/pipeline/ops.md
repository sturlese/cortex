# ops — the supervisor agent

`pipeline/clean/src/clean/ops.py`. A second level of agency: workers process documents, the supervisor
watches the **system**. It runs after a pass (or on a schedule), diagnoses, spot-audits, takes
bounded actions and reports to a human. Design record: [ADR 004](../decisions/004-supervisor-and-memory.md).

## What a run looks like

```
telemetry ──▶ diagnosis ──▶ sampled audits ──▶ bounded actions ──▶ ops-report.md
(aggregated    (the agent     (page vs fresh     (requeue ≤20,       (health, findings,
 by code)       interprets)    source extract)    playbook ≤1500c)    actions, asks)
```

Tools (each with a hard budget, every action recorded):

| Tool | What it does | Bound |
|---|---|---|
| `pipeline_status()` | telemetry aggregated by *code*: statuses, verdicts, OCR/retry counts, top errors | read-only |
| `list_pages(kind)` | pages by problem class (`verify_failed`, `manual_review`, `error`…) | 20 rows |
| `audit_page(id)` | the stored page next to a **freshly re-extracted** source — the sampled semantic judge of ADR 002 | 5 per run |
| `requeue(ids, reason)` | mark docs for reprocessing next pass | 20 per run |
| `update_playbook(content)` | distill lessons into the workers' advisory memory | once, ≤1500 chars |

Output is a structured `OpsReport` (health `green/yellow/red`, findings, actions taken,
recommendations *for a human*), rendered to `<state>/ops-report.md`.

## The learning loop

`update_playbook` closes the loop: recurring patterns the supervisor sees (a doc family that
should be digests, sources that always OCR badly) become ≤1500 chars of advisory context the
workers read on the next pass — then it can `requeue` the affected docs so they reprocess *with*
the new guidance. Memory guardrails live in [playbook.py](clean.md#env) and ADR 004: capped,
plain-file, advisory-only, kill switch.

## Run it

```bash
# in the stack, after a pass (profile "ops"):
docker compose --profile ops run --rm ops

# locally / offline (deterministic report, no keys):
CLEAN_LLM=fake CLEAN_STATE_DIR=... RAW_DIR=... BRAIN_MD_DIR=... python -m clean.ops
```

The supervisor mounts `brain-md` **read-only** — it can read every page and write none. Its only
writes are requeue marks, the playbook and its report, all inside the state volume.
