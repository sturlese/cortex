# slackexport — the Slack connector

`pipeline/slack/src/slackexport/`. Mirrors a standard Slack workspace export (the ZIP an admin
downloads) into a clean-compatible raw dir — deterministic, offline, **no tokens, stdlib only**.
It exists to prove [ADR 011](../decisions/011-source-contract.md): the ingestion abstraction is
a file contract, and a second source rides the entire pipeline unchanged.

## What it does

- One document per **channel-month** (`slack/<channel>/<YYYY-MM>.md`): messages in timestamp
  order, thread replies indented under their parents, `<@U…>` mentions and authors resolved to
  display names via `users.json`.
- Inventory entries under stable ids (`slack-<sha1(channel/month)>`) with the six contract
  fields; `orgUnit = channel` (pages land under `units/<channel>`, ACL rules can target them);
  `sourceUri` becomes a workspace permalink when `--team` is given.
- Mirror semantics: message edits change the content fingerprint (the month re-syncs); a
  channel-month gone from the export takes its file and entry with it. Entries owned by other
  connectors in a shared raw dir are never touched.

## Run

```bash
python -m slackexport.sync --export /path/to/export.zip --raw /data/raw [--team yourco]
# then the normal pipeline consumes it: clean -> pages/facts/dossiers -> graph -> answer
```

Like `corpus`, it's an ad-hoc bootstrap CLI, not a service (exports are point-in-time
snapshots). The golden scorecard runs the proof on every push: export → connector → clean
(unchanged) → a verified page with the conversation intact.
