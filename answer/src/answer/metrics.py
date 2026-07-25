"""Read-only queries over the facts store (facts.db, written by the pipeline's clean stage).

The schema is the contract documented in docs/pipeline/facts.md; this module deliberately
re-implements the read path instead of importing the pipeline package (ADR 001: packages share
no code, they talk through files). Superseded-document awareness comes from the page index:
rows whose page is superseded are flagged so consumers can prefer current truth.
"""
import os
import sqlite3

FACTS_DB = "facts.db"


def _db(facts_dir: str) -> str:
    return os.path.join(facts_dir, FACTS_DB)


def query_metrics(facts_dir: str, metric: str | None = None, entity: str | None = None,
                  period: str | None = None, limit: int = 50,
                  audiences: set | None = None) -> list[dict]:
    """Exact lookups: equality on metric/entity; period matches exactly or by year prefix.
    `audiences` filters to rows whose document the client may see (None = unrestricted)."""
    from answer.index import visible
    if not os.path.exists(_db(facts_dir)):
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
    conn = sqlite3.connect(_db(facts_dir))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT * FROM observations WHERE {' AND '.join(where)}"
            " ORDER BY entity, metric, period, source_ref LIMIT ?", [*args, limit]).fetchall()
        return [dict(r) for r in rows if visible(dict(r).get("acl"), audiences)]
    finally:
        conn.close()


def known_metrics(facts_dir: str, entity: str | None = None,
                  audiences: set | None = None) -> list[str]:
    """Distinct metric ids (optionally for one entity) — lets agents/users discover vocabulary.
    `audiences` scopes it exactly like query_metrics: the vocabulary is a read path too, and the
    ACL scope must filter every one of them (ADR 010: "even entity *existence* is scoped"). A
    metric only out-of-scope documents mention would otherwise disclose both that it exists and,
    via the `entity` argument, that the entity does — on the empty-result path of metrics_text,
    i.e. precisely when the row filter just hid them.

    `verified = 1` mirrors query_metrics / the pipeline's query_facts. factstore only ever inserts
    verified rows, so this changes no result today; it keeps the hand-mirrored copies faithful."""
    from answer.index import visible
    if not os.path.exists(_db(facts_dir)):
        return []
    where, args = ["verified = 1"], []
    if entity:
        where.append("entity = ?")
        args.append(entity.strip().lower())
    conn = sqlite3.connect(_db(facts_dir))
    try:
        # acl comes back alongside so the scope is applied here, not by the caller: one metric may
        # be carried by several documents with different audiences, and it stays visible if ANY of
        # them is in scope.
        rows = conn.execute(f"SELECT DISTINCT metric, acl FROM observations"
                            f" WHERE {' AND '.join(where)}", args).fetchall()
        return sorted({r[0] for r in rows if visible(r[1], audiences)})
    finally:
        conn.close()


def annotate_superseded(rows: list[dict], superseded_paths: set[str]) -> list[dict]:
    """Mark facts whose page is a superseded version — consumers should prefer current rows."""
    for r in rows:
        r["from_superseded_page"] = bool(r.get("page_path") and r["page_path"] in superseded_paths)
    return rows
