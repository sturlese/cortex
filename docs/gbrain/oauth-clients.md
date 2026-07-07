# OAuth clients (querying the brain over MCP)

How to register a client with the right scoping (write source + federated read). Hard-won details.

## Recipe

```bash
# 1) Create the client's WRITE source (once; an FK requires it to exist first)
docker compose exec -T gbrain-serve gbrain sources add <their-source> --path /data/<their-source>

# 2) Register the OAuth client
docker compose exec -T gbrain-serve gbrain auth register-client "<name>" \
  --scopes "read" \
  --source <their-source> \
  --federated-read "<comma-separated sources they may READ>" \
  --redirect-uri "<their client's — see table>"
```

Prints **`client_id` + `client_secret` ONCE**. Copy them then.

## The five rules that cost time

1. **Use `register-client`, NOT `auth create`.** `auth create` mints a bearer with **no source
   scoping** — it falls into the empty `default` source and sees nothing.
2. **The `--source` must exist beforehand** (FK). `gbrain sources add` first.
3. **Source names:** 1-32 chars, lowercase/digits, interior hyphens. **No underscores.**
4. **Without a correct `--federated-read` the client sees NOTHING** (the default source is empty).
   List the corpus sources it should read (e.g. the `GBRAIN_SYNC_SOURCE` of the ingest loop).
5. **Read-only by default.** `--scopes "read,write"` only if the client should write (into its own
   source, its isolated home).

## Redirect URI per client type

| Client | grant | `--redirect-uri` | Where credentials go |
|---|---|---|---|
| **Claude** (Desktop / claude.ai / mobile) | `authorization_code` | `https://claude.ai/api/mcp/auth_callback` | Settings → Connectors → Add → **Advanced settings**: Client ID (+ Secret) |
| **ChatGPT** (Developer Mode) | `authorization_code` (public, PKCE) | **per-connector**: `https://chatgpt.com/connector/oauth/<id>` (copy from the connector's **Callback URL** field) | Settings → Apps → Advanced → Developer mode → Create app; "User-Defined OAuth Client" |
| **Claude Code (CLI)** | `authorization_code` | `http://localhost/callback` + `http://127.0.0.1/callback` | `claude mcp add` |
| **Machine agent (script)** | `client_credentials` | — | client_id+secret → POST `/token` |

Notes:

- `authorization_code` + `refresh_token` are auto-inferred when you pass `--redirect-uri`.
- Confidential client (default) → there is a `client_secret`. For a public client (PKCE, no secret)
  add `--token-endpoint-auth-method none` (that's what ChatGPT needs).
- **ChatGPT:** each connector generates its own unique Callback URL — register *that exact one*,
  and don't recreate the app afterwards (it regenerates the id → mismatch). Paid plan; use the web
  app, not the desktop app.
- Sensitive sources: control exposure per channel via `--federated-read` — e.g. leave confidential
  sources out of the federated list for clients you don't fully trust.

## Manage / revoke

```bash
docker compose exec -T gbrain-serve gbrain auth list
docker compose exec -T gbrain-serve gbrain auth revoke-client "<client_id>"
# visual dashboard: https://<your-public-url>/admin   (GBRAIN_ADMIN_BOOTSTRAP_TOKEN)
```

## Client behavior (system prompt)

A client wired to the brain should follow the [page contract](../pipeline/brain-page-contract.md):
search the brain first; open originals (via `source_file_id`) only when `detail_in_source: true`
and the user needs exact/current figures; warn on `extraction_quality: manual_review`; never invent
figures the pages don't contain.
