# Ingestion pipeline

One compose stack (`pipeline/docker-compose.yml`, project `cortex-pipeline`) with four pieces:

| Service | What it does | Docs |
|---|---|---|
| `fetch` | mirrors a Drive folder into the `raw-drive` volume | [fetch.md](fetch.md) |
| `clean` | turns raw files into Markdown pages in `brain-md` | [clean.md](clean.md) |
| `gotenberg` | stateless sidecar: Office → PDF over HTTP | — |
| `graph` (profile) | derives entity nodes + wikilinks | [graph.md](graph.md) |
| `ops` (profile) | supervisor agent: telemetry → diagnosis → bounded actions → report | [ops.md](ops.md) |

`corpus` is not a service — it's an ad-hoc curation CLI for bootstrapping from a local copy
([corpus.md](corpus.md)).

## Operate

```bash
cd pipeline
cp .env.example .env      # fill in (see the file's comments)
docker volume create brain-md && docker volume create brain-md-graphed   # one-time: shared external volumes
docker compose build
docker compose up -d      # fetch + clean + gotenberg

docker compose logs -f clean
docker compose --profile graph run --rm graph        # graph layer, on demand
docker compose --profile ops run --rm ops            # supervisor: diagnose + report + learn
```

Safety defaults:

- `CLEAN_DRY_RUN=true` — clean boots as a no-op until you flip it to `false`.
- `CLEAN_MAX_DOCS=N` — bound a first run to N documents before committing to the whole corpus.
- Set a **hard spend cap** on your OpenAI key (platform.openai.com → Limits) before unleashing it.

## Reprocess from scratch

State is idempotent by content hash; to force a full rerun, drop clean's state volume:

```bash
docker compose rm -sf clean          # 'stop' does NOT release the volume; rm does
docker volume rm cortex-pipeline_clean-state
docker compose up -d clean
```

## Adaptive extraction (the agent's ocr tool)

Scanned or visual PDFs need no special mode: when the deterministic extraction comes out mangled,
the clean agent escalates **itself** to vision OCR during the same run (its `ocr()` tool, one shot
per document, hard-budgeted). Set `GEMINI_API_KEY` in `.env` to enable it; without a key the tool
degrades gracefully and such pages are flagged `extraction_quality: manual_review` instead.
Pages produced through OCR carry `extraction_method: vision` + `ocr_model:` in their frontmatter
(auditable provenance), and every pass reports `ocr_docs` in its stats.
