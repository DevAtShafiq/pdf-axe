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


# ── target size ("Under 100 KB") ─────────────────────────────────────────────

@pytest.fixture
def photo_pdf(tmp_path, photo_bytes):
    """Three pages, each with a large embedded photo (well over 1 MB in total)."""
    p = tmp_path / "photos.pdf"
    doc = fitz.open()
    for i in range(3):
        pg = doc.new_page()
        pg.insert_text((50, 50), f"Receipt page {i + 1}", fontsize=14)
        pg.insert_image(fitz.Rect(50, 80, 550, 455), stream=photo_bytes)
    doc.save(str(p))
    doc.close()
    return str(p)


@pytest.fixture
def raw_text_pdf(tmp_path):
    """Text-only PDF saved without compression (lots of uncompressed text)."""
    p = tmp_path / "letter.pdf"
    doc = fitz.open()
    rnd = random.Random(7)
    words = ["apostille", "certificate", "passport", "student", "invoice", "bank"]
    for i in range(40):
        pg = doc.new_page()
        for line in range(50):
            txt = " ".join(rnd.choice(words) for _ in range(9))
            pg.insert_text((40, 40 + line * 15), f"{i}.{line} {txt}", fontsize=9)
    doc.save(str(p), deflate=False, garbage=0)
    doc.close()
    return str(p)


def test_target_100kb_reached_with_large_images(photo_pdf):
    h0 = _digest(photo_pdf)
    assert os.path.getsize(photo_pdf) > 1024 * 1024
    steps = []
    r = mc.compress_pdf(photo_pdf, target_kb=100,
                        progress=lambda d, t, label: steps.append(label))
    assert r["target_met"] is True and not r["kept_original"]
    assert r["after"] <= 100 * 1024
    assert os.path.getsize(r["out"]) == r["after"]
    assert r["preset"] == "target" and r["target_bytes"] == 100 * 1024
    assert r["settings"] and r["attempts"] >= 2
    assert steps                                   # progress per attempt
    assert _digest(photo_pdf) == h0                # original untouched
    with fitz.open(r["out"]) as d:
        assert d.page_count == 3
        assert "Receipt page 1" in d[0].get_text()   # text stays text


def test_target_prefers_highest_quality_that_fits(photo_pdf):
    small = mc.compress_pdf(photo_pdf, target_kb=100)
    large = mc.compress_pdf(photo_pdf, target_kb=600)
    assert small["target_met"] and large["target_met"]
    assert small["after"] < large["after"] <= 600 * 1024
    # a looser target must not fall back to the strongest setting
    assert large["settings"] != "50 dpi, quality 30"


def test_target_already_met_keeps_original(photo_pdf, tmp_path):
    before = sorted(os.listdir(tmp_path))
    r = mc.compress_pdf(photo_pdf, target_kb=10 * 1024)
    assert r["kept_original"] and r["target_met"] is True and r["out"] == ""
    assert "Already under 10 MB" in r["note"]
    assert sorted(os.listdir(tmp_path)) == before


def test_text_only_target_unreachable_writes_nothing(text_pdf, tmp_path):
    before_files = sorted(os.listdir(tmp_path))
    size = os.path.getsize(text_pdf)
    r = mc.compress_pdf(text_pdf, target_kb=0.1)
    assert r["target_met"] is False and r["kept_original"] and r["out"] == ""
    assert "Could not reach" in r["note"] and "text and vector content" in r["note"]
    assert r["smallest"] >= size and r["can_save_smallest"] is False
    # even when asked to save the smallest, nothing bigger is ever written
    r2 = mc.compress_pdf(text_pdf, target_kb=0.1, save_smallest=True)
    assert r2["kept_original"] and r2["out"] == ""
    assert sorted(os.listdir(tmp_path)) == before_files
    assert os.path.getsize(text_pdf) == size


def test_unreachable_then_save_smallest(raw_text_pdf, tmp_path):
    h0 = _digest(raw_text_pdf)
    before_files = sorted(os.listdir(tmp_path))
    r = mc.compress_pdf(raw_text_pdf, target_kb=1)
    assert r["target_met"] is False and r["out"] == ""
    assert r["can_save_smallest"] and r["smallest"] < r["before"]
    assert f"smallest possible is {mc.fmt_short(r['smallest'])}" in r["note"]
    assert sorted(os.listdir(tmp_path)) == before_files          # asked first
    s = mc.compress_pdf(raw_text_pdf, target_kb=1, save_smallest=True)
    assert not s["kept_original"] and s["target_met"] is False
    assert s["after"] == r["smallest"] < s["before"]
    assert os.path.basename(s["out"]) == "letter_compressed.pdf"
    assert _digest(raw_text_pdf) == h0


def test_target_never_overwrites(photo_pdf, tmp_path):
    taken = tmp_path / "photos_compressed.pdf"
    taken.write_bytes(b"keep")
    r = mc.compress_pdf(photo_pdf, target_kb=200)
    assert os.path.basename(r["out"]) == "photos_compressed-2.pdf"
    assert taken.read_bytes() == b"keep"
    with pytest.raises(mc.ConvertError):
        mc.compress_pdf(photo_pdf, out_path=photo_pdf, target_kb=200)


def test_fmt_short():
    assert mc.fmt_short(100 * 1024) == "100 KB"
    assert mc.fmt_short(340 * 1024 + 100) == "340 KB"
    assert mc.fmt_short(1024 * 1024) == "1 MB"
    assert mc.fmt_short(int(1.5 * 1024 * 1024)) == "1.5 MB"


def test_bridge_target_flags(photo_pdf, text_pdf):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    r = b.compress_pdf_quality(photo_pdf, "ebook", "", 100)
    assert r["ok"] and r["target_met"] is True and r["after"] <= 100 * 1024
    assert r["out_path"] == r["out"] and r["path"] == photo_pdf
    r = b.compress_pdf(text_pdf, "", "ebook", 0.1)
    assert r["ok"] and r["target_met"] is False and not r["out"]
    assert r["note"].startswith("Could not reach")
    # old positional signatures still work
    r = b.compress_pdf(photo_pdf, "", "screen")
    assert r["ok"] and r["target_met"] is None and r["out"]
    sizes = b.file_sizes([photo_pdf, photo_pdf + ".missing"])
    assert sizes["ok"] and sizes["sizes"] == {photo_pdf: os.path.getsize(photo_pdf)}


def test_bridge_async_job_emits_progress_and_done(photo_pdf, monkeypatch):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    events = []
    monkeypatch.setattr(b, "_emit", lambda ev, payload=None: events.append((ev, payload)))
    monkeypatch.setattr(b, "_thread", lambda fn, *a, **k: fn(*a, **k))
    r = b.compress_pdf_async(photo_pdf, "", "ebook", 100, "job-1")
    assert r["ok"] and r["job"] == "job-1"
    names = [e for e, _ in events]
    assert "media_progress" in names and names[-1] == "media_done"
    done = events[-1][1]
    assert done["job"] == "job-1" and done["ok"] and done["target_met"] is True
    assert all(p["job"] == "job-1" for e, p in events if e == "media_progress")
    # legacy call (no job) still answers with compress_done
    events.clear()
    b.compress_pdf_async(photo_pdf, "", "screen")
    assert [e for e, _ in events] == ["compress_done"]
