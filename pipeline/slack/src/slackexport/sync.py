#!/usr/bin/env python3
"""slackexport — SECOND SOURCE CONNECTOR: a Slack export becomes clean's inventory.

The proof that the ingestion abstraction generalizes is a contract, not a framework (ADR 011):
any connector that mirrors files into a raw dir and writes `_state.json` entries of
{name, localPath, drivePath, sourceUri, orgUnit, mimeType} gets the WHOLE downstream for free —
clean's agentic pages, verification, facts, versions, dossiers, the graph and the answer server,
all unchanged.

This connector reads a standard Slack workspace export (the ZIP admins download: `channels.json`,
`users.json`, `<channel>/<YYYY-MM-DD>.json`) — deterministic, offline, no tokens:

- one document per channel-month (`slack/<channel>/<YYYY-MM>.md`): conversation rendered with
  resolved display names and thread replies indented under their parents, in timestamp order;
- stable ids (`slack-<sha1(channel/month)>`), content fingerprints for incremental sync, and
  mirror semantics: a channel-month gone from the export leaves the raw dir and the inventory;
- channel -> orgUnit (so pages land under units/<channel> and ACL rules can target them).

Usage:  python -m slackexport.sync --export <dir|zip> --raw <out-dir> [--team <domain>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

STATE_FILE = "_state.json"


def load_export(export_path: str) -> Path:
    """Accepts the export directory or the ZIP; ZIPs are unpacked to a temp dir."""
    p = Path(export_path)
    if p.is_dir():
        return p
    if p.is_file() and p.suffix == ".zip":
        out = Path(tempfile.mkdtemp(prefix="slack-export-"))
        with zipfile.ZipFile(p) as z:
            z.extractall(out)
        return out
    raise FileNotFoundError(f"export not found (dir or .zip): {export_path}")


def load_users(root: Path) -> dict[str, str]:
    """user id -> display name (fallbacks: real_name, name, the id)."""
    try:
        users = json.loads((root / "users.json").read_text())
    except FileNotFoundError:
        return {}
    out = {}
    for u in users:
        profile = u.get("profile") or {}
        out[u.get("id", "")] = (profile.get("display_name") or profile.get("real_name")
                                or u.get("name") or u.get("id", ""))
    return out


def _ts(msg: dict) -> float:
    try:
        return float(msg.get("ts", 0))
    except (TypeError, ValueError):
        return 0.0


def _substitute_mentions(text: str, users: dict[str, str]) -> str:
    return re.sub(r"<@([A-Z0-9]+)>", lambda m: "@" + users.get(m.group(1), m.group(1)), text or "")


def render_month(channel: str, month: str, messages: list[dict], users: dict[str, str]) -> str:
    """Deterministic conversation document: top-level messages in ts order, replies indented.
    Replies whose parent is not in this month are promoted to top level (marked ↳) — Slack files
    each reply under the day it was POSTED, so threads crossing a month boundary (or replies to
    a tombstoned root) would otherwise vanish from the mirror."""
    replies: dict[str, list[dict]] = defaultdict(list)
    top: list[dict] = []
    for m in sorted(messages, key=_ts):
        if not m.get("text") and not m.get("files"):
            continue
        thread = m.get("thread_ts")
        if thread and thread != m.get("ts"):
            replies[thread].append(m)
        else:
            top.append(m)

    # orphaned replies: their parent lives in another month (or was dropped as empty) — keep them
    top_ts = {m.get("ts") for m in top}
    orphan_ts: set[str] = set()
    for thread in [t for t in replies if t not in top_ts]:
        for r in replies.pop(thread):
            orphan_ts.add(r.get("ts", ""))
            top.append(r)
    top.sort(key=_ts)

    def line(m: dict, indent: str = "") -> str:
        when = datetime.fromtimestamp(_ts(m), tz=UTC).strftime("%Y-%m-%d %H:%M")
        who = users.get(m.get("user", ""), m.get("user") or m.get("username") or "unknown")
        text = _substitute_mentions(m.get("text", ""), users).replace("\n", f"\n{indent}  ")
        return f"{indent}{when} — {who}: {text}"

    lines = [f"#{channel} — {month} (Slack)", ""]
    for m in top:
        mark = "↳ " if m.get("ts", "") in orphan_ts else ""
        lines.append(mark + line(m))
        for r in replies.get(m.get("ts", ""), []):
            lines.append(line(r, indent="    ↳ "))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect_months(root: Path) -> dict[tuple[str, str], list[dict]]:
    """{(channel, YYYY-MM): messages} from the export's <channel>/<YYYY-MM-DD>.json files."""
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for day_file in sorted(root.glob("*/????-??-??.json")):
        channel = day_file.parent.name
        month = day_file.stem[:7]
        try:
            msgs = json.loads(day_file.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[slack-export] skipping malformed {day_file}", file=sys.stderr)
            continue
        if isinstance(msgs, list):
            out[(channel, month)].extend(m for m in msgs if isinstance(m, dict))
    return out


def doc_id(channel: str, month: str) -> str:
    return "slack-" + hashlib.sha1(f"{channel}/{month}".encode()).hexdigest()[:12]


def sync(export_path: str, raw_dir: str, team: str = "") -> dict:
    """Export -> raw mirror + inventory. Incremental by content hash; deletions propagate."""
    root = load_export(export_path)
    users = load_users(root)
    months = collect_months(root)
    raw = Path(raw_dir)
    raw.mkdir(parents=True, exist_ok=True)
    state_path = raw / STATE_FILE
    try:
        state = json.loads(state_path.read_text())
        if not isinstance(state, dict):
            state = {"files": {}}
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        state = {"files": {}}
    files: dict = state.setdefault("files", {})

    seen: set[str] = set()
    written = unchanged = 0
    for (channel, month), messages in sorted(months.items()):
        body = render_month(channel, month, messages, users)
        if len(body.splitlines()) <= 2:
            continue                     # nothing but the header: empty month
        fid = doc_id(channel, month)
        seen.add(fid)
        rel = f"slack/{channel}/{month}.md"
        fingerprint = hashlib.md5(body.encode("utf-8")).hexdigest()
        prev = files.get(fid)
        target = raw / rel
        if prev and prev.get("fingerprint") == fingerprint and target.exists():
            unchanged += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(target)
        files[fid] = {
            # the source contract clean consumes (ADR 011) — nothing more, nothing less
            "name": f"{channel} {month}.md",
            "localPath": rel,
            "drivePath": f"/Slack/{channel}/{month}.md",
            "orgUnit": channel,
            "sourceUri": (f"https://{team}.slack.com/archives/{channel}" if team
                          else f"slack://{channel}/{month}"),
            "mimeType": "text/markdown",
            "fingerprint": fingerprint,
        }
        written += 1

    removed = 0
    for fid in [f for f in files if f.startswith("slack-") and f not in seen]:
        rel = files[fid].get("localPath")
        if rel and (raw / rel).exists():
            (raw / rel).unlink()
        del files[fid]
        removed += 1

    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)
    return {"channels_months": len(months), "written": written,
            "unchanged": unchanged, "removed": removed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="slack-export",
        description="Mirror a Slack workspace export into a clean-compatible raw dir (offline, no tokens).")
    parser.add_argument("--export", required=True, help="export directory or .zip")
    parser.add_argument("--raw", required=True, help="output raw dir (clean's RAW_DIR)")
    parser.add_argument("--team", default="", help="workspace domain for permalink sourceUris (optional)")
    args = parser.parse_args(argv)
    try:
        stats = sync(args.export, args.raw, team=args.team)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"slack-export: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
