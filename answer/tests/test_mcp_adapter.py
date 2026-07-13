"""The MCP skin: tool surface + the bearer gate. The logic all lives in the service."""
import asyncio
import json

from answer.mcp_server import _with_bearer_auth, build_mcp
from answer.settings import Settings


def test_build_mcp_exposes_the_four_tools(service):
    mcp = build_mcp(service)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"ask_brain", "search_brain", "query_metrics", "read_page"}


def test_mcp_tools_route_to_the_service(service):
    mcp = build_mcp(service)

    async def call(name, **kw):
        return (await mcp.call_tool(name, kw))[0][0].text

    out = asyncio.run(call("search_brain", query="initech kpi"))
    assert "entities/initech/kpi.md" in out
    out = asyncio.run(call("query_metrics", metric="arr-usd", entity="initech", period="2026-03"))
    assert "512000" in out
    out = asyncio.run(call("read_page", path="entities/initech/kpi.md"))
    assert "UNTRUSTED-DATA" in out
    res = json.loads(asyncio.run(call("ask_brain", question="arr-usd for initech in 2026-03?")))
    assert res["verification"]["verdict"] == "verified"
    assert "512000" in res["answer"]


def test_bearer_gate_when_token_set():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    app = Starlette(routes=[Route("/", lambda r: PlainTextResponse("ok"))])
    _with_bearer_auth(app, "sekret")
    client = TestClient(app)
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer sekret"}).text == "ok"


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("BRAIN_MD_DIR", "/b")
    monkeypatch.setenv("ANSWER_LLM", "FAKE")
    monkeypatch.setenv("ANSWER_BEARER_TOKEN", "t0k")
    cfg = Settings.from_env()
    assert cfg.brain_md_dir == "/b" and cfg.llm == "fake" and cfg.bearer_token == "t0k"
