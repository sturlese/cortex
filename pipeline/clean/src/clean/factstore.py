"""The facts store — verified numeric observations, queryable and diffable.

Two synchronized representations in the facts dir (default /data/brain-facts):
- facts.db    SQLite: the query surface (exact metric/entity/period lookups for the answer layer)
- facts.jsonl one observation per line, sorted: human-diffable audit trail, same doctrine as the
              playbook — the store must never be opaque.

Single writer: clean. Idempotent per document: a reprocess REPLACEs the document's observations
atomically; a deleted source deletes them — facts propagate deletions exactly like pages do.
Every row carries source_ref = fileId!sheet!RnCm, so any number can be traced to its cell.
"""
import json
import os
import sqlite3

from clean.fsutil import write_text_atomic
from clean.numeric import parse_num

DB_FILE = "facts.db"
JSONL_FILE = "facts.jsonl"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  file_id      TEXT NOT NULL,
  page_path    TEXT,
  entity       TEXT,
  org_unit     TEXT,
  metric       TEXT NOT NULL,
  metric_raw   TEXT NOT NULL,
  value_raw    TEXT NOT NULL,
  value_num    REAL,
  unit         TEXT,
  period       TEXT,
  dimension    TEXT,
  source_ref   TEXT NOT NULL,
  extracted_at TEXT NOT NULL,
  verified     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_obs_metric ON observations (metric, entity, period);
CREATE INDEX IF NOT EXISTS idx_obs_file ON observations (file_id);
"""


def db_path(facts_dir: str) -> str:
    return os.path.join(facts_dir, DB_FILE)


def _connect(facts_dir: str) -> sqlite3.Connection:
    os.makedirs(facts_dir, exist_ok=True)
    conn = sqlite3.connect(db_path(facts_dir))
    conn.executescript(_SCHEMA)
    # additive migration: pre-ACL stores gain the column in place (NULL = open, same as no acl)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
    if "acl" not in cols:
        conn.execute("ALTER TABLE observations ADD COLUMN acl TEXT")
    return conn


def replace_facts(facts_dir: str, file_id: str, rows: list[dict],
                  *, page_path: str | None, entity: str | None, org_unit: str | None,
                  extracted_at: str) -> int:
    """Atomically replace the document's observations (reprocess-safe idempotency). `rows` are
    validated observations as dicts (facts.sheet_rows_for_store / prose_rows_for_store):
    {metric, metric_raw, value_raw, unit, period, dimension, source_ref}."""
    conn = _connect(facts_dir)
    try:
        with conn:
            conn.execute("DELETE FROM observations WHERE file_id = ?", (file_id,))
            conn.executemany(
                "INSERT INTO observations (file_id, page_path, entity, org_unit, metric,"
                " metric_raw, value_raw, value_num, unit, period, dimension, source_ref,"
                " extracted_at, verified, acl) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                [(file_id, page_path, entity, org_unit, r["metric"], r["metric_raw"],
                  r["value_raw"], parse_num(r["value_raw"]), r.get("unit"), r.get("period"),
                  r.get("dimension"), r["source_ref"], extracted_at, r.get("acl"))
                 for r in rows])
        return len(rows)
    finally:
        conn.close()


def delete_facts(facts_dir: str, file_id: str) -> int:
    """Deletion propagation: a removed source takes its numbers with it."""
    if not os.path.exists(db_path(facts_dir)):
        return 0
    conn = _connect(facts_dir)
    try:
        with conn:
            cur = conn.execute("DELETE FROM observations WHERE file_id = ?", (file_id,))
        return cur.rowcount
    finally:
        conn.close()


def query_facts(facts_dir: str, metric: str | None = None, entity: str | None = None,
                period: str | None = None, limit: int = 100) -> list[dict]:
    """Exact lookups over the store (the answer layer's numeric path). Filters are equality on
    the normalized columns; `period` also matches coarser rows by prefix (2026 matches 2026-03).

    Same semantics as the serving layer's hand-mirrored copy (answer/metrics.query_metrics —
    packages share no code): only verified rows, case-folded metric/entity inputs. The store
    only ever holds verified lowercase rows today, so these are invariants made explicit — the
    two read paths must never drift (the golden evals prove they agree)."""
    if not os.path.exists(db_path(facts_dir)):
        return []
    where, args = ["verified = 1"], []
    if metric:
        where.append("metric = ?")
        args.append(metric.strip().lower())
    if entity:
        where.append("entity = ?")
        args.append(entity.strip().lower())
    if period:
        where.append("(period = ? OR period LIKE ?)")
        args.extend([period, f"{period}-%"])
    sql = ("SELECT * FROM observations WHERE " + " AND ".join(where)
           + " ORDER BY entity, metric, period, source_ref LIMIT ?")
    args.append(limit)
    conn = _connect(facts_dir)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def export_jsonl(facts_dir: str) -> int:
    """Dump the whole store to facts.jsonl, deterministically sorted — the diffable audit trail.
    Called once per pass (not per document), atomically."""
    if not os.path.exists(db_path(facts_dir)):
        return 0
    conn = _connect(facts_dir)
    try:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM observations ORDER BY entity, metric, period, source_ref")]
    finally:
        conn.close()
    write_text_atomic(os.path.join(facts_dir, JSONL_FILE),
                      "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))
    return len(rows)
