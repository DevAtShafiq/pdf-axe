"""Tests for pdf_edit.py — real text editing of text-based PDFs."""
from __future__ import annotations

import base64
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fitz = pytest.importorskip("pymupdf")
from PIL import Image  # noqa: E402

import pdf_edit as pe  # noqa: E402

FONTS = pe.FONT_DIR
ARIAL = os.path.join(FONTS, "arial.ttf")
ARIALBD = os.path.join(FONTS, "arialbd.ttf")
MALGUN = os.path.join(FONTS, "malgun.ttf")
HAVE_ARIAL = os.path.isfile(ARIAL) and os.path.isfile(ARIALBD)


def _png(color=(200, 30, 30), size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _cert(path, rotation=0, base14=False):
    """Certificate-like PDF: border (line art), logo image, text blocks, a form field."""
    """Rotated pages hold the same layout, drawn so it reads upright on screen
    (like a scanned-then-rotated or landscape form); all positions below are
    display coordinates of a 612 x 792 view."""
    doc = fitz.open()
    if rotation in (90, 270):
        page = doc.new_page(width=792, height=612)
    else:
        page = doc.new_page(width=612, height=792)
    page.set_rotation(rotation)
    dm = page.derotation_matrix
    R = lambda *r: (fitz.Rect(*r) * dm).normalize()
    P = lambda x, y: fitz.Point(x, y) * dm
    page.draw_rect(R(20, 20, 592, 772), color=(0.1, 0.2, 0.6), width=3)
    page.draw_line(P(150, 330), P(462, 330), color=(0, 0, 0), width=1)
    page.insert_image(R(276, 40, 336, 80), stream=_png(), rotate=rotation)
    if base14 or not HAVE_ARIAL:
        reg = dict(fontname="helv")
        bold = dict(fontname="hebo")
    else:
        page.insert_font(fontname="F1", fontfile=ARIAL)
        page.insert_font(fontname="F2", fontfile=ARIALBD)
        reg = dict(fontname="F1")
        bold = dict(fontname="F2")
    put = lambda x, y, t, **kw: page.insert_text(P(x, y), t, rotate=rotation, **kw)
    put(180, 150, "CERTIFICATE OF COMPLETION", fontsize=22, **bold)
    put(230, 250, "This is to certify that", fontsize=14, **reg)
    put(240, 320, "John Smith", fontsize=28, color=(0.2, 0.1, 0.5), **bold)
    put(72, 420, "has successfully completed the course on document", fontsize=12, **reg)
    put(72, 436, "management and records keeping with distinction.", fontsize=12, **reg)
    put(72, 700, "Date: 2026-10-08", fontsize=11, **reg)
    w = fitz.Widget()
    w.field_name = "signature_name"
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.rect = R(350, 690, 550, 710)
    w.field_value = "Signed"
    page.add_widget(w)
    doc.save(path)
    doc.close()
    return path


def _block(path, text, page=0):
    info = pe.extract_blocks(path, page)
    for b in info["blocks"]:
        if text in b["text"]:
            return b, info
    raise AssertionError(f"{text!r} not in {[b['text'] for b in info['blocks']]}")


def _page_text(path, page=0):
    # the fixture's own text (inserted by MuPDF) copies out spaces as U+00A0
    with fitz.open(path) as d:
        return d[page].get_text("text").replace(" ", " ").replace("­", "-")


def test_extract_blocks(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    info = pe.extract_blocks(p, 0)
    texts = [b["text"] for b in info["blocks"]]
    assert "John Smith" in texts
    name = next(b for b in info["blocks"] if b["text"] == "John Smith")
    assert name["size"] == pytest.approx(28, abs=0.2)
    assert name["bold"] is True
    assert name["align"] == "center" or name["align"] == "left"
    assert name["color"] == "#331a80"
    para = next(b for b in info["blocks"] if "successfully" in b["text"])
    assert len(para["lines"]) == 2
    assert para["line_height"] == pytest.approx(16, abs=0.5)
    assert len(info["images"]) == 1 and info["images"][0]["xref"] > 0
    assert info["images"][0]["bbox"] == pytest.approx([276, 40, 336, 80], abs=0.5)


def test_replace_name_keeps_everything_else(tmp_path):
    p = _cert(str(tmp_path / "cert.pdf"))
    b, _ = _block(p, "John Smith")
    res = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"], "new_text": "Maria Garcia-Lopez"}])
    out = res["out_path"]
    assert os.path.basename(out) == "cert_edited.pdf"
    assert not res["replaced"]
    txt = _page_text(out)
    assert "Maria Garcia-Lopez" in txt
    assert "John Smith" not in txt
    # everything else is still there
    for keep in ("CERTIFICATE OF COMPLETION", "This is to certify that", "Date: 2026-10-08",
                 "management and records keeping"):
        assert keep in txt
    with fitz.open(out) as d:
        pg = d[0]
        assert len(pg.get_images()) == 1                       # image kept
        draws = pg.get_drawings()
        assert any(abs(dr["rect"].width - 572) < 4 for dr in draws)   # border kept
        assert any(abs(dr["rect"].y0 - 330) < 2 and dr["rect"].width > 300 for dr in draws)  # line kept
        ws = list(pg.widgets())
        assert len(ws) == 1 and ws[0].field_name == "signature_name" and ws[0].field_value == "Signed"
        # new text is selectable / searchable, at the old baseline, same size and colour
        hits = pg.search_for("Maria Garcia-Lopez")
        assert hits
        assert "Maria Garcia-Lopez" in pg.get_text("text")      # copies out with plain space/hyphen
        spans = [s for bl in pg.get_text("dict")["blocks"] if bl["type"] == 0
                 for ln in bl["lines"] for s in ln["spans"] if "Maria" in s["text"]]
        assert spans
        assert spans[0]["size"] == pytest.approx(28, abs=0.6) or spans[0]["size"] < 28
        assert spans[0]["origin"][1] == pytest.approx(320, abs=0.6)
        assert spans[0]["color"] == 0x331a80
        assert d.metadata["producer"] == "Office Axe"
    # the original is untouched
    assert "John Smith" in _page_text(p)


def test_delete_block(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "Date:")
    out = pe.apply_edits(p, [{"type": "delete", "page": 0, "block_id": b["id"]}])["out_path"]
    txt = _page_text(out)
    assert "Date:" not in txt
    assert "John Smith" in txt and "CERTIFICATE" in txt


def test_add_text(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    res = pe.apply_edits(p, [{"type": "add_text", "page": 0, "rect": [72, 500, 400, 540],
                              "text": "Awarded with honours", "font": "Arial", "size": 16,
                              "color": "#ff0000"}])
    with fitz.open(res["out_path"]) as d:
        hits = d[0].search_for("Awarded with honours")
        assert hits and 500 <= hits[0].y0 <= 520
    assert "John Smith" in _page_text(res["out_path"])


def test_move_block(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "Date:")
    x0, y0, x1, y1 = b["bbox"]
    out = pe.apply_edits(p, [{"type": "move", "page": 0, "block_id": b["id"],
                              "bbox": [x0 + 100, y0 - 50, x1 + 100, y1 - 50]}])["out_path"]
    with fitz.open(out) as d:
        hits = d[0].search_for("Date: 2026-10-08")
        assert len(hits) == 1
        assert hits[0].x0 == pytest.approx(x0 + 100, abs=1.5)
        assert hits[0].y0 == pytest.approx(y0 - 50, abs=2.5)


def test_multiline_wrap(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "successfully")
    new = ("has successfully completed the advanced course on document management, "
           "records keeping and digital archiving with distinction.")
    out = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"], "new_text": new}])["out_path"]
    with fitz.open(out) as d:
        words = d[0].get_text("words")
        right = max(w[2] for w in words if 400 < w[1] < 480)
        assert right <= b["bbox"][2] + 2           # wrapped inside the block width
        assert "archiving" in d[0].get_text("text")


def test_replace_image(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    info = pe.extract_blocks(p, 0)
    im = info["images"][0]
    du = "data:image/png;base64," + base64.b64encode(_png((10, 200, 10), (40, 40))).decode()
    out = pe.apply_edits(p, [{"type": "replace_image", "page": 0, "image_id": im["id"], "data_url": du}])["out_path"]
    with fitz.open(out) as d:
        infos = d[0].get_image_info(xrefs=True)
        assert len(infos) == 1
        assert list(infos[0]["bbox"]) == pytest.approx(im["bbox"], abs=0.6)
        pix = fitz.Pixmap(d, infos[0]["xref"])
        assert pix.width / pix.height == pytest.approx(60 / 40, rel=0.05)   # padded to keep aspect
    assert "John Smith" in _page_text(out)


def test_delete_image(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    im = pe.extract_blocks(p, 0)["images"][0]
    out = pe.apply_edits(p, [{"type": "delete_image", "page": 0, "image_id": im["id"]}])["out_path"]
    with fitz.open(out) as d:
        pix = d[0].get_pixmap(clip=fitz.Rect(*im["bbox"]))
        # the red logo is gone (page is white there)
        r, g, b = pix.pixel(pix.width // 2, pix.height // 2)[:3]
        assert r > 240 and g > 240 and b > 240


@pytest.mark.skipif(not os.path.isfile(MALGUN), reason="Malgun Gothic not installed")
def test_korean_line(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "John Smith")
    res = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"], "new_text": "김민준 Kim"}])
    with fitz.open(res["out_path"]) as d:
        assert "김민준 Kim" in d[0].get_text("text")
    assert any("Malgun" in s["used"] for s in res["substituted"])


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_rotated_page(tmp_path, rot):
    p = _cert(str(tmp_path / f"r{rot}.pdf"), rotation=rot)
    b, info = _block(p, "John Smith")
    assert info["rotation"] == rot
    assert b["editable"]
    out = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"], "new_text": "Jane Doe"}])["out_path"]
    b2, _ = _block(out, "Jane Doe")
    assert "John Smith" not in _page_text(out)
    # written upright, at the same place in display coordinates
    assert b2["editable"]
    assert b2["lines"][0]["origin"][1] == pytest.approx(b["lines"][0]["origin"][1], abs=1.0)
    assert abs((b2["bbox"][0] + b2["bbox"][2]) / 2 - (b["bbox"][0] + b["bbox"][2]) / 2) < 3


def test_scanned_page(tmp_path):
    p = str(tmp_path / "scan.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=300, height=400)
    pg.insert_image(pg.rect, stream=_png((240, 240, 230), (300, 400)))
    doc.save(p)
    doc.close()
    with pytest.raises(pe.PdfEditError) as ei:
        pe.extract_blocks(p, 0)
    assert ei.value.code == "scanned_page"
    assert pe.doc_info(p)["pages"][0]["scanned"] is True


def test_never_overwrites(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "Date:")
    e = [{"type": "delete", "page": 0, "block_id": b["id"]}]
    o1 = pe.apply_edits(p, e)["out_path"]
    o2 = pe.apply_edits(p, e)["out_path"]
    assert os.path.basename(o1) == "c_edited.pdf"
    assert os.path.basename(o2) == "c_edited (2).pdf"
    # editing an _edited file does not produce _edited_edited
    b3, _ = _block(o1, "John Smith")
    o3 = pe.apply_edits(o1, [{"type": "delete", "page": 0, "block_id": b3["id"]}])["out_path"]
    assert os.path.basename(o3) == "c_edited (3).pdf"


def test_replace_true_backup(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "John Smith")
    res = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"], "new_text": "Ann Lee"}],
                         replace=True)
    assert res["replaced"] and res["out_path"] == os.path.abspath(p)
    assert os.path.dirname(res["backup"]).endswith("_to_review")
    assert "John Smith" in _page_text(res["backup"])
    assert "Ann Lee" in _page_text(p)


def test_substitution_reported(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"), base14=True)
    b, _ = _block(p, "John Smith")
    res = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"], "new_text": "Ann Lee"}])
    assert res["substituted"] and res["substituted"][0]["block_id"] == b["id"]
    assert "Helvetica" in res["substituted"][0]["wanted"]
    assert res["substituted"][0]["used"]


@pytest.mark.skipif(not HAVE_ARIAL, reason="Arial not installed")
def test_original_font_reused(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "This is to certify")
    res = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"],
                              "new_text": "This is to confirm that"}])
    assert res["substituted"] == []
    with fitz.open(res["out_path"]) as d:
        assert "This is to confirm that" in d[0].get_text("text")    # raw: real spaces
    assert os.path.getsize(res["out_path"]) < 400_000                # embedded fonts are subset


def test_neighbour_lines_untouched(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    b, _ = _block(p, "successfully")
    # split the paragraph's first line only? The block is both lines; replace whole block
    out = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"], "new_text": "X"}])["out_path"]
    txt = _page_text(out)
    assert "successfully" not in txt and "management" not in txt
    assert "Date: 2026-10-08" in txt and "John Smith" in txt


def test_stale_block(tmp_path):
    p = _cert(str(tmp_path / "c.pdf"))
    with pytest.raises(pe.PdfEditError) as ei:
        pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": "b99", "new_text": "x"}])
    assert ei.value.code == "stale"


def test_tight_neighbours_widget_and_user_redaction(tmp_path):
    """Lines set solid (leading == size) stay intact; a form field over the edited
    text and an unapplied Redact annotation elsewhere are left alone."""
    p = str(tmp_path / "tight.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=400, height=300)
    pg.insert_text((40, 100), "Upper line Alpha", fontsize=12, fontname="helv")
    pg.insert_text((40, 112), "Lower line Beta", fontsize=12, fontname="tiro")
    pg.insert_text((40, 124), "Third line Gamma", fontsize=12, fontname="helv")
    w = fitz.Widget()
    w.field_name = "over"
    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    w.rect = fitz.Rect(70, 100, 85, 115)
    pg.add_widget(w)
    pg.add_redact_annot(fitz.Rect(40, 200, 200, 220))
    pg.insert_text((45, 214), "Keep me", fontsize=12, fontname="helv")
    doc.save(p)
    doc.close()
    b, _ = _block(p, "Lower line")
    assert b["text"] == "Lower line Beta"
    out = pe.apply_edits(p, [{"type": "text", "page": 0, "block_id": b["id"],
                              "new_text": "Lower line Changed"}])["out_path"]
    txt = _page_text(out)
    assert "Upper line Alpha" in txt and "Third line Gamma" in txt and "Lower line Changed" in txt
    assert "Beta" not in txt
    assert "Keep me" in txt                                   # user's redaction not applied
    with fitz.open(out) as d:
        assert [w.field_name for w in d[0].widgets()] == ["over"]
        assert any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in d[0].annots())


def test_fonts_list():
    fams = pe.available_fonts()
    if HAVE_ARIAL:
        assert any(f["name"] == "Arial" for f in fams)
