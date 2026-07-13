"""worker.process_one with stubbed agents: pages, skips, OCR escalation, the judge-retry loop."""
import asyncio
import types

from clean.schemas import PageMetadata, ProcessorOutput
from clean.worker import process_one


class FakeUsage:
    input_tokens = 100
    output_tokens = 50
    cache_read_tokens = 10
    details = {"reasoning_tokens": 5}


class FakeProcessor:
    """Scripted backend: returns the given outputs in order; can mutate deps like a tool would."""

    def __init__(self, *outputs, on_run=None):
        self._outputs = list(outputs)
        self._on_run = on_run
        self.prompts = []
        self.deps_seen = []

    async def run(self, prompt, *, deps=None, usage_limits=None):
        self.prompts.append(prompt)
        self.deps_seen.append(deps)
        if self._on_run:
            self._on_run(deps)
            self._on_run = None            # tools fire on the first attempt only
        out = self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]
        return types.SimpleNamespace(output=out, usage=FakeUsage())


def _output(**kw):
    defaults = dict(skipped=False, extraction_quality="usable", representation="full",
                    metadata=PageMetadata(title="Q1 update", type="report"),
                    body_markdown="content", reason="ok")
    defaults.update(kw)
    return ProcessorOutput(**defaults)


def _doc(tmp_path, name="update.md", drive_path="/X/Portfolio/1. Initech/update.md", content="raw text content"):
    p = tmp_path / name
    p.write_text(content)
    return {"fileId": "FID1", "path": str(p),
            "entry": {"name": name, "drivePath": drive_path, "orgUnit": "HQ",
                      "sourceUri": "https://example.com/doc"}}


def _run(doc, proc, tmp_path):
    return asyncio.run(process_one(doc, proc, str(tmp_path), str(tmp_path / "brain")))


def test_process_one_writes_page(tmp_path):
    proc = FakeProcessor(_output())
    res = _run(_doc(tmp_path), proc, tmp_path)
    assert res["skipped"] is False
    assert res["entity"] == "initech"
    assert res["unit"] == "HQ"
    assert res["path"].startswith("entities/initech/")
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "title: Q1 update" in page
    assert res["usage"] == {"in": 100, "out": 50, "cache_read": 10, "reasoning": 5}
    assert "raw text content" in proc.prompts[0]
    assert "EXTRACTED TEXT" in proc.prompts[0]
    # the agent got its per-document context (tools state)
    assert proc.deps_seen[0].full_text == "raw text content"
    assert "ocr" not in res and "retried" not in res


def test_process_one_skipped_writes_nothing(tmp_path):
    proc = FakeProcessor(ProcessorOutput(skipped=True, reason="noise"))
    res = _run(_doc(tmp_path), proc, tmp_path)
    assert res["skipped"] is True
    assert res["reason"] == "noise"
    assert not (tmp_path / "brain").exists()


def test_process_one_ocr_escalation_provenance_and_verification(tmp_path, monkeypatch):
    """The agent escalates to OCR: figures quoted from the OCR text must verify, and the page
    must carry the vision provenance."""
    from clean import worker as pl
    monkeypatch.setattr(pl, "extract", lambda path, method: {"method": method, "text": "(mangled)"})
    def simulate_ocr(deps):
        deps.ocr_used = True
        deps.ocr_text = "OCR transcription: revenue reached $7.7M in Q3."
        deps.ocr_model = "gemini-test"
    proc = FakeProcessor(_output(body_markdown="Revenue reached $7.7M in Q3."), on_run=simulate_ocr)
    doc = _doc(tmp_path, name="scan.pdf", content="(mangled pdftotext output)")
    res = _run(doc, proc, tmp_path)
    assert res["ocr"] is True
    assert res["verification"] == "verified"     # $7.7M lives only in the OCR text
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "extraction_method: vision" in page
    assert "ocr_model: gemini-test" in page
    assert "source_format: pdf" in page


