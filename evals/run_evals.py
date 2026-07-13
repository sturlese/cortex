#!/usr/bin/env python3
"""Offline golden evals — quality measured, not assumed.

Tests answer "does the code do what the code says"; evals answer "does the SYSTEM produce the
quality we promised". This harness runs the whole pipeline over the fictional corpus and scores
it against evals/golden.json:

  curation   — taxonomy types/verdicts + dedup allowlist over every corpus file
  placement  — every page lands in the entity-derived folder with the expected frontmatter
  trust      — the seeded hallucination (fake-flawed backend) is CAUGHT and CORRECTED, and the
               verifier raises zero false positives on the faithful pages
  graph      — mention canonicalization yields exactly the expected entity nodes

Everything is deterministic (content-derived ids, offline backend), so targets are exact and any
drift is a real regression — which is why CI runs this on every push. To eval a REAL model against
the same golden set: CLEAN_LLM=openai OPENAI_API_KEY=... python evals/run_evals.py (placement and
graph metrics stay exact; trust metrics then measure the live model).
"""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("pipeline/clean/src", "pipeline/graph/src", "pipeline/corpus/src"):
    sys.path.insert(0, str(ROOT / rel))

OUT = ROOT / "evals" / "out"
GOLDEN = json.loads((ROOT / "evals" / "golden.json").read_text())
CORPUS = ROOT / "examples" / "demo-corpus"

RESULTS: list[tuple[str, str, bool]] = []


def metric(name: str, result: str, passed: bool) -> None:
    RESULTS.append((name, result, passed))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def eval_curation(work: Path) -> None:
    from corpus.stages import build_inventory, classify_files, curate_manifest, enumerate_files, trim_manifest
    enumerate_files.run_stage(str(CORPUS), str(work))
    classify_files.run_stage(str(work))
    curate_manifest.run_stage(str(work))
    trim_manifest.run_stage(str(work))
    build_inventory.run_stage(str(work))

    rows = {r["path"]: r for r in _jsonl(work / "classification.jsonl")}
    golden = GOLDEN["taxonomy"]
    hits = sum(1 for p, exp in golden.items()
               if rows.get(p, {}).get("type") == exp["type"] and rows.get(p, {}).get("verdict") == exp["verdict"])
    metric("curation: taxonomy type+verdict", f"{hits}/{len(golden)}", hits == len(golden))

    kept = sorted(r["path"] for r in _jsonl(work / "manifest.jsonl"))
    metric("curation: dedup + allowlist", f"{len(kept)} kept", kept == sorted(GOLDEN["manifest"]))


def eval_clean_and_trust(work: Path, raw: Path, brain: Path, state_dir: Path, facts_dir: Path) -> dict:
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CORPUS, raw, dirs_exist_ok=True)
    shutil.copy(work / "inventory.json", raw / "_state.json")
    os.environ.setdefault("CLEAN_LLM", "fake-flawed")   # backend selection; everything else is explicit
    from clean.main import run_once
    from clean.settings import Settings
    cfg = Settings(raw_dir=str(raw), brain_md_dir=str(brain), state_dir=str(state_dir),
                   facts_dir=str(facts_dir), dry_run=False)
    stats = asyncio.run(run_once(cfg))
    metric("clean: pass completes", f"{stats.get('processed', 0)} processed, {stats.get('errors', 0)} errors",
           stats.get("errors", 0) == 0)

    pages = sorted(str(p.relative_to(brain)) for p in brain.rglob("*.md"))
    metric("placement: pages at expected paths", f"{len(pages)}/{len(GOLDEN['pages'])}",
           pages == sorted(GOLDEN["pages"].keys()))

    fm_hits, fm_total = 0, 0
    for rel, expectations in GOLDEN["pages"].items():
        try:
            text = (brain / rel).read_text()
        except FileNotFoundError:
            fm_total += len(expectations)
            continue
        for expected in expectations:
            fm_total += 1
            fm_hits += expected in text
    metric("placement: frontmatter contract", f"{fm_hits}/{fm_total}", fm_hits == fm_total)

    files = json.loads((state_dir / "clean-state.json").read_text())["files"]
    processed = {fid: f for fid, f in files.items() if f.get("status") == "processed"}
    seeded = [f for f in processed.values() if (f.get("lastResult") or {}).get("retried")]
    if os.environ.get("CLEAN_LLM", "fake-flawed") == "fake-flawed":
        # the offline demo backend deliberately injects one defect per trust check — an invented
        # figure (presence) and a real figure tied to the wrong month (period anchoring): each
        # must be caught by the verifier and corrected by the judge loop.
        def _seeded(doc_key):
            f = next((s for s in seeded if GOLDEN[doc_key] in s.get("name", "")), None)
            last = (f or {}).get("lastResult") or {}
            return (f is not None and last.get("verification") == "verified"
                    and not last.get("unverified_numbers") and not last.get("unanchored_numbers"))
        metric("trust: seeded hallucination caught + corrected",
               f"{len(seeded)} retries", _seeded("seeded_hallucination_doc") and len(seeded) == 2)
        metric("trust: seeded misattribution caught + corrected",
               "period anchored", _seeded("seeded_misattribution_doc"))
    else:
        # a real (or plain-fake) model has nothing seeded to catch; require instead that the loop
        # left no page in a failed verdict — so this metric is meaningful for a live-model eval.
        failed = [f for f in processed.values()
                  if (f.get("lastResult") or {}).get("verification") == "failed"]
        metric("trust: no unresolved verification failures", f"{len(failed)} failed", not failed)

    false_pos = [f["name"] for f in processed.values()
                 if (f.get("lastResult") or {}).get("verification") != "verified"
                 or (f.get("lastResult") or {}).get("unverified_numbers")
                 or (f.get("lastResult") or {}).get("unanchored_numbers")]
    metric("trust: zero false positives on faithful pages", f"{len(false_pos)} flagged", not false_pos)
    return stats


