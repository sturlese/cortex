"""The page index — SQLite + FTS5 over brain-md, incremental and fully regenerable.

Pure code. Parses each page's frontmatter (the contract of docs/pipeline/brain-page-contract.md)
into queryable columns and its body into a full-text index. Incremental by (mtime, size); pages
that disappear from brain-md disappear from the index — deletions keep propagating.
"""
import os
import re
import sqlite3

import yaml

DB_FILE = "answer-index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  path TEXT PRIMARY KEY,
  title TEXT, doc_type TEXT, entity TEXT, unit TEXT,
  period TEXT, as_of TEXT, date TEXT,
  verification TEXT, quality TEXT, representation TEXT, tier INTEGER,
  detail_in_source INTEGER NOT NULL DEFAULT 0,
  superseded_by TEXT, supersedes TEXT,
  source_file_id TEXT, source_uri TEXT,
  acl TEXT,
  body TEXT, mtime REAL, size INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(title, body, tags, entity, mentions);
"""


def db_path(state_dir: str) -> str:
    return os.path.join(state_dir, DB_FILE)


def connect(state_dir: str) -> sqlite3.Connection:
    os.makedirs(state_dir, exist_ok=True)
    conn = sqlite3.connect(db_path(state_dir))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # additive migration for pre-ACL indexes (the index is regenerable, but never break a boot)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
    if "acl" not in cols:
        conn.execute("ALTER TABLE pages ADD COLUMN acl TEXT")
    if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        # pre-fix indexes stored '' for BOTH "no acl" and "empty acl"; under the fixed encoding
        # '' means "empty ACL: restricted to nobody", so re-encode old rows to NULL (their
        # observed behavior: open) and let refresh re-derive the truth from the pages.
        with conn:
            conn.execute("UPDATE pages SET acl = NULL WHERE acl = ''")
            conn.execute("PRAGMA user_version = 1")
    return conn


def visible(acl: str | None, audiences: set[str] | None) -> bool:
    """The one visibility rule (mirrors the pipeline's acl.py — packages share no code):
    no ACL (None) -> visible to all; unrestricted client (None) -> sees everything; else
    intersect. An EMPTY acl ('') is not "no ACL": it is a deliberately empty intersection
    (a dossier whose members share no audience) — restricted to nobody below unrestricted,
    exactly like the pipeline's visible([], audiences)."""
    if acl is None or audiences is None:
        return True
    return bool({a for a in acl.split(",") if a} & audiences)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body); tolerant — an unparseable page indexes as body-only."""
    if text.startswith("---"):
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
                return (fm if isinstance(fm, dict) else {}), m.group(2)
            except yaml.YAMLError:
                return {}, text
    return {}, text


def _mentions_text(fm: dict) -> str:
    ms = fm.get("mentions")
    if not isinstance(ms, list):
        return ""
    return " ".join(str(m.get("name", "")) for m in ms if isinstance(m, dict))


def _walk_pages(brain_md_dir: str):
    for d, dirs, files in os.walk(brain_md_dir):
        dirs[:] = [s for s in dirs if s != ".git"]
        for fn in files:
            if fn.endswith(".md"):
                yield os.path.join(d, fn)


def refresh(conn: sqlite3.Connection, brain_md_dir: str) -> dict:
    """Incremental sync: (re)index changed pages, drop vanished ones. Returns counts."""
    known = {r["path"]: (r["mtime"], r["size"]) for r in conn.execute("SELECT path, mtime, size FROM pages")}
    seen: set[str] = set()
    added = updated = 0
    for abs_path in _walk_pages(brain_md_dir):
        rel = os.path.relpath(abs_path, brain_md_dir)
        st = os.stat(abs_path)
        seen.add(rel)
        if known.get(rel) == (st.st_mtime, st.st_size):
            continue
        with open(abs_path, encoding="utf-8") as f:
            text = f.read()
        fm, body = split_frontmatter(text)
        tags = " ".join(str(t) for t in fm.get("tags", []) if t) if isinstance(fm.get("tags"), list) else ""
        acl_list = fm.get("acl")
        # NULL = the page carries no acl (open); '' = it carries an EMPTY one (nobody) — the
        # CSV encoding must preserve that distinction or a restricted dossier is served open.
        acl = ",".join(str(a) for a in acl_list) if isinstance(acl_list, list) else None
        row = (
            rel, str(fm.get("title", "") or ""), str(fm.get("type", "") or ""),
            str(fm.get("entity", "") or ""), str(fm.get("unit", "") or ""),
            str(fm.get("period", "") or ""), str(fm.get("as_of", "") or ""), str(fm.get("date", "") or ""),
            str(fm.get("verification", "") or ""), str(fm.get("extraction_quality", "") or ""),
            str(fm.get("representation", "") or ""), int(fm.get("tier") or 0),
            1 if fm.get("detail_in_source") else 0,
            str(fm.get("superseded_by", "") or ""), str(fm.get("supersedes", "") or ""),
            str(fm.get("source_file_id", "") or ""), str(fm.get("source_uri", "") or ""),
            acl, body, st.st_mtime, st.st_size,
        )
        with conn:
            old = conn.execute("SELECT rowid FROM pages WHERE path = ?", (rel,)).fetchone()
            if old:
                conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (old["rowid"],))
                conn.execute("DELETE FROM pages WHERE path = ?", (rel,))
                updated += 1
            else:
                added += 1
            cur = conn.execute(
                "INSERT INTO pages (path, title, doc_type, entity, unit, period, as_of, date,"
                " verification, quality, representation, tier, detail_in_source, superseded_by,"
                " supersedes, source_file_id, source_uri, acl, body, mtime, size)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            conn.execute(
                "INSERT INTO pages_fts (rowid, title, body, tags, entity, mentions)"
                " VALUES (?,?,?,?,?,?)",
                (cur.lastrowid, row[1], body, tags, row[3], _mentions_text(fm)))
    removed = 0
    for rel in set(known) - seen:
        with conn:
            old = conn.execute("SELECT rowid FROM pages WHERE path = ?", (rel,)).fetchone()
            if old:
                conn.execute("DELETE FROM pages_fts WHERE rowid = ?", (old["rowid"],))
                conn.execute("DELETE FROM pages WHERE path = ?", (rel,))
                removed += 1
    return {"added": added, "updated": updated, "removed": removed,
            "total": conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"]}


def get_page(conn: sqlite3.Connection, path: str) -> dict | None:
    r = conn.execute("SELECT * FROM pages WHERE path = ?", (path,)).fetchone()
    return dict(r) if r else None


def superseded_paths(conn: sqlite3.Connection) -> set[str]:
    """Paths of pages a newer version supersedes — the page-index half of "current truth"
    (the facts store joins against it via metrics.annotate_superseded)."""
    return {r["path"] for r in conn.execute("SELECT path FROM pages WHERE superseded_by != ''")}
