"""Tests for the on-screen QR picker (qr_pick.py + SFMBridge.qr_pick_start).

No screen and no browser are used: the helper process is mocked, decoding runs
on rendered images, and webbrowser is replaced.
"""
from __future__ import annotations

import json
import os
import sys
import threading

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import qr_pick  # noqa: E402
import qr_scan  # noqa: E402
from _qrgen import qr_image  # noqa: E402

needs_decoder = pytest.mark.skipif(not qr_scan.decoder_available(),
                                   reason="pyzbar / zbar not available")


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda u, *a, **k: opened.append(u))
    monkeypatch.setattr(webbrowser, "open", lambda u, *a, **k: opened.append(u))
    return opened


# ── JSON protocol ────────────────────────────────────────────────────────────

def test_parse_result_success_line_among_noise():
    out = "some warning\n" + json.dumps({"ok": True, "text": "hello", "is_url": False,
                                         "rect": [1, 2, 3, 4]}) + "\n"
    r = qr_pick.parse_result(out)
    assert r["ok"] is True and r["text"] == "hello" and r["rect"] == [1, 2, 3, 4]


def test_parse_result_uses_last_json_line():
    out = '{"ok": false, "reason": "x"}\n{"ok": true, "text": "B"}\n'
    assert qr_pick.parse_result(out)["text"] == "B"


@pytest.mark.parametrize("reason", ["cancelled", "not_found", "unavailable"])
def test_parse_result_failures(reason):
    r = qr_pick.parse_result(json.dumps({"ok": False, "reason": reason}))
    assert r == {"ok": False, "reason": reason, "detail": ""}


def test_parse_result_garbage_reports_stderr():
    r = qr_pick.parse_result("not json\n{broken", "Traceback\nValueError: boom", 1)
    assert r["ok"] is False and r["reason"] == "error" and "boom" in r["detail"]
    r = qr_pick.parse_result("", "", 3)
    assert r["reason"] == "error" and "3" in r["detail"]


# ── URL normalisation / allow-list ──────────────────────────────────────────

@pytest.mark.parametrize("text,url", [
    ("https://verify.example.com/doc?id=1", "https://verify.example.com/doc?id=1"),
    ("  http://example.com/x  ", "http://example.com/x"),
    ("www.example.org/cert/42", "https://www.example.org/cert/42"),
    ("WWW.Example.org", "https://WWW.Example.org"),
])
def test_normalize_url_accepts_http_links(text, url):
    assert qr_pick.normalize_url(text) == url


@pytest.mark.parametrize("text", [
    "javascript:alert(1)", "file:///C:/Windows/win.ini", "data:text/html,<b>x</b>",
    "vbscript:msgbox", "ftp://example.com/", "mailto:a@b.c", "plain text",
    "https://", "https://exa mple.com/", "", "   ",
])
def test_normalize_url_rejects_everything_else(text):
    assert qr_pick.normalize_url(text) == ""


def test_finalize_opens_url_once():
    opened = []
    r = qr_pick.finalize({"ok": True, "text": "www.example.com/a"}, opener=opened.append)
    assert opened == ["https://www.example.com/a"]
    assert r["opened"] is True and r["is_url"] is True and r["url"] == "https://www.example.com/a"
    assert r["host"] == "www.example.com" and r["domain"] == "example.com"


def test_finalize_never_opens_text_or_scripts():
    opened = []
    for text in ("Certificate No. 12345", "javascript:alert(1)", "file:///C:/x"):
        r = qr_pick.finalize({"ok": True, "text": text}, opener=opened.append)
        assert r["opened"] is False and r["is_url"] is False and r["url"] == ""
    assert opened == []


def test_finalize_open_error_is_reported():
    def boom(_u):
        raise OSError("no browser")
    r = qr_pick.finalize({"ok": True, "text": "https://example.com/"}, opener=boom)
    assert r["opened"] is False and r["is_url"] and "no browser" in r["open_error"]


def test_helper_command_dev_and_frozen(monkeypatch):
    cmd = qr_pick.helper_command()
    assert cmd[0] == sys.executable and cmd[1].endswith("qr_pick.py") and os.path.isfile(cmd[1])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert qr_pick.helper_command() == [sys.executable, "--qr-pick"]


def test_run_helper_parses_subprocess_output(monkeypatch):
    import subprocess
    seen = {}

    class CP:
        returncode = 0
        stdout = (json.dumps({"ok": True, "text": "X", "rect": [0, 0, 1, 1]}) + "\n").encode()
        stderr = b""

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return CP()
    monkeypatch.setattr(subprocess, "run", fake_run)
    r = qr_pick.run_helper()
    assert r["ok"] and r["text"] == "X"
    if sys.platform == "win32":
        assert seen["kw"]["creationflags"] & subprocess.CREATE_NO_WINDOW


# ── Bridge ───────────────────────────────────────────────────────────────────

def _bridge(monkeypatch):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    events = []
    monkeypatch.setattr(b, "_emit", lambda ev, payload=None: events.append((ev, payload)))
    monkeypatch.setattr(b, "_thread", lambda fn, *a, **k: fn(*a, **k))
    return b, events


def test_bridge_pick_opens_url(monkeypatch, no_browser):
    b, events = _bridge(monkeypatch)
    monkeypatch.setattr(qr_pick, "run_helper",
                        lambda *a, **k: {"ok": True, "text": "https://verify.example.com/c?id=7",
                                         "is_url": True, "rect": [10, 10, 50, 50], "mode": "click"})
    r = b.qr_pick_start()
    assert r["ok"] and r["started"]
    ev, payload = events[-1]
    assert ev == "qr_pick_result"
    assert payload["ok"] and payload["opened"] is True and payload["url"] == "https://verify.example.com/c?id=7"
    assert payload["domain"] == "example.com"
    assert no_browser == ["https://verify.example.com/c?id=7"]


