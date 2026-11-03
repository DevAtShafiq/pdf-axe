"""Tests for the screen recorder bridge (recorder_bridge.py) and the
screenshot region picker helpers (screen_pick.py).

No screen, no browser: mss / the helper process are mocked.
"""
from __future__ import annotations

import base64
import os
import sys
import types

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import recorder_bridge as rb  # noqa: E402
import screen_pick  # noqa: E402


class FakeBridge(rb.RecorderBridgeMixin):
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.events = []
        self._window = None

    def _load_settings(self):
        return dict(self.settings)

    def _save_settings(self, data):
        self.settings.update(data)

    def _emit(self, event, payload=None):
        self.events.append((event, payload))


@pytest.fixture
def br(tmp_path):
    return FakeBridge({"rec_folder": str(tmp_path / "rec")})


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_clean_stem():
    assert rb.clean_stem("My clip") == "My clip"
    assert rb.clean_stem(r"..\..\evil:name?") == "evil_name_"
    assert rb.clean_stem("CON") == "Recording"
    assert rb.clean_stem("   ...  ", "X") == "X"
    assert rb.clean_stem("a/b/c") == "c"


def test_unique_path(tmp_path):
    p1 = rb.unique_path(str(tmp_path), "Rec", "mp4")
    assert os.path.basename(p1) == "Rec.mp4"
    open(p1, "wb").close()
    p2 = rb.unique_path(str(tmp_path), "Rec", ".mp4")
    assert os.path.basename(p2) == "Rec (2).mp4"
    open(p2, "wb").close()
    assert os.path.basename(rb.unique_path(str(tmp_path), "Rec", "mp4")) == "Rec (3).mp4"


def test_is_inside(tmp_path):
    d = str(tmp_path)
    assert rb.is_inside(d, os.path.join(d, "a.mp4"))
    assert rb.is_inside(d, os.path.join(d, "sub", "a.mp4"))
    assert not rb.is_inside(d, os.path.join(d, "..", "a.mp4"))
    assert not rb.is_inside(d, d)
    assert not rb.is_inside(d, os.path.join(os.path.dirname(d), "other", "x"))


def test_default_dir_is_videos_office_axe():
    p = rb.default_output_dir()
    assert os.path.basename(p) == "Office Axe"
    assert os.path.isabs(p)


# ── folder settings ──────────────────────────────────────────────────────────

def test_default_dir_created_and_set_dir(tmp_path):
    b = FakeBridge({"rec_folder": str(tmp_path / "x" / "y")})
    r = b.rec_default_dir()
    assert r["ok"] and r["path"] == str(tmp_path / "x" / "y") and not r["is_default"]
    assert os.path.isdir(r["path"])
    r = b.rec_set_dir(str(tmp_path / "other"))
    assert r["ok"] and r["path"] == str(tmp_path / "other")
    assert b.settings["rec_folder"] == str(tmp_path / "other")


def test_set_dir_reset_uses_default(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "default_output_dir", lambda: str(tmp_path / "Videos" / "Office Axe"))
    b = FakeBridge({"rec_folder": str(tmp_path / "custom")})
    r = b.rec_set_dir("")
    assert r["ok"] and r["is_default"] and r["path"].endswith("Office Axe")
    assert b.settings["rec_folder"] == ""


# ── recording lifecycle ──────────────────────────────────────────────────────

def test_begin_chunk_finish(br, tmp_path):
    r = br.rec_begin("mp4")
    assert r["ok"]
    rid, folder = r["id"], r["dir"]
    assert folder == str(tmp_path / "rec")
    assert r["final_name"].startswith("Recording ") and r["final_name"].endswith(".mp4")
    # nothing on disk until the first chunk arrives
    assert os.listdir(folder) == []
    assert br.rec_chunk(rid, b64(b"\x00\x00\x00\x18ftypmp42"))["bytes"] == 12
    assert br.rec_chunk(rid, b64(b"more-data"))["bytes"] == 21
    assert os.listdir(folder) == [f".recording-{rid}.mp4.part"]
    f = br.rec_finish(rid, "Lecture 1")
    assert f["ok"], f
    assert f["name"] == "Lecture 1.mp4" and f["size"] == 21
    with open(f["path"], "rb") as fh:
        assert fh.read() == b"\x00\x00\x00\x18ftypmp42more-data"
    assert sorted(os.listdir(folder)) == ["Lecture 1.mp4"]
    # session is gone
    assert not br.rec_chunk(rid, b64(b"x"))["ok"]
    assert not br.rec_finish(rid)["ok"]


def test_finish_never_overwrites(br):
    names = []
    for _ in range(3):
        rid = br.rec_begin("webm")["id"]
        br.rec_chunk(rid, b64(b"\x1aE\xdf\xa3data"))
        names.append(br.rec_finish(rid, "Same.webm")["name"])
    assert names == ["Same.webm", "Same (2).webm", "Same (3).webm"]


