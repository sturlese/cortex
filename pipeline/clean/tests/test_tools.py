"""Document tools: chunking, budgets, OCR one-shot semantics and graceful degradation."""
from clean import converters
from clean.tools import MAX_READ_MORE_CALLS, OCR_MAX_CHARS, READ_MORE_CHUNK, DocContext, ocr, read_more


def _ctx(text="X" * 40000, path="/doc.pdf", shown=16000):
    return DocContext(path=path, full_text=text, shown=shown)


# ── read_more ────────────────────────────────────────────────────────────────
def test_read_more_returns_next_chunk_and_advances():
    d = _ctx()
    chunk = read_more(d)
    assert chunk.startswith("X" * 100)
    assert d.shown == 16000 + READ_MORE_CHUNK
    assert "more chars not shown" in chunk
    assert d.read_more_calls == 1


def test_read_more_signals_end_of_text():
    d = _ctx(text="X" * 17000)
    chunk = read_more(d)
    assert "[END OF EXTRACTED TEXT]" in chunk
    assert d.shown == 17000
    assert "no more text" in read_more(d)
    assert d.read_more_calls == 1            # the no-op didn't consume budget


def test_read_more_budget_exhausted():
    d = _ctx(text="X" * 200000)
    for _ in range(MAX_READ_MORE_CALLS):
        read_more(d)
    refusal = read_more(d)
    assert "budget exhausted" in refusal
    assert d.read_more_calls == MAX_READ_MORE_CALLS


# ── ocr ──────────────────────────────────────────────────────────────────────
def test_ocr_only_for_pdfs():
    d = _ctx(path="/doc.xlsx")
    assert "only available for PDF" in ocr(d)
    assert d.ocr_used is False


def test_ocr_success_sets_provenance_and_caps(monkeypatch):
    monkeypatch.setattr(converters, "vision_extract",
                        lambda path: {"method": "vision", "text": "Y" * (OCR_MAX_CHARS + 500), "model": "gemini-test"})
    from clean import tools
    monkeypatch.setattr(tools, "vision_extract", converters.vision_extract)
    d = _ctx()
    out = ocr(d)
    assert d.ocr_used is True
    assert d.ocr_model == "gemini-test"
    assert len(d.ocr_text) == OCR_MAX_CHARS
    assert out.startswith("Y")


def test_ocr_is_one_shot(monkeypatch):
    from clean import tools
    monkeypatch.setattr(tools, "vision_extract", lambda path: {"text": "ok", "model": "m"})
    d = _ctx()
    ocr(d)
    assert "already used" in ocr(d)


def test_ocr_failure_degrades_gracefully(monkeypatch):
    from clean import tools
    def boom(path):
        raise RuntimeError("GEMINI_API_KEY missing")
    monkeypatch.setattr(tools, "vision_extract", boom)
    d = _ctx()
    msg = ocr(d)
    assert "ocr failed" in msg and "GEMINI_API_KEY" in msg
    assert d.ocr_text == "" and d.ocr_model is None