def test_process_one_judge_retry_recovers(tmp_path):
    """Generator-judge loop: invented figures trigger ONE retry with verifier feedback; the
    corrected output wins and usage is accumulated."""
    bad = _output(body_markdown="Revenue hit $9.9M with 77% margin.")
    good = _output(body_markdown="Revenue was $1.2M.")
    proc = FakeProcessor(bad, good)
    doc = _doc(tmp_path, content="Board notes. Revenue was $1.2M this quarter.")
    res = _run(doc, proc, tmp_path)
    assert res["retried"] is True
    assert res["verification"] == "verified"
    assert "unverified_numbers" not in res
    assert res["usage"]["in"] == 200                         # two attempts, summed
    assert "DETERMINISTIC VERIFIER" in proc.prompts[1]       # feedback carried the findings
    assert "9.9M" in proc.prompts[1]
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "$1.2M" in page and "9.9M" not in page


def test_process_one_judge_retry_keeps_original_when_worse(tmp_path):
    """If the retry doesn't improve, the first output (and its verdict) stands."""
    bad = _output(body_markdown="Figures: 111%, 222%.")
    worse = _output(body_markdown="Figures: 111%, 222%, 333%.")
    proc = FakeProcessor(bad, worse)
    doc = _doc(tmp_path, content="No figures at all in this source.")
    res = _run(doc, proc, tmp_path)
    assert res["retried"] is True
    assert res["verification"] == "failed"
    assert set(res["unverified_numbers"]) == {"111%", "222%"}
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "333%" not in page


def test_process_one_retry_on_misattributed_period(tmp_path):
    """Period anchoring feeds the judge loop: a real figure tied to the wrong month triggers the
    corrective retry, and the fixed attribution wins."""
    bad = _output(body_markdown="Revenue was 512000 in 2026-01.")
    good = _output(body_markdown="Revenue was 512000 in 2026-03.")
    proc = FakeProcessor(bad, good)
    doc = _doc(tmp_path, content="KPI row: 2026-03 revenue 512000.")
    res = _run(doc, proc, tmp_path)
    assert res["retried"] is True
    assert res["verification"] == "verified"
    assert "unanchored_numbers" not in res
    assert "ties them to a period" in proc.prompts[1]     # feedback names the misattribution
    assert "512000" in proc.prompts[1]
    assert res["figure_spans"]["512000"]                  # span traced back into the source
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "2026-03" in page and "2026-01" not in page


def test_process_one_misattribution_kept_when_retry_worse(tmp_path):
    """A retry that adds an invented figure on top of the misattribution must not win."""
    bad = _output(body_markdown="Revenue was 512000 in 2026-01.")
    worse = _output(body_markdown="Revenue was 512000 in 2026-01 and margin hit 999999.")
    proc = FakeProcessor(bad, worse)
    doc = _doc(tmp_path, content="KPI row: 2026-03 revenue 512000.")
    res = _run(doc, proc, tmp_path)
    assert res["retried"] is True
    assert res["unanchored_numbers"] == ["512000"]
    page = (tmp_path / "brain" / res["path"]).read_text()
    assert "999999" not in page
    assert 'unanchored_numbers: ["512000"]' in page


def test_process_one_no_retry_when_verified(tmp_path):
    proc = FakeProcessor(_output(body_markdown="plain prose, no figures"))
    res = _run(_doc(tmp_path), proc, tmp_path)
    assert "retried" not in res
    assert len(proc.prompts) == 1


def test_process_one_truncates_and_offers_read_more(tmp_path):
    doc = _doc(tmp_path, content="A" * 20000)
    proc = FakeProcessor(_output())
    _run(doc, proc, tmp_path)
    body = proc.prompts[0].split(":\n", 1)[1]
    assert len(body) == 16000
    assert "call read_more for the rest" in proc.prompts[0]
    assert proc.deps_seen[0].shown == 16000
