"""The supervisor: telemetry aggregation, bounded tools, fake mode, report, end-to-end run."""
import asyncio
import json
import os

from clean import ops
from clean.ops import (
    MAX_AUDITS,
    MAX_REQUEUE,
    OpsContext,
    aggregate_status,
    audit_page_impl,
    build_ops_agent,
    list_pages_impl,
    render_report,
    requeue_impl,
    update_playbook_impl,
)
from clean.schemas import OpsReport


def _state():
    return {"version": 1, "files": {
        "A": {"name": "a.md", "localPath": "a.md", "status": "processed",
              "lastResult": {"path": "general/a.md", "verification": "verified"}},
        "B": {"name": "b.pdf", "localPath": "b.pdf", "status": "processed",
              "lastResult": {"path": "general/b.md", "verification": "failed",
                             "unverified_numbers": ["9.9M"], "retried": True, "ocr": True,
                             "extraction_quality": "manual_review"}},
        "C": {"name": "c.md", "localPath": "c.md", "status": "error", "error": "gotenberg 500"},
        "D": {"name": "d.md", "localPath": "d.md", "status": "duplicate", "duplicateOf": "A"},
    }}


def _ctx(tmp_path, state=None):
    return OpsContext(state=state or _state(), state_dir=str(tmp_path),
                      raw_dir=str(tmp_path), brain_md_dir=str(tmp_path / "brain"))


def test_aggregate_status_counts():
    s = aggregate_status(_state())
    assert s["files_total"] == 4
    assert s["by_status"] == {"processed": 2, "error": 1, "duplicate": 1}
    assert s["verification"] == {"verified": 1, "failed": 1}
    assert s["extraction_quality"] == {"manual_review": 1}
    assert s["ocr_docs"] == 1 and s["verifier_retries"] == 1
    assert s["top_errors"] == [("gotenberg 500", 1)]


def test_list_pages_kinds(tmp_path):
    ctx = _ctx(tmp_path)
    assert "B" in list_pages_impl(ctx, "verify_failed")
    assert "9.9M" in list_pages_impl(ctx, "verify_failed")
    assert "C" in list_pages_impl(ctx, "error")
    assert "D" in list_pages_impl(ctx, "duplicate")
    assert "no pages" in list_pages_impl(ctx, "verify_partial")
    assert "unknown kind" in list_pages_impl(ctx, "everything")


def test_deleted_doc_not_surfaced_or_requeued(tmp_path):
    """A deleted doc keeps a failed lastResult, but must not appear as a live verify_failed nor
    consume the requeue budget."""
    state = {"version": 1, "files": {
        "X": {"name": "x.md", "status": "deleted",
              "lastResult": {"path": "general/x.md", "verification": "failed",
                             "unverified_numbers": ["9.9M"]}}}}
    ctx = _ctx(tmp_path, state=state)
    assert "no pages" in list_pages_impl(ctx, "verify_failed")
    assert "requeued 0 of 1" in requeue_impl(ctx, ["X"], "try")
    assert ctx.requeued == []


def test_audit_page_reads_page_and_fresh_source(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    os.makedirs(tmp_path / "brain" / "general")
    (tmp_path / "brain" / "general" / "b.md").write_text("PAGE BODY")
    (tmp_path / "b.pdf").write_text("raw")
    monkeypatch.setattr(ops, "extract", lambda path, method: {"text": "FRESH SOURCE"})
    out = audit_page_impl(ctx, "B")
    assert "PAGE BODY" in out and "FRESH SOURCE" in out
    assert ctx.audits_done == 1
    assert "unknown file id" in audit_page_impl(ctx, "ZZZ")


def test_audit_budget(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.audits_done = MAX_AUDITS
    assert "budget exhausted" in audit_page_impl(ctx, "B")


def test_requeue_bounded_and_recorded(tmp_path):
    ctx = _ctx(tmp_path)
    msg = requeue_impl(ctx, ["B", "C", "ZZZ"], "reprocess after playbook update")
    assert "requeued 2 of 3" in msg
    assert ctx.state["files"]["B"]["status"] == "requeued"
    assert ctx.state["files"]["C"]["requeueReason"].startswith("reprocess")
    assert ctx.requeued == ["B", "C"]
    assert any("requeued 2" in a for a in ctx.actions)
    # hard cap
    ctx2 = _ctx(tmp_path, state={"version": 1, "files": {
        str(i): {"status": "processed"} for i in range(50)}})
    requeue_impl(ctx2, [str(i) for i in range(50)], "bulk")
    assert len(ctx2.requeued) == MAX_REQUEUE


def test_update_playbook_once_per_run(tmp_path):
    ctx = _ctx(tmp_path)
    assert "playbook updated" in update_playbook_impl(ctx, "Prefer digest for KPI exports.")
    assert "already updated" in update_playbook_impl(ctx, "second attempt")
    assert ctx.playbook_updates == 1
    from clean.playbook import load_playbook
    assert "Prefer digest" in load_playbook(str(tmp_path))


def test_fake_ops_health_logic(tmp_path):
    ctx = _ctx(tmp_path)
    report = asyncio.run(ops.FakeOps(ctx).run("go")).output
    assert isinstance(report, OpsReport)
    assert report.health == "red"                    # errors + failed verification present
    green_ctx = _ctx(tmp_path, state={"version": 1, "files": {
        "A": {"status": "processed", "lastResult": {"verification": "verified"}}}})
    assert asyncio.run(ops.FakeOps(green_ctx).run("go")).output.health == "green"


def test_render_report_sections(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.requeued = ["B"]
    ctx.actions = ["requeued 1 doc(s): test"]
    report = OpsReport(health="yellow", summary="All fine-ish.",
                       findings=["finding one"], actions_taken=[], recommendations=["do X"])
    md = render_report(report, ctx)
    assert "Health: YELLOW" in md
    assert "- finding one" in md
    assert "requeued ids: B" in md
    assert "## Recommendations (for a human)" in md


def test_ops_main_end_to_end_fake(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLEAN_LLM", "fake")
    monkeypatch.setenv("CLEAN_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RAW_DIR", str(tmp_path))
    monkeypatch.setenv("BRAIN_MD_DIR", str(tmp_path / "brain"))
    (tmp_path / "clean-state.json").write_text(json.dumps(_state()))
    rc = asyncio.run(ops.main())
    assert rc == 0
    report = (tmp_path / "ops-report.md").read_text()
    assert "Health: RED" in report
    assert "gotenberg 500" in report
    assert "Recommendations" in report


def test_ops_main_empty_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLEAN_STATE_DIR", str(tmp_path))
    rc = asyncio.run(ops.main())
    assert rc == 0
    assert "nothing to supervise" in capsys.readouterr().out


def test_ops_agent_tools_wired_offline(tmp_path, monkeypatch):
    """Real Agent + real supervisor tools, offline via TestModel."""
    from pydantic_ai.models.test import TestModel

    monkeypatch.setenv("CLEAN_LLM", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    ctx = _ctx(tmp_path)
    agent = build_ops_agent(ctx)
    r = asyncio.run(agent.run("supervise", deps=ctx, model=TestModel(), usage_limits=ops.OPS_LIMITS))
    assert isinstance(r.output, OpsReport)
    assert ctx.playbook_updates <= 1                 # tool guard held even under a fuzzing model
