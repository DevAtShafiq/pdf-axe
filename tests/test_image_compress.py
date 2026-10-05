"""Tests for file_ops.compress_image / compress_images / convert_image."""
import hashlib
import os
import random
import sys

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import file_ops as fo  # noqa: E402


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _noisy_rgb(w, h, seed=1):
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 3 + rnd.randint(0, 40)) % 256,
                        (y * 2 + rnd.randint(0, 40)) % 256,
                        ((x + y) + rnd.randint(0, 40)) % 256)
    return img


@pytest.fixture
def big_jpeg(tmp_path):
    p = tmp_path / "photo.jpg"
    _noisy_rgb(800, 600).save(p, quality=95, dpi=(300, 300))
    return str(p)


def test_jpeg_smaller_and_original_untouched(big_jpeg):
    h0 = _digest(big_jpeg)
    ok, res = fo.compress_image(big_jpeg, quality=50)
    assert ok, res
    assert res["out"].endswith("photo_compressed.jpg")
    assert res["after"] < res["before"]
    assert os.path.getsize(res["out"]) == res["after"]
    assert _digest(big_jpeg) == h0
    with Image.open(res["out"]) as im:
        assert im.format == "JPEG"
        assert im.size == (800, 600)
        assert round(im.info["dpi"][0]) == 300


def test_max_edge_respected(big_jpeg):
    ok, res = fo.compress_image(big_jpeg, quality=70, max_edge=400)
    assert ok, res
    with Image.open(res["out"]) as im:
        assert max(im.size) == 400
        assert im.size == (400, 300)


def test_max_edge_never_upscales(big_jpeg):
    ok, res = fo.compress_image(big_jpeg, max_edge=3000)
    assert ok, res
    with Image.open(res["out"]) as im:
        assert im.size == (800, 600)


def test_alpha_png_to_jpg(tmp_path):
    p = tmp_path / "logo.png"
    img = Image.new("RGBA", (200, 100), (255, 0, 0, 0))
    for x in range(50, 150):
        for y in range(25, 75):
            img.putpixel((x, y), (0, 0, 255, 255))
    img.save(p)
    ok, res = fo.compress_image(str(p), quality=80, fmt="jpg")
    assert ok, res
    assert res["out"].endswith("logo_compressed.jpg")
    with Image.open(res["out"]) as im:
        assert im.format == "JPEG"
        assert im.mode == "RGB"
        r, g, b = im.getpixel((5, 5))
        assert r > 240 and g > 240 and b > 240  # transparent → white


def test_png_quantized_when_low_quality(tmp_path):
    p = tmp_path / "shot.png"
    _noisy_rgb(300, 200).save(p)
    ok, res = fo.compress_image(str(p), quality=40)
    assert ok, res
    with Image.open(res["out"]) as im:
        assert im.format == "PNG"
        assert im.mode == "P"
    assert res["after"] < res["before"]


def test_webp_output(big_jpeg):
    ok, res = fo.compress_image(big_jpeg, quality=60, fmt="webp")
    assert ok, res
    assert res["out"].endswith("photo_compressed.webp")
    with Image.open(res["out"]) as im:
        assert im.format == "WEBP"


def test_exif_orientation_applied(tmp_path):
    p = tmp_path / "rot.jpg"
    img = _noisy_rgb(120, 60)
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display
    img.save(p, exif=exif.tobytes(), quality=90)
    ok, res = fo.compress_image(str(p))
    assert ok, res
    with Image.open(res["out"]) as im:
        assert im.size == (60, 120)


def test_repeated_runs_auto_number(big_jpeg):
    outs = [fo.compress_image(big_jpeg)[1]["out"] for _ in range(3)]
    names = [os.path.basename(o) for o in outs]
    assert names == ["photo_compressed.jpg", "photo_compressed-2.jpg", "photo_compressed-3.jpg"]


def test_refuses_to_overwrite_original(big_jpeg):
    ok, msg = fo.compress_image(big_jpeg, out_path=big_jpeg)
    assert not ok
    ok, msg = fo.convert_image(big_jpeg, "jpg", out_path=big_jpeg)
    assert not ok


def test_compress_images_batch(tmp_path, big_jpeg):
    missing = str(tmp_path / "nope.jpg")
    r = fo.compress_images([big_jpeg, missing], quality=50)
    assert len(r["results"]) == 2
    assert r["results"][0]["ok"] and not r["results"][1]["ok"]
    assert r["saved"] == r["before"] - r["after"] > 0


@pytest.mark.parametrize("fmt,pil_fmt,ext", [
    ("png", "PNG", ".png"), ("webp", "WEBP", ".webp"),
    ("bmp", "BMP", ".bmp"), ("tiff", "TIFF", ".tiff"), ("jpg", "JPEG", ".jpg"),
])
def test_convert_image_formats(big_jpeg, fmt, pil_fmt, ext):
    h0 = _digest(big_jpeg)
    ok, out = fo.convert_image(big_jpeg, fmt)
    assert ok, out
    assert out.endswith(ext)
    with Image.open(out) as im:
        assert im.format == pil_fmt
    assert _digest(big_jpeg) == h0


