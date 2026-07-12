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


def test_load_state_non_dict_reinitializes(tmp_path):
    """Valid JSON that isn't an object (`[]`, `null`, ...) is corrupt in the same spirit as
    malformed JSON: recover to {"files": {}} rather than return it and crash sync_once at
    state.setdefault("files", ...) -- which main() swallows, wedging every later sync."""
    cfg = _cfg(tmp_path)
    for content in ["[]", "null", "42", '"x"']:
        (tmp_path / "_state.json").write_text(content)
        assert df.load_state(cfg) == {"files": {}}, content


def test_load_state_undecodable_reinitializes(tmp_path):
    """A _state.json that isn't valid UTF-8 (binary garbage) is corrupt too: recover to
    {"files": {}} instead of letting UnicodeDecodeError escape read_text() and wedge the sync."""
    cfg = _cfg(tmp_path)
    (tmp_path / "_state.json").write_bytes(b"\xff\xfe\x00\x81")
    assert df.load_state(cfg) == {"files": {}}


def test_write_atomic_and_sidecar_merge(tmp_path):
    cfg = _cfg(tmp_path)
    df.write_atomic(tmp_path / "deep" / "x.json", b'{"a": 1}')
    assert json.loads((tmp_path / "deep" / "x.json").read_text()) == {"a": 1}
    (tmp_path / "F.json").write_text(json.dumps({"kept": True}))
    df.merge_sidecar_lineage(cfg, "F", {"drivePath": "/X/a.pdf"})
    assert json.loads((tmp_path / "F.json").read_text()) == {"kept": True, "drivePath": "/X/a.pdf"}


def test_clobbered_by_sidecar_uses_file_identity_not_name(tmp_path, monkeypatch):
    """The clobber is decided by file identity, not name: an exact sidecar-name localPath is always
    the clobber, but a case-variant (<fid>.JSON) is the clobber ONLY on a case-folding filesystem
    (where it resolves to the sidecar). On a case-sensitive filesystem it is a distinct valid file
    that must be kept, else it is needlessly re-downloaded and orphaned."""
    cfg = _cfg(tmp_path)
    (tmp_path / "C.json").write_text("{}")                      # the sidecar
    assert df._clobbered_by_sidecar(cfg, "C", None) is False
    assert df._clobbered_by_sidecar(cfg, "C", "C.json") is True     # exact name -> always clobber
    assert df._clobbered_by_sidecar(cfg, "C", "C.pdf") is False     # unrelated content -> never
    # case-variant name: outcome follows the filesystem, probed via samefile
    monkeypatch.setattr(df.Path, "samefile", lambda self, other: False)   # case-sensitive fs
    assert df._clobbered_by_sidecar(cfg, "C", "C.JSON") is False    # distinct file -> keep it
    monkeypatch.setattr(df.Path, "samefile", lambda self, other: True)    # case-folding fs
    assert df._clobbered_by_sidecar(cfg, "C", "C.JSON") is True     # same file -> heal


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


def test_sync_once_isolates_one_bad_download(sync_env, monkeypatch):
    """One undownloadable file must not abort the pass: the good file lands, the bad one is counted
    and retried next cycle, and no file is wrongly deleted."""
    cfg, tmp_path, fake = sync_env
    real_download = df.download_file

    def flaky(cfg, d, fid, mime, lineage):
        if fid == "B":
            raise df.GogError("download disabled for this file")
        return real_download(cfg, d, fid, mime, lineage)
    monkeypatch.setattr(df, "download_file", flaky)

    stats = df.sync_once(cfg, "root")
    assert stats["errors"] == 1 and stats["added"] == 1
    assert (tmp_path / "A.pdf").exists()
    manifest = json.loads((tmp_path / "_state.json").read_text())["files"]
    assert "A" in manifest and "B" not in manifest      # B not recorded -> retried next pass

    monkeypatch.setattr(df, "download_file", real_download)
    stats2 = df.sync_once(cfg, "root")
    assert stats2["added"] == 1 and "B" in json.loads((tmp_path / "_state.json").read_text())["files"]


def test_sync_once_unlinks_previous_local_on_rename(sync_env):
    """A change that alters the local filename must not leave the old file orphaned."""
    cfg, tmp_path, fake = sync_env
    df.sync_once(cfg, "root")
    assert (tmp_path / "B.xlsx").exists()
    # B stops being a native sheet and becomes a binary .pdf (new fingerprint + new local name)
    fake.items[2].update(mimeType="application/pdf", name="b.pdf", modifiedTime="t2")
    df.sync_once(cfg, "root")
    assert (tmp_path / "B.pdf").exists()
    assert not (tmp_path / "B.xlsx").exists()           # stale local removed


