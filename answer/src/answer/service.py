"""AnswerService — the serving core, transport-agnostic (the MCP adapter is a thin skin).

Wires the index, the retrieval ranking, the facts store and the answering agent into the four
operations a client needs, enforcing the page contract server-side:
- search():        contract-aware ranked hits (superseded/failed demoted, reasons attached)
- query_metrics(): exact numeric answers with per-cell provenance, superseded-awareness
- read_page():     one page, trust signals first
- ask():           the full loop — agent gathers evidence, deterministic verifier judges the
                   answer (figures + citations), one corrective retry, verdict attached.
"""
from answer import index, metrics, retrieve
from answer.settings import Settings
from answer.synthesize import ANSWER_LIMITS, SynthesisContext, build_synthesizer
from answer.verify_answer import feedback, verify

PAGE_EXCERPT = 6000


class AnswerService:
    def __init__(self, settings: Settings):
        self.settings = settings
        # the deployment's ACL scope: every read path filters through it (None = unrestricted)
        self.audiences = set(settings.audiences) if settings.audiences else None
        self.conn = index.connect(settings.state_dir)
        self.refresh()

    # ── index lifecycle ──────────────────────────────────────────────────────
    def refresh(self) -> dict:
        return index.refresh(self.conn, self.settings.brain_md_dir)

    # ── primitives (used by tools, the fake, and the MCP adapter) ───────────
    def get_page(self, path: str) -> dict | None:
        page = index.get_page(self.conn, path)
        if page and not index.visible(page.get("acl"), self.audiences):
            return None                  # out of scope = does not exist for this client
        return page

    def search(self, query: str, k: int = retrieve.TOP_K) -> list[dict]:
        return retrieve.search(self.conn, query, k=k, audiences=self.audiences)

    def query_metrics(self, metric=None, entity=None, period=None, limit: int = 50) -> list[dict]:
        rows = metrics.query_metrics(self.settings.facts_dir, metric, entity, period, limit,
                                     audiences=self.audiences)
        return metrics.annotate_superseded(rows, index.superseded_paths(self.conn))

    def known_entities(self) -> list[str]:
        """Entities with at least one page THIS client may see — existence is also scoped."""
        out = set()
        for r in self.conn.execute("SELECT entity, acl FROM pages WHERE entity != ''"):
            if index.visible(r["acl"], self.audiences):
                out.add(r["entity"])
        return sorted(out)

    def match_metric(self, q_tokens: set, entity=None) -> str | None:
        """Best metric id whose kebab parts all appear in the question — 'arr usd', 'arr-usd'
        and 'the ARR (usd)' all resolve. Most specific (most parts) wins."""
        expanded = set(q_tokens) | {p for t in q_tokens for p in str(t).split("-")}
        best = None
        for m in metrics.known_metrics(self.settings.facts_dir, entity):
            parts = set(m.split("-"))
            if parts and parts <= expanded and (best is None or len(parts) > len(best.split("-"))):
                best = m
        return best

    def current_metric_rows(self, metric, entity=None, period=None) -> list[dict]:
        """Metric rows with current-truth preference: rows from superseded pages are dropped
        when a non-superseded row for the same (metric, entity, period) exists."""
        rows = self.query_metrics(metric, entity, period, limit=100)
        current = [r for r in rows if not r["from_superseded_page"]]
        return current or rows

    # ── textual renderings (what the agent's tools return) ──────────────────
    def search_text(self, query: str) -> str:
        hits = self.search(query)
        if not hits:
            return f"no results for: {query}"
        lines = []
        for h in hits:
            flags = []
            if h["superseded_by"]:
                flags.append("SUPERSEDED — prefer the current version")
            if h["verification"] and h["verification"] != "verified":
                flags.append(f"verification={h['verification']}")
            if h["detail_in_source"]:
                flags.append("detail_in_source (numbers: use query_metrics)")
            meta = " · ".join(x for x in (h["entity"] or h["unit"], h["as_of"] or h["period"]) if x)
            lines.append(f"- {h['path']}\n  {h['title']} ({meta})"
                         + (f" [{'; '.join(flags)}]" if flags else "")
                         + f"\n  {h['snippet'][:200]}")
        return "\n".join(lines)

    def page_text(self, path: str, ctx: SynthesisContext | None = None) -> str:
        page = self.get_page(path)
        if not page:
            return f"unknown page: {path}"
        if ctx is not None:
            ctx.read_paths.add(path)
        head = (f"path: {page['path']}\ntitle: {page['title']}\nentity: {page['entity']}"
                f"\nas_of: {page['as_of']}\nverification: {page['verification']}"
                f"\nsuperseded_by: {page['superseded_by'] or '(no — current)'}")
        return (f"{head}\n<<<UNTRUSTED-DATA\n{(page['body'] or '')[:PAGE_EXCERPT]}\nUNTRUSTED-DATA;end>>>")

    def metrics_text(self, metric=None, entity=None, period=None,
                     ctx: SynthesisContext | None = None) -> str:
        rows = self.query_metrics(metric, entity, period)
        if not rows:
            known = metrics.known_metrics(self.settings.facts_dir, entity)
            return ("no observations for that query. known metrics"
                    + (f" for {entity}" if entity else "") + f": {', '.join(known) or '(none)'}")
        lines = []
        for r in rows[:30]:
            note = " [from a SUPERSEDED page — prefer current]" if r["from_superseded_page"] else ""
            if ctx is not None and r.get("page_path"):
                ctx.read_paths.add(r["page_path"])
            lines.append(f"- {r['entity'] or '-'} · {r['metric']} · {r['period'] or '-'} = "
                         f"{r['value_raw']}{(' ' + r['unit']) if r['unit'] else ''} "
                         f"(source {r['source_ref']}; page {r['page_path']}){note}")
        return "\n".join(lines)

    # ── the full answering loop ──────────────────────────────────────────────
    async def ask(self, question: str) -> dict:
        agent = build_synthesizer(self.settings)
        ctx = SynthesisContext(service=self)
        result = await agent.run(question, deps=ctx, usage_limits=ANSWER_LIMITS)
        out = result.output
        verdict = verify(out, ctx.evidence_text(), self.get_page, ctx.read_paths)
        retried = False
        if verdict["verdict"] == "failed":
            retried = True
            result2 = await agent.run(feedback(question, out, verdict), deps=ctx,
                                      usage_limits=ANSWER_LIMITS)
            out2 = result2.output
            v2 = verify(out2, ctx.evidence_text(), self.get_page, ctx.read_paths)
            rank = {"verified": 0, "partial": 1, "failed": 2}
            if rank[v2["verdict"]] < rank[verdict["verdict"]]:
                out, verdict = out2, v2
        return {
            "question": question,
            "refused": out.refused,
            "answer": out.answer_markdown if not out.refused else "",
            "reason": out.reason,
            "citations": [{"path": c.path, "quote": c.quote} for c in out.citations],
            "confidence": out.confidence,
            "verification": verdict,
            "retried": retried,
        }
