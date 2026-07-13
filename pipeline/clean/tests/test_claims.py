"""Claim checks: chrome stripping, deterministic anchoring, judge plumbing, fake heuristic."""
import asyncio
import types

from clean.claims import (
    ClaimCheckOutput,
    ClaimFinding,
    FakeClaimJudge,
    anchor_claim,
    build_claim_prompt,
    check_page_claims,
    split_claims,
    strip_page_chrome,
)

PAGE = """---
title: Meeting notes
verification: verified
---

# Meeting notes

> [!WARNING]
> banner noise

Globex asked for SSO support before the renewal and Globex confirmed budget approval this week.

| a | b |
| 1 | 2 |

Action items: send the security whitepaper and schedule a technical deep-dive in March 2026.
"""

SOURCE = ("Meeting notes, 2026-02-14. Attendees: our account team and Globex ops.\n\n"
          "Globex asked for SSO support before the renewal. Globex confirmed budget approval.\n"
          "Action items: send security whitepaper; schedule technical deep-dive in March.")


def test_strip_page_chrome_removes_frontmatter_headings_tables_banners():
    body = strip_page_chrome(PAGE)
    assert "verification:" not in body
    assert "# Meeting notes" not in body
    assert "banner noise" not in body
    assert "| a | b |" not in body
    assert "Globex asked for SSO" in body


def test_split_claims_paragraphs_capped_and_minimum_length():
    body = "Short.\n\n" + "\n\n".join(
        f"A meaningful paragraph number {i} with enough length to be a claim." for i in range(12))
    claims = split_claims(body)
    assert len(claims) == 8                      # MAX_PARAGRAPHS cap
    assert all(len(c) >= 40 for c in claims)     # "Short." dropped


def test_strip_page_chrome_drops_system_footers():
    page = ("---\nt: x\n---\n\n# T\n\nA real claim paragraph that should absolutely survive here.\n\n"
            "Summary of a live spreadsheet — for exact/current figures, open the original: local://x\n\n"
            "Original file: local://y\n")
    body = strip_page_chrome(page)
    assert "real claim paragraph" in body
    assert "Summary of a live spreadsheet" not in body
    assert "Original file:" not in body


def test_anchor_finds_the_right_region():
    filler = ("Unrelated filler paragraph about office plants and catering budgets. " * 30)
    source = filler + "\n\nGlobex confirmed budget approval for the renewal.\n\n" + filler
    start, end, score = anchor_claim("Globex confirmed budget approval for the renewal", source)
    assert score >= 0.9
    assert "budget approval" in source[start:end]


def test_anchor_low_score_on_absent_claim():
    _s, _e, score = anchor_claim("Aliens landed in the parking lot yesterday", SOURCE)
    assert score < 0.5


def test_build_claim_prompt_fences_windows():
    prompt, scores = build_claim_prompt(["Globex confirmed budget approval today"], SOURCE)
    assert "<<<UNTRUSTED-DATA" in prompt and "UNTRUSTED-DATA;end>>>" in prompt
    assert "CLAIM 0:" in prompt
    assert scores and scores[0] > 0.5


def test_fake_judge_supports_grounded_and_flags_ungrounded():
    async def go():
        page = PAGE
        return await check_page_claims(FakeClaimJudge(), page, SOURCE)
    out = asyncio.run(go())
    verdicts = {f.paragraph_index: f.verdict for f in out.findings}
    assert verdicts[0] == "supported"            # the grounded meeting paragraph

    hostile_page = "---\nx: y\n---\n\nThe committee approved a 900% budget increase for alien defense measures.\n"
    out2 = asyncio.run(check_page_claims(FakeClaimJudge(), hostile_page, SOURCE))
    assert out2.findings[0].verdict == "unsupported"


def test_check_page_claims_empty_body_yields_no_findings():
    out = asyncio.run(check_page_claims(FakeClaimJudge(), "---\na: b\n---\n\n# T\n", SOURCE))
    assert out.findings == []


def test_check_page_claims_clamps_out_of_range_indexes():
    class ScriptedJudge:
        async def run(self, prompt, *, deps=None, usage_limits=None):
            out = ClaimCheckOutput(findings=[
                ClaimFinding(paragraph_index=0, verdict="contradicted", claim="c", evidence="e"),
                ClaimFinding(paragraph_index=99, verdict="unsupported", claim="ghost"),
            ])
            return types.SimpleNamespace(output=out, usage=None)
    out = asyncio.run(check_page_claims(ScriptedJudge(), PAGE, SOURCE))
    assert [f.paragraph_index for f in out.findings] == [0]
    assert out.findings[0].verdict == "contradicted"
