"""drive_fetch with explicit Config — no global monkeypatching, no env juggling.
The gog CLI is always faked; no network, no binary."""
import json

import drive_fetch as df
import pytest


def _cfg(tmp_path=None, **kw):
    defaults = dict(folder="Brain", raw_dir=tmp_path) if tmp_path else dict(folder="Brain")
    defaults.update(kw)
    return df.Config(**defaults)


# ── config ───────────────────────────────────────────────────────────────────
def test_config_from_env(monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER", "Brain")
    monkeypatch.setenv("RAW_DIR", "/mirror")
    monkeypatch.setenv("GOG_ALL_DRIVES", "false")
    cfg = df.Config.from_env()
    assert cfg.folder == "Brain"
    assert str(cfg.raw_dir) == "/mirror"
    assert cfg.all_drives is False
    assert str(cfg.state_path) == "/mirror/_state.json"


def test_export_table_follows_docs_format():
    cfg = _cfg(docs_format="pdf")
    assert cfg.export_for("application/vnd.google-apps.document") == ("pdf", ".pdf")
    assert cfg.export_for("application/vnd.google-apps.spreadsheet") == ("xlsx", ".xlsx")
    assert cfg.export_for("application/pdf") is None


# ── parsing helpers ──────────────────────────────────────────────────────────
def test_items_accepts_common_envelopes():
    assert df._items([{"id": "a"}, "junk"]) == [{"id": "a"}]
    assert df._items({"files": [{"id": "a"}]}) == [{"id": "a"}]
    assert df._items({"items": [{"id": "a"}]}) == [{"id": "a"}]
    assert df._items({"id": "solo"}) == [{"id": "solo"}]
    assert df._items({"unrelated": 1}) == []
    assert df._items("garbage") == []


def test_field_first_nonempty_wins():
    assert df._field({"a": "", "b": None, "c": "x"}, "a", "b", "c") == "x"
    assert df._field({}, "a", default="d") == "d"


def test_file_id_and_fingerprint():
    assert df.file_id({"fileId": "F"}) == "F"
    assert df.file_id({"id": "I"}) == "I"
    assert df.fingerprint({"modifiedTime": "T", "size": "9", "md5Checksum": "M"}) == "T|9|M"
    assert df.fingerprint({}) == "||"


def test_parents_tolerates_shapes():
    assert df.parents({"parents": ["p1", {"id": "p2"}, {"x": 1}]}) == ["p1", "p2"]
    assert df.parents({"parentId": "solo"}) == ["solo"]
    assert df.parents({}) == []


def test_ext_for_google_export_vs_binary(tmp_path):
    cfg = _cfg(tmp_path)
    assert df.ext_for(cfg, {}, "application/vnd.google-apps.spreadsheet") == ("xlsx", ".xlsx")
    assert df.ext_for(cfg, {"name": "report.pdf"}, "application/pdf") == (None, ".pdf")
    assert df.ext_for(cfg, {"name": "no-extension"}, "application/octet-stream") == (None, ".bin")


# ── lineage ──────────────────────────────────────────────────────────────────
def test_build_lineage_reconstructs_paths(tmp_path):
    cfg = _cfg(tmp_path)
    items = [
        {"id": "folder1", "name": "Portfolio", "parents": ["root"]},
        {"id": "doc1", "name": "a.pdf", "parents": ["folder1"]},
    ]
    lineage = df.build_lineage(cfg, items, "root")
    assert lineage["doc1"]["drivePath"] == "/Brain/Portfolio/a.pdf"
    assert lineage["doc1"]["parentPath"] == "/Brain/Portfolio"
    assert lineage["folder1"]["parentIds"] == ["root"]


def test_build_lineage_respects_explicit_path_and_missing_ancestor(tmp_path):
    cfg = _cfg(tmp_path)
    items = [
        {"id": "doc1", "name": "a.pdf", "path": "/Explicit/a.pdf", "parents": ["folder1"]},
        {"id": "doc2", "name": "b.pdf", "parents": ["ghost"]},   # ancestor not in inventory
    ]
    lineage = df.build_lineage(cfg, items, "root")
    assert lineage["doc1"]["drivePath"] == "/Explicit/a.pdf"
    assert lineage["doc2"]["drivePath"].endswith("/b.pdf")   # falls back under the root


# ── state ────────────────────────────────────────────────────────────────────
def test_load_state_corrupted(tmp_path):
    cfg = _cfg(tmp_path)
    (tmp_path / "_state.json").write_text("{broken")
    assert df.load_state(cfg) == {"files": {}}


def test_write_atomic_and_sidecar_merge(tmp_path):
    cfg = _cfg(tmp_path)
    df.write_atomic(tmp_path / "deep" / "x.json", b'{"a": 1}')
    assert json.loads((tmp_path / "deep" / "x.json").read_text()) == {"a": 1}
    (tmp_path / "F.json").write_text(json.dumps({"kept": True}))
    df.merge_sidecar_lineage(cfg, "F", {"drivePath": "/X/a.pdf"})
    assert json.loads((tmp_path / "F.json").read_text()) == {"kept": True, "drivePath": "/X/a.pdf"}


# ── folder resolution ────────────────────────────────────────────────────────
def test_resolve_folder_id_explicit_wins(tmp_path):
    assert df.resolve_folder_id(_cfg(tmp_path, folder_id="explicit-id")) == "explicit-id"


def test_resolve_folder_id_requires_config(tmp_path):
    with pytest.raises(df.GogError, match="DRIVE_FOLDER"):
        df.resolve_folder_id(_cfg(tmp_path, folder=""))


def test_resolve_folder_id_by_name_and_ambiguity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(df, "gog", lambda cfg, *a, **k: [{"id": "f1", "name": "Brain"},
                                                         {"id": "f2", "name": "Brain (old)"}])
    assert df.resolve_folder_id(cfg) == "f1"   # exact-name match filters the noise
    monkeypatch.setattr(df, "gog", lambda cfg, *a, **k: [{"id": "f1", "name": "Brain"},
                                                         {"id": "f2", "name": "Brain"}])
    with pytest.raises(df.GogError, match="ambiguous"):
        df.resolve_folder_id(cfg)
    monkeypatch.setattr(df, "gog", lambda cfg, *a, **k: [])
    with pytest.raises(df.GogError, match="no folder"):
        df.resolve_folder_id(cfg)


# ── sync ─────────────────────────────────────────────────────────────────────
class FakeGog:
    """Dispatches drive_fetch's gog() calls: inventory listing, downloads, metadata gets."""

    def __init__(self, items):
        self.items = items
        self.downloads = []

    def __call__(self, cfg, *args, json_out=True):
        if args[:2] == ("drive", "inventory"):
            return {"files": self.items}
        if args[:2] == ("drive", "download"):
            fid, out = args[2], args[args.index("--out") + 1]
            self.downloads.append(fid)
            with open(out, "w") as f:
                f.write(f"content-{fid}")
            return ""
        if args[:2] == ("drive", "get"):
            return {"file": {"id": args[2], "webViewLink": f"https://drive.example/{args[2]}"}}
        raise AssertionError(f"unexpected gog call: {args}")


@pytest.fixture()
def sync_env(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, folder="Docs Root")
    items = [
        {"id": "folder1", "name": "Docs", "mimeType": df.FOLDER_MIME, "parents": ["root"]},
        {"id": "A", "name": "a.pdf", "mimeType": "application/pdf",
         "modifiedTime": "t1", "parents": ["folder1"]},
        {"id": "B", "name": "b", "mimeType": "application/vnd.google-apps.spreadsheet",
         "modifiedTime": "t1", "parents": ["folder1"]},
    ]
    fake = FakeGog(items)
    monkeypatch.setattr(df, "gog", fake)
    return cfg, tmp_path, fake


def test_sync_once_downloads_new_files(sync_env):
    cfg, tmp_path, fake = sync_env
    stats = df.sync_once(cfg, "root")
    assert stats["added"] == 2 and stats["removed"] == 0
    assert (tmp_path / "A.pdf").read_text() == "content-A"
    assert (tmp_path / "B.xlsx").exists()          # native sheet exported as xlsx
    sidecar = json.loads((tmp_path / "A.json").read_text())
    assert sidecar["drivePath"].endswith("/Docs/a.pdf")
    manifest = json.loads((tmp_path / "_state.json").read_text())["files"]
    assert manifest["A"]["localPath"] == "A.pdf"


def test_sync_once_skips_unchanged_and_propagates_deletes(sync_env):
    cfg, tmp_path, fake = sync_env
    df.sync_once(cfg, "root")
    fake.downloads.clear()

    stats = df.sync_once(cfg, "root")               # nothing changed
    assert stats["skipped"] == 2 and fake.downloads == []

    fake.items[1]["modifiedTime"] = "t2"            # A changed -> re-download
    stats = df.sync_once(cfg, "root")
    assert stats["changed"] == 1 and fake.downloads == ["A"]

    del fake.items[2]                                # B gone from Drive
    stats = df.sync_once(cfg, "root")
    assert stats["removed"] == 1
    assert not (tmp_path / "B.xlsx").exists()
    assert "B" not in json.loads((tmp_path / "_state.json").read_text())["files"]


def test_sync_once_backfills_lineage_without_download(sync_env):
    cfg, tmp_path, fake = sync_env
    df.sync_once(cfg, "root")
    fake.downloads.clear()
    # strip lineage from the manifest to simulate an older state format
    state = json.loads((tmp_path / "_state.json").read_text())
    state["files"]["A"].pop("drivePath")
    (tmp_path / "_state.json").write_text(json.dumps(state))

    stats = df.sync_once(cfg, "root")
    assert stats["metadata_updated"] >= 1 and fake.downloads == []
    manifest = json.loads((tmp_path / "_state.json").read_text())["files"]
    assert manifest["A"]["drivePath"].endswith("/Docs/a.pdf")


def test_main_requires_folder_config(monkeypatch, capsys):
    monkeypatch.delenv("DRIVE_FOLDER", raising=False)
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    assert df.main([]) == 2
    assert "DRIVE_FOLDER" in capsys.readouterr().out
