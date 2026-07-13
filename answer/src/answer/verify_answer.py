"""The answer verifier — pure code judging the answering agent, before the answer leaves.

Mirror of the page pipeline's generator-judge loop, applied at query time:
- every figure in the answer must trace back to what the tools actually returned this run
  (the agent's visible evidence — not the whole corpus, so a lucky match elsewhere can't
  launder an invented number);
- every citation must point at a page the run actually surfaced, and its quote must appear
  verbatim (whitespace-tolerant) in that page.

The verdict ships with the answer (`verified` / `partial` / `failed`), and a failed first
attempt earns exactly one corrective retry with the findings as feedback.
"""
import re

from answer.numbers import unverified_figures


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def check_citations(citations, get_page, read_paths: set) -> list[str]:
    """Citation problems, human-readable. A citation is valid when its path was surfaced during
    the run and its quote appears (whitespace-normalized) in that page's body or title."""
    problems = []
    for c in citations:
        if c.path not in read_paths:
            problems.append(f"citation to a page the run never surfaced: {c.path}")
            continue
        page = get_page(c.path)
        if not page:
            problems.append(f"citation to an unknown page: {c.path}")
            continue
        hay = _normalize(f"{page.get('title', '')} {page.get('body', '')}")
        if c.quote and _normalize(c.quote) not in hay:
            problems.append(f"citation quote not found in {c.path}: {c.quote[:80]!r}")
    return problems


def verify(out, evidence_text: str, get_page, read_paths: set) -> dict:
    """Deterministic verdict on one AnswerOutput. Refusals are vacuously verified —
    refusing with no evidence is the correct behavior, not a defect."""
    if out.refused:
        return {"verdict": "verified", "unverified_figures": [], "citation_problems": []}
    figures = unverified_figures(out.answer_markdown, evidence_text)
    citation_problems = check_citations(out.citations, get_page, read_paths)
    if not out.citations and out.answer_markdown.strip():
        citation_problems.append("answer carries no citations")
    problems = len(figures) + len(citation_problems)
    verdict = "verified" if problems == 0 else ("partial" if problems == 1 else "failed")
    return {"verdict": verdict, "unverified_figures": figures, "citation_problems": citation_problems}


def feedback(question: str, out, verdict: dict) -> str:
    """The corrective-retry prompt: the original question plus the verifier's findings."""
    parts = []
    if verdict["unverified_figures"]:
        parts.append("these figures do NOT appear in any tool result you gathered: "
                     + ", ".join(verdict["unverified_figures"]))
    if verdict["citation_problems"]:
        parts.append("citation problems: " + "; ".join(verdict["citation_problems"]))
    return (f"{question}\n\nA previous attempt answered:\n---\n{out.answer_markdown[:2000]}\n---\n"
            f"DETERMINISTIC VERIFIER: {'; '.join(parts)}. Re-answer using ONLY figures present in "
            "tool results and verbatim quotes from surfaced pages — or refuse if the evidence is "
            "insufficient.")
