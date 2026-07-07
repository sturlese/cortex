# Deploy

Two independent stacks, each with its own `docker-compose.yml` and its own `.env` (secrets,
**never** in git):

- **pipeline** (`pipeline/`) — fetch + clean + gotenberg (+ graph profile). Outbound-only
  (Drive + LLM APIs). No database credentials.
- **gbrain** (`gbrain/`) — serve + autopilot + tailscale (+ ingest profile). DB on Supabase.

Any small VM works (the reference setup ran both stacks on ~4 GB RAM).

## Pipeline stack

```bash
cd pipeline
cp .env.example .env       # DRIVE_FOLDER, gog keyring, OPENAI_API_KEY (see comments in the file)
docker compose build
docker compose up -d
docker compose logs -f clean
```

One-time seeds (volumes):

- **gog OAuth** (Drive auth): interactive login into the `gog-state` volume — see
  [pipeline/fetch.md](../pipeline/fetch.md#auth-gog).
- Remember: `CLEAN_DRY_RUN` defaults to `true` (no-op). Flip it in `.env` when ready.

## gbrain stack

```bash
cd gbrain
cp .env.example .env
make up            # build + start
make doctor        # full health: dims, RLS, embeddings
make ts-funnel     # public URL
make logs / ps / down / nuke (⚠️ destroys volumes incl. tailscale identity)
```

Clean bring-up order:

1. `.env` with REAL values (no placeholders left): both DB URLs, `OPENAI_API_KEY`, `TS_AUTHKEY`,
   `GBRAIN_PUBLIC_URL`, `GBRAIN_ADMIN_BOOTSTRAP_TOKEN`
   (`head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 48`).
2. Tailscale admin: grant the `funnel` capability in the tailnet ACL **before** waiting on DNS
   (runbook Gotcha 2).
3. `make up`, verify internal `/health` → `make ts-funnel` → public `/health`.

## Validate without starting anything

```bash
docker compose -f pipeline/docker-compose.yml config >/dev/null
docker compose -f gbrain/docker-compose.yml config >/dev/null
```

## Sync code to a box

```bash
rsync -az --exclude=.env --exclude=.venv* --exclude=__pycache__ ./ user@box:/opt/cortex/
```
