"""Deterministic entity resolution from the source folder path.

Shared-drive folder trees usually encode ownership reliably (who a document belongs to), which the
knowledge base cannot see once files are flattened. This module turns a folder path into a resolved
entity that drives the output layout, the page frontmatter and the graph stage.

Two conventions are recognized out of the box (override via a JSON file, env CLEAN_CONVENTIONS):

  tracked entity   <...>/<entity anchor>/[<year or short label>/]<N>. <Name>[ - <status>]/...
                   e.g. "Portfolio/2024/3. Acme - archived/board/minutes.pdf"
  prospect         <...>/<prospect anchor>/<Stage>/<Name>/...
                   e.g. "Pipeline/Evaluating/Acme/deck.pdf"

Resolution runs in two passes: pass 1 builds a high-confidence catalog of entities over the whole
inventory (strict anchors only); pass 2 re-scans paths for any "N. Name" segment or folder whose
slug is already in the catalog — recovering docs filed under non-standard folders.
"""
import json
import os
import re
import unicodedata

DEFAULT_CONVENTIONS = {
    # Folder names (case-insensitive) whose numbered children are tracked entities.
    "entity_anchors": ["portfolio", "clients", "projects", "accounts", "companies"],
    # Folder names whose <stage>/<name> children are prospective entities.
    "prospect_anchors": ["pipeline", "dealflow", "prospects", "leads", "opportunities"],
    # Suffix markers on an entity folder name: "Acme - archived", "Acme (closed)".
    "status_markers": ["archived", "closed", "sold", "exit", "won", "lost", "paused", "on-hold", "churned"],
    # Numbered folders that are admin/process, NOT an entity ("3. Reporting").
    "non_entity_names": ["reporting", "legal", "finance", "financials", "admin", "hr", "contracts",
                         "meetings", "minutes", "invoices", "accounting", "compliance", "docs", "misc"],
}

_FILE_EXT = re.compile(
    r"\.(pdf|xlsx?|xlsm|docx?|pptx?|csv|tsv|txt|md|json|jpe?g|png|heic|zip|7z|rtf|odt|ods|odp)$", re.I)
_NUMBERED = re.compile(r"^(\d+)\s*[.\-]\s*(.+)$")
_YEAR = re.compile(r"\b(20\d\d)\b")
_QTR = re.compile(r"\bQ\s?([1-4])\b", re.I)
_YEARISH_SEG = re.compile(r"^20\d\d\b")   # optional intermediate segment between anchor and entity


def load_conventions(path: str | None = None) -> dict:
    """Merge a user JSON (env CLEAN_CONVENTIONS or explicit path) over the defaults."""
    path = path or os.environ.get("CLEAN_CONVENTIONS", "")
    conv = {k: list(v) for k, v in DEFAULT_CONVENTIONS.items()}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for k in conv:
            if isinstance(user.get(k), list):
                conv[k] = user[k]
    return conv


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


def _split_status(name: str, markers: list[str]):
    """Strip a trailing status marker: 'Acme - archived' -> ('Acme', 'archived')."""
    m = re.search(r"[-–(]\s*([^-–()]+?)\s*\)?\s*$", name)
    if m:
        candidate = m.group(1).strip().lower()
        for marker in markers:
            if marker in candidate:
                return name[: m.start()].strip(), marker
    return name, None


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _empty() -> dict:
    return {"name": None, "slug": None, "kind": None, "seq": None,
            "status": None, "stage": None, "unit": None, "date": None, "period": None}


def _match_tracked(segs: list[str], conv: dict) -> dict | None:
    anchors = {a.lower() for a in conv["entity_anchors"]}
    non_entity = {n.lower() for n in conv["non_entity_names"]}
    for i, seg in enumerate(segs):
        if seg.lower().split()[0] not in anchors and seg.lower() not in anchors:
            continue
        # entity is the first numbered child, allowing one year-ish intermediate folder
        for j in (i + 1, i + 2):
            if j >= len(segs):
                break
            if j == i + 2 and not _YEARISH_SEG.match(segs[i + 1]):
                break
            m = _NUMBERED.match(segs[j].strip())
            if not m:
                continue
            raw = m.group(2).strip()
            if _FILE_EXT.search(raw) or raw.lower().split()[0] in non_entity:
                break
            name, status = _split_status(raw, conv["status_markers"])
            return {"name": name, "slug": slugify(name), "kind": "tracked",
                    "seq": int(m.group(1)), "status": status}
    return None


def _match_prospect(segs: list[str], conv: dict) -> dict | None:
    anchors = {a.lower() for a in conv["prospect_anchors"]}
    for i, seg in enumerate(segs):
        if seg.lower() not in anchors or i + 2 >= len(segs):
            continue
        stage, raw = segs[i + 1], segs[i + 2].strip()
        if _FILE_EXT.search(raw):  # a lone file under a stage folder is not the entity
            continue
        name, status = _split_status(raw, conv["status_markers"])
        return {"name": name, "slug": slugify(name), "kind": "prospect",
                "stage": stage, "status": status}
    return None


def build_catalog(paths_with_unit, conventions: dict | None = None) -> dict:
    """Pass 1: sweep all paths and return the high-confidence {slug: name} entity catalog
    (strict anchor matches only). This is the canonical list for pass 2."""
    conv = conventions or load_conventions()
    cat: dict[str, str] = {}
    for path, unit_hint in paths_with_unit:
        e = resolve_entity(path, unit_hint, conventions=conv)
        if e["slug"]:
            cat.setdefault(e["slug"], e["name"])
    return cat


def resolve_entity(path: str, unit_hint=None, catalog=None, conventions: dict | None = None) -> dict:
    """path -> {name, slug, kind, seq, status, stage, unit, date, period}. Fields None when N/A.
    With `catalog` (from build_catalog), pass 2 also matches any `N. Name` segment or folder whose
    slug is already a known entity — recovering docs under non-standard anchors."""
    conv = conventions or load_conventions()
    out = _empty()
    segs = _segments(path)

    hit = _match_tracked(segs, conv) or _match_prospect(segs, conv)
    if hit:
        out.update(hit)

    if not out["slug"] and catalog:
        for seg in segs:
            m = _NUMBERED.match(seg.strip())
            raw = m.group(2).strip() if m else seg.strip()
            if _FILE_EXT.search(raw):
                continue
            name, status = _split_status(raw, conv["status_markers"])
            sl = slugify(name)
            if len(sl) >= 4 and sl in catalog:
                out.update(name=catalog[sl], slug=sl, kind="tracked", status=status,
                           seq=int(m.group(1)) if m else None)
                break

    years = _YEAR.findall(path)
    if years:
        out["date"] = max(years)
        q = _QTR.search(path)
        if q:
            out["period"] = f'{out["date"]}-Q{q.group(1)}'
    out["unit"] = unit_hint
    return out
