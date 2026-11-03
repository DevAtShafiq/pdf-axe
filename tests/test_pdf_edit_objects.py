"""Tests for page-object editing in the PDF editor (pdf_objects.py via pdf_edit.py):
images, vector graphics and form XObjects can be selected, moved, resized,
rotated, cropped, deleted, recoloured, replaced and added — without touching
the text (it stays selectable), the other objects or the form fields."""
from __future__ import annotations

import base64
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fitz = pytest.importorskip("pymupdf")
from PIL import Image, ImageChops, ImageDraw  # noqa: E402

import pdf_edit as pe  # noqa: E402
import pdf_objects as po  # noqa: E402
import pdf_studio as ps  # noqa: E402

TEXTS = ["Neighbour text stays here", "Second paragraph of the form", "Footer line 2026"]


def _png(color=(200, 30, 30), size=(60, 40), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def _du(data: bytes, mime="image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _make(path, rotation=0, clipped=True):
    """A form-like page: text, a red picture, a yellow box with a blue border,
    a 'logo' of three touching circles, a small table grid, a picture the way
    Word places it (clipped to its frame), a form field. Positions are display
    coordinates of an upright 612 x 792 view."""
    doc = fitz.open()
    page = doc.new_page(width=792, height=612) if rotation in (90, 270) else doc.new_page(width=612, height=792)
    page.set_rotation(rotation)
    dm = page.derotation_matrix
    R = lambda *r: (fitz.Rect(*r) * dm).normalize()
    P = lambda x, y: fitz.Point(x, y) * dm
    page.insert_text(P(60, 40), TEXTS[0], fontsize=12, rotate=rotation)
    page.insert_image(R(60, 60, 180, 140), stream=_png((220, 20, 20)), rotate=rotation)
    page.draw_rect(R(300, 100, 420, 160), color=(0, 0, 1), fill=(1, 1, 0), width=2)
    page.insert_text(P(300, 200), TEXTS[1], fontsize=12, rotate=rotation)
    for cx in (110, 140, 170):
        page.draw_circle(P(cx, 330), 18, color=None, fill=(0, 0.6, 0))
    page.insert_text(P(60, 420), TEXTS[2], fontsize=11, rotate=rotation)
    for y in (450, 500, 550):
        page.draw_line(P(60, y), P(300, y), color=(0, 0, 0), width=1)
    for x in (60, 180, 300):
        page.draw_line(P(x, 450), P(x, 550), color=(0, 0, 0), width=1)
    w = fitz.Widget()
    w.field_name = "name"
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.rect = R(350, 690, 550, 710)
    w.field_value = "Rahim"
    page.add_widget(w)
    if clipped and rotation == 0:
        # A blue picture drawn 200 x 150 but clipped to a 100 x 80 frame:
        # visible at display (400, 432, 500, 512).
        before = set(page.get_contents())
        xref = page.insert_image(fitz.Rect(0, 0, 10, 10), stream=_png((20, 40, 220), (80, 60)))
        name = [it[7] for it in page.get_images(full=True) if it[0] == xref][0]
        for x in page.get_contents():
            if x not in before and b"Do" in (doc.xref_stream(x) or b""):
                doc.update_stream(x, (f"q 400 280 100 80 re W n q 0.5 0 0 0.5 0 0 cm "
                                      f"q 400 0 0 300 760 520 cm /{name} Do Q Q Q").encode())
    doc.save(path)
    doc.close()
    return path


def _objects(path, page=0):
    return pe.extract_objects(path, page)["objects"]


def _obj(objs, kind, near=None):
    cands = [o for o in objs if o["kind"] == kind]
    if near is not None:
        cands.sort(key=lambda o: abs(o["bbox"][0] - near[0]) + abs(o["bbox"][1] - near[1]))
    assert cands, f"no {kind} in {[(o['kind'], o['bbox']) for o in objs]}"
    return cands[0]


def _render(path, page=0, scale=1.0) -> Image.Image:
    with fitz.open(path) as d:
        pix = d[page].get_pixmap(matrix=fitz.Matrix(scale, scale), annots=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _same_outside(a_path, b_path, *rects, page=0, pad=3, tol=40):
    """Both renders are equal everywhere except inside *rects* (display coords)."""
    a, b = _render(a_path, page), _render(b_path, page)
    assert a.size == b.size
    for im in (a, b):
        d = ImageDraw.Draw(im)
        for r in rects:
            d.rectangle([r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad], fill=(255, 0, 255))
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > tol else 0)
    assert diff.getbbox() is None, f"page changed outside the edited area: {diff.getbbox()}"


def _pixel(path, x, y, page=0):
    im = _render(path, page)
    return im.getpixel((int(x), int(y)))


def _texts(path, page=0):
    with fitz.open(path) as d:
        return d[page].get_text("text").replace(" ", " ")


def _assert_text_and_fields(path, page=0):
    t = _texts(path, page)
    for s in TEXTS:
        assert s in t, f"text lost: {s!r}"
    with fitz.open(path) as d:
        ws = list(d[page].widgets())
        assert [w.field_name for w in ws] == ["name"] and ws[0].field_value == "Rahim"


def _apply(path, edits, **kw):
    return pe.apply_edits(path, edits, **kw)["out_path"]


def _close(a, b, tol=1.0):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ─────────────────────────────────────────────────────────────────────────────

def test_tokenizer_keeps_strings_and_inline_images():
    raw = (b"q (a \\) (nested) string) Tj [<41> 10 (b)] TJ /Name#20x 2 0 R\n"
           b"BI /W 2 /H 1 /CS /G /BPC 8 ID \x00EI\xff EI Q")
    ops = po.tokenize(raw)
    names = [o.op for o in ops]
    assert names == ["q", "Tj", "TJ", "R", "BI", "Q"]
    assert raw[ops[4].start:ops[4].end].startswith(b"BI") and raw[ops[4].start:ops[4].end].endswith(b"EI")
    assert ops[3].args[0] == ("/", "Name x")


def test_extract_objects(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    objs = _objects(p)
    kinds = [o["kind"] for o in objs]
    assert kinds.count("image") == 2
    img = _obj(objs, "image", (60, 60))
    assert _close(img["bbox"], [60, 60, 180, 140])
    box = [o for o in objs if o["kind"] == "path" and o["fill"] == "#ffff00"]
    assert len(box) == 1 and _close(box[0]["bbox"], [299, 99, 421, 161])
    assert box[0]["stroke"] == "#0000ff" and box[0]["width"] == pytest.approx(2, abs=0.01)
    logo = [o for o in objs if o["kind"] == "path" and o["fill"] == "#009900"]
    assert len(logo) == 1 and logo[0]["paths"] == 3, "touching circles form one object"
    grid = [o for o in objs if o["kind"] == "path" and o["paths"] == 6]
    assert len(grid) == 1 and _close(grid[0]["bbox"], [59.5, 449.5, 300.5, 550.5])
    clipped = _obj(objs, "image", (400, 432))
    assert _close(clipped["bbox"], [400, 432, 500, 512]), "a clipped picture reports its visible frame"
    assert not any(o["background"] for o in objs)


def test_move_image_keeps_everything_else(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    img = _obj(_objects(p), "image", (60, 60))
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "orig_bbox": img["bbox"],
                      "bbox": [400, 560, 520, 640]}])
    with fitz.open(out) as d:
        bbs = [fitz.Rect(i["bbox"]) for i in d[0].get_image_info()]
    assert any(_close(b, [400, 560, 520, 640]) for b in bbs)
    assert not any(_close(b, [60, 60, 180, 140]) for b in bbs)
    _assert_text_and_fields(out)
    _same_outside(p, out, [60, 60, 180, 140], [400, 560, 520, 640])
    r, g, b = _pixel(out, 460, 600)
    assert r > 180 and g < 80                          # the red picture is there now
    assert _pixel(out, 120, 100) == (255, 255, 255)    # and gone from its old place


def test_resize_and_rotate_image(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    img = _obj(_objects(p), "image", (60, 60))
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "bbox": [60, 60, 120, 180],
                      "rotate": 90}])
    with fitz.open(out) as d:
        info = [i for i in d[0].get_image_info() if i["width"] == 60][0]
    assert _close(info["bbox"], [60, 60, 120, 180])
    t = fitz.Matrix(info["transform"])
    assert abs(t.a) < 1e-3 and abs(t.d) < 1e-3, "drawn rotated by 90°"
    _assert_text_and_fields(out)
    _same_outside(p, out, [60, 60, 180, 180])


