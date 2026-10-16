"""File-browser shell: SFMBridge folder / file / clipboard / settings methods."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfm_bridge  # noqa: E402


@pytest.fixture
def b(monkeypatch, tmp_path):
    monkeypatch.setattr(sfm_bridge.SFMBridge, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    return sfm_bridge.SFMBridge()


def _mk(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


# ── listing ──────────────────────────────────────────────────────────────────

def test_list_folder_entries_and_natural_order(b, tmp_path):
    for n in ("a10.txt", "a2.txt", "a1.txt"):
        _mk(tmp_path / "d" / n)
    (tmp_path / "d" / "sub").mkdir()
    r = b.list_folder(str(tmp_path / "d"))
    assert r["ok"]
    names = [e["name"] for e in r["entries"]]
    assert names == ["sub", "a1.txt", "a2.txt", "a10.txt"]
    e = r["entries"][1]
    assert e["ext"] == ".txt" and not e["is_dir"] and e["size"] == 1 and e["size_str"] == "1 B"
    assert r["entries"][0]["size_str"] == ""


def test_list_folder_missing_is_error(b, tmp_path):
    assert not b.list_folder(str(tmp_path / "nope"))["ok"]


def test_list_folder_bare_drive_means_root(b):
    drive = os.path.splitdrive(os.getcwd())[0]
    if not drive:
        pytest.skip("no drive letters")
    r = b.list_folder(drive)
    assert r["ok"]
    assert r["path"] == drive + "\\"
    assert sorted(e["name"] for e in r["entries"]) == sorted(os.listdir(drive + "\\"))


def test_list_folder_recursive_depth(b, tmp_path):
    _mk(tmp_path / "a" / "b" / "c" / "f.txt")
    r = b.list_folder_recursive(str(tmp_path / "a"), 1)
    assert r["ok"]
    top = r["tree"][0]
    assert top["name"] == "b" and top["children"][0]["name"] == "c"
    assert "children" not in top["children"][0]


def test_drives_home_desktop_exists(b, tmp_path):
    assert b.get_drives()["ok"]
    assert os.path.isdir(b.get_home()["path"])
    assert b.get_desktop()["path"].endswith("Desktop")
    r = b.path_exists(str(tmp_path))
    assert r["exists"] and r["is_dir"]
    assert not b.path_exists(str(tmp_path / "zz"))["exists"]


def test_get_file_info(b, tmp_path):
    r = b.get_file_info(_mk(tmp_path / "n.txt", "hello"))
    assert r["ok"] and r["size"] == 5 and r["name"] == "n.txt"


def test_open_native_reports_failure(b, tmp_path):
    # A folder / missing file can't be opened with the default app; it used to say ok.
    assert not b.open_native(str(tmp_path))["ok"]
    assert not b.open_native(str(tmp_path / "missing.pdf"))["ok"]


# ── create / rename ──────────────────────────────────────────────────────────

def test_create_folder_validation(b, tmp_path):
    assert b.create_folder(str(tmp_path), "New folder")["ok"]
    assert os.path.isdir(tmp_path / "New folder")
    assert not b.create_folder(str(tmp_path), "New folder")["ok"]       # exists
    assert not b.create_folder(str(tmp_path), "new FOLDER")["ok"]       # case-insensitive
    for bad in ("", "  ", ".", "..", "a/b", "a:b", "x.", "x "):
        assert not b.create_folder(str(tmp_path), bad)["ok"], bad
    assert not b.create_folder(str(tmp_path / "missing"), "x")["ok"]
    assert b.create_folder(str(tmp_path), "한국어 폴더")["ok"]


def test_rename_file_and_case_only(b, tmp_path):
    src = _mk(tmp_path / "a.pdf")
    r = b.rename_file(src, "b.pdf")
    assert r["ok"] and r["new_path"] == str(tmp_path / "b.pdf")
    r = b.rename_file(r["new_path"], "B.pdf")
    assert r["ok"] and "B.pdf" in os.listdir(tmp_path)
    r = b.rename_file(str(tmp_path / "B.pdf"), "B.pdf")
    assert r["ok"] and r["new_path"] == str(tmp_path / "B.pdf")


def test_rename_file_refuses_clash_and_bad_names(b, tmp_path):
    a = _mk(tmp_path / "a.txt")
    _mk(tmp_path / "b.txt")
    assert not b.rename_file(a, "b.txt")["ok"]
    assert not b.rename_file(a, "x?.txt")["ok"]
    assert not b.rename_file(a, "sub\\x.txt")["ok"]
    assert os.path.exists(a)


# ── soft delete / restore ────────────────────────────────────────────────────

def test_soft_delete_and_restore(b, tmp_path):
    f = _mk(tmp_path / "doc.pdf")
    d = tmp_path / "folder"
    _mk(d / "inner.txt")
    r = b.soft_delete([f, str(d)])
    assert r["ok"]
    assert not os.path.exists(f) and not d.exists()
    moved = r["results"]
    assert all(os.path.dirname(m["moved_to"]).endswith("_to_review") for m in moved)
    u = b.restore_soft_deleted([{"path": m["path"], "moved_to": m["moved_to"]} for m in moved])
    assert u["ok"]
    assert os.path.isfile(f) and (d / "inner.txt").is_file()


def test_restore_never_overwrites(b, tmp_path):
    f = _mk(tmp_path / "doc.txt", "old")
    m = b.soft_delete([f])["results"][0]
    _mk(tmp_path / "doc.txt", "new")
    u = b.restore_soft_deleted([m])
    assert u["ok"]
    assert (tmp_path / "doc.txt").read_text(encoding="utf-8") == "new"
    assert (tmp_path / "doc (2).txt").read_text(encoding="utf-8") == "old"


def test_soft_delete_missing_reports_error(b, tmp_path):
    r = b.soft_delete([str(tmp_path / "missing.txt")])
    assert not r["ok"] and "missing.txt" in r["error"]


# ── copy / move ──────────────────────────────────────────────────────────────

def test_copy_files_auto_numbers(b, tmp_path):
    f = _mk(tmp_path / "a.txt")
    r = b.copy_files([f], str(tmp_path))
    assert r["ok"] and r["results"][0]["dst"] == str(tmp_path / "a (2).txt")
    d = tmp_path / "my.folder"
    _mk(d / "x.txt")
    r = b.copy_files([str(d)], str(tmp_path))
    assert r["ok"] and r["results"][0]["dst"] == str(tmp_path / "my.folder (2)")
    assert (tmp_path / "my.folder (2)" / "x.txt").is_file()


def test_copy_folder_into_itself_is_refused(b, tmp_path):
    d = tmp_path / "top"
    _mk(d / "child" / "x.txt")
    r = b.copy_files([str(d)], str(d / "child"))
    assert not r["ok"] and "itself" in r["error"]
    assert not (d / "child" / "top").exists()


def test_copy_reports_failures(b, tmp_path):
    r = b.copy_files([str(tmp_path / "missing.txt")], str(tmp_path))
    assert not r["ok"] and "missing.txt" in r["error"]
    assert not b.copy_files([], str(tmp_path / "nope"))["ok"]


def test_move_never_replaces_or_merges(b, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    f = _mk(src / "a.txt", "src")
    _mk(dst / "a.txt", "dst")
    _mk(src / "box" / "1.txt")
    _mk(dst / "box" / "2.txt")
    r = b.move_files([f, str(src / "box")], str(dst))
    assert r["ok"]
    assert (dst / "a.txt").read_text(encoding="utf-8") == "dst"
    assert (dst / "a (2).txt").read_text(encoding="utf-8") == "src"
    # the moved folder is not nested inside the existing "box"
    assert not (dst / "box" / "box").exists()
    assert (dst / "box (2)" / "1.txt").is_file()


def test_move_into_same_folder_is_noop(b, tmp_path):
    f = _mk(tmp_path / "a.txt")
    r = b.move_files([f], str(tmp_path))
    assert r["ok"] and os.path.isfile(f)
    assert os.listdir(tmp_path) == ["a.txt"]


def test_move_folder_into_itself_refused(b, tmp_path):
    d = tmp_path / "top"
    _mk(d / "child" / "x.txt")
    r = b.move_files([str(d)], str(d / "child"))
    assert not r["ok"]
    assert (d / "child" / "x.txt").is_file()


def test_move_reports_missing(b, tmp_path):
    r = b.move_files([str(tmp_path / "gone.txt")], str(tmp_path))
    assert not r["ok"] and "gone.txt" in r["error"]


# ── clipboard (real Windows clipboard) ───────────────────────────────────────

# These replace the user's real clipboard, so they only run when asked:
#   SFM_TEST_CLIPBOARD=1 python -m pytest tests/test_shell_bridge.py
win_only = pytest.mark.skipif(sys.platform != "win32" or os.environ.get("SFM_TEST_CLIPBOARD") != "1",
                              reason="Windows clipboard (set SFM_TEST_CLIPBOARD=1)")


@win_only
def test_clipboard_files_round_trip(b, tmp_path):
    a = _mk(tmp_path / "한글 a.txt")
    c = _mk(tmp_path / "b.txt")
    assert b.set_clipboard_files([a, c], cut=True)["ok"]
    r = b.get_clipboard_files()
    assert r["ok"] and r["cut"] is True
    assert [os.path.normcase(p) for p in r["paths"]] == [os.path.normcase(a), os.path.normcase(c)]
    assert b.set_clipboard_files([a], cut=False)["ok"]
    r = b.get_clipboard_files()
    assert r["cut"] is False and len(r["paths"]) == 1
    assert not b.set_clipboard_files([str(tmp_path / "missing")])["ok"]


@win_only
def test_clipboard_text_unicode(b):
    import win32clipboard
    assert b.set_clipboard("여권 passport")["ok"]
    win32clipboard.OpenClipboard()
    try:
        assert win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) == "여권 passport"
    finally:
        win32clipboard.CloseClipboard()
    assert b.get_clipboard_files()["paths"] == []


# ── settings ─────────────────────────────────────────────────────────────────

def test_settings_round_trip_and_filter(b):
    assert b.save_settings({"theme": "light", "evil": 1, "zoom": 120})["ok"]
    s = b.get_settings()["settings"]
    assert s["theme"] == "light" and s["zoom"] == 120 and "evil" not in s
    assert s["has_api_key"] is False


def test_api_key_masked(b, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert b.set_api_key("  sk-test-1234567890abcd ")["ok"]
    r = b.get_api_key()
    assert r["has_key"] and r["masked"].startswith("sk-test-") and "1234567890" not in r["masked"]
    s = b.get_settings()["settings"]
    assert "openai_api_key" not in s and s["has_api_key"] is True
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_rename_lang_settings(b):
    assert b.set_rename_lang("en")["lang"] == "en"
    assert b.get_rename_lang()["lang"] == "en"
    assert not b.set_rename_lang("xx")["ok"]
    assert b.set_rename_lang("KO")["lang"] == "ko"


def test_log_js_never_fails(b):
    assert b.log_js("weird", "msg")["ok"]
