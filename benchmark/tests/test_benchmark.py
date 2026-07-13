"""The benchmark benchmarks itself: deterministic generation, honest ground truth, floor green."""
import json
from pathlib import Path

from benchmark.generate import generate
from benchmark.run import RESULTS, run


def _tree_bytes(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_generate_is_deterministic(tmp_path):
    gt1 = generate(str(tmp_path / "a"))
    gt2 = generate(str(tmp_path / "b"))
    assert gt1 == gt2
    assert _tree_bytes(tmp_path / "a") == _tree_bytes(tmp_path / "b")


def test_ground_truth_is_consistent_with_the_corpus(tmp_path):
    gt = generate(str(tmp_path / "c"))
    root = tmp_path / "c"
    for rel in gt["out_files"] + gt["duplicates"]:
        assert (root / rel.removeprefix("./")).exists(), rel
    assert len(gt["clients"]) == 4 and len(gt["prospects"]) == 2
    assert len(gt["facts"]) == 4 * 3 * 3                     # clients x months x metrics
    kinds = {q["kind"] for q in gt["qa"]}
    assert kinds == {"exact", "freshness", "refusal"}
    saved = json.loads((root / "ground-truth.json").read_text())
    assert saved == gt


def test_floor_run_is_green_end_to_end(tmp_path, monkeypatch):
    """THE test: the whole system meets every floor threshold against planted ground truth."""
    monkeypatch.setenv("CLEAN_LLM", "fake")
    RESULTS.clear()
    rc = run(tmp_path / "out", gate=True)
    assert rc == 0
    report = (tmp_path / "out" / "benchmark-report.md").read_text()
    assert "❌" not in report
    dims = {d for d, *_ in RESULTS}
    assert {"curation", "placement", "trust", "facts-captured", "facts-wrong",
            "versions", "dossiers", "graph", "qa-exact", "qa-freshness",
            "qa-refusal", "acl"} <= dims
    data = json.loads((tmp_path / "out" / "report.json").read_text())
    assert all(r["score"] >= r["threshold"] for r in data if r["dimension"] != "facts-wrong")
    assert next(r for r in data if r["dimension"] == "facts-wrong")["score"] == 0
