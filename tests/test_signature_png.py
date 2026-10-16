"""Signature → PNG in '<folder of the PDF>\\signature\\<name>_signature.png' (annotate_bridge)."""
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


def test_default_folder_is_next_to_the_pdf(monkeypatch):
    monkeypatch.delenv("SFM_SIGNATURE_DIR", raising=False)
    # a file → '<its folder>\signature' (also when it does not exist here)
    assert B._signature_dir(r"E:\business docs\x\form.pdf") == r"E:\business docs\x\signature"
    assert B._signature_dir(r"C:\Users\a\Desktop\scan.PNG") == r"C:\Users\a\Desktop\signature"
    assert B._signature_dir(r"E:\form.pdf") == r"E:\signature"
    # a folder → '<folder>\signature'
    assert B._signature_dir(r"E:\business docs\x") == r"E:\business docs\x\signature"
    assert B._signature_dir("E:\\business docs\\x\\") == r"E:\business docs\x\signature"
    assert B._signature_dir("").endswith(os.path.join("Documents", "signature"))


def test_real_file_and_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("SFM_SIGNATURE_DIR", raising=False)
    f = tmp_path / "noext"
    f.write_bytes(b"%PDF-1.4")
    assert B._signature_dir(str(f)) == os.path.join(str(tmp_path), "signature")
    sub = tmp_path / "dir.with.dot"
    sub.mkdir()
    assert B._signature_dir(str(sub)) == os.path.join(str(sub), "signature")


def test_save_png_next_to_pdf_with_suffix(tmp_path, monkeypatch):
    monkeypatch.delenv("SFM_SIGNATURE_DIR", raising=False)
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    r = B().annot_signature_save_png(_png(), "Shafiq", str(pdf))
    assert r["ok"], r
    assert r["folder"] == os.path.join(str(tmp_path), "signature")
    assert r["path"] == os.path.join(str(tmp_path), "signature", "Shafiq_signature.png")
    assert os.path.isfile(r["path"])


def test_signature_folder_reports_existence(tmp_path, monkeypatch):
    monkeypatch.delenv("SFM_SIGNATURE_DIR", raising=False)
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    r = B().annot_signature_folder(str(pdf))
    assert r["ok"] and r["folder"] == os.path.join(str(tmp_path), "signature") and r["exists"] is False


def test_save_png_creates_folder_and_never_overwrites(tmp_path, monkeypatch):
    d = tmp_path / "signature"
    monkeypatch.setenv("SFM_SIGNATURE_DIR", str(d))        # test override wins over the hint
    b = B()
    r1 = b.annot_signature_save_png(_png(), "Shafiq sign", r"E:\x\a.pdf")
    assert r1["ok"] and r1["path"] == str(d / "Shafiq sign_signature.png") and os.path.isfile(r1["path"])
    r2 = b.annot_signature_save_png(_png(), "Shafiq sign.png", r"E:\x\a.pdf")
    assert r2["ok"] and r2["name"] == "Shafiq sign_signature (2).png"
    # the suffix is never doubled
    r3 = b.annot_signature_save_png(_png(), "Shafiq sign_signature", r"E:\x\a.pdf")
    assert r3["ok"] and r3["name"] == "Shafiq sign_signature (3).png"
    r4 = b.annot_signature_save_png(_png(), "Other_SIGNATURE.png")
    assert r4["ok"] and r4["name"] == "Other_SIGNATURE.png"
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
    assert r["ok"] and r["name"] == "a_b_c_signature.png"
    assert not B().annot_signature_save_png("data:image/jpeg;base64,AAAA", "x")["ok"]
