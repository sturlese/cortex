"""Hybrid retrieval with the page contract enforced in the ranking. Pure code, explainable:
every hit carries the factors that shaped its score, so 'why did this page rank here' is always
answerable — no opaque similarity.

Base relevance is FTS5 BM25; deterministic factors then encode the contract:
- superseded pages are heavily demoted (current truth first; history stays reachable),
- verification/extraction problems demote (failed hardest),
- an entity or period named in the query boosts pages that match it exactly,
- 'current/latest'-style questions prefer fresher `as_of`.
"""
import re
import sqlite3

TOP_K = 5
_RECENCY_WORDS = {"current", "latest", "now", "today", "newest", "most recent"}

# multiplicative penalties on the BM25 rank (BM25 is better when lower, so factors > 1 demote)
_PENALTY_SUPERSEDED = 4.0
_PENALTY_FAILED = 2.5
_PENALTY_MANUAL_REVIEW = 1.5
_PENALTY_PARTIAL = 1.2
_BOOST_ENTITY = 0.5
_BOOST_PERIOD = 0.6
_BOOST_FRESH = 0.7


_STOP = {"the", "a", "an", "in", "on", "of", "for", "and", "or", "to", "is", "are", "was",
         "were", "what", "which", "who", "how", "when", "where", "with", "about", "does",
         "do", "did", "at", "by", "it", "its", "our", "their", "this", "that", "please"}


def _fts_query(query: str) -> str:
    """Robust FTS5 match string: content tokens quoted and OR'ed (user text must never be FTS
    syntax; stopwords must never make everything a hit)."""
    tokens = [t for t in re.findall(r"[\w][\w'-]*", query.lower())
              if t not in _STOP and len(t) >= 2]
    return " OR ".join(f'"{t}"' for t in tokens) if tokens else '""'


def _query_periods(query: str) -> set[str]:
    out = set()
    for m in re.finditer(r"\b(20\d\d)(?:[-/ ]?(Q[1-4])|[-/](0?[1-9]|1[0-2]))?\b", query, re.I):
        year = m.group(1)
        if m.group(2):
            out.add(f"{year}-{m.group(2).upper()}")
        elif m.group(3):
            out.add(f"{year}-{int(m.group(3)):02d}")
        out.add(year)
    return out


def search(conn: sqlite3.Connection, query: str, k: int = TOP_K,
           include_superseded: bool = True, audiences: set | None = None) -> list[dict]:
    """Top-k pages for the query with contract-aware ranking. Hits carry `factors` (the applied
    adjustments) and a snippet. `include_superseded=False` drops stale versions entirely;
    `audiences` filters to pages the client may see (None = unrestricted)."""
    from answer.index import visible
    rows = conn.execute(
        "SELECT p.*, bm25(pages_fts) AS bm25 FROM pages_fts"
        " JOIN pages p ON p.rowid = pages_fts.rowid"
        " WHERE pages_fts MATCH ? ORDER BY bm25 LIMIT 40",
        (_fts_query(query),)).fetchall()
    q_low = query.lower()
    q_tokens = set(re.findall(r"[a-z0-9][a-z0-9'-]*", q_low))
    periods = _query_periods(query)
    wants_fresh = any(w in q_low for w in _RECENCY_WORDS)

    hits = []
    for r in rows:
        p = dict(r)
        if not visible(p.get("acl"), audiences):
            continue                     # not an annotation: an invisible page simply isn't there
        if not include_superseded and p["superseded_by"]:
            continue
        adjustments: list[tuple[float, str]] = []
        if p["superseded_by"]:
            adjustments.append((_PENALTY_SUPERSEDED, "superseded"))
        if p["verification"] == "failed":
            adjustments.append((_PENALTY_FAILED, "verification-failed"))
        elif p["verification"] == "partial":
            adjustments.append((_PENALTY_PARTIAL, "verification-partial"))
        if p["quality"] == "manual_review":
            adjustments.append((_PENALTY_MANUAL_REVIEW, "manual-review"))
        if p["entity"] and p["entity"].lower() in q_tokens:
            adjustments.append((_BOOST_ENTITY, f"entity:{p['entity']}"))
        page_periods = {x for x in (p["as_of"], p["period"]) if x}
        if periods and any(pp == qp or pp.startswith(qp + "-") or qp.startswith(pp + "-")
                           for pp in page_periods for qp in periods):
            adjustments.append((_BOOST_PERIOD, "period-match"))
        if wants_fresh and p["as_of"] and not p["superseded_by"]:
            adjustments.append((_BOOST_FRESH, f"fresh:{p['as_of']}"))

        score = max(p["bm25"], 0.01)
        for factor, _label in adjustments:
            score *= factor
        body = p.pop("body") or ""
        hits.append({**p, "score": score, "factors": [label for _f, label in adjustments],
                     "snippet": _snippet(body, q_tokens)})
    hits.sort(key=lambda h: (h["score"], h["path"]))
    return hits[:k]


def _snippet(body: str, q_tokens: set[str], width: int = 240) -> str:
    """First region of the body containing a query token (fallback: the head)."""
    low = body.lower()
    best = 0
    for t in sorted(q_tokens, key=len, reverse=True):
        if len(t) < 3:
            continue
        i = low.find(t)
        if i >= 0:
            best = max(0, i - width // 3)
            break
    return re.sub(r"\s+", " ", body[best:best + width]).strip()
