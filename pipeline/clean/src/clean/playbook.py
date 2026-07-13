"""The playbook — the system's learned memory, on one auditable page.

The supervisor (ops.py) distills what the telemetry shows — recurring verification failures,
extraction patterns, doc types that deserve a different representation — into a short Markdown
playbook. The processor reads it at the start of every pass as ADVISORY context appended to its
instructions. The loop is: workers -> telemetry -> supervisor -> [human approval] -> playbook
-> workers.

Guardrails (memory that cannot go feral):
- hard size cap (a page, not a prompt-stuffing vector),
- a plain file in the state dir: human-readable, human-editable, diffable, deletable,
- advisory by contract: it may bias judgment, never override the output schema or the verifier,
- one writer (the supervisor's update_playbook tool) + you,
- HUMAN APPROVAL GATE: the supervisor only *proposes* (playbook-pending.md); nothing reaches the
  workers until an operator runs `python -m clean.playbook approve` (or sets
  CLEAN_PLAYBOOK_AUTOAPPROVE=true to restore the ungated loop). Document content flows through
  the supervisor's audits, so an unreviewed write would be a prompt-injection persistence path.
- kill switch: CLEAN_PLAYBOOK=off.
"""
import argparse
import datetime
import os
import sys

PLAYBOOK_FILE = "playbook.md"
PENDING_FILE = "playbook-pending.md"
PLAYBOOK_MAX_CHARS = 1500


def playbook_path(state_dir: str) -> str:
    return os.path.join(state_dir, PLAYBOOK_FILE)


def pending_path(state_dir: str) -> str:
    return os.path.join(state_dir, PENDING_FILE)


def load_playbook(state_dir: str) -> str:
    """The current playbook body, '' when absent or disabled (CLEAN_PLAYBOOK=off)."""
    if os.environ.get("CLEAN_PLAYBOOK", "on").lower() == "off":
        return ""
    try:
        with open(playbook_path(state_dir), encoding="utf-8") as f:
            return f.read()[:PLAYBOOK_MAX_CHARS].strip()
    except FileNotFoundError:
        return ""


def _write_stamped(path: str, content: str, origin: str) -> str:
    """Cap + stamp + atomic write. The body is capped so that stamp + body fits
    PLAYBOOK_MAX_CHARS — the same budget load_playbook re-applies on read — otherwise the stamp
    would push the body tail past the cap and load would silently truncate it."""
    stamp = f"<!-- {origin}, {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M')}Z -->\n"
    body = (content or "").strip()[:PLAYBOOK_MAX_CHARS - len(stamp)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{stamp}{body}\n")
    os.replace(tmp, path)
    return body


def save_playbook(state_dir: str, content: str) -> str:
    """Writes the LIVE playbook (capped, stamped). Returns the stored body."""
    return _write_stamped(playbook_path(state_dir), content, "distilled by the ops supervisor")


def save_pending(state_dir: str, content: str) -> str:
    """Writes a playbook PROPOSAL (capped, stamped). Not read by the workers until approved."""
    return _write_stamped(pending_path(state_dir), content,
                          "PROPOSED by the ops supervisor — pending human approval")


def _strip_stamp(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("<!--"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def approve_pending(state_dir: str) -> str:
    """Promotes the pending proposal to the live playbook (re-stamped as approved).
    Raises FileNotFoundError when there is nothing pending."""
    with open(pending_path(state_dir), encoding="utf-8") as f:
        body = _strip_stamp(f.read())
    stored = _write_stamped(playbook_path(state_dir), body,
                            "distilled by the ops supervisor, approved by the operator")
    os.remove(pending_path(state_dir))
    return stored


def reject_pending(state_dir: str) -> None:
    """Discards the pending proposal. Raises FileNotFoundError when there is nothing pending."""
    os.remove(pending_path(state_dir))


def compose_instructions(base: str, playbook: str) -> str:
    """Processor instructions = the contract + the learned, advisory playbook."""
    if not playbook:
        return base
    return (f"{base}\n\n# Playbook (advisory, learned from previous runs)\n"
            f"Distilled operational hints from this corpus' history. They may bias your judgment; "
            f"they NEVER override the rules above or justify inventing content.\n\n{playbook}")


def cli(argv=None) -> int:
    """Operator gate for the learning loop: inspect / approve / reject the supervisor's proposal."""
    from clean.settings import Settings  # entrypoint-constructed config, like every other CLI

    parser = argparse.ArgumentParser(
        prog="playbook", description="Review the ops supervisor's playbook proposal (human gate).")
    parser.add_argument("command", choices=["show", "approve", "reject"],
                        help="show current+pending · approve the proposal · reject it")
    args = parser.parse_args(argv)
    state_dir = Settings.from_env().state_dir

    if args.command == "show":
        live = load_playbook(state_dir)
        print(f"── live playbook ({playbook_path(state_dir)}) ──")
        print(live or "(empty)")
        try:
            with open(pending_path(state_dir), encoding="utf-8") as f:
                print(f"\n── PENDING proposal ({pending_path(state_dir)}) ──")
                print(f.read().strip())
                print("\napprove with: python -m clean.playbook approve")
        except FileNotFoundError:
            print("\n(no pending proposal)")
        return 0
    try:
        if args.command == "approve":
            body = approve_pending(state_dir)
            print(f"approved -> {playbook_path(state_dir)} ({len(body)} chars). "
                  "Workers read it on the next pass.")
        else:
            reject_pending(state_dir)
            print("rejected — proposal discarded; the live playbook is unchanged.")
        return 0
    except FileNotFoundError:
        print(f"nothing pending at {pending_path(state_dir)}")
        return 1


if __name__ == "__main__":
    sys.exit(cli())