def test_delete_image_only_this_placement(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    objs = _objects(p)
    img = _obj(objs, "image", (60, 60))
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "delete": True}])
    assert _pixel(out, 120, 100) == (255, 255, 255)
    _assert_text_and_fields(out)
    _same_outside(p, out, img["bbox"])
    left = _objects(out)
    assert len(left) == len(objs) - 1 and sum(o["kind"] == "image" for o in left) == 1


def test_crop_image(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    img = _obj(_objects(p), "image", (60, 60))
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "crop": [60, 60, 120, 100],
                      "bbox": [60, 60, 120, 100]}])
    assert _pixel(out, 90, 80)[0] > 180          # kept part
    assert _pixel(out, 150, 120) == (255, 255, 255)  # cropped away
    _assert_text_and_fields(out)
    _same_outside(p, out, img["bbox"])
    o2 = _obj(_objects(out), "image", (60, 60))
    assert _close(o2["bbox"], [60, 60, 120, 100])


def test_move_clipped_picture_moves_its_frame(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    img = _obj(_objects(p), "image", (400, 432))
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "bbox": [420, 250, 520, 330]}])
    o2 = _obj(_objects(out), "image", (420, 250))
    assert _close(o2["bbox"], [420, 250, 520, 330])
    r, g, b = _pixel(out, 470, 290)
    assert b > 180 and r < 80
    assert _pixel(out, 450, 470) == (255, 255, 255)
    # the part outside the frame stays hidden at the new place too
    assert _pixel(out, 530, 290) == (255, 255, 255)
    _assert_text_and_fields(out)
    _same_outside(p, out, img["bbox"], [420, 250, 520, 330])


