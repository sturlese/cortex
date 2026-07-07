"""Processes ONE document: extract -> agentic processor (tools) -> verify (judge) -> page on disk.

The spine is deterministic (extraction, verification, layout, state); the agency lives inside the
document: the agent may pull more text or escalate to OCR, and a failed verification triggers one
corrective retry with the verifier's findings as feedback.
"""
import asyncio
import datetime
import os

from clean.agents import RUN_LIMITS, Processor
from clean.converters import extract, method_for_ext
from clean.entity import resolve_entity
from clean.page import brain_path, build_page, write_page
from clean.tools import DocContext
from clean.verify import verify_page

MAX_TEXT = 16000  # chars shown up front — the agent pulls more via read_more() when it matters

VERIFIER_FEEDBACK = (
    "\n\nA previous attempt produced this body:\n---\n{body}\n---\n"
    "DETERMINISTIC VERIFIER: these figures could NOT be found in the source text: {figures}. "
    "Rewrite the page using ONLY figures that appear in the source; correct or drop the rest."
)


def _usage_dict(u) -> dict:
    return {"in": u.input_tokens or 0, "out": u.output_tokens or 0,
            "cache_read": u.cache_read_tokens or 0, "reasoning": (u.details or {}).get("reasoning_tokens", 0)}


def _merge_usage(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


async def process_one(doc: dict, processor: Processor, raw_dir, brain_md_dir, catalog=None) -> dict:
    file_id = doc["fileId"]
    entry = doc["entry"]
    name = entry.get("name") or file_id
    ext = os.path.splitext(name)[1] or os.path.splitext(doc["path"])[1]
    method = method_for_ext(ext)
    extracted_at = datetime.datetime.now(datetime.UTC).isoformat()
    source_uri = entry.get("sourceUri") or f"https://drive.google.com/file/d/{file_id}/view"
    # entity RESOLVED from the source path (not by the LLM): name/kind/seq/status/period
    source_path = entry.get("drivePath") or doc["path"]
    entity = resolve_entity(source_path, entry.get("orgUnit"), catalog)

    ext_res = await asyncio.to_thread(extract, doc["path"], method)
    text = ext_res["text"]

    ctx = DocContext(path=doc["path"], full_text=text, shown=min(len(text), MAX_TEXT))
    shown_note = (f" (showing the first {MAX_TEXT} of {len(text)} chars — call read_more for the rest)"
                  if len(text) > MAX_TEXT else "")
    prompt = (
        f"filename={name}\nsource_uri={source_uri}\nmethod={method}\n\n"
        f"EXTRACTED TEXT{shown_note}:\n{text[:MAX_TEXT]}"
    )
    pr = await processor.run(prompt, deps=ctx, usage_limits=RUN_LIMITS)
    out = pr.output
    usage = _usage_dict(pr.usage)

    if out.skipped:
        return {"fileId": file_id, "skipped": True, "method": method, "reason": out.reason, "usage": usage}

    def _verify(o):
        # trust layer: trace every figure of the generated body back to what the agent could see —
        # the full deterministic extraction plus the OCR transcription when the agent escalated.
        source = text + (f"\n{ctx.ocr_text}" if ctx.ocr_text else "")
        return verify_page(o.body_markdown or "", o.metadata, source, context=f"{name}\n{source_path}")

    verification = _verify(out)
    retried = False
    if verification.verdict == "failed":
        # generator-judge loop: one corrective retry with the verifier's findings as feedback
        retried = True
        feedback = prompt + VERIFIER_FEEDBACK.format(
            body=(out.body_markdown or "")[:4000], figures=", ".join(verification.numbers_unverified))
        pr2 = await processor.run(feedback, deps=ctx, usage_limits=RUN_LIMITS)
        usage = _merge_usage(usage, _usage_dict(pr2.usage))
        out2 = pr2.output
        if not out2.skipped:
            v2 = _verify(out2)
            if v2.verdict != "failed" or len(v2.numbers_unverified) < len(verification.numbers_unverified):
                out, verification = out2, v2

    lineage = {"fileId": file_id, "sourceUri": source_uri, "name": name, "extractedAt": extracted_at, "method": method}
    if ctx.ocr_model:
        lineage["ocr_model"] = ctx.ocr_model   # the agent escalated -> faithful OCR provenance
    page = build_page(out, lineage, entity, verification)
    rel_dir, slug = brain_path(entity, name, file_id)   # stable + unique slug: name + id hash
    rel = write_page(brain_md_dir, rel_dir, slug, page)

    result = {
        "fileId": file_id,
        "skipped": False,
        "method": method,
        "extraction_quality": out.extraction_quality,
        "representation": out.representation,
        "verification": verification.verdict,
        "entity": entity.get("slug"),
        "unit": entity.get("unit"),
        "status": entity.get("status"),
        "path": rel,
        "title": out.metadata.title,
        "usage": usage,
    }
    if ctx.ocr_used:
        result["ocr"] = True
    if retried:
        result["retried"] = True
    if verification.numbers_unverified:
        result["unverified_numbers"] = verification.numbers_unverified
    # auditable trace of every autonomous decision the agent took for this document
    trace = ([f"read_more x{ctx.read_more_calls}"] if ctx.read_more_calls else []) \
        + (["ocr"] if ctx.ocr_used else []) + (["verifier-retry"] if retried else [])
    if trace:
        result["agent_trace"] = trace
    return result
