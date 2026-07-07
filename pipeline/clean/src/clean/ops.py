"""The supervisor — a second level of agency that watches the first.

Workers process documents; this agent supervises the SYSTEM: it reads the pipeline's telemetry,
diagnoses patterns (verification failures, error clusters, OCR spend), spot-audits pages against
freshly re-extracted sources (the sampled semantic judge promised in ADR 002), takes BOUNDED
actions — requeue up to 20 documents, distill lessons into the playbook the workers read next
pass — and writes a report a human can act on. Human-on-the-loop, not human-out-of-the-loop:
everything it does is capped, recorded in the report, and reversible.

Run it after a pass (or on a schedule):
    docker compose --profile ops run --rm ops          # in the stack
    CLEAN_STATE_DIR=... RAW_DIR=... python ops.py      # locally
"""
import asyncio
import datetime
import json
import os
import sys
import types
from collections import Counter
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from clean.agents import build_model
from clean.converters import extract, method_for_ext
from clean.playbook import save_playbook
from clean.schemas import OpsReport
from clean.state import load_state, save_state

OPS_MODEL = os.environ.get("CLEAN_MODEL", "gpt-5.4")
OPS_LIMITS = UsageLimits(request_limit=14, tool_calls_limit=12)
MAX_REQUEUE = 20      # bounded write-action: the supervisor never mass-mutates state
MAX_AUDITS = 5        # sampled semantic audit, not an exhaustive (expensive) sweep
MAX_LIST = 20
EXCERPT = 6000

REPORT_FILE = "ops-report.md"


@dataclass
class OpsContext:
    state: dict
    state_dir: str
    raw_dir: str
    brain_md_dir: str
    requeued: list = field(default_factory=list)
    audits_done: int = 0
    playbook_updates: int = 0
    actions: list = field(default_factory=list)


# ── deterministic telemetry (pure) ───────────────────────────────────────────
def aggregate_status(state: dict) -> dict:
    """Everything the supervisor needs to reason, computed by code — the agent interprets,
    it does not count."""
    files = state.get("files", {})
    by_status = Counter(f.get("status", "unknown") for f in files.values())
    results = [f.get("lastResult") or {} for f in files.values() if f.get("status") == "processed"]
    verification = Counter(r.get("verification") for r in results if r.get("verification"))
    quality = Counter(r.get("extraction_quality") for r in results if r.get("extraction_quality"))
    top_errors = Counter(
        (f.get("error") or "")[:120] for f in files.values() if f.get("status") == "error"
    ).most_common(5)
    return {
        "files_total": len(files),
        "by_status": dict(by_status),
        "verification": dict(verification),
        "extraction_quality": dict(quality),
        "ocr_docs": sum(1 for r in results if r.get("ocr")),
        "verifier_retries": sum(1 for r in results if r.get("retried")),
        "skipped_as_noise": sum(1 for r in results if r.get("skipped")),
        "top_errors": top_errors,
    }


_KINDS = ("verify_failed", "verify_partial", "manual_review", "error", "duplicate")


def list_pages_impl(ctx: OpsContext, kind: str) -> str:
    if kind not in _KINDS:
        return f"unknown kind {kind!r} — use one of {_KINDS}"
    rows = []
    for fid, f in ctx.state.get("files", {}).items():
        r = f.get("lastResult") or {}
        match = (
            (kind == "error" and f.get("status") == "error")
            or (kind == "duplicate" and f.get("status") == "duplicate")
            or (kind == "verify_failed" and r.get("verification") == "failed")
            or (kind == "verify_partial" and r.get("verification") == "partial")
            or (kind == "manual_review" and r.get("extraction_quality") == "manual_review")
        )
        if not match:
            continue
        note = f.get("error") or ", ".join(r.get("unverified_numbers", [])) or r.get("path") or ""
        rows.append(f"{fid} · {f.get('name', '?')} · {note[:100]}")
        if len(rows) >= MAX_LIST:
            break
    return "\n".join(rows) or f"no pages of kind {kind}"


