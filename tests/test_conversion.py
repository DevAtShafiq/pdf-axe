"""Tests for media_convert: image -> PDF, PDF -> images, image format conversion,
plus the sfm_bridge wrappers around them."""
import hashlib
import io
import os
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


def _img(path, size=(400, 300), color=(200, 30, 30), mode="RGB", **save):
    Image.new(mode, size, color).save(path, **save)
    return str(path)


def _pdf(path, pages=3, text="Page"):
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=300, height=400)
        pg.insert_text((40, 60), f"{text} {i + 1}", fontsize=20)
    doc.save(str(path))
    doc.close()
    return str(path)


# ── image -> PDF ─────────────────────────────────────────────────────────────

def test_single_image_fit_page(tmp_path):
    src = _img(tmp_path / "scan.jpg", (400, 300), dpi=(100, 100))
    h0 = _digest(src)
    r = mc.images_to_pdf([src])
    assert os.path.basename(r["out"]) == "scan.pdf"
    assert r["pages"] == 1
    with fitz.open(r["out"]) as d:
        rect = d[0].rect
        assert rect.width == pytest.approx(400 * 72 / 100, abs=0.5)
        assert rect.height == pytest.approx(300 * 72 / 100, abs=0.5)
    assert _digest(src) == h0


def test_never_overwrites_existing_pdf(tmp_path):
    src = _img(tmp_path / "scan.png")
    existing = tmp_path / "scan.pdf"
    existing.write_bytes(b"keep me")
    r = mc.images_to_pdf([src])
    assert os.path.basename(r["out"]) == "scan-2.pdf"
    assert existing.read_bytes() == b"keep me"


def test_multi_images_selection_order_and_name_order(tmp_path):
    a = _img(tmp_path / "img10.png", (100, 200))
    b = _img(tmp_path / "img2.png", (300, 100))
    r = mc.images_to_pdf([a, b], page_size="fit")
    assert os.path.basename(r["out"]) == "img10_combined.pdf"
    with fitz.open(r["out"]) as d:
        assert d.page_count == 2
        assert d[0].rect.height > d[0].rect.width   # img10 first (selection)
    r2 = mc.images_to_pdf([a, b], order="name")
    with fitz.open(r2["out"]) as d:
        assert d[0].rect.width > d[0].rect.height   # img2 first (natural sort)


def test_a4_auto_orientation_and_margin(tmp_path):
    wide = _img(tmp_path / "wide.png", (800, 400))
    tall = _img(tmp_path / "tall.png", (400, 800))
    r = mc.images_to_pdf([wide, tall], page_size="a4", margin_mm=10)
    with fitz.open(r["out"]) as d:
        p0, p1 = d[0].rect, d[1].rect
        assert (round(p0.width), round(p0.height)) == (842, 595)   # landscape
        assert (round(p1.width), round(p1.height)) == (595, 842)   # portrait
        info = d[1].get_image_info()[0]
        x0, y0, x1, y1 = info["bbox"]
        m = 10 * 72 / 25.4
        assert y0 >= m - 0.5 and y1 <= p1.height - m + 0.5
        assert x0 >= m - 0.5 and x1 <= p1.width - m + 0.5


def test_letter_forced_portrait(tmp_path):
    wide = _img(tmp_path / "wide.png", (800, 400))
    r = mc.images_to_pdf([wide], page_size="letter", orientation="portrait")
    with fitz.open(r["out"]) as d:
        assert (round(d[0].rect.width), round(d[0].rect.height)) == (612, 792)


