"""MCP adapter — a thin skin over AnswerService (stdio for local clients, streamable HTTP for a
deployment behind your own ingress). The contract enforcement all lives in the service; this
file only shapes tools.

Run:
    python -m answer.mcp_server                          # stdio (Claude Desktop, IDEs)
    python -m answer.mcp_server --transport http --port 3141
With ANSWER_BEARER_TOKEN set, the HTTP transport requires `Authorization: Bearer <token>`.
"""
import argparse
import json

from answer.service import AnswerService
from answer.settings import Settings


def build_mcp(service: AnswerService):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("cortex-answer")

    @mcp.tool()
    async def ask_brain(question: str) -> str:
        """Answer a question from the company brain: evidence-gathering agent + deterministic
        verification (figures traced to evidence, citations to pages). Refuses when the brain
        doesn't contain the answer."""
        service.refresh()
        return json.dumps(await service.ask(question), ensure_ascii=False, indent=1)

    @mcp.tool()
    def search_brain(query: str) -> str:
        """Search pages with contract-aware ranking (superseded/unverified demoted). Returns
        paths, trust signals and snippets."""
        service.refresh()
        return service.search_text(query)

    @mcp.tool()
    def query_metrics(metric: str = "", entity: str = "", period: str = "") -> str:
        """Exact numeric lookups from the verified facts store: value, unit, period and the
        source cell/quote reference for every number."""
        return service.metrics_text(metric or None, entity or None, period or None)

    @mcp.tool()
    def read_page(path: str) -> str:
        """Read one brain page (trust signals first, body fenced as untrusted data)."""
        return service.page_text(path)

    return mcp


def _with_bearer_auth(app, token: str):
    """Minimal static-token gate for the HTTP transport (front TLS/ingress is yours)."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import PlainTextResponse

    class Bearer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("authorization") != f"Bearer {token}":
                return PlainTextResponse("unauthorized", status_code=401)
            return await call_next(request)

    app.add_middleware(Bearer)
    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="answer", description="cortex answer server (MCP).")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3141)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    service = AnswerService(settings)
    mcp = build_mcp(service)
    if args.transport == "stdio":
        mcp.run()
        return 0
    import uvicorn
    app = mcp.streamable_http_app()
    if settings.bearer_token:
        app = _with_bearer_auth(app, settings.bearer_token)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
