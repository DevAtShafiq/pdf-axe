"""Tests for "Crop to photo size" (photo_size.py): presets, pixel size, DPI,
KB limit, naming, never overwriting, straighten, print sheet, user presets."""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import photo_size  # noqa: E402


@pytest.fixture(autouse=True)
def _no_user_file(tmp_path, monkeypatch):
    """Point the office's presets file at the temp dir (absent by default)."""
    monkeypatch.setattr(photo_size, "app_dir", lambda: str(tmp_path / "app"))
    (tmp_path / "app").mkdir()


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _noisy(path, size=(1200, 1600)):
    """Busy photo-like image (hard to compress) with a red square at the top left."""
    rnd = random.Random(1)
    im = Image.effect_noise(size, 60).convert("RGB")
    px = im.load()
    for _ in range(4000):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        px[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
    im.paste((255, 0, 0), (0, 0, 100, 100))
    im.save(path, quality=95)
    return path


def _p(key):
    return photo_size.find_preset(key)


EXPECTED = {
    "kr_passport": (413, 531), "kr_id": (354, 472), "kr_arc": (413, 531),
    "np_passport": (413, 531), "in_passport": (413, 531), "bd_passport": (413, 531),
    "pk_passport": (413, 531), "us_passport": (600, 600), "cn_visa": (390, 567),
    "jp_visa": (531, 531), "schengen": (413, 531), "uk": (413, 531),
    "ca": (591, 827), "au": (413, 531),
}


def test_builtin_preset_pixel_sizes_at_300_dpi():
    got = {p["key"]: (p["px_w"], p["px_h"]) for p in photo_size.presets()["presets"]}
    for k, v in EXPECTED.items():
        assert got[k] == v, k
    for p in photo_size.presets()["presets"]:
        assert p["dpi"] == 300
        g = p["guide"]
        assert g and 0 < g["crown"] < g["eye"] < g["chin"] < 1, p["key"]


@pytest.mark.parametrize("key", list(EXPECTED))
def test_output_size_and_dpi(tmp_path, key):
    src = _noisy(str(tmp_path / "student.jpg"))
    p = _p(key)
    r = photo_size.render_photo(src, {"preset": key, "rect": [100, 100, 700, 700 * p["px_h"] / p["px_w"]]})
    with Image.open(r["out"]) as im:
        assert im.format == "JPEG"
        assert im.size == EXPECTED[key]
        assert round(im.info["dpi"][0]) == 300 and round(im.info["dpi"][1]) == 300
    assert os.path.basename(r["out"]) == f"student_{p['tag']}.jpg"


def test_kb_limit_respected_and_size_kept(tmp_path):
    src = _noisy(str(tmp_path / "a.jpg"))
    full = photo_size.render_photo(src, {"preset": "kr_passport", "rect": [0, 0, 1200, 1542.86]})
    assert os.path.getsize(full["out"]) > 60 * 1024          # noise is big unlimited
    r = photo_size.render_photo(src, {"preset": "kr_passport", "rect": [0, 0, 1200, 1542.86], "max_kb": 60})
    assert r["target_met"] is True
    assert os.path.getsize(r["out"]) <= 60 * 1024
    with Image.open(r["out"]) as im:
        assert im.size == (413, 531)
        assert round(im.info["dpi"][0]) == 300


def test_kb_limit_unreachable_keeps_pixel_size(tmp_path):
    src = _noisy(str(tmp_path / "a.jpg"))
    r = photo_size.render_photo(src, {"preset": "ca", "rect": [0, 0, 1000, 1400], "max_kb": 1})
    assert r["target_met"] is False
    with Image.open(r["out"]) as im:
        assert im.size == (591, 827)


def test_never_overwrites_and_numbers(tmp_path):
    src = _noisy(str(tmp_path / "photo.png"))
    before = _sha(src)
    outs = [photo_size.render_photo(src, {"preset": "us_passport", "rect": [0, 0, 1000, 1000]})["out"]
            for _ in range(3)]
    names = [os.path.basename(o) for o in outs]
    assert names == ["photo_us_passport.jpg", "photo_us_passport (2).jpg", "photo_us_passport (3).jpg"]
    assert _sha(src) == before
    # an unrelated existing file with the target name is kept intact
    taken = tmp_path / "x_china_visa.jpg"
    src2 = _noisy(str(tmp_path / "x.jpg"))
    taken.write_bytes(b"keep me")
    r = photo_size.render_photo(src2, {"preset": "cn_visa", "rect": [0, 0, 330, 480]})
    assert taken.read_bytes() == b"keep me"
    assert os.path.basename(r["out"]) == "x_china_visa (2).jpg"


def test_crop_picks_the_right_area(tmp_path):
    src = _noisy(str(tmp_path / "a.jpg"))
    # 100×100 red square at the top left → whole output red
    r = photo_size.render_photo(src, {"preset": "custom", "unit": "px", "w": 50, "h": 50, "rect": [10, 10, 80, 80]})
    with Image.open(r["out"]) as im:
        assert im.size == (50, 50)
        c = im.convert("RGB").getpixel((25, 25))
        assert c[0] > 200 and c[1] < 60 and c[2] < 60


def test_rotate_90_and_straighten_fill_white(tmp_path):
    src = str(tmp_path / "w.jpg")
    Image.new("RGB", (400, 300), (40, 40, 40)).save(src)
    # rotated 90° → 300×400; frame at 10° is bigger: corners are white
    W, H = 300, 400
    Wf, Hf = photo_size.frame_size(W, H, 10)
    assert Wf > W and Hf > H
    r = photo_size.render_photo(src, {"preset": "free", "rotate": 90, "angle": 10, "rect": [0, 0, Wf, Hf]})
    with Image.open(r["out"]) as im:
        assert abs(im.width - round(Wf)) <= 1 and abs(im.height - round(Hf)) <= 1
        assert min(im.convert("RGB").getpixel((2, 2))) > 240              # white corner
        assert max(im.convert("RGB").getpixel((im.width // 2, im.height // 2))) < 80   # photo centre
    with pytest.raises(ValueError):
        photo_size.render_photo(src, {"preset": "free", "angle": 20})


def test_custom_mm_and_dpi(tmp_path):
    src = _noisy(str(tmp_path / "a.jpg"))
    r = photo_size.render_photo(src, {"preset": "custom", "unit": "mm", "w": 25, "h": 30, "dpi": 600,
                                      "rect": [0, 0, 500, 600]})
    with Image.open(r["out"]) as im:
        assert im.size == (591, 709)
        assert round(im.info["dpi"][0]) == 600
    assert os.path.basename(r["out"]) == "a_25x30mm.jpg"
    with pytest.raises(ValueError):
        photo_size.render_photo(src, {"preset": "custom", "w": 0, "h": 3})


def test_print_sheet_4x6(tmp_path):
    src = _noisy(str(tmp_path / "a.jpg"))
    r = photo_size.render_photo(src, {"preset": "kr_passport", "rect": [0, 0, 700, 900], "print_sheet": True})
    with Image.open(r["sheet"]) as im:
        assert im.size in ((1800, 1200), (1200, 1800))
        assert round(im.info["dpi"][0]) == 300
    assert os.path.basename(r["sheet"]) == "a_korea_passport_4x6.jpg"
    assert r["sheet_copies"] == 8                       # 4×2 of 35×45 mm on 6×4 in
    L = photo_size.sheet_layout(600, 600)
    assert L["count"] == 6 and L["gap"] == 0             # 2×2 in: six fill 6×4 edge to edge
    assert photo_size.sheet_layout(413, 531, copies=3)["count"] == 3
    with pytest.raises(ValueError):
        photo_size.sheet_layout(2000, 2000)


def test_user_presets_file_extends_and_overrides(tmp_path):
    path = photo_size.user_presets_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump([
            {"key": "my_visa", "name": "My visa", "w": 40, "h": 50, "unit": "mm", "tag": "my visa!"},
            {"key": "uk", "name": "UK (changed)", "w": 35, "h": 45, "unit": "mm", "max_kb": 300},
            {"key": "jp_visa", "hidden": True},
            {"key": "bad", "w": -1, "h": 2},
        ], f)
    ps = {p["key"]: p for p in photo_size.presets()["presets"]}
    assert ps["my_visa"]["px_w"] == 472 and ps["my_visa"]["tag"] == "my_visa"
    assert ps["uk"]["name"] == "UK (changed)" and ps["uk"]["max_kb"] == 300
    assert "jp_visa" not in ps and "bad" not in ps


def test_template_never_overwrites_existing_file():
    p = photo_size.write_presets_template()
    data = json.load(open(p, encoding="utf-8"))
    assert data and data[0]["key"] == "example_visa"
    with open(p, "w", encoding="utf-8") as f:
        f.write("[]")
    assert photo_size.write_presets_template() == p
    assert open(p, encoding="utf-8").read() == "[]"