def test_convert_name_rules(tmp_path, big_jpeg):
    ok, out1 = fo.convert_image(big_jpeg, "png")
    assert os.path.basename(out1) == "photo.png"
    ok, out2 = fo.convert_image(big_jpeg, "png")
    assert os.path.basename(out2) == "photo-2.png"
    ok, out3 = fo.convert_image(big_jpeg, "jpg")  # same ext → numbered
    assert os.path.basename(out3) == "photo-2.jpg"


def test_convert_alpha_png_to_jpg(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(p)
    ok, out = fo.convert_image(str(p), "jpg")
    assert ok, out
    with Image.open(out) as im:
        assert im.mode == "RGB"
        assert im.getpixel((1, 1))[0] > 240


def test_bad_format(big_jpeg):
    assert not fo.compress_image(big_jpeg, fmt="gif")[0]
    assert not fo.convert_image(big_jpeg, "xyz")[0]


# ── media_convert.compress_image (used by the bridge): target size, keep-original ──

import media_convert as mc  # noqa: E402


def test_target_size_met(big_jpeg):
    target = 40 * 1024
    r = mc.compress_image(big_jpeg, target_bytes=target)
    assert r["target_met"] is True
    assert r["after"] <= target
    assert os.path.getsize(r["out"]) == r["after"]
    assert 10 <= r["quality"] <= 95


def test_target_size_shrinks_dimensions_when_needed(big_jpeg):
    r = mc.compress_image(big_jpeg, target_bytes=6 * 1024)
    assert r["after"] <= 6 * 1024 or r["target_met"] is False
    with Image.open(r["out"]) as im:
        assert max(im.size) < 800


def test_target_size_png_saved_as_jpg(tmp_path):
    p = tmp_path / "shot.png"
    _noisy_rgb(400, 300).save(p)
    r = mc.compress_image(str(p), target_bytes=30 * 1024)
    assert r["out"].endswith("shot_compressed.jpg")
    assert "JPG" in r["note"]


def test_already_small_jpeg_keeps_original(tmp_path):
    p = tmp_path / "small.jpg"
    _noisy_rgb(200, 150).save(p, quality=30)
    before = sorted(os.listdir(tmp_path))
    r = mc.compress_image(str(p), quality=90)
    assert r["kept_original"] and r["out"] == ""
    assert sorted(os.listdir(tmp_path)) == before


def test_mc_compress_never_overwrites(big_jpeg, tmp_path):
    (tmp_path / "photo_compressed.jpg").write_bytes(b"x")
    r = mc.compress_image(big_jpeg, quality=50)
    assert os.path.basename(r["out"]) == "photo_compressed-2.jpg"
    with pytest.raises(mc.ConvertError):
        mc.compress_image(big_jpeg, out_path=big_jpeg)


def test_bridge_compress_images_target_and_summary(big_jpeg, tmp_path):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    r = b.compress_images([big_jpeg, str(tmp_path / "missing.jpg")], 70, 0, "", 50)
    assert r["ok"] and r["failed"] == 1
    good = r["results"][0]
    assert good["ok"] and good["after"] <= 50 * 1024 and good["target_met"]
    assert r["saved"] == r["before"] - r["after"] > 0
    one = b.compress_image(big_jpeg, 60, 400, "webp")
    assert one["ok"] and one["out"].endswith(".webp") and one["size"] == [400, 300]


def test_target_png_note_explains_jpg(tmp_path):
    p = tmp_path / "scan.png"
    _noisy_rgb(500, 400).save(p)
    r = mc.compress_image(str(p), target_bytes=40 * 1024)
    assert r["target_met"] is True and r["out"].endswith(".jpg")
    assert "Saved as JPG to reach the target" in r["note"]
    assert r["format_changed"] is True


def test_target_already_under_keeps_original(tmp_path):
    p = tmp_path / "small.jpg"
    _noisy_rgb(120, 90).save(p, quality=60)
    before = sorted(os.listdir(tmp_path))
    r = mc.compress_image(str(p), target_bytes=500 * 1024)
    assert r["kept_original"] and r["target_met"] is True and r["out"] == ""
    assert "Already under 500 KB" in r["note"]
    assert sorted(os.listdir(tmp_path)) == before


def test_target_unreachable_asks_before_saving(big_jpeg, tmp_path):
    before = sorted(os.listdir(tmp_path))
    r = mc.compress_image(big_jpeg, target_bytes=300, save_smallest=False)
    assert r["target_met"] is False and r["kept_original"] and r["out"] == ""
    assert r["can_save_smallest"] and r["smallest"] > 300
    assert r["note"].startswith("Could not reach")
    assert sorted(os.listdir(tmp_path)) == before
    s = mc.compress_image(big_jpeg, target_bytes=300, save_smallest=True)
    assert s["target_met"] is False and s["out"] and s["after"] < s["before"]


def test_bridge_image_target_flags(big_jpeg):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    ok = b.compress_image(big_jpeg, 70, 0, "", "", 60)
    assert ok["ok"] and ok["target_met"] is True and ok["after"] <= 60 * 1024
    miss = b.compress_image(big_jpeg, 70, 0, "", "", 0.3, False)
    assert miss["ok"] and miss["target_met"] is False and not miss["out"]
