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
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        # pre-fix indexes stored '' for BOTH "no acl" and "empty acl"; under the fixed encoding
        # '' means "empty ACL: restricted to nobody", so re-encode old rows to NULL (their
        # observed behavior: open) and let refresh re-derive the truth from the pages.
        with conn:
            conn.execute("UPDATE pages SET acl = NULL WHERE acl = ''")
            conn.execute("PRAGMA user_version = 1")
    if version < 2:
        # A row indexed before refresh learned to read a non-list `acl:` value (or to reject a
        # comma-bearing label) holds NULL — open — for a page that carries an ACL. refresh re-reads
        # a page only when its mtime/size changed, so without this those rows would be served open
        # for the life of the index. Force a re-encode by invalidating the cached stat, and close
        # them until it happens rather than after: unknown is not open. Note this closes EVERY page
        # to scoped clients until the next refresh completes, including pages whose ACL was fine —
        # AnswerService runs refresh() immediately after connect(), and unrestricted clients are
        # unaffected (visible('', None) is True), but a caller that connects without refreshing
        # serves nothing to a scoped client. If the UPDATE cannot run (another process holds a write
        # lock) connect() raises rather than proceeding: no service beats a leaky one.
        with conn:
            conn.execute("UPDATE pages SET acl = '', mtime = -1")
            conn.execute("PRAGMA user_version = 2")
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


_FM_BLOCK_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
# The page OPENS a frontmatter block: `---` on its own first line. A leading BOM (editors and
# Windows tooling add one invisibly) and leading blank lines do not change the author's intent.
_FM_OPEN_RE = re.compile(r"^﻿?\s*---[ \t]*\r?\n")


def split_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body); tolerant — an unparseable page indexes as body-only."""
    text = text.lstrip("﻿")        # a BOM is encoding noise, not content: never let it hide an acl
    if text.startswith("---"):
        m = _FM_BLOCK_RE.match(text)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
                return (fm if isinstance(fm, dict) else {}), m.group(2)
            except Exception:
                # Unconditional, because the promise above is unconditional and PyYAML's failures do
                # not share a base class: "2026-02-30" raises ValueError from datetime.date(),
                # `!!bool maybe` a KeyError, `!!timestamp hello` an AttributeError, deep nesting a
                # RecursionError. Enumerating types here is the hand-maintained-list trap the _yaml
                # writers avoid by round-tripping instead. One unreadable page must never abort the
                # refresh for every other page. Scope is one safe_load, so nothing else is masked.
                return {}, text
    return {}, text


def _label_ok(label) -> bool:
    """Whether an audience label survives this index's CSV encoding. A comma would split one label
    into two audiences at enforcement time — silently WIDENING access — and a blank label would
    vanish in the round-trip. The pipeline's acl._check_labels rejects exactly these on the write
    side; the index re-derives the same encoding from page text, so it must reject them too rather
    than trust that whoever wrote the page ran that validation."""
    # `isinstance(label, str)`, not `str(label)`: coercing would RENAME the label rather than reject
    # it, and a renamed label is grantable. YAML 1.1 reads `acl: [010]` as the int 8 and `[12:30]` as
    # 750, so str() would index audiences "8" and "750" — labels nobody granted, which is the widening
    # this predicate exists to stop. It also collided `[~]` with `["None"]` and `[1.50]` with `[1.5]`.
    # Nothing the pipeline writes is affected: clean.page._yaml emits a scalar plain only when it
    # round-trips as the identical string, so every label it writes reads back as a str.
    return isinstance(label, str) and "," not in label and bool(label.strip())


def carries_frontmatter_block(text: str) -> bool:
    """True when the page OPENS a frontmatter block, whether or not that block parses or is even
    closed — the distinction split_frontmatter throws away (both cases give {}). refresh needs it to
    encode the acl: a page with no block carries no ACL and is open, while a page whose block we
    could not read has an ACL nobody can read and must not be mistaken for the first case.

    It deliberately does NOT require the closing `---`. Requiring it meant a page carrying
    `acl: [finance]` inside a block that is unclosed, truncated mid-write, or ended with YAML's
    `...` was indexed as "no ACL" — open to everyone. Same for a BOM or a leading blank line before
    the opener, which is why those are tolerated here and in split_frontmatter."""
    return bool(_FM_OPEN_RE.match(text))


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
        if isinstance(acl_list, list) and all(_label_ok(a) for a in acl_list):
            acl = ",".join(str(a) for a in acl_list)
        elif "acl" in fm or (not fm and carries_frontmatter_block(text)):
            # An acl this encoding cannot represent is still an acl, so the page's audience is
            # UNKNOWN — and unknown must not resolve to open. '' is the "restricted to nobody"
            # encoding: an unrestricted operator still sees the page (so the breakage is
            # discoverable) while no scoped client does. NULL here would serve a page written as
            # `acl: finance` — or as an unreadable block — to everyone. Only a page with genuinely
            # no `acl` key is open, exactly as docs/pipeline/brain-page-contract.md states.
            acl = ""
        else:
            acl = None
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