def test_vector_box_move_resize_recolour(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    box = [o for o in _objects(p) if o["kind"] == "path" and o["fill"] == "#ffff00"][0]
    out = _apply(p, [{"type": "object", "page": 0, "object_id": box["id"], "bbox": [319, 599, 501, 661],
                      "fill": "#ff0000", "stroke": "#00ff00", "width": 4}])
    with fitz.open(out) as d:
        dr = [x for x in d[0].get_drawings() if x.get("fill") and x["fill"] == pytest.approx((1, 0, 0), abs=0.01)]
    assert len(dr) == 1
    assert _close(dr[0]["rect"], [320, 600, 500, 660], tol=1.5)
    assert dr[0]["color"] == pytest.approx((0, 1, 0), abs=0.01)
    assert dr[0]["width"] == pytest.approx(4, abs=0.2)
    _assert_text_and_fields(out)
    _same_outside(p, out, box["bbox"], [315, 595, 505, 665])
    # the yellow box is gone from its old place
    assert _pixel(out, 360, 130) == (255, 255, 255)


def test_vector_remove_fill_and_stroke_only(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    box = [o for o in _objects(p) if o["kind"] == "path" and o["fill"] == "#ffff00"][0]
    out = _apply(p, [{"type": "object", "page": 0, "object_id": box["id"], "fill": "none"}])
    assert _pixel(out, 360, 130) == (255, 255, 255)        # no fill
    r, g, b = _pixel(out, 300, 130)
    assert b > 150 and r < 100                            # border still blue
    logo = [o for o in _objects(p) if o["kind"] == "path" and o["fill"] == "#009900"][0]
    out2 = _apply(p, [{"type": "object", "page": 0, "object_id": logo["id"], "stroke": "#ff0000", "width": 2}])
    with fitz.open(out2) as d:
        greens = [x for x in d[0].get_drawings() if x.get("fill") == pytest.approx((0, 0.6, 0), abs=0.01)]
    assert len(greens) == 3 and all(x["color"] == pytest.approx((1, 0, 0), abs=0.01) for x in greens)


def test_delete_vector_group(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    grid = [o for o in _objects(p) if o["kind"] == "path" and o["paths"] == 6][0]
    out = _apply(p, [{"type": "object", "page": 0, "object_id": grid["id"], "delete": True}])
    assert _pixel(out, 120, 450) == (255, 255, 255)
    _assert_text_and_fields(out)
    _same_outside(p, out, grid["bbox"])


def test_replace_image_only_this_placement(tmp_path):
    p = str(tmp_path / "f.pdf")
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((60, 40), TEXTS[0])
    red = _png((220, 20, 20))
    pg.insert_image(fitz.Rect(60, 60, 180, 140), stream=red)
    pg.insert_image(fitz.Rect(300, 60, 420, 140), stream=red)      # same picture again (one xref)
    doc.save(p)
    doc.close()
    objs = _objects(p)
    assert objs[0]["xref"] == objs[1]["xref"]
    out = _apply(p, [{"type": "object", "page": 0, "object_id": objs[0]["id"],
                      "data_url": _du(_png((20, 200, 20), (40, 40)))}])
    assert _pixel(out, 120, 100)[1] > 150         # new green picture (padded to fit)
    assert _pixel(out, 360, 100)[0] > 180         # the other copy is still red
    with fitz.open(out) as d:
        infos = d[0].get_image_info(xrefs=True)
    assert len(infos) == 2 and infos[0]["xref"] != infos[1]["xref"]
    assert _close(infos[0]["bbox"], [60, 60, 180, 140])
    assert TEXTS[0] in _texts(out)


def test_add_image(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    out = _apply(p, [{"type": "add_image", "page": 0, "rect": [450, 300, 550, 350],
                      "data_url": _du(_png((250, 120, 0), (100, 50)))}])
    r, g, b = _pixel(out, 500, 325)
    assert r > 200 and 80 < g < 160 and b < 60
    _assert_text_and_fields(out)
    _same_outside(p, out, [450, 300, 550, 350])
    assert any(_close(o["bbox"], [450, 300, 550, 350]) for o in _objects(out) if o["kind"] == "image")


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_rotated_page_move(tmp_path, rot):
    p = _make(str(tmp_path / f"r{rot}.pdf"), rotation=rot)
    objs = _objects(p)
    img = _obj(objs, "image", (60, 60))
    assert _close(img["bbox"], [60, 60, 180, 140])
    box = [o for o in objs if o["kind"] == "path" and o["fill"] == "#ffff00"][0]
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "bbox": [400, 560, 520, 640]},
                     {"type": "object", "page": 0, "object_id": box["id"], "bbox": [59, 599, 181, 661]}])
    objs2 = _objects(out)
    assert any(_close(o["bbox"], [400, 560, 520, 640]) for o in objs2 if o["kind"] == "image")
    assert any(_close(o["bbox"], [59, 599, 181, 661]) for o in objs2 if o["kind"] == "path")
    _assert_text_and_fields(out)
    _same_outside(p, out, img["bbox"], [400, 560, 520, 640], box["bbox"], [59, 599, 181, 661])


def test_text_and_object_edits_together(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    img = _obj(_objects(p), "image", (60, 60))
    blk = [b for b in pe.extract_blocks(p, 0)["blocks"] if "Footer" in b["text"]][0]
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "bbox": [400, 560, 520, 640]},
                     {"type": "text", "page": 0, "block_id": blk["id"], "orig_text": blk["text"],
                      "new_text": "Footer line 2027"}])
    t = _texts(out)
    assert "Footer line 2027" in t and TEXTS[0] in t and TEXTS[1] in t
    assert any(_close(o["bbox"], [400, 560, 520, 640]) for o in _objects(out) if o["kind"] == "image")


