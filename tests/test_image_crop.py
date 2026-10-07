"""Tests for the full-resolution, EXIF-aware cropper (image_crop.py)."""
from __future__ import annotations

import hashlib
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import image_crop  # noqa: E402


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _quadrants(size=(400, 300)):
    """Red TL, green TR, blue BL, white BR."""
    w, h = size
    im = Image.new("RGB", size, "white")
    im.paste((255, 0, 0), (0, 0, w // 2, h // 2))
    im.paste((0, 255, 0), (w // 2, 0, w, h // 2))
    im.paste((0, 0, 255), (0, h // 2, w // 2, h))
    return im


def _close(c1, c2, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(c1, c2))


def test_crop_writes_new_file_and_never_overwrites(tmp_path):
    src = tmp_path / "photo.jpg"
    _quadrants().save(src, quality=95)
    before = _sha(src)
    ok, out1 = image_crop.crop_image(str(src), 0, 0, 100, 80)
    assert ok, out1
    assert os.path.basename(out1) == "photo_cropped.jpg"
    ok, out2 = image_crop.crop_image(str(src), 0, 0, 100, 80)
    assert os.path.basename(out2) == "photo_cropped-2.jpg"
    # explicit out_path pointing at the original is refused (numbered instead)
    ok, out3 = image_crop.crop_image(str(src), 0, 0, 50, 50, out_path=str(src))
    assert ok and os.path.normcase(out3) != os.path.normcase(str(src))
    assert _sha(src) == before
    with Image.open(out1) as im:
        assert im.size == (100, 80)
        assert _close(im.getpixel((50, 40)), (255, 0, 0))


def test_full_resolution_and_clamping(tmp_path):
    src = tmp_path / "big.png"
    _quadrants((4000, 3000)).save(src)
    info = image_crop.crop_source(str(src), 1600)
    assert (info["width"], info["height"]) == (4000, 3000)
    assert max(info["preview_w"], info["preview_h"]) == 1600
    ok, out = image_crop.crop_image(str(src), 3500, 2500, 1000, 1000)   # runs off the edge
    assert ok
    with Image.open(out) as im:
        assert im.size == (500, 500)
        assert _close(im.getpixel((10, 10)), (255, 255, 255))


def test_exif_orientation_respected(tmp_path):
    # Stored landscape, EXIF 6 = display rotated 90° clockwise (portrait)
    stored = _quadrants((400, 300))
    exif = Image.Exif()
    exif[0x0112] = 6
    src = tmp_path / "phone.jpg"
    stored.save(src, quality=95, exif=exif.tobytes())
    info = image_crop.crop_source(str(src))
    assert (info["width"], info["height"]) == (300, 400)
    # displayed top-left quadrant is the stored bottom-left (blue)
    ok, out = image_crop.crop_image(str(src), 0, 0, 150, 200)
    assert ok
    with Image.open(out) as im:
        assert im.size == (150, 200)
        assert _close(im.getpixel((75, 100)), (0, 0, 255))
        assert im.getexif().get(0x0112, 1) == 1


def test_rotate_in_dialog(tmp_path):
    src = tmp_path / "q.png"
    _quadrants((400, 300)).save(src)
    # rotate 90° clockwise → 300x400; top-left becomes former bottom-left (blue)
    ok, out = image_crop.crop_image(str(src), 0, 0, 150, 200, rotate=90)
    assert ok
    with Image.open(out) as im:
        assert im.size == (150, 200)
        assert _close(im.getpixel((75, 100)), (0, 0, 255))
    ok, msg = image_crop.crop_image(str(src), 0, 0, 10, 10, rotate=45)
    assert not ok


def test_png_alpha_kept_and_empty_rejected(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGBA", (50, 50), (10, 20, 30, 0)).save(src)
    ok, out = image_crop.crop_image(str(src), 5, 5, 20, 20)
    assert ok
    with Image.open(out) as im:
        assert im.mode == "RGBA" and im.getpixel((1, 1))[3] == 0
    ok, msg = image_crop.crop_image(str(src), 60, 60, 10, 10)
    assert not ok and "empty" in msg.lower()


def test_bridge_crop(tmp_path):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    src = tmp_path / "p.jpg"
    _quadrants().save(src)
    r = b.get_crop_source(str(src), 200)
    assert r["ok"] and r["width"] == 400 and r["data_url"].startswith("data:image/")
    r = b.crop_image(str(src), 200, 0, 200, 150, "", 0)
    assert r["ok"] and r["out"].endswith("p_cropped.jpg")
    assert b.crop_image(str(tmp_path / "x.jpg"), 0, 0, 1, 1)["ok"] is False
