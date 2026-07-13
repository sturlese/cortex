"""Shared fixture: a small brain-md corpus + facts store shaped exactly like the pipeline's
output contracts (docs/pipeline/brain-page-contract.md, docs/pipeline/facts.md) — built by
hand here because the packages talk through files, never imports."""
import os
import sqlite3

import pytest

from answer.service import AnswerService
from answer.settings import Settings

_FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  file_id TEXT NOT NULL, page_path TEXT, entity TEXT, org_unit TEXT,
  metric TEXT NOT NULL, metric_raw TEXT NOT NULL, value_raw TEXT NOT NULL, value_num REAL,
  unit TEXT, period TEXT, dimension TEXT, source_ref TEXT NOT NULL,
  extracted_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 1, acl TEXT
);
"""


def write_page(brain: str, rel: str, fm: dict, body: str) -> str:
    lines = ["---"] + [f"{k}: {v}" for k, v in fm.items()] + ["---", "", body, ""]
    path = os.path.join(brain, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return rel


def add_fact(facts_dir: str, **kw):
    os.makedirs(facts_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(facts_dir, "facts.db"))
    conn.executescript(_FACTS_SCHEMA)
    row = {"file_id": "F", "page_path": None, "entity": None, "org_unit": None,
           "metric": "m", "metric_raw": "m", "value_raw": "0", "value_num": 0.0,
           "unit": None, "period": None, "dimension": None, "source_ref": "F!S!R1C1",
           "extracted_at": "t", "verified": 1, "acl": None}
    row.update(kw)
    with conn:
        conn.execute(
            "INSERT INTO observations (file_id, page_path, entity, org_unit, metric, metric_raw,"
            " value_raw, value_num, unit, period, dimension, source_ref, extracted_at, verified, acl)"
            " VALUES (:file_id, :page_path, :entity, :org_unit, :metric, :metric_raw, :value_raw,"
            " :value_num, :unit, :period, :dimension, :source_ref, :extracted_at, :verified, :acl)", row)
    conn.close()


@pytest.fixture()
def corpus(tmp_path):
    brain = str(tmp_path / "brain-md")
    facts = str(tmp_path / "facts")
    os.makedirs(brain, exist_ok=True)

    write_page(brain, "entities/globex/q1-report.md",
               {"type": "report", "title": "Quarterly Report Q1 2026", "entity": "globex",
                "as_of": "2026-Q1", "verification": "verified", "tier": 1,
                "superseded_by": '"local-new"'},
               "Quarterly business review for Globex. Revenue impact was $1.2M ARR, up 40% QoQ.")
    write_page(brain, "entities/globex/q1-report-final.md",
               {"type": "report", "title": "Quarterly Report Q1 2026 FINAL", "entity": "globex",
                "as_of": "2026-Q1", "verification": "verified", "tier": 1,
                "supersedes": '"local-old"'},
               "Quarterly business review for Globex (final). Revenue impact was $1.3M ARR, up 45% QoQ.")
    write_page(brain, "entities/initech/kpi.md",
               {"type": "report", "title": "KPI metrics 2026", "entity": "initech",
                "as_of": "2026-01", "verification": "verified", "representation": "digest",
                "detail_in_source": "true"},
               "Monthly KPI digest for Initech. See the facts store for exact figures.")
    write_page(brain, "units/product/roadmap.md",
               {"type": "product-doc", "title": "Roadmap 2026", "unit": "Product",
                "verification": "failed", "unverified_numbers": '["99%"]'},
               "Roadmap themes: SSO in Q1 2026, routing engine v2, self-serve onboarding. 99% done.")

    for period, users, arr in (("2026-01", "1250", "480000"),
                               ("2026-02", "1310", "495000"),
                               ("2026-03", "1400", "512000")):
        add_fact(facts, file_id="local-kpi", page_path="entities/initech/kpi.md",
                 entity="initech", org_unit="Clients", metric="active-users", metric_raw="active_users",
                 value_raw=users, value_num=float(users), period=period,
                 source_ref=f"local-kpi!Sheet1!R{period[-1]}C2")
        add_fact(facts, file_id="local-kpi", page_path="entities/initech/kpi.md",
                 entity="initech", org_unit="Clients", metric="arr-usd", metric_raw="arr_usd",
                 value_raw=arr, value_num=float(arr), unit="usd", period=period,
                 source_ref=f"local-kpi!Sheet1!R{period[-1]}C3")
    add_fact(facts, file_id="local-old", page_path="entities/globex/q1-report.md",
             entity="globex", metric="revenue-impact", metric_raw="Revenue impact",
             value_raw="1.2M", value_num=1_200_000.0, unit="usd",
             source_ref="local-old!text!40")
    add_fact(facts, file_id="local-new", page_path="entities/globex/q1-report-final.md",
             entity="globex", metric="revenue-impact", metric_raw="Revenue impact",
             value_raw="1.3M", value_num=1_300_000.0, unit="usd",
             source_ref="local-new!text!48")

    return Settings(brain_md_dir=brain, facts_dir=facts, state_dir=str(tmp_path / "state"), llm="fake")


@pytest.fixture()
def service(corpus):
    return AnswerService(corpus)
