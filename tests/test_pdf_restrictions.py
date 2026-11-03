"""Copy/print restrictions survive compress, merge, split, extract and arrange."""
from __future__ import annotations

import io
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fitz = pytest.importorskip("pymupdf")

import media_convert as mc  # noqa: E402
import pdf_tools as pt  # noqa: E402

ALL = pt._PERM_ALL


def _pdf(path, pages=3, perms=None, image=False):
    d = fitz.open()
    for i in range(pages):
        pg = d.new_page()
        pg.insert_text((72, 72), f"Page {i + 1} text", fontsize=14)
        if image:
            from PIL import Image
            rnd = random.Random(i)
            im = Image.new("RGB", (400, 400)); im.putdata([(rnd.randrange(256),) * 3 for _ in range(160000)])
            b = io.BytesIO(); im.save(b, "PNG")
            pg.insert_image(fitz.Rect(72, 100, 500, 500), stream=b.getvalue())
    kw = {}
    if perms is not None:
        kw = dict(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="owner", permissions=perms)
    d.save(str(path), **kw)
    d.close()
    return str(path)


def _perms(path):
    d = fitz.open(path)
    try:
        assert not d.needs_pass                      # still opens without a password
        return pt.restrictions(d), "".join(p.get_text() for p in d)
    finally:
        d.close()


PRINT_ONLY = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ


def test_restrictions_detect(tmp_path):
    assert _perms(_pdf(tmp_path / "plain.pdf"))[0] is None
    assert _perms(_pdf(tmp_path / "r.pdf", perms=PRINT_ONLY))[0] == PRINT_ONLY
    # encrypted but everything allowed counts as unrestricted
    assert _perms(_pdf(tmp_path / "all.pdf", perms=ALL))[0] is None


def test_split_keeps_restriction(tmp_path):
    src = _pdf(tmp_path / "r.pdf", perms=PRINT_ONLY)
    r = pt.split(src, "each")
    assert r["count"] == 3
    for f in r["files"]:
        perms, text = _perms(f)
        assert perms == PRINT_ONLY and "text" in text


def test_extract_keeps_restriction(tmp_path):
    r = pt.extract(_pdf(tmp_path / "r.pdf", perms=PRINT_ONLY), "2")
    assert _perms(r["out_path"])[0] == PRINT_ONLY


def test_merge_takes_the_strictest(tmp_path):
    a = _pdf(tmp_path / "a.pdf", perms=PRINT_ONLY)
    b = _pdf(tmp_path / "b.pdf", perms=fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY)
    c = _pdf(tmp_path / "c.pdf")
    r = pt.merge([a, b, c])
    perms, text = _perms(r["out_path"])
    assert perms == fitz.PDF_PERM_PRINT and text.count("Page 1 text") == 3


def test_merge_unrestricted_stays_open(tmp_path):
    r = pt.merge([_pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")])
    assert _perms(r["out_path"])[0] is None


def test_arrange_keeps_restriction(tmp_path):
    src = _pdf(tmp_path / "r.pdf", perms=PRINT_ONLY)
    r = pt.build_pdf([{"path": src, "page": 2}, {"path": src, "page": 0, "rotate": 90}],
                     str(tmp_path / "out.pdf"))
    assert _perms(r["out_path"])[0] == PRINT_ONLY


def test_compress_keeps_restriction(tmp_path):
    src = _pdf(tmp_path / "big.pdf", pages=2, perms=PRINT_ONLY, image=True)
    r = mc.compress_pdf(src, "screen")
    assert not r["kept_original"]
    perms, text = _perms(r["out"])
    assert perms == PRINT_ONLY and "Page 1 text" in text
    assert os.path.getsize(r["out"]) < os.path.getsize(src)


def test_compress_target_keeps_restriction(tmp_path):
    src = _pdf(tmp_path / "big.pdf", pages=2, perms=PRINT_ONLY, image=True)
    r = mc.compress_pdf(src, target_kb=max(20, os.path.getsize(src) // 1024 // 3), save_smallest=True)
    assert r["out"] and _perms(r["out"])[0] == PRINT_ONLY
