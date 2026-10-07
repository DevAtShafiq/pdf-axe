"""Tests for annotate.py (annotation engine) and its bridge mixin."""
import base64
import io
import json
import os
import sys

import pytest

fitz = pytest.importorskip("fitz")
from PIL import Image, ImageChops, ImageDraw  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import annotate as an  # noqa: E402
from annotate_bridge import AnnotateBridgeMixin  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def make_pdf(path, pages=2, rotate_last=False):
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Hello world page {i + 1} sample text", fontsize=14)
        p.insert_text((72, 140), "Second line of words", fontsize=14)
        if rotate_last and i == pages - 1:
            p.set_rotation(90)
    doc.save(str(path))
    doc.close()
    return str(path)


def sig_png():
    im = Image.new("RGBA", (240, 90), (0, 0, 0, 0))
    ImageDraw.Draw(im).line([(10, 70), (120, 20), (230, 60)], fill=(10, 20, 120, 255), width=6)
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def all_types(page=0, word=(72, 85, 110, 104)):
    w = list(word)
    return [
        dict(type="highlight", page=page, quads=[w], rect=w, color="#ffd400", opacity=0.6, text="check this"),
        dict(type="underline", page=page, quads=[w], rect=w, color="#16a34a"),
        dict(type="strikeout", page=page, quads=[w], rect=w, color="#dc2626"),
        dict(type="squiggly", page=page, quads=[w], rect=w, color="#2563eb"),
        dict(type="ink", page=page, points=[[[100, 300], [150, 330], [200, 300]]], color="#db2777", width=3),
        dict(type="rect", page=page, rect=[100, 400, 200, 450], color="#2563eb", width=2, fill="#dbeafe"),
        dict(type="ellipse", page=page, rect=[250, 400, 350, 450], color="#dc2626", width=2),
        dict(type="line", page=page, line=[100, 500, 200, 520], color="#000000", width=2),
        dict(type="arrow", page=page, line=[250, 500, 350, 520], color="#dc2626", width=3),
        dict(type="text", page=page, rect=[100, 550, 300, 600], text="Free text box", font_size=14,
             color="#1d4ed8", fill="#fef9c3"),
        dict(type="note", page=page, rect=[420, 100, 440, 120], text="Sticky comment", color="#ffd400",
             author="reviewer@example.com"),
        dict(type="stamp", page=page, rect=[300, 620, 460, 670], text="APPROVED\n06 Oct 2026",
             font_size=16, color="#b91c1c"),
        dict(type="signature", page=page, rect=[100, 680, 260, 740], image=sig_png()),
        dict(type="cover", page=page, rect=[400, 300, 500, 340], color="#000000"),
    ]


EXPECT_TYPES = sorted(["highlight", "underline", "strikeout", "squiggly", "ink", "rect", "ellipse",
                       "line", "arrow", "text", "note", "stamp", "signature", "cover"])


def pdf_annot_count(path):
    doc = fitz.open(path)
    try:
        return sum(len(list(p.annots() or [])) for p in doc)
    finally:
        doc.close()


# ── PDF round trip ───────────────────────────────────────────────────────────