def test_stale_object(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    img = _obj(_objects(p), "image", (60, 60))
    with pytest.raises(pe.PdfEditError) as ei:
        pe.apply_edits(p, [{"type": "object", "page": 0, "object_id": img["id"], "orig_bbox": [0, 0, 5, 5],
                            "delete": True}])
    assert ei.value.code == "stale"
    with pytest.raises(pe.PdfEditError):
        pe.apply_edits(p, [{"type": "object", "page": 0, "object_id": "o999", "delete": True}])


def test_previews(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    img = _obj(_objects(p), "image", (60, 60))
    before = os.path.getmtime(p), os.path.getsize(p)
    r = pe.render_page(p, 0, 1.0, {"objects": [{"type": "object", "object_id": img["id"], "delete": True}]})
    im = Image.open(io.BytesIO(base64.b64decode(r["data_url"].split(",", 1)[1]))).convert("RGB")
    assert im.getpixel((120, 100)) == (255, 255, 255)
    s = pe.render_object(p, 0, img["id"], 1.0)
    sp = Image.open(io.BytesIO(base64.b64decode(s["data_url"].split(",", 1)[1])))
    assert sp.mode == "RGBA" and abs(sp.width - 120) <= 2 and abs(sp.height - 80) <= 2
    assert sp.getpixel((60, 40))[0] > 180 and sp.getpixel((60, 40))[3] == 255
    box = [o for o in _objects(p) if o["kind"] == "path" and o["fill"] == "#ffff00"][0]
    s2 = pe.render_object(p, 0, box["id"], 1.0, {"fill": "#ff0000"})
    sp2 = Image.open(io.BytesIO(base64.b64decode(s2["data_url"].split(",", 1)[1])))
    c = sp2.getpixel((sp2.width // 2, sp2.height // 2))
    assert c[0] > 200 and c[1] < 60 and c[3] == 255           # recoloured, no text, no page
    corner = Image.open(io.BytesIO(base64.b64decode(s["data_url"].split(",", 1)[1])))
    assert (os.path.getmtime(p), os.path.getsize(p)) == before    # previews never write


def test_studio_step_and_undo(tmp_path):
    """Studio: an object edit is written into a NEW step file; the previous
    step (Undo goes back to it) is untouched."""
    src = _make(str(tmp_path / "f.pdf"))
    s = ps.begin(src)
    step0 = s["work"]
    data0 = open(step0, "rb").read()
    img = _obj(_objects(step0), "image", (60, 60))
    nxt = ps.next_step(s["session"])["path"]
    r = pe.apply_edits(step0, [{"type": "object", "page": 0, "object_id": img["id"], "delete": True}], out_path=nxt)
    assert r["out_path"] == nxt and os.path.isfile(nxt)
    assert open(step0, "rb").read() == data0
    assert len(_objects(nxt)) == len(_objects(step0)) - 1
    _assert_text_and_fields(nxt)
    fin = ps.finish(nxt, src, out_path=str(tmp_path / "result.pdf"))
    _assert_text_and_fields(fin["out_path"])


def test_word_like_content_with_marked_content(tmp_path):
    """Tagged content (BDC/EMC), nested q/Q, a colour set inside a text object
    that later paths rely on: moving a picture out of its clip keeps all of it."""
    p = str(tmp_path / "w.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((60, 40), TEXTS[0], fontsize=12)
    before = set(pg.get_contents())
    xref = pg.insert_image(fitz.Rect(0, 0, 10, 10), stream=_png((20, 40, 220), (80, 60)))
    name = [it[7] for it in pg.get_images(full=True) if it[0] == xref][0]
    font = [f[4] for f in doc.get_page_fonts(0)][0]
    content = (f"/Artifact BMC q 0 0 612 792 re W n q 1 0 0 1 0 0 cm "
               f"/P <</MCID 0>> BDC q 50 600 100 80 re W n 200 0 0 150 0 550 cm /{name} Do Q EMC "
               f"BT /{font} 12 Tf 1 0 0 rg 60 300 Td (Red words) Tj ET "
               f"0 0 0 RG 2 w 300 300 m 500 300 l S "
               f"20 400 50 30 re f Q Q EMC").encode()
    for x in pg.get_contents():
        if x not in before and b"Do" in (doc.xref_stream(x) or b""):
            doc.update_stream(x, content)
    doc.save(p)
    doc.close()
    objs = _objects(p)
    img = _obj(objs, "image", (50, 112))
    assert _close(img["bbox"], [50, 112, 150, 192])
    out = _apply(p, [{"type": "object", "page": 0, "object_id": img["id"], "bbox": [350, 600, 450, 680]}])
    _same_outside(p, out, img["bbox"], [350, 600, 450, 680])
    assert "Red words" in _texts(out)
    # the rectangle after the text is still red-filled (colour from inside BT)
    r, g, b = _pixel(out, 45, 377)
    assert r > 200 and g < 60
    o2 = _obj(_objects(out), "image", (350, 600))
    assert _close(o2["bbox"], [350, 600, 450, 680])


def test_text_pdf_preserved_after_object_edits(tmp_path):
    """The owner's rule: editing objects never flattens the page."""
    p = _make(str(tmp_path / "f.pdf"))
    objs = _objects(p)
    edits = [{"type": "object", "page": 0, "object_id": o["id"], "bbox": [o["bbox"][0] + 5, o["bbox"][1] + 5,
                                                                         o["bbox"][2] + 5, o["bbox"][3] + 5]}
             for o in objs]
    out = _apply(p, edits)
    _assert_text_and_fields(out)
    with fitz.open(out) as d:
        assert len(d[0].get_images()) >= 2
        assert len(d[0].get_drawings()) >= 10        # vectors stay vectors
        assert d[0].get_fonts()


def test_add_image_rotated_and_load_image_keeps_alpha(tmp_path):
    p = _make(str(tmp_path / "f.pdf"))
    png = tmp_path / "logo.png"
    im = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([0, 0, 49, 49], fill=(250, 120, 0, 255))   # orange left half
    im.save(png)
    li = pe.load_image(str(png))
    assert li["data_url"].startswith("data:image/png") and (li["width"], li["height"]) == (100, 50)
    out = _apply(p, [{"type": "add_image", "page": 0, "rect": [450, 300, 500, 400], "rotate": 90,
                      "data_url": li["data_url"]}])
    # rotated clockwise: the orange (left) half is now on top
    c = _pixel(out, 475, 320)
    assert c[0] > 200 and c[2] < 80
    assert _pixel(out, 475, 380) == (255, 255, 255)          # transparent half shows the page
    _assert_text_and_fields(out)
