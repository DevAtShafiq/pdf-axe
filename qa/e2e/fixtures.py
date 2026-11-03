"""Test files for the editor E2E scripts — always created inside a scratch dir."""
from __future__ import annotations

import os

from cdp import ROOT  # noqa: F401  (puts the project + tests on sys.path)


def make_all(d: str) -> dict:
    import pymupdf as fitz
    from PIL import Image
    from test_pdf_edit import _cert

    os.makedirs(d, exist_ok=True)
    out = {}
    out["cert"] = _cert(os.path.join(d, "cert.pdf"))
    # 3-page PDF with a form field on page 1 and plain text on all pages
    doc = fitz.open()
    for i in range(3):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"Page {i + 1} heading text", fontsize=20)
        pg.insert_text((72, 140), "The quick brown fox jumps over the lazy dog.", fontsize=12)
        pg.insert_text((72, 160), "Second line of body text for markup tests.", fontsize=12)
    w = fitz.Widget()
    w.field_name = "name"
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.rect = fitz.Rect(72, 300, 300, 322)
    w.field_value = "Form value"
    doc[0].add_widget(w)
    p = os.path.join(d, "multi.pdf")
    doc.save(p)
    doc.close()
    out["multi"] = p
    img = os.path.join(d, "picture.png")
    Image.new("RGB", (800, 600), (230, 236, 245)).save(img)
    out["png"] = img
    jpg = os.path.join(d, "snap.jpg")
    Image.new("RGB", (640, 480), (250, 240, 220)).save(jpg, quality=92)
    out["jpg"] = jpg
    photo = os.path.join(d, "passport.jpg")
    Image.new("RGB", (300, 400), (120, 80, 60)).save(photo, quality=92)
    out["photo"] = photo
    return out
