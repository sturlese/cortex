"""run_once orchestration with explicit Settings: happy path, errors, breakers, dedup, deletion."""
import asyncio
import dataclasses
import json
import os
import types

import pytest

from clean import main as clean_main
from clean.main import run_once
from clean.settings import Settings


@pytest.fixture()
def env(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.md").write_text("doc a")
    (raw / "b.md").write_text("doc b")
    (raw / "_state.json").write_text(json.dumps({"files": {
        "A": {"name": "a.md", "localPath": "a.md", "drivePath": "/X/a.md"},
        "B": {"name": "b.md", "localPath": "b.md", "drivePath": "/X/b.md"},
    }}))
    monkeypatch.setattr(clean_main, "build_agent", lambda **kw: object())
    cfg = Settings(raw_dir=str(raw), brain_md_dir=str(tmp_path / "brain"),
                   state_dir=str(tmp_path / "state"), dry_run=False)
    return types.SimpleNamespace(root=tmp_path, raw=raw, cfg=cfg)


def _state_of(env):
    return json.loads((env.root / "state" / "clean-state.json").read_text())


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("RAW_DIR", "/r")
    monkeypatch.setenv("CLEAN_TOKEN_BUDGET", "5000")
    monkeypatch.setenv("CLEAN_DRY_RUN", "false")
    cfg = Settings.from_env()
    assert cfg.raw_dir == "/r" and cfg.token_budget == 5000 and cfg.dry_run is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.raw_dir = "/mutated"          # configuration is data, not shared mutable state


def test_run_once_processes_all_pending(env, monkeypatch):
    async def fake_process_one(doc, processor, raw, out, catalog=None):
        return {"fileId": doc["fileId"], "skipped": False, "method": "text",
                "path": f"general/{doc['fileId']}.md", "representation": "full",
                "usage": {"in": 10, "out": 5, "reasoning": 1}}
    monkeypatch.setattr(clean_main, "process_one", fake_process_one)
    stats = asyncio.run(run_once(env.cfg))
    assert stats["processed"] == 2
    assert stats["errors"] == 0
    assert stats["in_tok"] == 20
    assert set(_state_of(env)["files"]) == {"A", "B"}
    # second pass: nothing pending (hash idempotency)
    stats2 = asyncio.run(run_once(env.cfg))
    assert stats2["pending"] == 0


def test_run_once_marks_errors_and_retries(env, monkeypatch):
    async def failing(doc, *a, **kw):
        raise ValueError("parse explosion")
    monkeypatch.setattr(clean_main, "process_one", failing)
    stats = asyncio.run(run_once(env.cfg))
    assert stats["errors"] == 2
    assert all(f["status"] == "error" for f in _state_of(env)["files"].values())

    # error docs are re-queued next pass
    async def ok(doc, *a, **kw):
        return {"fileId": doc["fileId"], "skipped": False, "method": "text", "path": "p.md", "usage": {}}
    monkeypatch.setattr(clean_main, "process_one", ok)
    stats2 = asyncio.run(run_once(env.cfg))
    assert stats2["processed"] == 2


def test_run_once_rate_limit_aborts_without_burning_backlog(env, monkeypatch):
    class RateLimited(Exception):
        status_code = 429
    async def limited(doc, *a, **kw):
        raise RateLimited("429 too many requests")
    monkeypatch.setattr(clean_main, "process_one", limited)
    stats = asyncio.run(run_once(dataclasses.replace(env.cfg, max_concurrent=1)))
    assert stats["aborted"] is True
    assert stats["errors"] == 0            # docs stay pending, not marked error
    assert _state_of(env)["files"] == {}


def test_run_once_respects_max_docs(env, monkeypatch):
    seen = []
    async def fake(doc, *a, **kw):
        seen.append(doc["fileId"])
        return {"fileId": doc["fileId"], "skipped": False, "method": "text", "path": "p.md", "usage": {}}
    monkeypatch.setattr(clean_main, "process_one", fake)
    stats = asyncio.run(run_once(dataclasses.replace(env.cfg, max_docs=1)))
    assert stats["processed"] == 1
    assert len(seen) == 1


def test_run_once_no_inventory(tmp_path):
    cfg = Settings(raw_dir=str(tmp_path), brain_md_dir=str(tmp_path / "brain"),
                   state_dir=str(tmp_path / "state"))
    stats = asyncio.run(run_once(cfg))
    assert stats == {"total": 0, "pending": 0, "processed": 0, "errors": 0}


def test_run_once_token_budget_pauses_pass(env, monkeypatch):
    async def fake(doc, *a, **kw):
        return {"fileId": doc["fileId"], "skipped": False, "method": "text",
                "path": f"general/{doc['fileId']}.md", "usage": {"in": 80, "out": 30}}
    monkeypatch.setattr(clean_main, "process_one", fake)
    stats = asyncio.run(run_once(dataclasses.replace(env.cfg, token_budget=100, max_concurrent=1)))
    assert stats["aborted_budget"] is True
    assert stats["processed"] == 1                 # first doc landed, second stayed pending
    assert len(_state_of(env)["files"]) == 1
    # relaunch without budget -> the pending doc processes
    stats3 = asyncio.run(run_once(env.cfg))
    assert stats3["processed"] == 1


def test_run_once_injects_playbook_into_agent(env, monkeypatch):
    from clean.playbook import save_playbook
    save_playbook(env.cfg.state_dir, "Prefer digest for KPI exports.")
    seen = {}
    monkeypatch.setattr(clean_main, "build_agent", lambda **kw: seen.update(kw) or object())
    async def fake(doc, *a, **kw):
        return {"fileId": doc["fileId"], "skipped": False, "method": "text", "path": "p.md", "usage": {}}
    monkeypatch.setattr(clean_main, "process_one", fake)
    asyncio.run(run_once(env.cfg))
    assert "Prefer digest for KPI exports." in seen["playbook"]


def test_run_once_aggregates_verification_verdicts(env, monkeypatch, capsys):
    verdicts = {"A": ("verified", None), "B": ("failed", ["9.9M"])}
    async def fake(doc, *a, **kw):
        vd, nums = verdicts[doc["fileId"]]
        res = {"fileId": doc["fileId"], "skipped": False, "method": "text",
               "path": f"general/{doc['fileId']}.md", "verification": vd, "usage": {}}
        if nums:
            res["unverified_numbers"] = nums
        return res
    monkeypatch.setattr(clean_main, "process_one", fake)
    stats = asyncio.run(run_once(env.cfg))
    assert stats["verify_verified"] == 1
    assert stats["verify_failed"] == 1
    assert "VERIFY FAILED general/B.md" in capsys.readouterr().out


def test_run_once_dedups_identical_content(env, monkeypatch):
    (env.raw / "b.md").write_text("doc a")   # same content as a.md -> exact duplicate
    calls = []
    async def fake_process_one(doc, processor, raw, out, catalog=None):
        calls.append(doc["fileId"])
        return {"fileId": doc["fileId"], "skipped": False, "method": "text",
                "path": f"general/{doc['fileId']}.md", "usage": {}}
    monkeypatch.setattr(clean_main, "process_one", fake_process_one)
    stats = asyncio.run(run_once(env.cfg))
    assert stats["duplicates"] == 1
    assert calls == ["A"]                         # only the canonical hits the LLM
    files = _state_of(env)["files"]
    assert files["B"]["status"] == "duplicate"
    assert files["B"]["duplicateOf"] == "A"
    # stable on the next pass: nothing pending, nothing re-duplicated
    stats2 = asyncio.run(run_once(env.cfg))
    assert stats2["pending"] == 0 and stats2["duplicates"] == 0


def test_run_once_dedups_against_previously_processed(env, monkeypatch):
    async def ok(doc, *a, **kw):
        return {"fileId": doc["fileId"], "skipped": False, "method": "text",
                "path": f"general/{doc['fileId']}.md", "usage": {}}
    monkeypatch.setattr(clean_main, "process_one", ok)
    asyncio.run(run_once(env.cfg))                # A and B processed (different content)
    inv = json.loads((env.raw / "_state.json").read_text())
    inv["files"]["C"] = {"name": "c.md", "localPath": "c.md", "drivePath": "/X/c.md"}
    (env.raw / "_state.json").write_text(json.dumps(inv))
    (env.raw / "c.md").write_text("doc a")        # same content as processed A
    stats = asyncio.run(run_once(env.cfg))
    assert stats["duplicates"] == 1 and stats["processed"] == 0
    assert _state_of(env)["files"]["C"]["duplicateOf"] == "A"


def test_run_once_deletes_page_when_source_disappears(env, monkeypatch):
    async def fake_process_one(doc, processor, raw, out, catalog=None):
        rel = f"general/{doc['fileId']}.md"
        os.makedirs(os.path.join(out, "general"), exist_ok=True)
        with open(os.path.join(out, rel), "w") as f:
            f.write("page")
        return {"fileId": doc["fileId"], "skipped": False, "method": "text",
                "path": rel, "representation": "full", "usage": {}}
    monkeypatch.setattr(clean_main, "process_one", fake_process_one)
    asyncio.run(run_once(env.cfg))
    page = env.root / "brain" / "general" / "A.md"
    assert page.exists()

    # A disappears from the inventory -> its page must go too
    inv = json.loads((env.raw / "_state.json").read_text())
    del inv["files"]["A"]
    os.remove(env.raw / "a.md")
    (env.raw / "_state.json").write_text(json.dumps(inv))

    stats = asyncio.run(run_once(env.cfg))
    assert stats["processed"] == 1
    assert not page.exists()
    assert _state_of(env)["files"]["A"]["status"] == "deleted"
    assert (env.root / "brain" / "general" / "B.md").exists()   # untouched


def test_main_dry_run_is_noop(tmp_path, capsys):
    cfg = Settings(raw_dir=str(tmp_path), brain_md_dir=str(tmp_path / "b"),
                   state_dir=str(tmp_path / "s"), dry_run=True)
    asyncio.run(clean_main.main(cfg))
    assert "no-op" in capsys.readouterr().out
