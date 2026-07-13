#!/usr/bin/env python3
"""The cortex benchmark — the whole system scored against a corpus with known ground truth.

Evals guard the golden demo; the benchmark measures CAPABILITY: the generator (generate.py)
plants every fact, duplicate, revision, ACL scope and unanswerable probe, records them in
ground-truth.json, and this runner drives the full loop (curation → clean → facts → versions →
dossiers → graph → answer) and scores what came out against what went in.

Two tiers:
- FLOOR (offline fake backends): deterministic; every dimension must meet its threshold — this
  is what CI runs, and what "the machinery works" means.
- MODEL (CLEAN_LLM=openai …): same corpus, same ground truth, real models; the report shows
  where a model beats or misses the floor (thresholds don't gate).

Usage:  python benchmark/run.py [--out benchmark/out]
"""
import argparse
import asyncio
import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # benchmark/src/benchmark/run.py -> repo root
for rel in ("pipeline/clean/src", "pipeline/graph/src", "pipeline/corpus/src", "answer/src",
            "benchmark/src"):
    sys.path.insert(0, str(ROOT / rel))

from benchmark.generate import generate  # noqa: E402

RESULTS: list[tuple[str, float, float, str]] = []   # (dimension, score, threshold, detail)


def score(dimension: str, value: float, threshold: float, detail: str) -> None:
    RESULTS.append((dimension, value, threshold, detail))


