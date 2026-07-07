"""Taxonomy rules engine: ordering, matchers, units, matrix, validation."""
import json

import pytest

from corpus.schemas import FileRecord
from corpus.stages.classify_files import classify, classify_records, ext_of, load_taxonomy, norm, topdir, unit_of


@pytest.fixture()
def tax():
    return load_taxonomy()   # the packaged example taxonomy


def test_load_taxonomy_packaged(tax):
    assert tax["rules"]
    assert tax["fallback"]["type"] == "other"
    assert "internal-admin" in tax["demoted_types"]


def test_helpers():
    assert norm("Café/Ñoño") == "cafe/nono"
    assert topdir("./Unit A/x/y.pdf") == "Unit A"
    assert ext_of("a/b/report.PDF") == ".pdf"
    assert ext_of("a/no-ext") == ""


def test_classify_first_match_wins(tax):
    # .ds_store hits system-noise (rule 1) even under a "reports" folder
    assert classify("./U/quarterly report/.DS_Store", tax) == ("system-noise", "OUT")


def test_classify_by_path_and_basename(tax):
    assert classify("./U/2024/Quarterly Report Q1.pdf", tax) == ("reports", "IN")
    assert classify("./U/Board Meeting/notes.docx", tax) == ("meeting-minutes", "IN")
    assert classify("./U/pitch deck v3.pptx", tax) == ("presentations", "IN")
    assert classify("./U/Contracts/NDA - Initech.pdf", tax) == ("legal-contracts", "OUT")
    assert classify("./U/random-notes.txt", tax) == ("other", "MAYBE")


def test_classify_accent_insensitive(tax):
    typ, verdict = classify("./U/Comptabilité/invoice-münchen-2024.pdf", tax)
    assert (typ, verdict) == ("finance-accounting", "OUT")


def test_custom_taxonomy_with_regex(tmp_path):
    p = tmp_path / "tax.json"
    p.write_text(json.dumps({
        "rules": [{"type": "cases", "verdict": "IN", "path_regex": ["case-\\d+.*final"]}],
        "fallback": {"type": "other", "verdict": "OUT"},
    }))
    tax = load_taxonomy(str(p))
    assert classify("./U/case-42/report FINAL.pdf", tax) == ("cases", "IN")
    assert classify("./U/case-42/report draft.pdf", tax) == ("other", "OUT")


def test_load_taxonomy_validates(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"rules": [{"type": "x", "verdict": "YES"}]}))
    with pytest.raises(ValueError, match="verdict"):
        load_taxonomy(str(p))
    p.write_text(json.dumps({"rules": [{"verdict": "IN"}]}))
    with pytest.raises(ValueError, match="type"):
        load_taxonomy(str(p))


def test_unit_of_uses_org_units_map(tmp_path):
    p = tmp_path / "tax.json"
    p.write_text(json.dumps({"rules": [], "org_units": {"Engineering Dept": "ENG"},
                             "fallback": {"type": "other", "verdict": "MAYBE"}}))
    tax = load_taxonomy(str(p))
    assert unit_of("./Engineering Dept/x.pdf", tax) == "ENG"
    assert unit_of("./Sales/x.pdf", tax) == "Sales"


def test_classify_records_matrix_and_root_skip(tax):
    files = [
        FileRecord(path="./Sales/Quarterly Report Q1.pdf", size=10, mtime=0, md5="a"),
        FileRecord(path="./Sales/random.txt", size=5, mtime=0, md5="b"),
        FileRecord(path="./root-file.txt", size=1, mtime=0, md5="c"),   # at root -> skipped
    ]
    rows, matrix = classify_records(files, tax)
    assert len(rows) == 2
    assert {r.unit for r in rows} == {"Sales"}
    assert matrix["units"] == ["Sales"]
    assert matrix["cells"]["reports"]["Sales"] == [1, 10]
    assert matrix["examples"]["reports"] == ["./Sales/Quarterly Report Q1.pdf"]
