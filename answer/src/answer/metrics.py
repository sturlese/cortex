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


def _carries_acl(conn: sqlite3.Connection) -> bool:
    """Whether this store records audience labels at all. `acl` arrives through an additive
    migration clean runs on its WRITE path (factstore); this package only reads facts.db and never
    migrates it, so a store predating the ACL feature legitimately has no such column."""
    return "acl" in {r[1] for r in conn.execute("PRAGMA table_info(observations)")}


def query_metrics(facts_dir: str, metric: str | None = None, entity: str | None = None,
                  period: str | None = None, limit: int = 50,
                  audiences: set | None = None) -> list[dict]:
    """Exact lookups: equality on metric/entity; period matches exactly or by year prefix.
    `audiences` filters to rows whose document the client may see (None = unrestricted).
    A store with no `acl` column carries no audience information, so a SCOPED client gets nothing:
    unknown is not open — the same direction the page index takes for an unreadable acl."""
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
        if audiences is not None and not _carries_acl(conn):
            return []
        # The cap counts rows the client may SEE, so it cannot be a SQL LIMIT: an invisible row is
        # not there, and a row that is not there must not occupy a slot. Capping in SQL and filtering
        # afterwards let out-of-scope rows crowd out the visible tail — down to zero results for a
        # scoped client while open rows it is entitled to existed. Stream the ordered cursor instead
        # and stop once the cap is full; the WHERE clause still bounds how much can be scanned, and
        # `visible` stays the single copy of the rule.
        cur = conn.execute(f"SELECT * FROM observations WHERE {' AND '.join(where)}"
                           " ORDER BY entity, metric, period, source_ref", args)
        out = []
        # `0 < limit <= len(out)` keeps SQL's LIMIT semantics exactly, which the old query had for
        # free: 0 means no rows, negative means unlimited. Breaking on `len(out) >= limit` would
        # have returned ONE row for both.
        if limit != 0:
            for r in cur:
                row = dict(r)
                if visible(row.get("acl"), audiences):
                    out.append(row)
                    if 0 < limit <= len(out):
                        break
        return out
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

    `verified = 1` matches query_metrics and the pipeline's query_facts. factstore inserts verified
    rows only, so this changes no result today; it keeps the hand-mirrored read paths saying the
    same thing."""
    from answer.index import visible
    if not os.path.exists(_db(facts_dir)):
        return []
    where, args = ["verified = 1"], []
    if entity:
        where.append("entity = ?")
        args.append(entity.strip().lower())
    conn = sqlite3.connect(_db(facts_dir))
    try:
        if audiences is None:
            # Unrestricted: nothing to filter, so don't read acl — which also keeps the query inside
            # the covering index idx_obs_metric (adding a column to the SELECT costs a temp B-tree).
            rows = conn.execute(f"SELECT DISTINCT metric FROM observations"
                                f" WHERE {' AND '.join(where)}", args).fetchall()
            return sorted({r[0] for r in rows})
        if not _carries_acl(conn):
            return []
        # acl comes back alongside so the scope is applied HERE, not by the caller: one metric may
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
