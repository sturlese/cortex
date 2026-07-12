#!/usr/bin/env python3
"""
drive_fetch.py — DOWNLOAD stage of the ingestion pipeline.

Mirrors a Google Drive folder -> raw/ using the `gog` CLI. DETERMINISTIC, no LLM.
Strict boundary: it does NOT write Markdown, does NOT talk to the brain server, does NOT call an LLM.

Incremental model: folder-scoped via `gog drive inventory --parent <folder>` (recursive listing)
plus a MANIFEST keyed by Drive file id in raw/_state.json. Each run: list the folder, compare each
file's fingerprint (modifiedTime) against the manifest, and download only new/changed files;
whatever disappeared from the folder is removed from raw/ and from the manifest (deletions
propagate).

CREDENTIALS: none hardcoded. All configuration is read ONCE, in `Config.from_env()` at the
entrypoint, and passed down explicitly — this module never reads the environment at import time.
Drive auth is managed by `gog` (its keyring, via GOG_KEYRING_*).

ENV (see Config.from_env):
  DRIVE_FOLDER / DRIVE_FOLDER_ID   what to mirror (define one; id wins)
  GOG_ACCOUNT                      (optional) gog account (-a); empty = keyring default
  RAW_DIR                          default /data/raw
  POLL_INTERVAL_SECONDS            default 1800
  GOOGLE_DOCS_FORMAT               default md (export for native Google Docs)
  GOG_BIN                          default gog
  GOG_ALL_DRIVES                   default true (include shared drives)

Usage:  drive_fetch.py [--once]   (default: loop)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class Config:
    """All runtime configuration, constructed once at the entrypoint."""
    folder: str = ""              # root folder by NAME (the script resolves its id)
    folder_id: str = ""           # explicit id (wins when both are set)
    account: str = ""
    raw_dir: Path = Path("/data/raw")
    poll_seconds: int = 1800
    docs_format: str = "md"
    gog_bin: str = "gog"
    all_drives: bool = True

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            folder=os.environ.get("DRIVE_FOLDER", "").strip(),
            folder_id=os.environ.get("DRIVE_FOLDER_ID", "").strip(),
            account=os.environ.get("GOG_ACCOUNT", "").strip(),
            raw_dir=Path(os.environ.get("RAW_DIR", "/data/raw")),
            poll_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "1800")),
            docs_format=os.environ.get("GOOGLE_DOCS_FORMAT", "md").strip(),
            gog_bin=os.environ.get("GOG_BIN", "gog").strip(),
            all_drives=os.environ.get("GOG_ALL_DRIVES", "true").strip().lower() in ("1", "true", "yes"),
        )

    @property
    def state_path(self) -> Path:
        return self.raw_dir / "_state.json"

    def export_for(self, mime: str) -> tuple[str | None, str] | None:
        """Native Google types -> (export_format, extension); None for binary files.
        xlsx (not csv) for Sheets: csv only exports the first tab; clean reads every sheet."""
        table = {
            "application/vnd.google-apps.document": (self.docs_format, "." + self.docs_format),
            "application/vnd.google-apps.spreadsheet": ("xlsx", ".xlsx"),
            "application/vnd.google-apps.presentation": ("pdf", ".pdf"),
        }
        return table.get(mime)


class GogError(RuntimeError):
    pass


def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[drive-fetch {ts}] {msg}", flush=True)


# ── gog wrapper ──────────────────────────────────────────────────────────────
def gog(cfg: Config, *args: str, json_out: bool = True) -> object:
    """Runs `gog ...` (+ -a account, --no-input). With json_out, parses stdout."""
    cmd = [cfg.gog_bin, *args, "--no-input"]
    if cfg.account:
        cmd += ["-a", cfg.account]
    if json_out:
        cmd += ["--json"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise GogError(f"`gog {' '.join(args)}` rc={res.returncode}: {res.stderr.strip()[:600]}")
    if not json_out:
        return res.stdout
    try:
        return json.loads(res.stdout or "null")
    except json.JSONDecodeError as e:
        raise GogError(f"`gog {' '.join(args)}` non-JSON output: {e}; head={res.stdout[:200]!r}") from e


def _items(payload: object) -> list[dict]:
    """Extracts the file list from gog's JSON defensively (envelope shapes vary by version)."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("files", "items", "entries", "result", "results", "data"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        if payload.get("id") or payload.get("fileId"):  # a single object
            return [payload]
    return []


def _field(d: dict, *names: str, default=None):
    for n in names:
        if d.get(n) not in (None, ""):
            return d[n]
    return default


