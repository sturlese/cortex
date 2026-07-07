# fetch — Drive mirror

`pipeline/fetch/src/drive_fetch.py`. Mirrors one Google Drive folder into `raw/`, incrementally.
Deterministic, stdlib-only, no LLM. Strict boundary: it never writes Markdown and never calls an LLM.

## How it works

1. Resolve the root folder: `DRIVE_FOLDER_ID` wins; otherwise search `DRIVE_FOLDER` by name
   (ambiguity → hard error telling you to pin the id).
2. `gog drive inventory --parent <id>` lists the tree recursively.
3. For each file, compare the fingerprint `modifiedTime|size|md5` against the manifest
   (`raw/_state.json`). Only new/changed files are downloaded (atomically: temp file + rename).
4. Native Google types are exported (Docs → `GOOGLE_DOCS_FORMAT`, Sheets → `xlsx` so every tab
   survives, Slides → `pdf`); binaries download as-is. Files land as `<fileId><ext>` plus a
   `<fileId>.json` metadata sidecar carrying the reconstructed `drivePath` (lineage for clean).
5. Files that disappeared from Drive are deleted from `raw/` and the manifest — deletions propagate.

Unchanged files still get their path metadata backfilled (cheap) so lineage improves without
re-downloads.

## Auth (gog)

Drive auth is owned by the [`gog` CLI](https://github.com/openclaw/gogcli), never by this script.
In containers use the file keyring:

```bash
# .env: GOG_KEYRING_BACKEND=file, GOG_KEYRING_PASSWORD=<random>, GOG_ACCOUNT=<email>
# one-time interactive login INTO the gog-state volume:
docker compose run --rm --entrypoint sh fetch
gog auth add <email> --services drive        # follow the OAuth flow; token lands in GOG_HOME
```

The OAuth `client_secret` also lives inside the volume — never in git.

## ENV

| Var | Default | Meaning |
|---|---|---|
| `DRIVE_FOLDER` / `DRIVE_FOLDER_ID` | — | what to mirror (define one; id wins) |
| `RAW_DIR` | `/data/raw` | mirror dir (raw-drive volume) |
| `POLL_INTERVAL_SECONDS` | `1800` | loop cadence (`--once` for a single pass) |
| `GOOGLE_DOCS_FORMAT` | `md` | export format for native Google Docs |
| `GOG_ACCOUNT` | keyring default | gog account |
| `GOG_ALL_DRIVES` | `true` | include shared drives |
