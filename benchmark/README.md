# The cortex benchmark

Evals guard the golden demo; the benchmark measures **capability**: a synthetic company drive is
generated WITH ground truth (`generate.py` records every planted fact, duplicate, revision, ACL
scope and unanswerable probe), the whole system runs over it, and `run.py` scores what came out
against what went in.

```bash
make benchmark                          # the offline floor (deterministic; thresholds gate)
CLEAN_LLM=openai OPENAI_API_KEY=... \
  python benchmark/src/benchmark/run.py   # the same corpus + ground truth, scored on a real model
```

## Dimensions

| Dimension | What is scored against ground truth |
|---|---|
| `curation` | OUT-class files (NDAs, invoices, web assets) and exact duplicates never survive curation; the kept count is exact |
| `placement` | every planted document lands under its entity's folder |
| `trust` | every produced page leaves `verification: verified` |
| `facts-captured` / `facts-wrong` | every planted grid value is in the facts store with the exact value and period — and **zero** conflicting values are stored |
| `versions` | every planted revision (draft + FINAL with corrected figures) becomes a supersedes chain |
| `dossiers` | every entity gets a verified rollup |
| `graph` | entity nodes materialize from mentions |
| `qa-exact` / `qa-freshness` / `qa-refusal` | auto-generated questions: exact figures answered and cited; revision conflicts resolve to the current value; questions about entities that don't exist are **refused** |
| `acl` | a sales-scoped instance answers what an eng-scoped instance must refuse |

Two tiers: the **floor** (offline fake backends — deterministic, every dimension gates in CI)
and the **model tier** (real backends; thresholds don't gate, the report shows where the model
beats or misses the floor). The corpus plants model-tier probes too — e.g. a Spanish memo with
dot-grouped/decimal-comma figures the offline heuristic deliberately skips.

The report lands in `benchmark/out/benchmark-report.md` (+ `report.json`). The benchmark has
already paid for itself: its refusal probe caught the answer layer serving another entity's data
for a metric question about an unknown entity — fixed with a regression test in the same change.
