"""Page building: output layout, slug stability/uniqueness, frontmatter contract."""
from clean.page import brain_path, build_page, write_page
from clean.schemas import Mention, PageMetadata, ProcessorOutput


def _out(**kw):
    defaults = dict(skipped=False, extraction_quality="usable", representation="full",
                    metadata=PageMetadata(title="Board minutes", type="meeting-notes"),
                    body_markdown="body text", reason="test")
    defaults.update(kw)
    return ProcessorOutput(**defaults)


def _lineage(**kw):
    d = {"fileId": "F123", "sourceUri": "https://example.com/f", "name": "doc.pdf",
         "extractedAt": "2026-01-01T00:00:00Z", "method": "pdf"}
    d.update(kw)
    return d


def test_brain_path_tracked_entity():
    rel, slug = brain_path({"slug": "initech", "kind": "tracked"}, "Q1 report.pdf", "abc123")
    assert rel == "entities/initech"
    assert slug.startswith("q1-report-")


def test_brain_path_prospect_unit_and_fallback():
    assert brain_path({"slug": "hooli", "kind": "prospect"}, "x.pdf", "id")[0] == "prospects/hooli"
    assert brain_path({"unit": "EU Team"}, "x.pdf", "id")[0] == "units/eu-team"
    assert brain_path({}, "x.pdf", "id")[0] == "general"
    assert brain_path(None, "x.pdf", "id")[0] == "general"


def test_brain_path_slug_unique_for_same_filename():
    """Two different files with the same name must not collide (id hash disambiguates)."""
    _, s1 = brain_path({}, "report.pdf", "file-id-1")
    _, s2 = brain_path({}, "report.pdf", "file-id-2")
    assert s1 != s2


def test_brain_path_slug_stable():
    assert brain_path({}, "report.pdf", "same-id") == brain_path({}, "report.pdf", "same-id")


def test_build_page_frontmatter_core_fields():
    page = build_page(_out(), _lineage(), {"slug": "initech", "kind": "tracked", "name": "Initech",
                                           "seq": 3, "status": "archived", "unit": "EU", "period": "2023-Q2"})
    for expected in ("type: meeting-notes", "title: Board minutes", 'id: "drive:F123"',
                     "source_file_id: F123", "entity: initech", "entity_kind: tracked",
                     "seq: 3", "status: archived", "unit: EU", "period: 2023-Q2",
                     "representation: full", "extraction_quality: usable", "source_format: pdf"):
        assert expected in page, expected
    assert page.count("# Board minutes") == 1


def test_build_page_ocr_provenance():
    """When the agent escalated to OCR, the page records it — method stays the converter route."""
    page = build_page(_out(), _lineage(method="pdf", ocr_model="gemini-3-flash"), {})
    assert "extraction_method: vision" in page
    assert "ocr_model: gemini-3-flash" in page
    assert "source_format: pdf" in page


def test_build_page_normal_has_no_vision_marker():
    page = build_page(_out(), _lineage(), {})
    assert "extraction_method: vision" not in page
    assert "ocr_model" not in page


def test_build_page_sheet_detail_in_source_and_link():
    page = build_page(_out(representation="digest"), _lineage(method="sheet"), {})
    assert "detail_in_source: true" in page
    assert "https://example.com/f" in page


def test_build_page_digest_appends_source_link_once():
    page = build_page(_out(representation="digest", body_markdown="see https://example.com/f"),
                      _lineage(), {})
    assert page.count("https://example.com/f") == 2  # frontmatter + the one already in the body


def test_build_page_manual_review_banner():
    page = build_page(_out(extraction_quality="manual_review"), _lineage(), {})
    assert "[!WARNING]" in page


def test_build_page_strips_model_h1_and_neutralizes_hr():
    page = build_page(_out(body_markdown="# Title dup\n\ncontent\n---\nmore"), _lineage(), {})
    assert "# Title dup" not in page
    assert "\n***\n" in page


def test_build_page_mentions_block():
    out = _out(metadata=PageMetadata(title="T", type="report",
                                     mentions=[Mention(name="Initech", type="company")]))
    page = build_page(out, _lineage(), {})
    assert "mentions:" in page
    assert "{ name: Initech, type: company }" in page


def test_build_page_verification_frontmatter():
    from clean.schemas import Verification
    v = Verification(verdict="partial", numbers_total=4,
                     numbers_unverified=["9.9M"], mentions_unverified=["Ghost Corp"])
    page = build_page(_out(), _lineage(), {}, verification=v)
    assert "verification: partial" in page
    assert "unverified_numbers: [9.9M]" in page
    assert "unverified_mentions: [Ghost Corp]" in page
    assert "[!WARNING]" not in page                     # partial: frontmatter only, no banner


def test_build_page_verification_failed_banner():
    from clean.schemas import Verification
    v = Verification(verdict="failed", numbers_total=2, numbers_unverified=["9.9M", "77%"])
    page = build_page(_out(), _lineage(), {}, verification=v)
    assert "verification: failed" in page
    assert "Verification failed: 2 figure(s)" in page


def test_build_page_without_verification_has_no_field():
    page = build_page(_out(), _lineage(), {})
    assert "verification:" not in page


def test_build_page_frontmatter_survives_hostile_strings():
    """Titles/filenames/tags/mentions with YAML-special characters must still yield PARSEABLE
    frontmatter — the page contract is consumed as YAML by the brain and graph stages."""
    import yaml

    out = _out(
        metadata=PageMetadata(
            title='Project "Phoenix": Q1 plan',            # inner quotes + colon
            type="report",
            tags=["a: b", "c,d", "normal"],                 # colon and comma in tags
            mentions=[Mention(name='AT&T "wireless"', type="company"),
                      Mention(name="On", type="company")],  # "On" is a YAML 1.1 boolean if unquoted
        ),
        body_markdown="body",
    )
    page = build_page(out, _lineage(name='*URGENT* "payroll".xlsx',
                                    sourceUri='local://Clients/a"b.md'), {})
    fm_text = page.split("\n---\n", 1)[0].removeprefix("---\n")
    fm = yaml.safe_load(fm_text)                            # must not raise
    assert fm["title"] == 'Project "Phoenix": Q1 plan'
    assert fm["tags"] == ["a: b", "c,d", "normal"]
    names = [m["name"] for m in fm["mentions"]]
    assert 'AT&T "wireless"' in names
    assert "On" in names                                    # preserved as the string "On", not True


def test_write_page_atomic(tmp_path):
    rel = write_page(str(tmp_path), "entities/initech", "doc-abc123", "content")
    assert rel == "entities/initech/doc-abc123.md"
    assert (tmp_path / rel).read_text() == "content"