def test_finish_default_name_and_bad_name(br):
    rid = br.rec_begin("webm")["id"]
    br.rec_chunk(rid, b64(b"abc"))
    f = br.rec_finish(rid, "")
    assert f["ok"] and f["name"].startswith("Recording ") and f["name"].endswith(".webm")
    rid = br.rec_begin("webm")["id"]
    br.rec_chunk(rid, b64(b"abc"))
    f = br.rec_finish(rid, r"..\..\escape")
    assert f["ok"] and f["name"] == "escape.webm"
    assert os.path.dirname(f["path"]) == f["dir"]


def test_finish_empty_recording(br):
    rid = br.rec_begin("mp4")["id"]
    r = br.rec_finish(rid)
    assert not r["ok"] and r["code"] == "empty"


def test_discard_moves_part_to_review(br, tmp_path):
    rid = br.rec_begin("mp4")["id"]
    br.rec_chunk(rid, b64(b"abc"))
    d = br.rec_discard(rid)
    assert d["ok"] and d["moved_to"]
    folder = str(tmp_path / "rec")
    assert os.listdir(folder) == ["_to_review"]
    assert os.path.isfile(d["moved_to"])
    assert os.path.dirname(d["moved_to"]) == os.path.join(folder, "_to_review")
    assert os.path.basename(d["moved_to"]).startswith("Discarded recording ")


def test_discard_before_data_leaves_nothing(br, tmp_path):
    rid = br.rec_begin("mp4")["id"]
    assert br.rec_discard(rid) == {"ok": True, "moved_to": ""}
    assert os.listdir(str(tmp_path / "rec")) == []


def test_validation(br, monkeypatch):
    assert not br.rec_begin("exe")["ok"]
    assert not br.rec_chunk("../../etc", b64(b"x"))["ok"]
    assert not br.rec_chunk("0123456789abcdef", b64(b"x"))["ok"]       # unknown id
    rid = br.rec_begin("mp4")["id"]
    assert br.rec_chunk(rid, "not base64!!")["error"] == "Invalid chunk data"
    assert not br.rec_chunk(rid, 12345)["ok"]
    monkeypatch.setattr(rb, "MAX_CHUNK_B64", 8)
    assert br.rec_chunk(rid, b64(b"0123456789"))["error"] == "Chunk too large"


def test_disk_full(br, monkeypatch):
    rid = br.rec_begin("mp4")["id"]
    monkeypatch.setattr(rb.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=10))
    r = br.rec_chunk(rid, b64(b"abc"))
    assert not r["ok"] and r["code"] == "disk_full"


def test_begin_with_explicit_dir(br, tmp_path):
    r = br.rec_begin("mp4", str(tmp_path / "explicit"))
    assert r["ok"] and r["dir"] == str(tmp_path / "explicit")


# ── list / recover ───────────────────────────────────────────────────────────

def test_list_and_recover(br, tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    for i, n in enumerate(["a.mp4", "b.webm", "Screenshot 1.png", "notes.txt"]):
        p = folder / n
        p.write_bytes(b"x" * (i + 1))
        os.utime(p, (1000 + i, 1000 + i))
    orphan = folder / ".recording-0123456789abcdef.webm.part"
    orphan.write_bytes(b"partial")
    rid = br.rec_begin("mp4")["id"]
    br.rec_chunk(rid, b64(b"live"))         # an active recording is not "unfinished"
    r = br.rec_list(10)
    assert r["ok"]
    assert [i["name"] for i in r["items"]] == ["Screenshot 1.png", "b.webm", "a.mp4"]
    assert r["items"][0]["kind"] == "image" and r["items"][1]["kind"] == "video"
    assert [p["name"] for p in r["unfinished"]] == [orphan.name]
    assert br.rec_list(1)["items"][0]["name"] == "Screenshot 1.png"

    rec = br.rec_recover(str(orphan))
    assert rec["ok"] and rec["name"].startswith("Recovered recording ") and rec["name"].endswith(".webm")
    assert not orphan.exists()
    # active part cannot be "recovered"; arbitrary files neither
    active = folder / f".recording-{rid}.mp4.part"
    assert not br.rec_recover(str(active))["ok"]
    assert not br.rec_recover(str(folder / "a.mp4"))["ok"]
    assert not br.rec_recover(str(tmp_path / ".recording-0123456789abcdef.webm.part"))["ok"]


# ── title / hotkey ───────────────────────────────────────────────────────────

def test_set_title(br):
    titles = []
    br._window = types.SimpleNamespace(set_title=titles.append)
    assert br.rec_set_title("● Recording 00:05")["ok"]
    assert br.rec_set_title("")["ok"]
    assert titles == ["● Recording 00:05", "Office Axe"]


def test_hotkey_disable_without_enable(br):
    assert br.rec_hotkey(False) == {"ok": True, "registered": False}


# ── screenshots ──────────────────────────────────────────────────────────────

@pytest.fixture
def fake_grab(monkeypatch):
    import qr_pick
    img = Image.new("RGB", (64, 48), (10, 120, 200))
    monkeypatch.setattr(qr_pick, "grab_virtual_screen", lambda: (img, (0, 0)))
    return img


def test_screenshot_full(br, fake_grab, tmp_path):
    r = br.screenshot_full(hide_app=True)
    assert r["ok"], r
    assert r["name"].startswith("Screenshot ") and r["name"].endswith(".png")
    assert (r["width"], r["height"]) == (64, 48)
    with Image.open(r["path"]) as im:
        assert im.size == (64, 48)
    r2 = br.screenshot_full(hide_app=False)
    assert r2["ok"] and r2["path"] != r["path"]          # never overwrites


def test_screenshot_full_mss_with_fake_module(br, monkeypatch):
    """grab_virtual_screen itself, with a fake mss module."""
    import qr_pick

    class Shot:
        width, height = 4, 2
        rgb = bytes([255, 0, 0] * 8)

    class Sct:
        monitors = [{"left": 0, "top": 0, "width": 4, "height": 2}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, mon):
            return Shot()

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(MSS=Sct, mss=Sct))
    r = br.screenshot_full(hide_app=False)
    assert r["ok"], r
    with Image.open(r["path"]) as im:
        assert im.size == (4, 2) and im.getpixel((0, 0)) == (255, 0, 0)


