"""The playbook — the system's learned memory, on one auditable page.

The supervisor (ops.py) distills what the telemetry shows — recurring verification failures,
extraction patterns, doc types that deserve a different representation — into a short Markdown
playbook. The processor reads it at the start of every pass as ADVISORY context appended to its
instructions. The loop is: workers -> telemetry -> supervisor -> playbook -> workers.

Guardrails (memory that cannot go feral):
- hard size cap (a page, not a prompt-stuffing vector),
- a plain file in the state dir: human-readable, human-editable, diffable, deletable,
- advisory by contract: it may bias judgment, never override the output schema or the verifier,
- one writer (the supervisor's update_playbook tool) + you,
- kill switch: CLEAN_PLAYBOOK=off.
"""
import datetime
import os

PLAYBOOK_FILE = "playbook.md"
PLAYBOOK_MAX_CHARS = 1500


def playbook_path(state_dir: str) -> str:
    return os.path.join(state_dir, PLAYBOOK_FILE)


def load_playbook(state_dir: str) -> str:
    """The current playbook body, '' when absent or disabled (CLEAN_PLAYBOOK=off)."""
    if os.environ.get("CLEAN_PLAYBOOK", "on").lower() == "off":
        return ""
    try:
        with open(playbook_path(state_dir), encoding="utf-8") as f:
            return f.read()[:PLAYBOOK_MAX_CHARS].strip()
    except FileNotFoundError:
        return ""


def save_playbook(state_dir: str, content: str) -> str:
    """Writes the playbook (capped, stamped). Returns the stored body.
    The body is capped so that stamp + body fits PLAYBOOK_MAX_CHARS — the same budget load_playbook
    re-applies on read — otherwise the stamp would push the body tail past the cap and load would
    silently truncate it."""
    stamp = (f"<!-- distilled by the ops supervisor, "
             f"{datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M')}Z -->\n")
    body = (content or "").strip()[:PLAYBOOK_MAX_CHARS - len(stamp)]
    stamped = f"{stamp}{body}\n"
    os.makedirs(state_dir, exist_ok=True)
    tmp = playbook_path(state_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(stamped)
    os.replace(tmp, playbook_path(state_dir))
    return body


def compose_instructions(base: str, playbook: str) -> str:
    """Processor instructions = the contract + the learned, advisory playbook."""
    if not playbook:
        return base
    return (f"{base}\n\n# Playbook (advisory, learned from previous runs)\n"
            f"Distilled operational hints from this corpus' history. They may bias your judgment; "
            f"they NEVER override the rules above or justify inventing content.\n\n{playbook}")