def file_id(d: dict) -> str | None:
    return _field(d, "id", "fileId")


def fingerprint(d: dict) -> str:
    """Change fingerprint: modifiedTime (+ size/md5 as reinforcement when present)."""
    mt = _field(d, "modifiedTime", "modified", "modifiedDate", default="")
    sz = _field(d, "size", "fileSize", default="")
    md5 = _field(d, "md5Checksum", "md5", default="")
    return f"{mt}|{sz}|{md5}"


def parents(d: dict) -> list[str]:
    """Drive parents as a list of ids, tolerating different gog envelopes."""
    raw = _field(d, "parents", "parentIds", "parentId", default=[])
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        out: list[str] = []
        for p in raw:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                pid = _field(p, "id", "fileId")
                if pid:
                    out.append(pid)
        return out
    return []


def _path_join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}".replace("//", "/")


def build_lineage(cfg: Config, items: list[dict], root_id: str) -> dict[str, dict]:
    """Derives drivePath/parentPath from the recursive inventory. Respects explicit path fields
    when gog provides them; falls back to the root when an ancestor is missing (never blocks)."""
    root_label = cfg.folder or root_id
    by_id = {fid: d for d in items if (fid := file_id(d))}
    memo: dict[str, str] = {root_id: f"/{root_label}"}

    def path_for(fid: str) -> str:
        if fid in memo:
            return memo[fid]
        d = by_id.get(fid)
        if not d:
            return f"/{root_label}"
        explicit = _field(d, "path", "drivePath", "fullPath")
        if isinstance(explicit, str) and explicit.startswith("/"):
            memo[fid] = explicit
            return explicit
        name = _field(d, "name", "title", default=fid)
        ps = parents(d)
        parent_id = ps[0] if ps else root_id
        parent_path = path_for(parent_id) if parent_id != fid else f"/{root_label}"
        memo[fid] = _path_join(parent_path, name)
        return memo[fid]

    out: dict[str, dict] = {}
    for d in items:
        fid = file_id(d)
        if not fid:
            continue
        ps = parents(d)
        parent_id = ps[0] if ps else root_id
        out[fid] = {
            "drivePath": path_for(fid),
            "parentPath": path_for(parent_id),
            "parentIds": ps,
        }
    return out


# ── state (manifest + atomic writes) ─────────────────────────────────────────
def load_state(cfg: Config) -> dict:
    if cfg.state_path.exists():
        try:
            state = json.loads(cfg.state_path.read_text())
            if isinstance(state, dict):
                return state
            # valid JSON but not an object (e.g. `[]`, `null`): corrupt in the same spirit as a
            # parse error -- return it and sync_once crashes at state.setdefault("files", ...).
            log("_state.json is not an object, re-initializing")
        except (json.JSONDecodeError, UnicodeDecodeError):
            # malformed JSON, or read_text() hitting non-UTF-8 bytes: either way the file's
            # contents are corrupt -> re-initialize (access errors like OSError still propagate).
            log("_state.json corrupted, re-initializing")
    return {"files": {}}


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)  # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_state(cfg: Config, state: dict) -> None:
    write_atomic(cfg.state_path, json.dumps(state, indent=2, ensure_ascii=False).encode())


# ── download ─────────────────────────────────────────────────────────────────
def ext_for(cfg: Config, d: dict, mime: str) -> tuple[str | None, str]:
    """(export_format|None, extension). Native Google types export; binaries as-is."""
    exported = cfg.export_for(mime)
    if exported:
        return exported
    name = _field(d, "name", "title", default="")
    return None, (os.path.splitext(name)[1] or ".bin")


def sidecar_name(fid: str) -> str:
    """The metadata sidecar's filename — the on-disk contract clean reads. Single-sourced."""
    return f"{fid}.json"


def content_name(cfg: Config, d: dict, mime: str, fid: str) -> tuple[str | None, str]:
    """(export_format|None, on-disk content filename), disambiguated from the sidecar so content
    can never overwrite <fid>.json. Compared case-insensitively for case-folding filesystems
    (macOS APFS, Windows, Docker Desktop bind mounts), where <fid>.JSON == <fid>.json."""
    fmt, ext = ext_for(cfg, d, mime)
    name = f"{fid}{ext}"
    if name.lower() == sidecar_name(fid).lower():
        name = f"{fid}.data{ext}"
    return fmt, name