def test_transparency_flattened_on_white(tmp_path):
    src = _img(tmp_path / "logo.png", (60, 60), (0, 0, 0, 0), mode="RGBA")
    r = mc.images_to_pdf([src])
    with fitz.open(r["out"]) as d:
        pix = d[0].get_pixmap(dpi=72)
        assert pix.pixel(pix.width // 2, pix.height // 2)[:3] == (255, 255, 255)


def test_exif_orientation_respected(tmp_path):
    p = tmp_path / "rot.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6   # display rotated 90° CW
    Image.new("RGB", (200, 100), (10, 120, 10)).save(p, exif=exif.tobytes())
    r = mc.images_to_pdf([str(p)])
    with fitz.open(r["out"]) as d:
        assert d[0].rect.height > d[0].rect.width


def test_multiframe_tiff_all_pages(tmp_path):
    p = tmp_path / "multi.tiff"
    frames = [Image.new("RGB", (100, 100), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(p, save_all=True, append_images=frames[1:])
    r = mc.images_to_pdf([str(p)])
    assert r["pages"] == 3


def test_original_quality_embeds_jpeg_untouched(tmp_path):
    src = _img(tmp_path / "photo.jpg", (300, 200), quality=90)
    r = mc.images_to_pdf([src], quality=100)
    with fitz.open(r["out"]) as d:
        xref = d[0].get_images()[0][0]
        assert d.extract_image(xref)["image"] == open(src, "rb").read()


def test_bad_inputs_skipped_or_error(tmp_path):
    good = _img(tmp_path / "ok.png")
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    r = mc.images_to_pdf([good, str(txt), str(broken)])
    assert r["pages"] == 1
    assert len(r["skipped"]) == 2
    with pytest.raises(mc.ConvertError):
        mc.images_to_pdf([str(broken)])
    with pytest.raises(mc.ConvertError):
        mc.images_to_pdf([])


def test_heic_without_plugin_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_heic_state", False)
    p = tmp_path / "phone.heic"
    p.write_bytes(b"\x00" * 64)
    with pytest.raises(mc.ConvertError, match="pillow-heif"):
        mc.images_to_pdf([str(p)])


# ── PDF -> images ────────────────────────────────────────────────────────────

def test_pdf_to_images_folder_and_range(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", pages=5)
    r = mc.pdf_to_images(pdf, "png", 72, "1-2,5")
    assert os.path.basename(r["out_dir"]) == "doc_images"
    names = [os.path.basename(f) for f in r["files"]]
    assert names == ["doc_p01.png", "doc_p02.png", "doc_p05.png"]
    with Image.open(r["files"][0]) as im:
        assert im.size == (300, 400)   # 72 dpi = 1px per point
    # second run never overwrites: new folder
    r2 = mc.pdf_to_images(pdf, "jpg", 150, "")
    assert os.path.basename(r2["out_dir"]) == "doc_images-2"
    assert len(r2["files"]) == 5
    with Image.open(r2["files"][0]) as im:
        assert im.format == "JPEG"
        assert im.size[0] == 625 and abs(im.size[1] - 833) <= 1


def test_pdf_to_images_webp_and_progress(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", pages=2)
    seen = []
    r = mc.pdf_to_images(pdf, "webp", 72, progress=lambda d, t, l: seen.append((d, t)))
    with Image.open(r["files"][1]) as im:
        assert im.format == "WEBP"
    assert seen[-1] == (2, 2)


@pytest.mark.parametrize("spec", ["0", "9", "abc", "3-x"])
def test_pdf_to_images_bad_range(tmp_path, spec):
    pdf = _pdf(tmp_path / "doc.pdf", pages=3)
    with pytest.raises(mc.ConvertError):
        mc.pdf_to_images(pdf, "png", 72, spec)


def test_pdf_to_images_bad_format(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", pages=1)
    with pytest.raises(mc.ConvertError):
        mc.pdf_to_images(pdf, "gif", 72)


# ── image format conversion ──────────────────────────────────────────────────

@pytest.mark.parametrize("fmt,pil_fmt,ext", [
    ("jpg", "JPEG", ".jpg"), ("png", "PNG", ".png"), ("webp", "WEBP", ".webp"),
    ("bmp", "BMP", ".bmp"), ("tiff", "TIFF", ".tiff"),
])
def test_convert_image_formats(tmp_path, fmt, pil_fmt, ext):
    src = _img(tmp_path / ("pic.png" if fmt == "webp" else "pic.webp"), (50, 40))
    h0 = _digest(src)
    r = mc.convert_image(src, fmt)
    assert r["out"].endswith(ext)
    with Image.open(r["out"]) as im:
        assert im.format == pil_fmt
    assert _digest(src) == h0


def test_convert_alpha_to_jpg_white_and_no_overwrite(tmp_path):
    src = _img(tmp_path / "a.png", (40, 40), (0, 0, 0, 0), mode="RGBA")
    (tmp_path / "a.jpg").write_bytes(b"existing")
    r = mc.convert_image(src, "jpg", quality=80)
    assert os.path.basename(r["out"]) == "a-2.jpg"
    assert (tmp_path / "a.jpg").read_bytes() == b"existing"
    with Image.open(r["out"]) as im:
        assert im.mode == "RGB" and im.getpixel((5, 5))[0] > 240
    with pytest.raises(mc.ConvertError):
        mc.convert_image(src, "png", out_path=src)
    with pytest.raises(mc.ConvertError):
        mc.convert_image(src, "xyz")


# ── bridge wrappers ──────────────────────────────────────────────────────────

@pytest.fixture
def bridge():
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    b.events = []
    b._emit = lambda ev, payload=None: b.events.append((ev, payload))
    b._thread = lambda fn, *a, **k: fn(*a, **k)   # run "async" work inline
    return b


def test_bridge_convert_to_pdf(bridge, tmp_path):
    src = _img(tmp_path / "x.png")
    r = bridge.convert_to_pdf(src)
    assert r["ok"] and os.path.basename(r["out_path"]) == "x.pdf"
    r = bridge.convert_to_pdf(str(tmp_path / "x.pdf"))
    assert not r["ok"] and "Only images" in r["error"]


def test_bridge_images_to_pdf_async_combine_and_separate(bridge, tmp_path):
    a = _img(tmp_path / "a.png")
    b = _img(tmp_path / "b.png")
    bridge.images_to_pdf_async([a, b], {"page_size": "a4", "margin_mm": 5}, "j1")
    ev, done = bridge.events[-1]
    assert ev == "media_done" and done["ok"] and done["job"] == "j1"
    assert done["pages"] == 2 and done["out_path"].endswith("a_combined.pdf")
    assert any(e == "media_progress" for e, _ in bridge.events)
    bridge.images_to_pdf_async([a, b], {"mode": "separate"}, "j2")
    ev, done = bridge.events[-1]
    assert done["ok"] and [os.path.basename(f) for f in done["files"]] == ["a.pdf", "b.pdf"]


def test_bridge_pdf_to_images(bridge, tmp_path):
    pdf = _pdf(tmp_path / "d.pdf", pages=3)
    r = bridge.pdf_to_images(pdf, 72, "jpg", "2")
    assert r["ok"] and len(r["files"]) == 1
    bridge.pdf_to_images_async(pdf, {"fmt": "png", "dpi": 72, "pages": "1-3"}, "k")
    ev, done = bridge.events[-1]
    assert ev == "media_done" and done["ok"] and len(done["files"]) == 3
    bridge.pdf_to_images_async(pdf, {"pages": "7"}, "k2")
    ev, done = bridge.events[-1]
    assert not done["ok"] and "out of range" in done["error"]


def test_bridge_convert_image(bridge, tmp_path):
    src = _img(tmp_path / "p.png")
    r = bridge.convert_image(src, "webp", "", 80)
    assert r["ok"] and r["out"].endswith("p.webp")
    assert not bridge.convert_image(src, "nope")["ok"]