def test_screenshot_full_failure(br, monkeypatch):
    import qr_pick

    def boom():
        raise RuntimeError("no screen")
    monkeypatch.setattr(qr_pick, "grab_virtual_screen", boom)
    r = br.screenshot_full()
    assert not r["ok"] and "no screen" in r["error"]


def test_screenshot_region(br, monkeypatch, tmp_path):
    calls = []

    def fake_helper(out_path, timeout=None, env=None):
        calls.append(out_path)
        img = Image.new("RGB", (100, 80), "white")
        return screen_pick.save_crop(img, (10, 10, 30, 20), out_path)
    monkeypatch.setattr(screen_pick, "run_helper", fake_helper)
    r = br.screenshot_region()
    assert r["ok"], r
    assert r["path"] == calls[0] and os.path.dirname(r["path"]) == str(tmp_path / "rec")
    with Image.open(r["path"]) as im:
        assert im.size == (30, 20)


def test_screenshot_region_cancel_and_escape(br, monkeypatch, tmp_path):
    monkeypatch.setattr(screen_pick, "run_helper",
                        lambda p, **k: {"ok": False, "reason": "cancelled", "detail": ""})
    r = br.screenshot_region()
    assert not r["ok"] and r["reason"] == "cancelled"
    # a helper that claims a path outside the folder is rejected
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"x")
    monkeypatch.setattr(screen_pick, "run_helper",
                        lambda p, **k: {"ok": True, "path": str(outside)})
    assert not br.screenshot_region()["ok"]


# ── screen_pick helpers ──────────────────────────────────────────────────────

def test_normalize_rect():
    assert screen_pick.normalize_rect(50, 40, 10, 10, 100, 100) == (10, 10, 40, 30)
    assert screen_pick.normalize_rect(-5, -5, 20, 20, 100, 100) == (0, 0, 20, 20)
    assert screen_pick.normalize_rect(90, 90, 150, 150, 100, 100) == (90, 90, 10, 10)
    assert screen_pick.normalize_rect(10, 10, 12, 30, 100, 100) is None


def test_save_crop_no_overwrite(tmp_path):
    img = Image.new("RGB", (20, 20), "red")
    out = str(tmp_path / "s.png")
    r = screen_pick.save_crop(img, (0, 0, 5, 6), out)
    assert r == {"ok": True, "path": out, "rect": [0, 0, 5, 6], "width": 5, "height": 6}
    with pytest.raises(FileExistsError):
        screen_pick.save_crop(img, (0, 0, 5, 6), out)


def test_helper_command_and_parse(monkeypatch):
    cmd = screen_pick.helper_command(r"C:\x\a.png")
    assert cmd[-2:] == ["--out", r"C:\x\a.png"] and cmd[1].endswith("screen_pick.py")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert screen_pick.helper_command("p")[1:] == ["--screen-pick", "--out", "p"]


def test_run_helper_parses_output(monkeypatch):
    class CP:
        stdout = b'noise\n{"ok": true, "path": "C:/a.png", "width": 3, "height": 4}\n'
        stderr = b""
        returncode = 0
    monkeypatch.setattr(screen_pick.subprocess, "run", lambda *a, **k: CP())
    r = screen_pick.run_helper(r"C:\a.png")
    assert r == {"ok": True, "path": "C:/a.png", "width": 3, "height": 4}


def test_main_requires_out():
    assert screen_pick.main(["--screen-pick"]) == 2
