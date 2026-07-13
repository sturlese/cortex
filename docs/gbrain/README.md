# gbrain — the brain server (vector-search option)

`gbrain/` is a **deploy wrapper** around [gbrain](https://github.com/garrytan/gbrain), an external
MCP memory engine (Bun/TypeScript, Postgres + pgvector). It ingests the `brain-md` corpus, embeds
it, and serves semantic search + retrieval to MCP clients with per-client OAuth scoping.

> Serving options: gbrain brings embeddings and multi-user OAuth; the first-party
> [`answer` server](../answer.md) brings the pipeline's guarantees (contract-aware ranking,
> exact facts, deterministically verified answers). They read the same volume — run either or both.

The wrapper pins gbrain to an immutable commit (`GBRAIN_REF` in `.env`) and runs:

| Container | Command | Role |
|---|---|---|
| `gbrain-serve` | `gbrain serve --http` | the MCP endpoint (:3131, private) |
| `gbrain-autopilot` | `gbrain autopilot` | background enrichment |
| `tailscale` | funnel sidecar | the ONLY public ingress (:443 → :3131) |
| `ingest` (profile) | sync loop | `GBRAIN_SYNC_PATH` → database, every `GBRAIN_SYNC_INTERVAL`s |

The ingest loop indexes `/data/brain-md` by default; set `GBRAIN_SYNC_PATH=/data/brain-md-graphed`
to index the graph layer (wikilinks + entity nodes) instead, if you run the pipeline's graph stage.

## Setup decisions

- **Database:** Supabase Postgres. Use the *transaction pooler* string (`:6543`) as `DATABASE_URL`
  and the *session pooler* (`:5432`) as `GBRAIN_DIRECT_DATABASE_URL` — see
  [operations/runbook.md](../operations/runbook.md) Gotcha 1 for why.
- **Embeddings:** `openai:text-embedding-3-small` (1536d) by default — cheap, and OpenAI supports a
  hard monthly spend cap. Dimensions are fixed at first boot; changing models later = full reindex.
- **Context tier:** pages carry `contextual_retrieval: title` in frontmatter — the title is
  prepended to each chunk at embedding time. Free context, no LLM.
- **Ingress:** no published ports; a Tailscale Funnel gives you a stable public HTTPS URL without
  opening the box.

## Bring-up

```bash
cd gbrain
cp .env.example .env      # fill in: DB URLs, OPENAI_API_KEY, TS_AUTHKEY, admin token, public URL
make up
make doctor               # checks dims / RLS / embeddings against the DB
make ts-funnel            # shows the public URL once the Funnel is live
docker volume create brain-md && docker volume create brain-md-graphed   # one-time (shared with the pipeline stack)
docker compose --profile ingest up -d ingest    # start syncing brain-md
```

The `ingest` profile mounts the external `brain-md` / `brain-md-graphed` volumes, so create them
once (as above) if you haven't already from the pipeline stack — otherwise the command aborts with
`external volume "brain-md" not found`.

`gbrain-serve` applies schema migrations on boot (idempotent). Success looks like
`N migration(s) applied` + `Engine: postgres` + a healthy container.

Upgrades: bump `GBRAIN_REF` in `.env`, `make up`.

## Local / stdio client

`mcp-stdio.sh` runs the same image over stdio for local MCP clients (Claude Desktop):

```json
{ "mcpServers": { "gbrain": { "command": "/path/to/cortex/gbrain/mcp-stdio.sh" } } }
```

## Registering clients

See [oauth-clients.md](oauth-clients.md) — including the exact redirect URIs Claude and ChatGPT
need, and how to scope what each client can read.