def test_sync_once_json_content_does_not_collide_with_sidecar(sync_env):
    """<fid>.json is the sidecar, so a file whose local extension is .json must keep its
    content at <fid>.data.json instead of being clobbered by the sidecar write."""
    cfg, tmp_path, fake = sync_env
    fake.items.append({"id": "C", "name": "config.json", "mimeType": "application/json",
                       "modifiedTime": "t1", "parents": ["folder1"]})
    df.sync_once(cfg, "root")
    assert (tmp_path / "C.data.json").read_text() == "content-C"
    sidecar = json.loads((tmp_path / "C.json").read_text())
    assert sidecar["drivePath"].endswith("/Docs/config.json")
    manifest = json.loads((tmp_path / "_state.json").read_text())["files"]
    assert manifest["C"]["localPath"] == "C.data.json"


def test_sync_once_uppercase_json_content_does_not_collide_with_sidecar(sync_env):
    """The collision is case-insensitive: on a case-folding filesystem (macOS APFS, Windows,
    Docker Desktop bind mounts) <fid>.JSON and the sidecar <fid>.json are the same file, so an
    uppercase-extension JSON must also be routed to <fid>.data.JSON."""
    cfg, tmp_path, fake = sync_env
    fake.items.append({"id": "C", "name": "CONFIG.JSON", "mimeType": "application/json",
                       "modifiedTime": "t1", "parents": ["folder1"]})
    df.sync_once(cfg, "root")
    assert (tmp_path / "C.data.JSON").read_text() == "content-C"
    assert json.loads((tmp_path / "C.json").read_text())["id"] == "C"   # sidecar not clobbered
    manifest = json.loads((tmp_path / "_state.json").read_text())["files"]
    assert manifest["C"]["localPath"] == "C.data.JSON"


def test_sync_once_heals_json_entry_clobbered_by_sidecar(sync_env):
    """A pre-fix manifest whose localPath points at the sidecar must re-download the content
    (even with an unchanged fingerprint) and must not delete the sidecar while healing."""
    cfg, tmp_path, fake = sync_env
    fake.items.append({"id": "C", "name": "config.json", "mimeType": "application/json",
                       "modifiedTime": "t1", "parents": ["folder1"]})
    df.sync_once(cfg, "root")
    # forge the pre-fix state: manifest points at the sidecar, real content file gone
    state = json.loads((tmp_path / "_state.json").read_text())
    state["files"]["C"]["localPath"] = "C.json"
    (tmp_path / "_state.json").write_text(json.dumps(state))
    (tmp_path / "C.data.json").unlink()
    fake.downloads.clear()

    df.sync_once(cfg, "root")
    assert "C" in fake.downloads                        # not skipped despite same fingerprint
    assert (tmp_path / "C.data.json").read_text() == "content-C"
    assert json.loads((tmp_path / "C.json").read_text())["id"] == "C"   # sidecar survived
    manifest = json.loads((tmp_path / "_state.json").read_text())["files"]
    assert manifest["C"]["localPath"] == "C.data.json"


def test_sync_once_refuses_mass_deletion_on_empty_inventory(sync_env):
    """rc=0 with zero items while the manifest is populated must NOT wipe the mirror."""
    cfg, tmp_path, fake = sync_env
    df.sync_once(cfg, "root")
    fake.items.clear()                                   # inventory now returns nothing
    with pytest.raises(df.GogError, match="mass deletion"):
        df.sync_once(cfg, "root")
    assert (tmp_path / "A.pdf").exists()                 # nothing deleted
    assert json.loads((tmp_path / "_state.json").read_text())["files"]


def test_once_returns_nonzero_when_pass_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER", "Brain")
    monkeypatch.setenv("RAW_DIR", str(tmp_path))
    monkeypatch.setattr(df, "resolve_folder_id", lambda cfg: "root")
    monkeypatch.setattr(df, "sync_once", lambda cfg, fid: (_ for _ in ()).throw(df.GogError("auth expired")))
    assert df.main(["--once"]) == 1


def test_sync_once_redownloads_when_localpath_missing(sync_env):
    """A manifest entry with an empty localPath must be treated as absent (not vacuously present)."""
    cfg, tmp_path, fake = sync_env
    df.sync_once(cfg, "root")
    state = json.loads((tmp_path / "_state.json").read_text())
    state["files"]["A"]["localPath"] = ""               # older/foreign state with no local name
    (tmp_path / "_state.json").write_text(json.dumps(state))
    fake.downloads.clear()
    df.sync_once(cfg, "root")
    assert "A" in fake.downloads                          # re-downloaded instead of skipped


def test_main_requires_folder_config(monkeypatch, capsys):
    monkeypatch.delenv("DRIVE_FOLDER", raising=False)
    monkeypatch.delenv("DRIVE_FOLDER_ID", raising=False)
    assert df.main([]) == 2
    assert "DRIVE_FOLDER" in capsys.readouterr().out