def eval_facts(facts_dir: Path, stats: dict) -> None:
    from clean.factstore import query_facts
    golden = GOLDEN["facts"]
    rows = query_facts(str(facts_dir), limit=500)
    metric("facts: verified observations in the store", f"{len(rows)} rows",
           len(rows) == golden["total"])
    hits = 0
    for g in golden["spot_checks"]:
        got = query_facts(str(facts_dir), metric=g["metric"], entity=g["entity"], period=g["period"])
        hits += any(r["value_raw"] == g["value_raw"] for r in got)
    metric("facts: exact value+period spot-checks", f"{hits}/{len(golden['spot_checks'])}",
           hits == len(golden["spot_checks"]))
    # the fake-flawed backend seeds one observation whose value is NOT in its cell: the
    # deterministic validator must reject it and it must never reach the store.
    bad = query_facts(str(facts_dir), metric="seeded-bad-value")
    metric("facts: seeded bad value rejected by the validator",
           f"{stats.get('facts_rejected', 0)} rejected", not bad and stats.get("facts_rejected") == 1)


def eval_graph(brain: Path, graphed: Path) -> None:
    from graph.build import build_graph
    stats = build_graph(str(brain), str(graphed), min_mentions=2)
    node_ok = (graphed / GOLDEN["graph"]["node"]).exists()
    metric("graph: canonical entity nodes", f"{stats['entities']} entities",
           stats["entities"] == GOLDEN["graph"]["entities_total"] and node_ok)


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    work, raw, brain, state_dir, graphed, facts_dir = (
        OUT / d for d in ("work", "raw", "brain-md", "state", "graphed", "facts"))
    work.mkdir(parents=True)

    eval_curation(work)
    stats = eval_clean_and_trust(work, raw, brain, state_dir, facts_dir)
    eval_facts(facts_dir, stats)
    eval_graph(brain, graphed)

    width = max(len(n) for n, _, _ in RESULTS)
    lines = ["# Eval scorecard", "", "| Metric | Result | Pass |", "|---|---|---|"]
    print()
    for name, result, passed in RESULTS:
        mark = "PASS" if passed else "FAIL"
        print(f"  {name.ljust(width)}  {result.ljust(14)}  {mark}")
        lines.append(f"| {name} | {result} | {'✅' if passed else '❌'} |")
    (OUT / "scorecard.md").write_text("\n".join(lines) + "\n")
    failed = [n for n, _, p in RESULTS if not p]
    print(f"\n  scorecard: {len(RESULTS) - len(failed)}/{len(RESULTS)} metrics green"
          f" -> {OUT / 'scorecard.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
