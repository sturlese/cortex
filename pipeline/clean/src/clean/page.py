"""Builds and writes the brain-md page (frontmatter contract shared with the brain server)."""
import hashlib
import os
import re
import unicodedata

from clean.schemas import ProcessorOutput

# Format of the underlying source (so a client knows which tool to open it with).
SOURCE_FORMAT = {"pdf": "pdf", "sheet": "spreadsheet", "docx": "document", "office": "office", "text": "text"}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:80] or "untitled"


def _yaml(v: str) -> str:
    return f'"{v}"' if re.search(r'[:\#\[\]{}",]', v) else v


def brain_path(entity: dict, filename: str, file_id: str):
    """DETERMINISTIC folder (from the resolved entity) + STABLE, UNIQUE slug.
    slug = slug(original filename) + short hash of the file id: the name keeps it readable, the
    hash guarantees uniqueness and stability (independent of the LLM and of processing order).
    Layout: entities/<slug>/ · prospects/<slug>/ · units/<unit>/ · general/ (fallback)."""
    ent = entity or {}
    stem = filename.rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    base = slugify(stem)[:70] or "doc"
    suffix = hashlib.sha1((file_id or "").encode("utf-8")).hexdigest()[:6]
    slug = f"{base}-{suffix}"
    if ent.get("slug") and ent.get("kind") == "prospect":
        rel = f"prospects/{ent['slug']}"
    elif ent.get("slug"):
        rel = f"entities/{ent['slug']}"
    elif ent.get("unit"):
        rel = f"units/{slugify(str(ent['unit']))}"
    else:
        rel = "general"
    return rel, slug


def build_page(out: ProcessorOutput, lineage: dict, entity: dict = None, verification=None) -> str:
    m = out.metadata
    method = lineage.get("method", "")
    source_format = SOURCE_FORMAT.get(method, "other")
    ent = entity or {}
    fm = ["---", f"type: {m.type}", f"title: {_yaml(m.title)}"]
    if m.date:
        fm.append(f"date: {m.date}")
    fm.append(f"tags: [{', '.join(m.tags)}]")
    fm += [
        f'id: "drive:{lineage["fileId"]}"',
        f'source_file_id: {lineage["fileId"]}',
        f'source_uri: "{lineage["sourceUri"]}"',
        f"source_kind: {'local' if lineage['sourceUri'].startswith('local://') else 'google-drive'}",
        f"source_name: {_yaml(lineage.get('name', ''))}",
        f'extracted_at: "{lineage["extractedAt"]}"',
        f"representation: {out.representation}",
        f"extraction_quality: {out.extraction_quality}",
        f"source_format: {source_format}",
        "contextual_retrieval: title",   # embedding-context tier: prepend the title to each chunk (free, no LLM)
        f"tier: {m.tier}",
    ]
    if verification:
        # trust layer (verify.py): deterministic figure-tracing against the source, NOT LLM-judged
        fm.append(f"verification: {verification.verdict}")
        if verification.numbers_unverified:
            fm.append("unverified_numbers: [" + ", ".join(_yaml(t) for t in verification.numbers_unverified) + "]")
        if verification.mentions_unverified:
            fm.append("unverified_mentions: [" + ", ".join(_yaml(t) for t in verification.mentions_unverified) + "]")
    if method == "sheet":
        # machine-readable signal: this page summarizes a LIVE spreadsheet — a client should go to
        # the source (via source_file_id) for exact/current figures.
        fm.append("detail_in_source: true")
    if lineage.get("ocr_model"):
        # provenance: the agent escalated to vision OCR for this page (auditable trust signal)
        fm.append("extraction_method: vision")
        fm.append(f"ocr_model: {lineage['ocr_model']}")
    # entity RESOLVED from the path (not by the LLM) — the document's "owner"; feeds the graph stage
    if ent.get("slug"):
        fm.append(f"entity: {ent['slug']}")
        if ent.get("kind"):
            fm.append(f"entity_kind: {ent['kind']}")
        if ent.get("name") and slugify(ent["name"]) != ent["slug"]:
            fm.append(f"entity_aliases: [{_yaml(ent['name'])}]")
        if ent.get("seq") is not None:
            fm.append(f"seq: {ent['seq']}")
        if ent.get("status"):
            fm.append(f"status: {ent['status']}")
        if ent.get("stage"):
            fm.append(f"stage: {_yaml(str(ent['stage']))}")
    if ent.get("unit"):
        fm.append(f"unit: {_yaml(str(ent['unit']))}")
    if ent.get("period"):
        fm.append(f"period: {ent['period']}")
    if m.mentions:
        fm.append("mentions:")
        for mn in m.mentions:
            fm.append(f"  - {{ name: {_yaml(mn.name)}, type: {mn.type} }}")
    fm.append("---")

    banner = ""
    if out.extraction_quality == "manual_review":
        banner = (
            "> [!WARNING]\n> Incomplete extraction — needs human review "
            "(visual/broken content not captured by deterministic conversion).\n\n"
        )
    if verification and verification.verdict == "failed":
        banner += (
            "> [!WARNING]\n> Verification failed: "
            f"{len(verification.numbers_unverified)} figure(s) could not be traced back to the "
            "source text — treat numbers with caution and open the original.\n\n"
        )

    body = out.body_markdown or ""
    # the page owns the H1; strip one the model may have added + neutralize --- separators
    body = re.sub(r"^#\s+[^\n]*\n+", "", body).replace("\n---\n", "\n***\n")
    # digest/minimal: guarantee a visible link to the source
    if out.representation in ("digest", "minimal") and lineage["sourceUri"] not in body:
        if method == "sheet":
            body = (f"{body}\n\nSummary of a live spreadsheet — "
                    f"for exact/current figures, open the original: {lineage['sourceUri']}")
        else:
            body = f"{body}\n\nOriginal file: {lineage['sourceUri']}"

    return "\n".join(fm) + f"\n\n{banner}# {m.title}\n\n{body}\n"


def write_page(brain_md_dir: str, rel_dir: str, slug: str, content: str) -> str:
    out_dir = os.path.join(brain_md_dir, rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{slug}.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    return os.path.relpath(path, brain_md_dir)
