# Evals — quality measured, not assumed

Tests answer *"does the code do what the code says"*. Evals answer *"does the **system** produce
the quality we promised"*. This harness runs the entire pipeline over the fictional corpus in
[`examples/demo-corpus`](../examples/demo-corpus) and scores it against [`golden.json`](golden.json):

| Dimension | What is scored |
|---|---|
| **curation** | taxonomy type + verdict for every corpus file; md5 dedup + allowlist |
| **placement** | every page lands in its entity-derived folder with the expected frontmatter |
| **trust** | the **seeded hallucination** (the `fake-flawed` backend invents two figures in one doc, on purpose) and the **seeded misattribution** (a real figure tied to the wrong month in another doc) are both caught by the verifier and corrected by the judge loop — and the verifier raises **zero false positives** on the faithful pages |
| **graph** | mention canonicalization yields exactly the expected entity nodes |

```bash
make eval        # or: bash evals/run-evals.sh
```

Everything is deterministic — content-derived ids, offline backend — so targets are exact and any
drift is a real regression. **CI runs the full scorecard on every push.**

## Evaluating a real model

The same golden set works as a live-model eval: placement/curation/graph metrics stay exact, and
the trust metrics then measure the actual model's faithfulness instead of the seeded failure:

```bash
CLEAN_LLM=openai OPENAI_API_KEY=sk-... evals/.venv/bin/python evals/run_evals.py
```

Extend `golden.json` with your own corpus and expectations before pointing this at production —
the harness is the pattern, the golden set is yours.