def _clobbered_by_sidecar(cfg: Config, fid: str, prev_local: str | None) -> bool:
    """True if the manifest's content path IS the sidecar file — its bytes were overwritten by the
    sidecar write (the pre-.data.json bug), so the entry must re-download to heal. Decided by file
    identity, not name: an exact sidecar-name localPath is always the clobber; a case-variant (e.g.
    <fid>.JSON) is the clobber only on a case-folding filesystem, where it resolves to the same file
    as the sidecar — on a case-sensitive one it is a distinct, valid content file that must be kept
    (else it would be needlessly re-downloaded and orphaned)."""
    if not prev_local:
        return False
    sidecar = sidecar_name(fid)
    if prev_local == sidecar:
        return True
    if prev_local.lower() != sidecar.lower():
        return False
    try:
        return (cfg.raw_dir / prev_local).samefile(cfg.raw_dir / sidecar)
    except OSError:  # content or sidecar missing -> not a live clobber; let the exists() skip re-fetch
        return False


def merge_sidecar_lineage(cfg: Config, fid: str, lineage: dict) -> None:
    """Adds lineage paths to an existing sidecar without touching the rest of its metadata."""
    sidecar = cfg.raw_dir / sidecar_name(fid)
    meta: object = {}
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text())
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta.update(lineage)
    write_atomic(sidecar, json.dumps(meta, indent=2, ensure_ascii=False).encode())


def download_file(cfg: Config, d: dict, fid: str, mime: str, lineage: dict) -> str:
    """Downloads to raw/<content-name> (atomically) + a metadata sidecar. Returns the local name.
    The content name is disambiguated from the sidecar (<fid>.json) so the two never collide."""
    fmt, name = content_name(cfg, d, mime, fid)
    out = cfg.raw_dir / name
    tmp = cfg.raw_dir / f".tmp-{name}"
    args = ["drive", "download", fid, "--out", str(tmp)]
    if fmt:
        args += ["--format", fmt]
    gog(cfg, *args, json_out=False)
    os.replace(tmp, out)
    # sidecar (Files.Get) — clean uses it for frontmatter/lineage. gog wraps it in {"file": {...}}.
    meta = gog(cfg, "drive", "get", fid)
    if isinstance(meta, dict) and isinstance(meta.get("file"), dict):
        meta = meta["file"]
    if isinstance(meta, dict):
        meta.update(lineage)
    write_atomic(cfg.raw_dir / sidecar_name(fid), json.dumps(meta, indent=2, ensure_ascii=False).encode())
    return out.name


def remove_local(cfg: Config, entry: dict, fid: str) -> None:
    for p in (entry.get("localPath"), sidecar_name(fid)):
        if p and (cfg.raw_dir / p).exists():
            (cfg.raw_dir / p).unlink()


# ── folder resolution (name -> id) ───────────────────────────────────────────
def resolve_folder_id(cfg: Config) -> str:
    """folder_id wins; otherwise resolve the folder name -> id via search."""
    if cfg.folder_id:
        return cfg.folder_id
    if not cfg.folder:
        raise GogError("set DRIVE_FOLDER (name) or DRIVE_FOLDER_ID")
    safe = cfg.folder.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{safe}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    matches = _items(gog(cfg, "drive", "search", q, "--results-only"))
    exact = [m for m in matches if _field(m, "name", "title") == cfg.folder]
    matches = exact or matches
    if not matches:
        raise GogError(f"no folder named '{cfg.folder}' found")
    if len(matches) > 1:
        opts = ", ".join(f"{_field(m, 'name')}={file_id(m)}" for m in matches[:8])
        raise GogError(f"'{cfg.folder}' is ambiguous ({len(matches)} folders): {opts}. "
                       f"Set DRIVE_FOLDER_ID to disambiguate.")
    fid = file_id(matches[0])
    log(f"folder '{cfg.folder}' -> id {fid}")
    return fid


# ── incremental sync ─────────────────────────────────────────────────────────
def inventory(cfg: Config, folder_id: str) -> list[dict]:
    payload = gog(
        cfg, "drive", "inventory",
        "--parent", folder_id,
        "--depth", "0", "--max", "0",  # 0 = unlimited (recursive, everything)
        *(["--all-drives"] if cfg.all_drives else ["--no-all-drives"]),
        "--results-only",
    )
    return _items(payload)


