"""Signature → PNG in '<root>\\Signature' (annotate_bridge)."""
import base64
import io
import os

import pytest
from PIL import Image

from annotate_bridge import AnnotateBridgeMixin


class B(AnnotateBridgeMixin):
    pass


def _png():
    im = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    im.putpixel((5, 5), (0, 0, 0, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def test_default_folder_is_drive_root(monkeypatch):
    monkeypatch.delenv("SFM_SIGNATURE_DIR", raising=False)
    assert B._signature_dir(r"E:\business docs\x") == r"E:\Signature"
    assert B._signature_dir(r"C:\Users\a\Desktop") == r"C:\Signature"
    assert B._signature_dir("").endswith(os.path.join("Documents", "Signature"))


def test_save_png_creates_folder_and_never_overwrites(tmp_path, monkeypatch):
    d = tmp_path / "Signature"
    monkeypatch.setenv("SFM_SIGNATURE_DIR", str(d))
    b = B()
    r1 = b.annot_signature_save_png(_png(), "Shafiq sign", r"E:\x")
    assert r1["ok"] and r1["path"] == str(d / "Shafiq sign.png") and os.path.isfile(r1["path"])
    r2 = b.annot_signature_save_png(_png(), "Shafiq sign.png", r"E:\x")
    assert r2["ok"] and r2["name"] == "Shafiq sign (2).png"
    with Image.open(r1["path"]) as im:
        assert im.mode == "RGBA"


@pytest.mark.parametrize("name", ["", "   ", "CON", "..."])
def test_save_png_rejects_bad_names(tmp_path, monkeypatch, name):
    monkeypatch.setenv("SFM_SIGNATURE_DIR", str(tmp_path / "S"))
    r = B().annot_signature_save_png(_png(), name)
    assert not r["ok"]


def test_save_png_cleans_name_and_rejects_non_png(tmp_path, monkeypatch):
    monkeypatch.setenv("SFM_SIGNATURE_DIR", str(tmp_path / "S"))
    r = B().annot_signature_save_png(_png(), 'a/b:c')
    assert r["ok"] and r["name"] == "a_b_c.png"
    assert not B().annot_signature_save_png("data:image/jpeg;base64,AAAA", "x")["ok"]
