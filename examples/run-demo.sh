#!/usr/bin/env bash
# End-to-end demo over the fictional corpus in examples/demo-corpus — NO API keys needed.
#
#   corpus (curate + inventory)  ->  clean (offline fake LLM)  ->  graph (entity wikilinks)
#
# The clean stage runs with CLEAN_LLM=fake: a deterministic heuristic that mimics the real
# agent's output shape. Swap in CLEAN_LLM=openai + OPENAI_API_KEY to see real pages.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/examples/out"
VENV="$ROOT/examples/.venv"        # OUTSIDE $OUT so it survives the clean below (truly one-time)
PY="$VENV/bin/python"

rm -rf "$OUT"
mkdir -p "$OUT"

# Bootstrap the venv once; a `.deps-ok` stamp (written only after a successful install) means an
# interrupted install is retried instead of leaving a broken, deps-less venv behind.
if [ ! -f "$VENV/.deps-ok" ]; then
  echo "==> [0/5] Python env (one-time; ~a minute)"
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install -r "$ROOT/pipeline/clean/requirements.txt" -r "$ROOT/pipeline/graph/requirements.txt"
  touch "$VENV/.deps-ok"
else
  echo "==> [0/5] Python env (cached)"
fi

echo "==> [1/5] corpus: enumerate -> classify -> curate -> trim -> inventory"
export PYTHONPATH="$ROOT/pipeline/corpus/src"
"$PY" -m corpus.cli build-manifest --corpus "$ROOT/examples/demo-corpus" --workdir "$OUT/work"
"$PY" -m corpus.cli build-inventory --workdir "$OUT/work"

echo "==> [2/5] stage the raw dir (mirror + inventory as _state.json)"
mkdir -p "$OUT/raw"
cp -R "$ROOT/examples/demo-corpus/." "$OUT/raw/"
cp "$OUT/work/inventory.json" "$OUT/raw/_state.json"

echo "==> [3/5] clean: raw -> brain-md (offline fake LLM; a SEEDED hallucination + a SEEDED misattribution to watch the loop work)"
CLEAN_LLM=fake-flawed CLEAN_DRY_RUN=false \
RAW_DIR="$OUT/raw" BRAIN_MD_DIR="$OUT/brain-md" CLEAN_STATE_DIR="$OUT/state" \
BRAIN_FACTS_DIR="$OUT/brain-facts" \
PYTHONPATH="$ROOT/pipeline/clean/src" "$PY" -m clean.main --once

echo "==> [4/5] graph: brain-md -> brain-md-graphed (entity nodes + wikilinks)"
PYTHONPATH="$ROOT/pipeline/graph/src" "$PY" -m graph.cli \
  --in "$OUT/brain-md" --out "$OUT/brain-md-graphed" --min-mentions 2

echo "==> [5/5] ops: the supervisor inspects the run and writes its report"
CLEAN_LLM=fake \
RAW_DIR="$OUT/raw" BRAIN_MD_DIR="$OUT/brain-md" CLEAN_STATE_DIR="$OUT/state" \
PYTHONPATH="$ROOT/pipeline/clean/src" "$PY" -m clean.ops > /dev/null

echo
echo "Done. Look around:"
echo "  supervisor report    examples/out/state/ops-report.md (health, findings, recommendations)"
echo "  curation artifacts   examples/out/work/            (classification matrix, manifest, inventory)"
echo "  brain pages          examples/out/brain-md/        (entities/ prospects/ units/ general/)"
echo "  graphed layer        examples/out/brain-md-graphed/ (entity nodes + '## Related entities')"
echo
echo "Things to notice:"
echo "  - the NDA, the invoice and style.css never became pages (taxonomy verdict OUT)"
echo "  - the duplicated quarterly report yields ONE page (md5 dedup in corpus)"
echo "  - Globex landed under entities/globex with status: won; Hooli under prospects/hooli"
echo "  - the CSV page is a digest with detail_in_source: true — AND its numbers became typed,"
echo "    cell-verified facts: examples/out/brain-facts/facts.jsonl (metric/period/value/source_ref)."
echo "    The fake backend also proposed one observation with a WRONG value; the deterministic"
echo "    validator rejected it (see 'FACTS REJECTED' above) — the grid decides, not the model"
echo "  - every page carries 'verification: verified' — the trust layer traced each figure"
echo "    in the body back to the source text (deterministic, no LLM)"
echo "  - THE CONTROL LOOP, LIVE: the fake backend deliberately invented two figures in the"
echo "    quarterly report AND tied a real KPI figure to the wrong month; the verifier caught"
echo "    both (presence check + period anchoring) and the judge loop corrected the pages —"
echo "    look for '· self-corrected' in the clean log above and verify_retries=2 in the stats"
echo "  - the supervisor read the run's telemetry, ran SAMPLED CLAIM CHECKS (each paragraph"
echo "    anchored to its source window and judged) and wrote ops-report.md (health + findings)"
find "$OUT/brain-md" -name '*.md' | sed "s|$OUT/|  |" | sort