def sync_once(cfg: Config, folder_id: str) -> dict:
    state = load_state(cfg)
    manifest: dict = state.setdefault("files", {})
    items = inventory(cfg, folder_id)
    # Sanity brake: rc=0 with zero items while we hold prior state is almost always a gog/API glitch
    # (empty stdout, an envelope shape _items doesn't recognize, a trashed root), NOT a real mass
    # deletion — refuse rather than wipe the whole mirror (which clean would then propagate).
    if not items and manifest:
        raise GogError(f"inventory returned 0 items but the manifest has {len(manifest)} entries — "
                       "refusing to treat this as a mass deletion")
    lineage_by_id = build_lineage(cfg, items, folder_id)

    seen: set[str] = set()
    added = changed = skipped = metadata_updated = errors = 0
    for d in items:
        mime = _field(d, "mimeType", "mime", default="")
        if mime == FOLDER_MIME:
            continue  # folders are not downloaded
        fid = file_id(d)
        if not fid:
            continue
        seen.add(fid)
        fp = fingerprint(d)
        prev = manifest.get(fid)
        prev_local = prev.get("localPath") if prev else None
        lineage = lineage_by_id.get(fid, {
            "drivePath": f"/{cfg.folder or folder_id}/{_field(d, 'name', 'title', default=fid)}",
            "parentPath": f"/{cfg.folder or folder_id}",
            "parentIds": parents(d),
        })
        # A localPath that is really the sidecar file is a pre-.data.json entry whose content the
        # sidecar write clobbered: treat it as absent so the file re-downloads and heals, and so the
        # rename-cleanup below never unlinks the freshly written sidecar.
        if _clobbered_by_sidecar(cfg, fid, prev_local):
            prev_local = None
        if prev and prev.get("fingerprint") == fp and prev_local and (cfg.raw_dir / prev_local).exists():
            # Backfill/refresh path metadata without redownloading unchanged files.
            touched = False
            for k, v in lineage.items():
                if prev.get(k) != v:
                    prev[k] = v
                    touched = True
            if touched:
                metadata_updated += 1
                merge_sidecar_lineage(cfg, fid, lineage)
            skipped += 1
            continue
        # new or changed -> download + sidecar + update manifest (AFTER the file landed).
        # Isolate per-file failures: an undownloadable file (Google Form, view-only, shortcut)
        # must not abort the pass — it stays in `seen` (not deleted) and retries next cycle.
        try:
            local = download_file(cfg, d, fid, mime, lineage)
        except GogError as e:
            errors += 1
            log(f"download failed for {fid} ({_field(d, 'name', 'title', default='?')}): {e}")
            continue
        # a rename or export-format change alters the local name: drop the previous file so its
        # stale content isn't orphaned in the mirror (deletion later only removes the current name).
        if prev_local and prev_local != local and (cfg.raw_dir / prev_local).exists():
            (cfg.raw_dir / prev_local).unlink()
        manifest[fid] = {
            "name": _field(d, "name", "title", default=""),
            "mimeType": mime,
            "fingerprint": fp,
            "localPath": local,
            **lineage,
        }
        changed += 1 if prev else 0
        added += 0 if prev else 1

    # deletions: in the manifest but no longer in the folder
    removed = 0
    for fid in list(manifest):
        if fid not in seen:
            remove_local(cfg, manifest[fid], fid)
            del manifest[fid]
            removed += 1

    save_state(cfg, state)
    return {"total": len(seen), "added": added, "changed": changed,
            "skipped": skipped, "metadata_updated": metadata_updated,
            "removed": removed, "errors": errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="drive-fetch",
                                     description="Mirror a Google Drive folder into raw/ (incremental, no LLM).")
    parser.add_argument("--once", action="store_true", help="single sync pass instead of looping")
    parser.add_argument("--loop", action="store_true", help="poll every POLL_INTERVAL_SECONDS (default)")
    once = parser.parse_args(argv).once

    cfg = Config.from_env()
    if not (cfg.folder or cfg.folder_id):
        log("ERROR: set DRIVE_FOLDER (name) or DRIVE_FOLDER_ID.")
        return 2
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)

    try:
        folder_id = resolve_folder_id(cfg)
    except GogError as e:
        log(f"ERROR resolving the folder: {e}")
        return 2

    log(f"start — folder={cfg.folder or '(id)'} id={folder_id} raw={cfg.raw_dir} "
        f"interval={cfg.poll_seconds}s once={once} account={cfg.account or '(default)'}")
    while True:
        t0 = time.monotonic()
        failed = False
        try:
            s = sync_once(cfg, folder_id)
            log(f"sync OK {s} ({time.monotonic() - t0:.1f}s)")
        except GogError as e:
            failed = True
            log(f"sync gog-error: {e}")  # doesn't kill the loop; retries next cycle
        except Exception as e:  # noqa: BLE001
            failed = True
            log(f"unexpected sync error: {e}")
        if once:
            return 1 if failed else 0   # a one-shot pass must report failure to the caller (cron/CI)
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