def test_round_trip_every_type(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf")
    anns = all_types(0) + all_types(1)
    r = an.save(src, anns, author="me@example.com")
    out = r["out_path"]
    assert os.path.basename(out) == "doc_annotated.pdf"
    assert r["count"] == len(anns)

    loaded = an.load(out)
    assert loaded["kind"] == "pdf" and len(loaded["pages"]) == 2
    got = loaded["annotations"]
    assert len(got) == len(anns)
    assert sorted(a["type"] for a in got if a["page"] == 0) == EXPECT_TYPES
    by = {a["type"]: a for a in got if a["page"] == 0}
    assert by["highlight"]["color"] == "#ffd400"
    assert abs(by["highlight"]["opacity"] - 0.6) < 0.01
    assert by["highlight"]["text"] == "check this"
    assert by["rect"]["fill"] == "#dbeafe" and by["rect"]["rect"] == [100, 400, 200, 450]
    assert by["ellipse"]["color"] == "#dc2626"
    assert by["arrow"]["line"] == [250, 500, 350, 520]
    assert by["text"]["text"] == "Free text box" and by["text"]["font_size"] == 14
    assert by["text"]["color"] == "#1d4ed8" and by["text"]["fill"] == "#fef9c3"
    assert by["note"]["text"] == "Sticky comment" and by["note"]["author"] == "reviewer@example.com"
    assert by["stamp"]["text"] == "APPROVED\n06 Oct 2026" and by["stamp"]["color"] == "#b91c1c"
    assert by["signature"]["image"].startswith("data:image/png;base64,")
    assert by["cover"]["color"] == "#000000"
    assert by["ink"]["points"][0][1] == [150, 330]
    assert by["rect"]["author"] == "me@example.com"
    # real PDF annotations
    assert pdf_annot_count(out) == len(anns)


def test_resave_is_stable_and_edits_apply(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf")
    out1 = an.save(src, all_types(0))["out_path"]
    first = an.load(out1)["annotations"]
    # move the rectangle, erase the ellipse, edit the note
    edited = []
    for a in first:
        if a["type"] == "ellipse":
            continue
        if a["type"] == "rect":
            a = dict(a, rect=[120, 420, 220, 470])
        if a["type"] == "note":
            a = dict(a, text="Changed")
        edited.append(a)
    out2 = an.save(out1, edited)["out_path"]
    assert os.path.basename(out2) == "doc_annotated (2).pdf"     # never _annotated_annotated
    second = an.load(out2)["annotations"]
    assert len(second) == len(first) - 1
    by = {a["type"]: a for a in second}
    assert "ellipse" not in by
    assert by["rect"]["rect"] == [120, 420, 220, 470]
    assert by["note"]["text"] == "Changed"
    # untouched geometry does not drift
    for t in ("stamp", "text", "cover"):
        assert by[t]["rect"] == {a["type"]: a for a in first}[t]["rect"]


def test_rotated_page_round_trip(tmp_path):
    src = make_pdf(tmp_path / "rot.pdf", pages=1, rotate_last=True)
    words = an.words(src, 0)
    assert words
    w = words[0][:4]
    anns = [dict(type="highlight", page=0, quads=[w], rect=w, color="#ffd400"),
            dict(type="rect", page=0, rect=[300, 100, 400, 200], color="#2563eb", width=2)]
    out = an.save(src, anns)["out_path"]
    got = {a["type"]: a for a in an.load(out)["annotations"]}
    assert got["highlight"]["quads"][0] == pytest.approx(w, abs=0.1)
    assert got["rect"]["rect"] == pytest.approx([300, 100, 400, 200], abs=0.1)


def test_flatten_removes_annots_and_changes_content(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    plain = fitz.open(src)
    before = plain[0].get_pixmap(dpi=40).samples
    plain.close()
    r = an.save(src, all_types(0), flatten=True)
    assert pdf_annot_count(r["out_path"]) == 0
    doc = fitz.open(r["out_path"])
    try:
        assert doc[0].get_pixmap(dpi=40, annots=False).samples != before
    finally:
        doc.close()


def test_redaction_applied_only_when_asked(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    w = an.words(src, 0)[0]
    red = [dict(type="redact", page=0, rect=w[:4], color="#000000")]
    kept = an.save(src, red)["out_path"]
    assert w[4] in fitz.open(kept)[0].get_text()           # mark only
    gone = an.save(src, red, apply_redactions=True)["out_path"]
    doc = fitz.open(gone)
    try:
        assert w[4] not in doc[0].get_text()
    finally:
        doc.close()


def test_never_overwrites(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    a = an.save(src, all_types(0)[:1])["out_path"]
    b = an.save(src, all_types(0)[:1])["out_path"]
    c = an.save(src, all_types(0)[:1], out_path=a)["out_path"]
    assert len({a, b, c}) == 3
    assert os.path.basename(b) == "doc_annotated (2).pdf"


def test_save_over_original_keeps_backup(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    orig = open(src, "rb").read()
    r = an.save(src, all_types(0)[:3], replace=True)
    assert r["replaced"] and r["out_path"] == os.path.abspath(src)
    assert os.path.dirname(r["backup"]) == os.path.join(str(tmp_path), "_to_review")
    assert open(r["backup"], "rb").read() == orig
    assert pdf_annot_count(src) == 3


def test_words_api_returns_boxes(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    ws = an.words(src, 0)
    assert len(ws) >= 8
    x0, y0, x1, y1, text = ws[0][:5]
    assert text == "Hello" and x1 > x0 and y1 > y0
    assert an.words(src, 5) == []


def test_render_page_excludes_annotations(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    out = an.save(src, [dict(type="cover", page=0, rect=[0, 0, 595, 842], color="#000000")])["out_path"]
    r = an.render_page(out, 0, 0.5)
    assert r["data_url"].startswith("data:image/jpeg;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(r["data_url"].split(",", 1)[1])))
    assert img.convert("L").getpixel((5, 5)) > 200           # white page, not the black cover


def test_unknown_annotations_are_kept(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    doc = fitz.open(src)
    doc[0].add_polygon_annot([(100, 100), (200, 120), (150, 200)])
    path2 = str(tmp_path / "poly.pdf")
    doc.save(path2)
    doc.close()
    loaded = an.load(path2)["annotations"]
    assert [a["type"] for a in loaded] == ["other"]
    out = an.save(path2, loaded + all_types(0)[:1])["out_path"]
    assert sorted(a["type"] for a in an.load(out)["annotations"]) == ["highlight", "other"]
    out2 = an.save(path2, [])["out_path"]                       # erased in the editor
    assert pdf_annot_count(out2) == 0


# ── images ───────────────────────────────────────────────────────────────────

def make_img(path, size=(400, 300)):
    Image.new("RGB", size, (240, 240, 240)).save(str(path))
    return str(path)


def test_image_annotation_output_and_sidecar(tmp_path):
    src = make_img(tmp_path / "photo.png")
    anns = [dict(type="rect", page=0, rect=[20, 20, 120, 100], color="#dc2626", width=4),
            dict(type="highlight", page=0, quads=[[150, 40, 300, 70]], rect=[150, 40, 300, 70], color="#ffd400"),
            dict(type="text", page=0, rect=[20, 150, 300, 200], text="Hello", font_size=24, color="#000000"),
            dict(type="arrow", page=0, line=[200, 250, 350, 180], color="#2563eb", width=4),
            dict(type="signature", page=0, rect=[250, 200, 390, 290], image=sig_png())]
    r = an.save(src, anns, author="me")
    out = r["out_path"]
    assert os.path.basename(out) == "photo_annotated.png"
    assert os.path.isfile(out) and os.path.isfile(r["sidecar"])
    assert os.path.basename(r["sidecar"]) == "photo_annotated.annot.json"
    a, b = Image.open(src).convert("RGB"), Image.open(out).convert("RGB")
    assert a.size == b.size and ImageChops.difference(a, b).getbbox() is not None
    assert open(src, "rb").read() == open(src, "rb").read()      # source untouched
    # re-open the output: original pixels + editable layer
    loaded = an.load(out)
    assert loaded["kind"] == "image" and loaded["source"] == os.path.abspath(src)
    assert [x["type"] for x in loaded["annotations"]] == [x["type"] for x in anns]
    side = json.load(open(r["sidecar"], encoding="utf-8"))
    assert side["version"] == 1 and side["source"] == "photo.png"
    # saving again from the annotated copy → a new free name, from the clean source
    r2 = an.save(out, loaded["annotations"][:1])
    assert os.path.basename(r2["out_path"]) == "photo_annotated (2).png"


def test_image_save_over_original_keeps_backup(tmp_path):
    src = make_img(tmp_path / "scan.jpg")
    orig = open(src, "rb").read()
    r = an.save(src, [dict(type="cover", page=0, rect=[0, 0, 50, 50], color="#000000")], replace=True)
    assert r["replaced"] and open(r["backup"], "rb").read() == orig
    assert os.path.dirname(r["backup"]).endswith("_to_review")
    loaded = an.load(src)                                         # still editable
    assert loaded["source"] == r["backup"] and len(loaded["annotations"]) == 1


# ── bridge ───────────────────────────────────────────────────────────────────

class _Bridge(AnnotateBridgeMixin):
    def __init__(self):
        self.settings = {}
        self._cloud_user_cache = {"email": "staff@example.com"}

    def _load_settings(self):
        return dict(self.settings)

    def _save_settings(self, data):
        self.settings.update(data)


def test_bridge_round_trip(tmp_path):
    b = _Bridge()
    src = make_pdf(tmp_path / "doc.pdf", pages=1)
    assert b.annot_author()["author"] == "staff@example.com"
    r = b.annot_load(src)
    assert r["ok"] and r["kind"] == "pdf" and r["annotations"] == [] and r["author"]
    assert b.annot_words(src, 0)["words"]
    assert b.annot_page_png(src, 0, 0.5)["data_url"].startswith("data:image/")
    s = b.annot_save(src, all_types(0)[:2], {"mode": "new"})
    assert s["ok"] and s["out_path"].endswith("doc_annotated.pdf")
    assert b.annot_load(s["out_path"])["annotations"][0]["author"] == "staff@example.com"
    bad = b.annot_load(str(tmp_path / "missing.pdf"))
    assert not bad["ok"] and "not found" in bad["error"]


def test_bridge_signatures(tmp_path):
    b = _Bridge()
    sig = sig_png()
    assert b.annot_signatures()["signatures"] == []
    assert b.annot_signature_save(sig)["signatures"] == [sig]
    assert b.annot_signature_save(sig)["signatures"] == [sig]          # no duplicates
    assert not b.annot_signature_save("javascript:alert(1)")["ok"]
    assert b.annot_signature_forget(0)["signatures"] == []
