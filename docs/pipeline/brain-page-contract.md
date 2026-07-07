# Brain page contract

Every page clean writes is Markdown with YAML frontmatter. This is the interface consumed by the
brain server and by MCP clients — treat it as an API.

```yaml
---
type: meeting-notes                  # kebab-case doc type (LLM-chosen)
title: Q1 board minutes
date: 2026-03-14                     # content date (optional)
tags: [board, minutes, q1]
id: "drive:1AbC..."                  # stable id
source_file_id: 1AbC...              # handle for opening the original (Drive MCP, links)
source_uri: "https://drive.google.com/file/d/1AbC.../view"
source_kind: google-drive
source_name: 2026 Q1 minutes.pdf
extracted_at: "2026-04-01T12:00:00Z"
representation: full                 # full | digest | minimal
extraction_quality: usable           # usable | manual_review
source_format: pdf                   # pdf | spreadsheet | document | office | text | other
contextual_retrieval: title          # embedding-context tier: prepend title to each chunk
tier: 1                              # 1 primary, 2 second-hand, 3 AI-generated
verification: verified               # trust layer: verified | partial | failed (deterministic, not LLM)
unverified_numbers: ["9.9M"]         # only when figures couldn't be traced to the source
unverified_mentions: [Ghost Corp]    # advisory: mentions not literally found in the source
detail_in_source: true               # spreadsheets only: exact figures live in the source
extraction_method: vision            # the agent escalated to its ocr tool (+ ocr_model: <model>)
entity: initech                      # resolved from the folder path, NOT by the LLM
entity_kind: tracked                 # tracked | prospect
entity_aliases: [Initech, S.L.]      # when the folder name differs from the slug
seq: 3                               # entity's folder number, when present
status: archived                     # entity status marker from the folder name
stage: Evaluating                    # prospects only
unit: Sales                          # org unit (top-level folder)
period: 2026-Q1                      # year/quarter detected in the path
mentions:                            # unresolved entities — the graph stage links them
  - { name: Initech, type: company }
  - { name: Jane Doe, type: person }
---

# Q1 board minutes

...body...
```

## How a client should read it

| Field | Client behavior |
|---|---|
| `detail_in_source: true` + `source_format: spreadsheet` | page is a summary of a *live* sheet — open the source (via `source_file_id`) for exact/current figures |
| `representation: digest` / `minimal` | summary/pointer; detail is in the source |
| `extraction_quality: manual_review` | page carries a warning banner; extraction was lossy — offer to open the original |
| `verification: failed` | one or more figures could not be traced to the source (banner on the page) — do NOT quote its numbers without opening the original |
| `verification: partial` | isolated untraced figure(s), listed in `unverified_numbers` — quote those with caution |
| `extraction_method: vision` | body came from OCR (`ocr_model` says which); trust accordingly |
| `mentions` | unresolved names; the graphed layer links them as `[[wikilinks]]` |

## Body rules (enforced by prompt + post-processing)

- The page owns the single `# H1` (from `title`); model-added H1s are stripped.
- No `[[wikilinks]]` from the LLM (linking is graph's job); `---` lines are neutralized so they
  can't break frontmatter parsing.
- `digest`/`minimal` pages always end with a visible link to the original file.
- Zero invention: figures are quoted exactly from the extracted text or omitted — and this is
  **enforced**, not just prompted: after generation, every number in the body is deterministically
  traced back to the source text (`verification` frontmatter; see
  [decisions/002](../decisions/002-deterministic-verification.md)).
