import pytest
from pydantic import ValidationError

from corpus.schemas import ClassRecord, FileRecord, InventoryEntry, ManifestRecord


def test_file_record_validation():
    fr = FileRecord(path="./U/a.pdf", size=1, mtime=0.0, md5="m")
    assert fr.path == "./U/a.pdf"
    with pytest.raises(ValidationError):
        FileRecord(path="./U/a.pdf", size="not-int-able", mtime=0.0, md5="m")


def test_manifest_hash_optional():
    mr = ManifestRecord(path="./U/a.pdf", type="reports", verdict="IN", unit="U", size=1)
    assert mr.hash is None


def test_inventory_entry_minimal():
    e = InventoryEntry(name="a.pdf", localPath="U/a.pdf", drivePath="U/a.pdf", sourceUri="local://U/a.pdf")
    dumped = e.model_dump(exclude_none=True)
    assert "orgUnit" not in dumped
    assert "mimeType" not in dumped


def test_class_record_roundtrip_json():
    cr = ClassRecord(path="./U/a.pdf", type="reports", verdict="IN", unit="U", size=1)
    assert ClassRecord.model_validate_json(cr.model_dump_json()) == cr
