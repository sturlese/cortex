#!/usr/bin/env bash
# gbrain MCP over stdio, for local MCP clients (e.g. Claude Desktop).
#
# Runs the gbrain image (built by this stack: `docker compose build gbrain-serve`)
# with the environment from ./.env, on the compose network so it reaches the same
# database. Portable: no absolute paths, docker taken from PATH.
#
# Claude Desktop config example (claude_desktop_config.json):
#   { "mcpServers": { "gbrain": { "command": "/path/to/cortex/gbrain/mcp-stdio.sh" } } }
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${GBRAIN_ENV_FILE:-$HERE/.env}"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (cp .env.example .env and fill it in)" >&2; exit 1; }

exec docker run -i --rm \
  --env-file "$ENV_FILE" \
  ${GBRAIN_DOCKER_NETWORK:+--network "$GBRAIN_DOCKER_NETWORK"} \
  -v gbrain_gbrain-data:/data \
  gbrain:local serve
