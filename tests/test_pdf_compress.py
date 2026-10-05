"""Tests for media_convert.compress_pdf and the bridge's PDF compression methods."""
import hashlib
import io
import os
import random
import sys

import pytest

pytest.importorskip("PIL")
fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import media_convert as mc  # noqa: E402


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _noisy_jpeg(w, h, seed=1, quality=95):
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 3 + rnd.randint(0, 40)) % 256,
                        (y * 2 + rnd.randint(0, 40)) % 256,
                        ((x + y) + rnd.randint(0, 40)) % 256)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


@pytest.fixture(scope="module")
def photo_bytes():
    return _noisy_jpeg(1600, 1200)


@pytest.fixture
def scan_pdf(tmp_path, photo_bytes):
    """Two pages, each a 1600px photo squeezed into ~4 inches (≈400 dpi) + text."""
    p = tmp_path / "scan.pdf"
    doc = fitz.open()
    for i in range(2):
        pg = doc.new_page()
        pg.insert_text((50, 50), f"Invoice text page {i + 1}", fontsize=14)
        pg.insert_image(fitz.Rect(50, 80, 338, 296), stream=photo_bytes)
    doc.save(str(p))
    doc.close()
    return str(p)


@pytest.fixture
def text_pdf(tmp_path):
    """A text-only PDF that has already been through the compressor."""
    raw = tmp_path / "raw" / "text.pdf"
    raw.parent.mkdir()
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "tiny", fontsize=12)
    doc.save(str(raw))
    doc.close()
    r = mc.compress_pdf(str(raw), "lossless", str(tmp_path / "text.pdf"))
    assert not r["kept_original"]
    return r["out"]


@pytest.mark.parametrize("preset", ["screen", "ebook", "printer"])
def test_presets_shrink_and_keep_text(scan_pdf, preset):
    h0 = _digest(scan_pdf)
    r = mc.compress_pdf(scan_pdf, preset)
    assert not r["kept_original"]
    assert os.path.basename(r["out"]).startswith("scan_compressed")
    assert r["after"] < r["before"]
    assert r["after"] == os.path.getsize(r["out"])
    assert r["reduction"] > 0
    assert _digest(scan_pdf) == h0
    with fitz.open(r["out"]) as d:
        assert d.page_count == 2
        assert "Invoice text page 1" in d[0].get_text()


def test_presets_are_ordered_by_size(scan_pdf):
    sizes = [mc.compress_pdf(scan_pdf, p)["after"] for p in ("screen", "ebook", "printer")]
    assert sizes[0] < sizes[1] < sizes[2]


def test_screen_downsamples_images(scan_pdf):
    r = mc.compress_pdf(scan_pdf, "screen")
    with fitz.open(r["out"]) as d:
        info = d[0].get_image_info()[0]
        eff_dpi = info["width"] / ((info["bbox"][2] - info["bbox"][0]) / 72)
        assert eff_dpi <= 110


def test_aliases(scan_pdf):
    assert mc.compress_pdf(scan_pdf, "low")["preset"] == "screen"
    assert mc.compress_pdf(scan_pdf, "medium")["preset"] == "ebook"
    assert mc.compress_pdf(scan_pdf, "prepress")["preset"] == "lossless"
    with pytest.raises(mc.ConvertError):
        mc.compress_pdf(scan_pdf, "ultra")


def test_never_overwrites(scan_pdf, tmp_path):
    taken = tmp_path / "scan_compressed.pdf"
    taken.write_bytes(b"keep")
    r = mc.compress_pdf(scan_pdf, "ebook")
    assert os.path.basename(r["out"]) == "scan_compressed-2.pdf"
    assert taken.read_bytes() == b"keep"
    with pytest.raises(mc.ConvertError):
        mc.compress_pdf(scan_pdf, "ebook", out_path=scan_pdf)


def test_already_small_keeps_original(text_pdf, tmp_path):
    before = sorted(os.listdir(tmp_path))
    r = mc.compress_pdf(text_pdf, "screen")
    assert r["kept_original"] and r["out"] == ""
    assert r["after"] == r["before"]
    assert r["note"]
    assert sorted(os.listdir(tmp_path)) == before   # nothing written


def test_errors(tmp_path):
    with pytest.raises(mc.ConvertError):
        mc.compress_pdf(str(tmp_path / "missing.pdf"))
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-garbage")
    with pytest.raises(mc.ConvertError):
        mc.compress_pdf(str(bad))


def test_fallback_without_rewrite_images(scan_pdf, monkeypatch):
    """Older PyMuPDF without Document.rewrite_images still downsamples."""
    if hasattr(fitz.Document, "rewrite_images"):
        monkeypatch.delattr(fitz.Document, "rewrite_images")
    r = mc.compress_pdf(scan_pdf, "screen")
    assert not r["kept_original"] and r["after"] < r["before"]


# ── bridge ───────────────────────────────────────────────────────────────────

def test_bridge_compress_pdf_quality(scan_pdf, text_pdf):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    r = b.compress_pdf_quality(scan_pdf, "ebook", "")
    assert r["ok"] and r["out"] and r["reduction"] > 0
    assert r["before_str"] and r["after_str"]
    r = b.compress_pdf(text_pdf, "", "screen")
    assert r["ok"] and r["kept_original"] and not r["out"]
    r = b.compress_pdf_quality(scan_pdf + ".missing", "ebook")
    assert not r["ok"] and r["error"]
