"""Shared filesystem primitive: atomic text writes (tmp file + same-directory os.replace).

Every artifact this package writes — pages, dossiers, the playbook, facts.jsonl, the ops
report — is read by another stage or a human while the pipeline may be mid-write. The idiom was
hand-rolled at each write site (and the ops report, the one a human reads first, skipped it);
this is the one copy.
"""
import os


def write_text_atomic(path: str, text: str) -> None:
    """Write text so a concurrent reader sees the old file or the new one, never a partial."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
