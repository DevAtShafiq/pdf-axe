"""Tests for QR checking (qr_scan.py + the SFMBridge QR methods)."""
from __future__ import annotations

import io
import os
import sys

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import qr_scan  # noqa: E402
from _qrgen import qr_image  # noqa: E402

needs_decoder = pytest.mark.skipif(not qr_scan.decoder_available(),
                                   reason="pyzbar / zbar not available")


# ── payload classification (no decoder needed) ───────────────────────────────

def test_https_url_clean():
    c = qr_scan.classify_payload("https://verify.example.com/doc?id=1")
    assert c["type"] == "url" and c["openable"]
    assert c["host"] == "verify.example.com" and c["domain"] == "example.com"
    assert c["warnings"] == []


@pytest.mark.parametrize("url,needle", [
    ("http://example.com/x", "http"),
    ("https://192.168.0.10/login", "IP address"),
    ("https://bit.ly/abc", "Shortened"),
    ("https://paypal.com@evil.example/", "@"),
    ("https://xn--pypal-4ve.com/", "punycode"),
    ("https://example.zip/", ".zip"),
    ("https://example.com:8443/", "port"),
])
def test_url_warnings(url, needle):
    c = qr_scan.classify_payload(url)
    assert c["type"] == "url"
    assert any(needle.lower() in w.lower() for w in c["warnings"]), c["warnings"]


def test_government_note_and_cc_domain():
    c = qr_scan.classify_payload("https://www.apostille.mofa.go.kr/check")
    assert c["domain"] == "mofa.go.kr"
    assert "Government / education domain" in c["notes"]


def test_bare_domain_gets_https_and_warning():
    c = qr_scan.classify_payload("www.example.org/verify")
    assert c["type"] == "url" and c["url"] == "https://www.example.org/verify"
    assert any("prefix" in w for w in c["warnings"])


@pytest.mark.parametrize("text,typ", [
    ("BEGIN:VCARD\nFN:A\nEND:VCARD", "vcard"),
    ("WIFI:S:net;T:WPA;P:x;;", "wifi"),
    ("mailto:a@b.c", "email"),
    ("tel:+8210123", "phone"),
    ("geo:37.5,127.0", "geo"),
    ("MECARD:N:A;;", "contact"),
    ("Certificate No. 12345", "text"),
])
def test_payload_types(text, typ):
    assert qr_scan.classify_payload(text)["type"] == typ


def test_dangerous_schemes_not_openable():
    c = qr_scan.classify_payload("javascript:alert(1)")
    assert c["type"] == "other" and not c["openable"] and c["warnings"]
    assert not qr_scan.is_safe_to_open("javascript:alert(1)")
    assert not qr_scan.is_safe_to_open("file:///C:/x")
    assert qr_scan.is_safe_to_open("https://example.com/a?b=1")


def test_rotation_mapper_round_trip():
    src = Image.new("L", (300, 200), 255)
    ImageDraw.Draw(src).rectangle((48, 38, 52, 42), fill=0)
    for ang in (30, 45, 90, 315):
        rot = qr_scan._rotated(src, ang)
        px = rot.load()
        pts = [(x, y) for y in range(rot.height) for x in range(rot.width) if px[x, y] < 100]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        ox, oy = qr_scan._rot_mapper(src.size, ang, rot.size)(cx, cy)
        assert abs(ox - 50) < 2.5 and abs(oy - 40) < 2.5, (ang, ox, oy)


