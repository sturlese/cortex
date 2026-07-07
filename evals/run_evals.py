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


def eval_clean_and_trust(work: Path, raw: Path, brain: Path, state_dir: Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CORPUS, raw, dirs_exist_ok=True)
    shutil.copy(work / "inventory.json", raw / "_state.json")
    os.environ.setdefault("CLEAN_LLM", "fake-flawed")   # backend selection; everything else is explicit
    from clean.main import run_once
    from clean.settings import Settings
    cfg = Settings(raw_dir=str(raw), brain_md_dir=str(brain), state_dir=str(state_dir), dry_run=False)
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
    caught = (len(seeded) == 1
              and GOLDEN["seeded_hallucination_doc"] in seeded[0].get("name", "")
              and seeded[0]["lastResult"]["verification"] == "verified")
    metric("trust: seeded hallucination caught + corrected", f"{len(seeded)} retry", caught)

    false_pos = [f["name"] for f in processed.values()
                 if (f.get("lastResult") or {}).get("verification") != "verified"
                 or (f.get("lastResult") or {}).get("unverified_numbers")]
    metric("trust: zero false positives on faithful pages", f"{len(false_pos)} flagged", not false_pos)


def eval_graph(brain: Path, graphed: Path) -> None:
    from graph.build import build_graph
    stats = build_graph(str(brain), str(graphed), min_mentions=2)
    node_ok = (graphed / GOLDEN["graph"]["node"]).exists()
    metric("graph: canonical entity nodes", f"{stats['entities']} entities",
           stats["entities"] == GOLDEN["graph"]["entities_total"] and node_ok)


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    work, raw, brain, state_dir, graphed = (OUT / d for d in ("work", "raw", "brain-md", "state", "graphed"))
    work.mkdir(parents=True)

    eval_curation(work)
    eval_clean_and_trust(work, raw, brain, state_dir)
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
