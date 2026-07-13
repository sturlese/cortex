"""The cortex benchmark corpus — a synthetic company drive WITH ground truth.

Fully deterministic (enumerated, not random): every planted fact, duplicate, revision, ACL scope
and unanswerable probe is recorded in ground-truth.json, so the runner can score any
configuration — the offline floor (fake backends) or a real model — against what is actually
in the corpus. The hard cases are planted on purpose:

- near-duplicate revisions with corrected figures (version chains + freshness),
- the same metric with different values across revisions (conflict resolution),
- exact duplicates across folders (dedup),
- KPI grids per client (facts exactness at cell granularity),
- a Spanish memo with decimal-comma / dot-grouped figures (locale-proofing; model tier),
- OUT-class material (NDAs, invoices, web assets) that must never become pages,
- a sales-only ACL scope with probes from the wrong audience,
- questions about entities that do not exist (refusal probes).
"""
import json
from pathlib import Path

CLIENTS = [
    ("Aurora Systems", 1.2, 40),
    ("Borealis Logistics", 2.4, 25),
    ("Cascade Foods", 3.6, 15),
    ("Delta Robotics", 4.8, 10),
]
PROSPECTS = ["Everest Analytics", "Foxglove Health"]
REVISED = {0, 1}          # client indexes that get a FINAL revision with a corrected ARR
MONTHS = ("2026-01", "2026-02", "2026-03")


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def _kpis(i: int) -> dict:
    """Per-client monthly KPI grid values, formulaic and collision-free across clients."""
    base = 1000 + i * 250
    return {m: {"active_users": base + k * 37, "arr_usd": 400000 + i * 100000 + k * 12000,
                "nps": 40 + i + k}
            for k, m in enumerate(MONTHS)}


def _quarterly(name: str, arr_m: float, growth: int, final: bool = False) -> str:
    tag = " (final revision)" if final else ""
    return (f"Quarterly business review for {name}, Q1 2026{tag}.\n\n"
            f"{name} expanded operations this quarter. Revenue impact for {name} was\n"
            f"${arr_m}M ARR, up {growth}% QoQ. Churn risk: low.\n\n"
            f"Next steps: renewal proposal, joint case study pending approval.\n")


def generate(corpus_dir: str) -> dict:
    """Writes the corpus and returns the ground truth (also saved as ground-truth.json)."""
    root = Path(corpus_dir)
    root.mkdir(parents=True, exist_ok=True)
    gt: dict = {"clients": [], "prospects": [], "out_files": [], "duplicates": [],
                "versions": [], "facts": [], "qa": [], "acl": {"unit": "Clients", "audiences": ["sales"]},
                "model_tier": []}

    for i, (name, arr, growth) in enumerate(CLIENTS):
        slug = _slug(name)
        cdir = root / "Clients" / f"{i + 1}. {name}"
        cdir.mkdir(parents=True, exist_ok=True)
        gt["clients"].append({"name": name, "slug": slug})

        (cdir / "Quarterly Report Q1 2026.md").write_text(_quarterly(name, arr, growth))
        if i in REVISED:
            revised_arr = round(arr + 0.1, 1)
            (cdir / "Quarterly Report Q1 2026 FINAL.md").write_text(
                _quarterly(name, revised_arr, growth + 5, final=True))
            gt["versions"].append({"entity": slug, "old_value": f"{arr}M", "new_value": f"{revised_arr}M"})
            gt["qa"].append({"kind": "freshness", "q": f"what is the revenue impact for {slug}?",
                             "expect_contains": f"{revised_arr}M", "expect_absent": f"{arr}M"})

        kpis = _kpis(i)
        csv = "month,active_users,arr_usd,nps\n" + "\n".join(
            f"{m},{v['active_users']},{v['arr_usd']},{v['nps']}" for m, v in kpis.items())
        (cdir / "KPI metrics 2026.csv").write_text(csv + "\n")
        for m, v in kpis.items():
            for metric, value in (("active-users", v["active_users"]),
                                  ("arr-usd", v["arr_usd"]), ("nps", v["nps"])):
                gt["facts"].append({"entity": slug, "metric": metric, "period": m,
                                    "value_raw": str(value)})
        gt["qa"].append({"kind": "exact", "q": f"what is the arr-usd for {slug} in 2026-02?",
                         "expect_contains": str(kpis["2026-02"]["arr_usd"])})

        (cdir / f"meeting notes 2026-02-1{i}.txt").write_text(
            f"Meeting notes, 2026-02-1{i}. {name} confirmed the rollout plan.\n"
            f"{name} asked for SSO support. {name} budget approval pending.\n")
        (cdir / f"NDA {name}.md").write_text(f"Mutual NDA between us and {name}. Confidential.\n")
        gt["out_files"].append(f"./Clients/{i + 1}. {name}/NDA {name}.md")

    for name in PROSPECTS:
        pdir = root / "Pipeline" / "Evaluating" / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "pitch deck.md").write_text(
            f"{name} expansion proposal. {name} operates in 12 regions. {name} decision in Q3 2026.\n")
        gt["prospects"].append({"name": name, "slug": _slug(name)})

    # exact duplicate across folders -> dedup ground truth
    fin = root / "Finance"
    fin.mkdir(exist_ok=True)
    dup_src = root / "Clients" / "1. Aurora Systems" / "Quarterly Report Q1 2026.md"
    (fin / "Quarterly Report Q1 2026 (copy).md").write_text(dup_src.read_text())
    gt["duplicates"].append("./Finance/Quarterly Report Q1 2026 (copy).md")
    (fin / "invoice-9917.md").write_text("Invoice 9917. Amount due: 1200 EUR.\n")
    gt["out_files"].append("./Finance/invoice-9917.md")

    web = root / "Sales" / "web"
    web.mkdir(parents=True, exist_ok=True)
    (web / "style.css").write_text("body { color: red }\n")
    gt["out_files"].append("./Sales/web/style.css")

    (root / "Product").mkdir(exist_ok=True)
    (root / "Product" / "Roadmap 2026.md").write_text(
        "Product roadmap 2026. Q1: SSO. Q2: routing v2. Q3: self-serve onboarding.\n")

    # locale-proofing probe (model tier: the offline prose heuristic deliberately skips it)
    (root / "Sales" / "Memo estrategia 2026.md").write_text(
        "Memo de estrategia 2026. El ARR alcanzó 1.234.567 EUR, un crecimiento del 12,5%\n"
        "respecto al año anterior. Objetivo: 15 nuevos logos.\n")
    gt["model_tier"].append({"kind": "locale-facts", "file": "./Sales/Memo estrategia 2026.md",
                             "value_num": 1234567})

    # refusal probes: entities that do not exist anywhere in the corpus
    gt["qa"].append({"kind": "refusal", "q": "what is the arr-usd for zenith-corp?"})
    gt["qa"].append({"kind": "refusal", "q": "what is our office plant watering policy?"})

    (root / "ground-truth.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2))
    return gt