def audit_page_impl(ctx: OpsContext, file_id: str) -> str:
    """Page vs freshly re-extracted source, side by side — the input for a semantic spot-check."""
    if ctx.audits_done >= MAX_AUDITS:
        return "audit budget exhausted — reason from the audits you already did."
    f = ctx.state.get("files", {}).get(file_id)
    if not f:
        return f"unknown file id {file_id!r}"
    rel = (f.get("lastResult") or {}).get("path")
    page = ""
    if rel:
        try:
            with open(os.path.join(ctx.brain_md_dir, rel), encoding="utf-8") as fh:
                page = fh.read()
        except FileNotFoundError:
            page = "(page file missing)"
    local = f.get("localPath")
    source = "(source file missing)"
    if local and os.path.exists(os.path.join(ctx.raw_dir, local)):
        try:
            res = extract(os.path.join(ctx.raw_dir, local), method_for_ext(os.path.splitext(local)[1]))
            source = res["text"]
        except Exception as ex:  # noqa: BLE001 — an unreadable source is itself a finding
            source = f"(extraction failed: {str(ex)[:200]})"
    ctx.audits_done += 1
    return (f"== PAGE {rel or '(none)'} ==\n{page[:EXCERPT]}\n\n"
            f"== FRESH SOURCE EXTRACT ({local}) ==\n{source[:EXCERPT]}")


def requeue_impl(ctx: OpsContext, file_ids: list[str], reason: str) -> str:
    """Marks documents for reprocessing next pass. Hard-capped; every requeue is recorded."""
    accepted = []
    for fid in file_ids:
        if len(ctx.requeued) >= MAX_REQUEUE:
            break
        f = ctx.state.get("files", {}).get(fid)
        if not f or f.get("status") == "requeued":
            continue
        f["status"] = "requeued"
        f["requeueReason"] = reason[:200]
        ctx.requeued.append(fid)
        accepted.append(fid)
    if accepted:
        ctx.actions.append(f"requeued {len(accepted)} doc(s): {reason[:120]}")
    left = MAX_REQUEUE - len(ctx.requeued)
    return f"requeued {len(accepted)} of {len(file_ids)} (budget left: {left})"


def update_playbook_impl(ctx: OpsContext, content: str) -> str:
    """Distills lessons into the workers' advisory memory. Once per run, capped size."""
    if ctx.playbook_updates >= 1:
        return "playbook already updated this run"
    body = save_playbook(ctx.state_dir, content)
    ctx.playbook_updates += 1
    ctx.actions.append(f"updated playbook ({len(body)} chars)")
    return f"playbook updated ({len(body)} chars). It will be injected into the next pass."


OPS_SYS = f"""You are the supervisor of a document-ingestion pipeline (workers turn company files
into knowledge-base pages; a deterministic verifier judges every page). You are the second pair of
eyes: diagnose the SYSTEM, not individual typos.

Method:
1. pipeline_status() first — read the telemetry.
2. Investigate what stands out: list_pages() for the problem classes; audit_page() to spot-check a
   FEW representative pages against their freshly re-extracted source (semantic faithfulness:
   wrong attribution, inverted trends, missing key facts — things the numeric verifier can't see).
3. Act, sparingly: requeue() docs that a reprocess can plausibly fix (transient errors, pages that
   failed verification for reasons your playbook update addresses). update_playbook() ONCE with
   short, concrete, corpus-specific guidance if you saw a recurring pattern (max ~1500 chars).
4. Report: health green/yellow/red, findings (most important first), actions you took,
   recommendations for the human (anything you could NOT or SHOULD not fix yourself).

You cannot delete anything, touch more than {MAX_REQUEUE} docs, or write anywhere except the
playbook. Be the operator you would want at 3am: calm, specific, no drama."""


