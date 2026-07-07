# Runbook — real gotchas and fixes

Problems actually hit while standing this up, with diagnosis and fix.

## gbrain

### Gotcha 1 — Migrations fail: `ECONNREFUSED <ipv6>:5432` (Supabase direct host is IPv6-only)

**Symptom:** `gbrain-serve` crash-loops (unhealthy); `relation "oauth_clients" does not exist`;
`Schema probe failed: connect ECONNREFUSED 2a05:...:5432`.

**Cause:** gbrain dual-pools: runtime through the transaction pooler (`:6543`, IPv4) and
DDL/migrations through the direct host (`db.<ref>.supabase.co:5432`), which on Supabase is
**IPv6-only** — most VMs can't reach it.

**Fix (`.env`):** point the direct URL at the **session pooler** (same host, port 5432 — IPv4):

```
DATABASE_URL=postgresql://postgres.<ref>:<pass>@aws-0-<region>.pooler.supabase.com:6543/postgres
GBRAIN_DIRECT_DATABASE_URL=postgresql://postgres.<ref>:<pass>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Then `docker compose up -d --force-recreate gbrain-serve`.

### Gotcha 2 — Funnel says "on" but the public URL is NXDOMAIN (missing ACL grant)

**Symptom:** `tailscale funnel status` = on, cert OK, but `https://<host>.<tailnet>.ts.net` →
NXDOMAIN. Works internally, unreachable publicly.

**Cause:** "funnel on" is node-local config. If the tailnet ACL doesn't grant the `funnel`
capability, the control plane never publishes the public DNS record.

**Fix (Tailscale admin console — not bakeable into the repo):**

```json
"nodeAttrs": [ { "target": ["autogroup:member"], "attr": ["funnel"] } ]
```

Plus HTTPS Certificates ON + MagicDNS ON. Then `docker compose restart tailscale`. First `curl`
after the fix can take ~30-60s (ingress path warms up).

### Minor gotchas

- `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` empty → generate: `head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 48`.
- Placeholders left in `.env` → grep for `PROJECTREF`, `REGION`, `YOUR-`, `sk-...` before boot.
- `GBRAIN_PUBLIC_URL` chicken-and-egg: boot anyway, get the FQDN from
  `tailscale status --json | grep DNSName`, set it, recreate `gbrain-serve`.
- Pooler saturation (free tier ≈ 60 connections; autopilot + clients compete): upgrade the plan or
  reduce concurrency.
- Minimal Ubuntu images lack `make`: `apt-get update && apt-get install -y make`.

## Pipeline stack

### gog hangs locally (macOS Keychain)

`gog drive ls` hangs ~10s waiting on a Keychain prompt. **Local fix:** run it once in a terminal
and click "Always Allow". **Containers:** never use the Keychain — `GOG_KEYRING_BACKEND=file` +
`GOG_KEYRING_PASSWORD`.

### gogcli 404 at image build

The project moved (`steipete/gogcli` → `openclaw/gogcli`) and the binary inside the tarball is
named `gog`. The correct URL is baked into `pipeline/fetch/Dockerfile`; if a version bump 404s,
check the repo's Releases page first.

### clean operations

```bash
# full reprocess (state is idempotent by hash):
docker compose rm -sf clean            # 'stop' does NOT release the volume
docker volume rm cortex-pipeline_clean-state
docker compose up -d clean
```

- `volume rm` says "in use" → a stopped container still holds it; `docker compose rm -sf clean` first.
- Persistent 429 (provider rate limit) → the pass aborts cleanly; pending docs resume on relaunch.
- `manual_review` pages = the agent could not obtain usable content even after escalating to its
  OCR tool (or the tool was disabled — no `GEMINI_API_KEY`). They carry a warning banner; fix the
  source or enable OCR and reprocess.

## Pattern: historyless git over brain-md

To prune destructively (Drive deletions propagate) **without retaining sensitive history**:

```bash
# once:
cd /data/brain-md && git init && git commit --allow-empty -m base
# each sync cycle:
git add -A && git commit --amend --no-edit    # folds current state into a SINGLE root commit
git gc --prune=now                            # orphaned blobs leave the disk
```

If git complains about "dubious ownership" inside a container:
`git config --global --add safe.directory /data/brain-md`.
