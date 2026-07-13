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
for rel in ("pipeline/clean/src", "pipeline/graph/src", "pipeline/corpus/src", "answer/src"):
    sys.path.insert(0, str(ROOT / rel))

OUT = ROOT / "evals" / "out"
GOLDEN = json.loads((ROOT / "evals" / "golden.json").read_text())
QA_GOLDEN = json.loads((ROOT / "evals" / "qa_golden.json").read_text())
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
    # the fake-flawed backend seeds one sheet observation whose value is NOT in its cell and one
    # prose observation whose quote is NOT in the document: the deterministic validators must
    # reject both and neither may ever reach the store.
    bad = (query_facts(str(facts_dir), metric="seeded-bad-value")
           + query_facts(str(facts_dir), metric="seeded-prose-fact"))
    metric("facts: seeded bad sheet+prose facts rejected",
           f"{stats.get('facts_rejected', 0)} rejected", not bad and stats.get("facts_rejected") == 2)


def eval_versions(state_dir: Path, brain: Path) -> None:
    """The near-duplicate revision must become an explicit supersedes chain: state links both
    documents and both pages carry the frontmatter — the raw material for freshness ranking."""
    files = json.loads((state_dir / "clean-state.json").read_text())["files"]
    by_path = {(f.get("lastResult") or {}).get("path"): f for f in files.values()}
    old = by_path.get(GOLDEN["versions"]["old_page"], {}).get("lastResult", {})
    new = by_path.get(GOLDEN["versions"]["new_page"], {}).get("lastResult", {})
    chain_ok = bool(old.get("superseded_by")) and new.get("supersedes") == \
        next((fid for fid, f in files.items()
              if (f.get("lastResult") or {}).get("path") == GOLDEN["versions"]["old_page"]), None)
    old_page = (brain / GOLDEN["versions"]["old_page"]).read_text()
    new_page = (brain / GOLDEN["versions"]["new_page"]).read_text()
    pages_ok = "superseded_by:" in old_page and "supersedes:" in new_page
    metric("time: version chain detected (state + both pages)",
           "linked" if chain_ok and pages_ok else "missing", chain_ok and pages_ok)


def eval_ops_claims(state_dir: Path, raw: Path, brain: Path) -> None:
    """Run the offline supervisor over the produced state: the sampled claim judge must check
    pages and raise zero problems on faithful (verbatim) pages — the semantic no-false-alarms
    counterpart of the numeric zero-false-positives metric."""
    import clean.ops as ops_mod
    os.environ.update({"CLEAN_STATE_DIR": str(state_dir), "RAW_DIR": str(raw),
                       "BRAIN_MD_DIR": str(brain)})
    rc = asyncio.run(ops_mod.main())
    metric("ops: offline supervision completes", f"rc={rc}", rc == 0)
    files = json.loads((state_dir / "clean-state.json").read_text())["files"]
    checked = [f for f in files.values() if f.get("claims")]
    problems = sum(len(f["claims"]["unsupported"]) + len(f["claims"]["contradicted"])
                   for f in checked)
    metric("claims: sampled judge, zero false alarms on faithful pages",
           f"{len(checked)} page(s) checked", len(checked) == 2 and problems == 0)


def eval_graph(brain: Path, graphed: Path) -> None:
    from graph.build import build_graph
    stats = build_graph(str(brain), str(graphed), min_mentions=2)
    node_ok = (graphed / GOLDEN["graph"]["node"]).exists()
    metric("graph: canonical entity nodes", f"{stats['entities']} entities",
           stats["entities"] == GOLDEN["graph"]["entities_total"] and node_ok)


def eval_answers(brain: Path, facts_dir: Path, answer_state: Path) -> None:
    """The promise, measured end to end: questions against the produced brain, answered by the
    answer service (offline synthesizer) and judged against golden expectations — exact figures,
    current-truth conflicts, honest refusals, correct citations. Every answer must also leave
    with the deterministic answer verifier's 'verified' verdict."""
    from answer.service import AnswerService
    from answer.settings import Settings as AnswerSettings
    svc = AnswerService(AnswerSettings(brain_md_dir=str(brain), facts_dir=str(facts_dir),
                                       state_dir=str(answer_state), llm="fake"))
    for case in QA_GOLDEN["questions"]:
        res = asyncio.run(svc.ask(case["q"]))
        exp = case["expect"]
        ok = True
        if "refused" in exp:
            ok &= res["refused"] is exp["refused"]
        for needle in exp.get("contains", []):
            ok &= needle in res["answer"]
        for needle in exp.get("not_contains", []):
            ok &= needle not in res["answer"]
        if "cites" in exp:
            ok &= exp["cites"] in [c["path"] for c in res["citations"]]
        if "verdict" in exp:
            ok &= res["verification"]["verdict"] == exp["verdict"]
        detail = "refused" if res["refused"] else f"verdict={res['verification']['verdict']}"
        metric(f"qa: {case['id']}", detail, bool(ok))


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    work, raw, brain, state_dir, graphed, facts_dir = (
        OUT / d for d in ("work", "raw", "brain-md", "state", "graphed", "facts"))
    work.mkdir(parents=True)

    eval_curation(work)
    stats = eval_clean_and_trust(work, raw, brain, state_dir, facts_dir)
    eval_facts(facts_dir, stats)
    eval_versions(state_dir, brain)
    eval_ops_claims(state_dir, raw, brain)
    eval_graph(brain, graphed)
    eval_answers(brain, facts_dir, OUT / "answer-state")

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