class FakeOps:
    """Offline supervisor (CLEAN_LLM=fake): deterministic report from the real telemetry —
    the demo shows the full loop with zero keys. No actions, ever."""

    def __init__(self, ctx: OpsContext):
        self.ctx = ctx

    async def run(self, prompt, *, deps=None, usage_limits=None):
        s = aggregate_status(self.ctx.state)
        errors = s["by_status"].get("error", 0)
        failed = s["verification"].get("failed", 0)
        partial = s["verification"].get("partial", 0)
        review = s["extraction_quality"].get("manual_review", 0)
        health = "red" if (errors or failed) else ("yellow" if (partial or review) else "green")
        findings = [f"{s['files_total']} files tracked; statuses: {s['by_status']}"]
        if s["verification"]:
            findings.append(f"verification verdicts: {s['verification']}")
        if s["ocr_docs"]:
            findings.append(f"{s['ocr_docs']} doc(s) needed the OCR tool")
        if s["verifier_retries"]:
            findings.append(f"{s['verifier_retries']} verifier-triggered retries")
        for msg, n in s["top_errors"]:
            findings.append(f"error x{n}: {msg}")
        recs = []
        if failed:
            recs.append("inspect verify-failed pages; consider a requeue after a playbook update")
        if review:
            recs.append("manual_review pages: enable GEMINI_API_KEY (ocr tool) or fix sources")
        if not recs:
            recs.append("no action needed — keep the loop running")
        report = OpsReport(health=health,
                           summary=f"Pipeline is {health}: {s['files_total']} files, "
                                   f"{errors} errors, {failed} failed verifications.",
                           findings=findings, actions_taken=[], recommendations=recs)
        usage = types.SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_tokens=0, details={})
        return types.SimpleNamespace(output=report, usage=usage)


def build_ops_agent(ctx: OpsContext):
    if os.environ.get("CLEAN_LLM", "openai").lower().startswith("fake"):
        return FakeOps(ctx)
    model, settings = build_model(OPS_MODEL)
    agent = Agent(model, output_type=OpsReport, instructions=OPS_SYS,
                  model_settings=settings, deps_type=OpsContext)

    @agent.tool
    async def pipeline_status(rc: RunContext[OpsContext]) -> str:
        """Aggregated pipeline telemetry: statuses, verification verdicts, OCR/retry counts, top errors."""
        return json.dumps(aggregate_status(rc.deps.state))

    @agent.tool
    async def list_pages(rc: RunContext[OpsContext], kind: str) -> str:
        """Up to 20 pages of a problem class: verify_failed | verify_partial | manual_review | error | duplicate."""
        return list_pages_impl(rc.deps, kind)

    @agent.tool
    async def audit_page(rc: RunContext[OpsContext], file_id: str) -> str:
        """Spot-audit: the stored page next to a fresh extraction of its source (max 5 per run)."""
        return await asyncio.to_thread(audit_page_impl, rc.deps, file_id)

    @agent.tool
    async def requeue(rc: RunContext[OpsContext], file_ids: list[str], reason: str) -> str:
        """Mark documents for reprocessing next pass (hard cap 20 per run). State your reason."""
        return requeue_impl(rc.deps, file_ids, reason)

    @agent.tool
    async def update_playbook(rc: RunContext[OpsContext], content: str) -> str:
        """Replace the workers' advisory playbook with distilled, corpus-specific guidance (once per run)."""
        return update_playbook_impl(rc.deps, content)

    return agent


def render_report(report: OpsReport, ctx: OpsContext) -> str:
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Ops report — {ts}", "",
             f"**Health: {report.health.upper()}**", "", report.summary, ""]
    if report.findings:
        lines += ["## Findings", *[f"- {x}" for x in report.findings], ""]
    actions = report.actions_taken or ctx.actions
    if actions:
        lines += ["## Actions taken (bounded, reversible)", *[f"- {x}" for x in actions], ""]
    if ctx.requeued:
        lines += [f"- requeued ids: {', '.join(ctx.requeued[:MAX_REQUEUE])}", ""]
    if report.recommendations:
        lines += ["## Recommendations (for a human)", *[f"- {x}" for x in report.recommendations], ""]
    return "\n".join(lines)


async def main() -> int:
    from clean.observability import maybe_instrument
    maybe_instrument("ops")
    state_dir = os.environ.get("CLEAN_STATE_DIR", "/data/state")
    ctx = OpsContext(
        state=load_state(state_dir),
        state_dir=state_dir,
        raw_dir=os.environ.get("RAW_DIR", "/data/raw"),
        brain_md_dir=os.environ.get("BRAIN_MD_DIR", "/data/brain-md"),
    )
    if not ctx.state.get("files"):
        print("[ops] nothing to supervise yet (empty state)")
        return 0

    agent = build_ops_agent(ctx)
    result = await agent.run("Supervise the pipeline now.", deps=ctx, usage_limits=OPS_LIMITS)
    report = result.output

    if ctx.requeued:
        save_state(state_dir, ctx.state)
    md = render_report(report, ctx)
    path = os.path.join(state_dir, REPORT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"[ops] report written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