def test_bridge_pick_text_not_opened(monkeypatch, no_browser):
    b, events = _bridge(monkeypatch)
    monkeypatch.setattr(qr_pick, "run_helper", lambda *a, **k: {"ok": True, "text": "Reg No 445/2020"})
    b.qr_pick_start()
    payload = events[-1][1]
    assert payload["ok"] and payload["opened"] is False and payload["is_url"] is False
    assert payload["text"] == "Reg No 445/2020" and no_browser == []


@pytest.mark.parametrize("reason", ["not_found", "cancelled", "unavailable"])
def test_bridge_pick_failures_pass_through(monkeypatch, no_browser, reason):
    b, events = _bridge(monkeypatch)
    monkeypatch.setattr(qr_pick, "run_helper", lambda *a, **k: {"ok": False, "reason": reason, "detail": "d"})
    b.qr_pick_start()
    assert events[-1] == ("qr_pick_result", {"ok": False, "reason": reason, "detail": "d"})
    assert no_browser == []


def test_bridge_pick_busy_guard(monkeypatch):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    events = []
    monkeypatch.setattr(b, "_emit", lambda ev, payload=None: events.append((ev, payload)))
    gate, started = threading.Event(), threading.Event()

    def slow_helper(*a, **k):
        started.set()
        gate.wait(5)
        return {"ok": False, "reason": "cancelled", "detail": ""}
    monkeypatch.setattr(qr_pick, "run_helper", slow_helper)
    assert b.qr_pick_start()["ok"]
    assert started.wait(5)
    second = b.qr_pick_start()
    assert second["ok"] is False and second.get("busy") is True
    gate.set()
    for _ in range(100):
        if events:
            break
        threading.Event().wait(0.02)
    assert events and events[-1][0] == "qr_pick_result"
    # finished → a new session can start again
    gate.set()
    assert b.qr_pick_start()["ok"]


def test_bridge_pick_helper_exception(monkeypatch):
    b, events = _bridge(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("spawn failed")
    monkeypatch.setattr(qr_pick, "run_helper", boom)
    b.qr_pick_start()
    payload = events[-1][1]
    assert payload["ok"] is False and payload["reason"] == "error" and "spawn failed" in payload["detail"]
    assert b.qr_pick_start()["ok"]       # not left busy


# ── Decoding (pure, no screen) ───────────────────────────────────────────────

def _screen_with_codes():
    shot = Image.new("RGB", (1600, 900), (40, 44, 52))          # dark "desktop"
    page = Image.new("RGB", (700, 800), "white")                # a certificate page
    shot.paste(page, (500, 50))
    shot.paste(qr_image("https://verify.example.org/cert?id=A12", 5), (620, 160))
    shot.paste(qr_image("SECOND-CODE", 5), (920, 520))
    return shot


@needs_decoder
def test_pick_decode_click_on_code():
    shot = _screen_with_codes()
    qr1 = qr_image("https://verify.example.org/cert?id=A12", 5)
    cx, cy = 620 + qr1.width // 2, 160 + qr1.height // 2
    hit = qr_pick.pick_decode(shot, cx, cy)
    assert hit and hit["text"] == "https://verify.example.org/cert?id=A12"
    x, y, w, h = hit["bbox"]
    assert x <= cx <= x + w and y <= cy <= y + h        # bbox in screenshot coords


@needs_decoder
def test_pick_decode_picks_nearest_code():
    shot = _screen_with_codes()
    qr2 = qr_image("SECOND-CODE", 5)
    hit = qr_pick.pick_decode(shot, 920 + qr2.width // 2, 520 + qr2.height // 2)
    assert hit and hit["text"] == "SECOND-CODE"


@needs_decoder
def test_pick_decode_near_but_not_on_code():
    shot = _screen_with_codes()
    # a click just beside the code still finds it via the larger crops
    hit = qr_pick.pick_decode(shot, 600, 150)
    assert hit and hit["text"].startswith("https://verify.example.org")


@needs_decoder
def test_pick_decode_miss_and_out_of_bounds():
    shot = _screen_with_codes()
    assert qr_pick.pick_decode(shot, 100, 800) is None
    assert qr_pick.pick_decode(shot, -5, 10) is None
    assert qr_pick.pick_decode(shot, 5000, 10) is None


@needs_decoder
def test_region_decode_and_success_payload():
    shot = _screen_with_codes()
    hit = qr_pick.region_decode(shot, 880, 480, 300, 300)
    assert hit and hit["text"] == "SECOND-CODE" and hit["bbox"][0] >= 900
    assert qr_pick.region_decode(shot, 0, 0, 300, 300) is None
    assert qr_pick.region_decode(shot, 10, 10, 4, 4) is None
    p = qr_pick.success_payload(hit, origin=(-1920, 0), mode="drag")
    assert p["ok"] and p["mode"] == "drag" and p["is_url"] is False
    assert p["rect"][0] == hit["bbox"][0] - 1920


@needs_decoder
def test_pick_decode_with_overlay_tint():
    # if the light tint ever ends up in the capture, decoding must still work
    shot = _screen_with_codes()
    tint = Image.new("RGB", shot.size, (11, 13, 18))
    tinted = Image.blend(shot, tint, 0.15)
    qr1 = qr_image("https://verify.example.org/cert?id=A12", 5)
    hit = qr_pick.pick_decode(tinted, 620 + qr1.width // 2, 160 + qr1.height // 2)
    assert hit and hit["text"] == "https://verify.example.org/cert?id=A12"