def run(out_dir: Path, gate: bool) -> int:
    from clean.main import run_once
    from clean.settings import Settings
    from graph.build import build_graph

    shutil.rmtree(out_dir, ignore_errors=True)
    corpus = out_dir / "corpus"
    gt = generate(str(corpus))

    # ── curation (corpus stages over the generated drive) ───────────────────
    from corpus.stages import build_inventory, classify_files, curate_manifest, enumerate_files, trim_manifest
    work = out_dir / "work"
    work.mkdir(parents=True)
    enumerate_files.run_stage(str(corpus), str(work))
    classify_files.run_stage(str(work))
    curate_manifest.run_stage(str(work))
    trim_manifest.run_stage(str(work))
    build_inventory.run_stage(str(work))
    kept = {json.loads(line)["path"] for line in (work / "manifest.jsonl").read_text().splitlines()}
    out_leaks = [p for p in gt["out_files"] if p in kept]
    dup_leaks = [p for p in gt["duplicates"] if p in kept]
    curation_ok = (not out_leaks and not dup_leaks and len(kept) == gt["expected_kept_count"])
    score("curation", 1.0 if curation_ok else 0.0, 1.0,
          f"{len(kept)}/{gt['expected_kept_count']} kept · leaks: {out_leaks + dup_leaks or 'none'}")

    # ── one clean pass over the whole corpus (ACL config ON; fake backends unless overridden)
    raw = out_dir / "raw"
    shutil.copytree(corpus, raw)
    (raw / "ground-truth.json").unlink()             # the pipeline must not see the answers
    shutil.copy(work / "inventory.json", raw / "_state.json")
    acl_cfg = out_dir / "acl-config.json"
    acl_cfg.write_text(json.dumps({"default": ["all"], "rules": [
        {"unit": gt["acl"]["unit"], "audiences": gt["acl"]["audiences"]}]}))
    os.environ.setdefault("CLEAN_LLM", "fake")
    brain, facts, dossiers, state = (out_dir / d for d in ("brain-md", "facts", "dossiers", "state"))
    cfg = Settings(raw_dir=str(raw), brain_md_dir=str(brain), state_dir=str(state),
                   facts_dir=str(facts), dossiers_dir=str(dossiers), acl_path=str(acl_cfg),
                   dry_run=False)
    stats = asyncio.run(run_once(cfg))

    # ── placement: every entity's documents under its folder ────────────────
    expected = found = 0
    for c in gt["clients"]:
        for stem in ("quarterly-report", "kpi-metrics", "meeting-notes"):
            expected += 1
            found += any(p.name.startswith(stem) for p in (brain / "entities" / c["slug"]).glob("*.md"))
    for p_ in gt["prospects"]:
        expected += 1
        found += bool(list((brain / "prospects" / p_["slug"]).glob("*.md")))
    score("placement", found / expected, 1.0, f"{found}/{expected} documents under their entity")

    # ── trust: every page leaves verified (floor: faithful backends) ────────
    pages = list(brain.rglob("*.md"))
    verified = sum(1 for p in pages if "verification: verified" in p.read_text())
    score("trust", verified / len(pages) if pages else 0.0, 1.0,
          f"{verified}/{len(pages)} pages verified")

    # ── facts: every planted grid value captured exactly; zero wrong values ─
    from clean.factstore import query_facts
    captured = wrong = 0
    for f in gt["facts"]:
        rows = query_facts(str(facts), metric=f["metric"], entity=f["entity"], period=f["period"])
        exact = [r for r in rows if r["period"] == f["period"]]
        captured += any(r["value_raw"] == f["value_raw"] for r in exact)
        wrong += sum(1 for r in exact if r["value_raw"] != f["value_raw"])
    score("facts-captured", captured / len(gt["facts"]), 1.0, f"{captured}/{len(gt['facts'])} planted values")
    score("facts-wrong", float(wrong), 0.0, f"{wrong} conflicting stored values (must be 0)")

    # ── versions: every planted revision becomes a supersedes chain ─────────
    files = json.loads((state / "clean-state.json").read_text())["files"]
    linked = 0
    for v in gt["versions"]:
        ents = [f for f in files.values()
                if (f.get("lastResult") or {}).get("entity") == v["entity"]]
        linked += any((f.get("lastResult") or {}).get("supersedes") for f in ents)
    score("versions", linked / len(gt["versions"]), 1.0, f"{linked}/{len(gt['versions'])} chains")

    # ── dossiers: one verified rollup per entity ─────────────────────────────
    slugs = [c["slug"] for c in gt["clients"]] + [p_["slug"] for p_ in gt["prospects"]]
    ok = sum(1 for s in slugs if (dossiers / f"{s}.md").exists()
             and "verification: verified" in (dossiers / f"{s}.md").read_text())
    score("dossiers", ok / len(slugs), 1.0, f"{ok}/{len(slugs)} verified rollups")

    # ── graph ────────────────────────────────────────────────────────────────
    gstats = build_graph(str(brain), str(out_dir / "graphed"), min_mentions=2)
    score("graph", 1.0 if gstats["entities"] >= len(gt["clients"]) else 0.0, 1.0,
          f"{gstats['entities']} entity nodes")

    # ── qa: exactness, freshness, refusal — measured at the answer ───────────
    from answer.service import AnswerService
    from answer.settings import Settings as AnswerSettings
    base = AnswerSettings(brain_md_dir=str(brain), facts_dir=str(facts),
                          state_dir=str(out_dir / "answer-state"), llm="fake")
    svc = AnswerService(base)
    by_kind: dict[str, list[bool]] = {}
    for case in gt["qa"]:
        res = asyncio.run(svc.ask(case["q"]))
        if case["kind"] == "refusal":
            ok_ = res["refused"] is True
        else:
            ok_ = (not res["refused"] and case["expect_contains"] in res["answer"]
                   and res["verification"]["verdict"] == "verified"
                   and case.get("expect_absent", "\x00") not in res["answer"])
        by_kind.setdefault(case["kind"], []).append(ok_)
    for kind, oks in sorted(by_kind.items()):
        score(f"qa-{kind}", sum(oks) / len(oks), 1.0, f"{sum(oks)}/{len(oks)} questions")

    # ── acl: scoped instances ────────────────────────────────────────────────
    probe = next(c["q"] for c in gt["qa"] if c["kind"] == "exact")
    sales = asyncio.run(AnswerService(dataclasses.replace(base, audiences=("sales",),
                                                          state_dir=str(out_dir / "as-sales"))).ask(probe))
    eng = asyncio.run(AnswerService(dataclasses.replace(base, audiences=("eng",),
                                                        state_dir=str(out_dir / "as-eng"))).ask(probe))
    acl_ok = (not sales["refused"]) and eng["refused"]
    score("acl", 1.0 if acl_ok else 0.0, 1.0,
          "sales answered, eng refused" if acl_ok else "LEAK")

    # ── report ───────────────────────────────────────────────────────────────
    llm = os.environ.get("CLEAN_LLM", "fake")
    lines = [f"# cortex benchmark — backend: `{llm}`", "",
             "| Dimension | Score | Threshold | Detail |", "|---|---|---|---|"]
    failed = []
    for dim, value, threshold, detail in RESULTS:
        inverted = dim == "facts-wrong"              # lower is better
        ok_ = value <= threshold if inverted else value >= threshold
        mark = "✅" if ok_ else "❌"
        if not ok_:
            failed.append(dim)
        shown = f"{value:.2f}" if not inverted else f"{int(value)}"
        lines.append(f"| {dim} | {shown} {mark} | {threshold} | {detail} |")
    lines += ["", f"pipeline stats: {json.dumps({k: v for k, v in stats.items() if not k.endswith('_tok')})}"]
    report = "\n".join(lines) + "\n"
    (out_dir / "benchmark-report.md").write_text(report)
    (out_dir / "report.json").write_text(json.dumps(
        [{"dimension": d, "score": v, "threshold": t, "detail": x} for d, v, t, x in RESULTS], indent=2))
    print(report)
    if gate and failed:
        print(f"FLOOR GATE FAILED: {failed}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark", description="Run the cortex benchmark.")
    parser.add_argument("--out", default=str(ROOT / "benchmark" / "out"))
    args = parser.parse_args(argv)
    gate = os.environ.get("CLEAN_LLM", "fake").startswith("fake")   # thresholds gate the floor only
    return run(Path(args.out), gate)


if __name__ == "__main__":
    sys.exit(main())
