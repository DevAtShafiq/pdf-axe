"""Text-based PDFs must stay text-based through every feature that writes a PDF.

The owner's rule: a PDF whose text can be selected and copied must keep that
text (and its fillable form fields) after annotating, signing, compressing,
merging, splitting, arranging and password-protecting — nothing may turn the
pages into pictures.
"""
from __future__ import annotations

import base64
import io
import os

import pytest

pymupdf = pytest.importorskip("pymupdf")
from PIL import Image, ImageDraw  # noqa: E402

import annotate  # noqa: E402
import archive_tools as at  # noqa: E402
import media_convert as mc  # noqa: E402
import pdf_tools as pt  # noqa: E402

PHRASE = "Applicant: Rahim Uddin"


@pytest.fixture()
def form_pdf(tmp_path):
    """Two pages: real text, a photo, and fillable form fields."""
    doc = pymupdf.open()
    for i in range(2):
        pg = doc.new_page()
        pg.insert_text((72, 80), f"Page {i + 1} - {PHRASE} - Passport A1234567", fontsize=12)
        img = Image.effect_noise((600, 400), 50).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        pg.insert_image(pymupdf.Rect(72, 300, 520, 600), stream=buf.getvalue())
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = f"name{i}"
        w.rect = pymupdf.Rect(72, 120, 300, 140)
        w.field_value = "Rahim"
        pg.add_widget(w)
    path = tmp_path / "form.pdf"
    doc.save(str(path))
    return str(path)


def _sig():
    im = Image.new("RGBA", (240, 90), (0, 0, 0, 0))
    ImageDraw.Draw(im).line([(10, 70), (120, 20), (230, 60)], fill=(10, 20, 120, 255), width=6)
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def _assert_text_pdf(path, *, pages=2, fields=2, password=None):
    doc = pymupdf.open(path)
    if password:
        assert doc.authenticate(password)
    assert doc.page_count == pages
    for p in doc:
        assert PHRASE in p.get_text(), "page text is no longer selectable"
    assert sum(len(list(p.widgets())) for p in doc) == fields, "form fields were lost"


def _signed(page=0):
    return [
        dict(type="highlight", page=page, quads=[[72, 68, 300, 84]], rect=[72, 68, 300, 84], color="#ffd400"),
        dict(type="signature", page=page, rect=[300, 680, 460, 740], image=_sig()),
        dict(type="stamp", page=page, rect=[300, 620, 460, 670], text="APPROVED", font_size=16, color="#b91c1c"),
    ]


def test_sign_keeps_text_and_fields(form_pdf):
    r = annotate.save(form_pdf, _signed())
    _assert_text_pdf(r["out_path"])
    assert len(list(pymupdf.open(r["out_path"])[0].annots())) >= 3   # still real, editable annotations


def test_lock_annotations_keeps_text_and_fields(form_pdf):
    r = annotate.save(form_pdf, _signed(), flatten=True)
    _assert_text_pdf(r["out_path"])


@pytest.mark.parametrize("preset", ["screen", "ebook", "printer"])
def test_compress_keeps_text_and_fields(form_pdf, preset):
    r = mc.compress_pdf(form_pdf, preset)
    out = r.get("out_path")
    if out:                               # a preset may keep the original if it can't save ≥1 %
        _assert_text_pdf(out)


def test_merge_split_extract_arrange_keep_text(form_pdf, tmp_path):
    _assert_text_pdf(pt.merge([form_pdf, form_pdf])["out_path"], pages=4, fields=4)
    _assert_text_pdf(pt.extract(form_pdf, "1")["out_path"], pages=1, fields=1)
    out = str(tmp_path / "arranged.pdf")
    r = pt.build_pdf([{"path": form_pdf, "page": 1, "rotate": 0},
                      {"path": form_pdf, "page": 0, "rotate": 90}], out)
    _assert_text_pdf(r["out_path"])


def test_password_keeps_text_and_fields(form_pdf):
    r = at.pdf_set_password(form_pdf, "pw123")
    _assert_text_pdf(r["out_path"], password="pw123")
    u = at.pdf_remove_password(r["out_path"], "pw123")
    _assert_text_pdf(u["out_path"])


def _photo_jpeg():
    im = Image.new("RGB", (350, 450), (40, 90, 160))
    ImageDraw.Draw(im).ellipse([100, 80, 250, 260], fill=(230, 200, 170))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def test_photo_on_form_keeps_text_and_fields(form_pdf):
    """Attaching a photo onto a form (e.g. a passport photo in its box)."""
    anns = [dict(type="photo", page=0, rect=[400, 120, 500, 248], image=_photo_jpeg(), text="me.jpg"),
            dict(type="signature", page=0, rect=[300, 680, 460, 740], image=_sig())]
    r = annotate.save(form_pdf, anns)
    _assert_text_pdf(r["out_path"])
    loaded = annotate.load(r["out_path"])["annotations"]
    photo = [a for a in loaded if a["type"] == "photo"]
    assert len(photo) == 1 and photo[0]["image"].startswith("data:image/")
    assert [round(v) for v in photo[0]["rect"]] == [400, 120, 500, 248]
    # The photo is visible on the rendered page (blue pixels inside its box).
    pix = pymupdf.open(r["out_path"])[0].get_pixmap(clip=pymupdf.Rect(405, 125, 495, 140))
    assert pix.samples[2] > 120 and pix.samples[0] < 120