def test_unsupported_and_missing_files(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x")
    r = qr_scan.scan_file(str(p))
    assert r["ok"] is False and "Unsupported" in r["error"]
    r = qr_scan.scan_file(str(tmp_path / "nope.png"))
    assert r["ok"] is False and "not found" in r["error"]


# ── decoding (skipped when pyzbar/zbar is missing, e.g. on CI) ───────────────

def _page_with_two_codes(path):
    page = Image.new("RGB", (1400, 1800), "white")
    page.paste(qr_image("https://example.com/verify?id=1", 6), (80, 90))
    page.paste(qr_image("BEGIN:VCARD\nFN:Test\nEND:VCARD", 5), (900, 1300))
    page.save(path)


@needs_decoder
def test_image_with_two_codes(tmp_path):
    p = str(tmp_path / "cert.png")
    _page_with_two_codes(p)
    r = qr_scan.scan_file(p)
    assert r["ok"], r
    texts = {x["text"]: x for x in r["results"]}
    assert set(texts) == {"https://example.com/verify?id=1", "BEGIN:VCARD\nFN:Test\nEND:VCARD"}
    url = texts["https://example.com/verify?id=1"]
    assert url["type"] == "url" and url["page"] is None
    x, y, w, h = url["bbox"]
    assert 80 <= x <= 140 and 90 <= y <= 150 and 120 <= w <= 200
    assert url["thumb"].startswith("data:image/png;base64,")
    assert texts["BEGIN:VCARD\nFN:Test\nEND:VCARD"]["type"] == "vcard"


@needs_decoder
def test_exif_rotated_jpeg(tmp_path):
    img = qr_image("https://rot.example.org/", 5).rotate(90, expand=True)
    exif = Image.Exif()
    exif[0x0112] = 6
    p = str(tmp_path / "rot.jpg")
    img.save(p, quality=95, exif=exif.tobytes())
    r = qr_scan.scan_file(p)
    assert [x["text"] for x in r["results"]] == ["https://rot.example.org/"]


@needs_decoder
def test_low_contrast_needs_hard_pass(tmp_path):
    img = qr_image("LOWCONTRAST-42", 6).convert("L").point(lambda v: 118 if v < 128 else 150)
    p = str(tmp_path / "lc.png")
    img.save(p)
    r = qr_scan.scan_file(p)
    assert [x["text"] for x in r["results"]] == ["LOWCONTRAST-42"]


@needs_decoder
def test_pdf_all_pages(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page()                       # page 1: nothing
    for i, txt in ((2, "https://p2.example.com/"), (3, "PAGE-THREE")):
        page = doc.new_page()
        buf = io.BytesIO()
        qr_image(txt, 4).save(buf, "PNG")
        page.insert_image(fitz.Rect(380, 680, 440, 740), stream=buf.getvalue())
    p = str(tmp_path / "doc.pdf")
    doc.save(p)
    r = qr_scan.scan_file(p)
    assert r["ok"] and r["page_count"] == 3 and r["pages_scanned"] == 3
    assert [(x["text"], x["page"]) for x in r["results"]] == [
        ("https://p2.example.com/", 2), ("PAGE-THREE", 3)]
    assert 370 < r["results"][0]["bbox_pt"][0] < 390


@needs_decoder
def test_blank_image_has_no_results(tmp_path):
    p = str(tmp_path / "blank.png")
    Image.new("RGB", (600, 400), "white").save(p)
    r = qr_scan.scan_file(p)
    assert r["ok"] and r["results"] == []


# ── bridge ────────────────────────────────────────────────────────────────────

def _bridge(monkeypatch):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    events = []
    monkeypatch.setattr(b, "_emit", lambda ev, payload=None: events.append((ev, payload)))
    monkeypatch.setattr(b, "_thread", lambda fn, *a, **k: fn(*a, **k))
    return b, events


@needs_decoder
def test_bridge_batch_scan(tmp_path, monkeypatch):
    b, events = _bridge(monkeypatch)
    good = str(tmp_path / "cert.png")
    _page_with_two_codes(good)
    blank = str(tmp_path / "blank.png")
    Image.new("RGB", (300, 300), "white").save(blank)
    r = b.scan_qr_files([good, blank], "job1")
    assert r["ok"] and r["job_id"] == "job1" and r["total"] == 2
    prog = [p for e, p in events if e == "qr_scan_progress"]
    assert [p["index"] for p in prog] == [1, 2]
    done = [p for e, p in events if e == "qr_scan_done"][0]
    assert done["ok"] and not done["cancelled"]
    assert len(done["files"][0]["results"]) == 2 and done["files"][1]["results"] == []


@needs_decoder
def test_bridge_single_file_event(tmp_path, monkeypatch):
    b, events = _bridge(monkeypatch)
    p = str(tmp_path / "one.png")
    qr_image("HELLO-QR", 6).save(p)
    b.scan_qr_from_file(p)
    ev, payload = events[-1]
    assert ev == "qr_result" and payload["ok"] and payload["text"] == "HELLO-QR"


@needs_decoder
def test_bridge_screen_region_never_opens_browser(monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda u: opened.append(u))
    b, _ = _bridge(monkeypatch)
    shot = Image.new("RGB", (1600, 900), "white")
    shot.paste(qr_image("https://screen.example.com/", 5), (700, 300))
    b._last_screenshot = shot
    r = b.decode_qr_at_point(780, 380)
    assert r["ok"] and r["text"] == "https://screen.example.com/" and r["is_url"]
    r = b.decode_qr_in_region(650, 250, 300, 300)
    assert r["ok"] and r["results"][0]["bbox"][0] >= 700
    assert b.decode_qr_in_region(0, 0, 200, 200)["ok"] is False
    assert opened == []          # only qr_open_url (a user click) opens links
    assert b.qr_open_url("javascript:alert(1)")["ok"] is False
    assert b.qr_open_url("https://screen.example.com/")["ok"] is True
    assert opened == ["https://screen.example.com/"]
