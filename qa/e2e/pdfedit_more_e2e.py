"""E2E extras for the PDF text editor: Esc cancels an edit, double-click edits,
image replace / delete (picker stubbed), scanned page notice, empty edit = delete.

    python qa/e2e/pdfedit_more_e2e.py <scratch dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402
from pdfedit_e2e import blk, close_editor, pt, st, wait_open  # noqa: E402

R = Results()


def run(d):
    import pymupdf as fitz
    from PIL import Image
    f = make_all(d)
    cert = f["cert"]
    # scanned PDF: one page that is only an image
    scan_img = os.path.join(d, "scan.png")
    Image.new("RGB", (1240, 1754), (250, 250, 245)).save(scan_img)
    sd = fitz.open()
    sp = sd.new_page(width=595, height=842)
    sp.insert_image(sp.rect, filename=scan_img)
    scan_pdf = os.path.join(d, "scan.pdf")
    sd.save(scan_pdf)
    sd.close()
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        close_editor(page)
        page.evaluate("p => PdfEdit.open(p)", cert)
        wait_open(page)
        b = blk(cert, "John Smith")
        c = pt(page, 0, (b["bbox"][0] + b["bbox"][2]) / 2, (b["bbox"][1] + b["bbox"][3]) / 2)
        page.mouse.click(*c)
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        page.keyboard.type("Nope")
        page.keyboard.press("Escape"); time.sleep(0.2)
        R.check("Esc cancels the edit", not st(page)["model"]["ov"], st(page)["model"])
        page.keyboard.press("Escape"); time.sleep(0.1)   # deselect
        page.mouse.dblclick(*c)
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        R.check("double-click edits", True)
        page.keyboard.press("Control+a"); page.keyboard.press("Delete")
        page.mouse.click(*pt(page, 0, 500, 600)); time.sleep(0.2)
        ov = st(page)["model"]["ov"].get("0:" + b["id"], {})
        R.check("emptying a block = delete", ov.get("deleted") is True, ov)
        # image: select the logo, replace with a picked image (picker stubbed)
        im = page.query_selector(".pe-imgbox")
        R.check("image box present", im is not None)
        im.click(); time.sleep(0.2)
        R.check("image tools shown", page.evaluate("!document.querySelector('#pe-imgtools').classList.contains('hidden')"))
        page.evaluate("p => { window.__pick = SFM.annotPickPhoto; SFM.annotPickPhoto = async () => ({ok: true, path: p}); }", f["photo"])
        page.click(".pe-bar [data-a=img-replace]")
        wait(page, "Object.keys(PdfEdit._state().model.imgs).length === 1")
        page.evaluate("() => { SFM.annotPickPhoto = window.__pick; }")
        R.check("replace queued + preview", page.query_selector(".pe-imgbox .pe-newimg") is not None)
        page.fill("#pe-name", "cert_img"); page.fill("#pe-dir", d)
        page.click("#pe-save")
        wait(page, "document.querySelector('#pe-notice') && !document.querySelector('#pe-notice').classList.contains('hidden')", timeout=30)
        out = os.path.join(d, "cert_img.pdf")
        doc = fitz.open(out)
        info = doc[0].get_image_info(xrefs=True)
        pix = fitz.Pixmap(doc, info[0]["xref"]) if info else None
        txt = doc[0].get_text()
        doc.close()
        R.check("image replaced in output", pix is not None and pix.pixel(pix.width // 2, pix.height // 2)[:3] != (200, 30, 30),
                pix.pixel(pix.width // 2, pix.height // 2) if pix else None)
        R.check("deleted block gone", "John" not in txt)
        # now delete the image in the saved file
        wait(page, "document.querySelector('.pe-imgbox')")
        page.click(".pe-imgbox"); time.sleep(0.2)
        page.click(".pe-bar [data-a=img-delete]"); time.sleep(0.2)
        page.fill("#pe-name", "cert_noimg")
        page.click("#pe-save")
        wait(page, "PdfEdit._state() && PdfEdit._state().src.toLowerCase().endsWith('cert_noimg.pdf')", timeout=30)
        doc = fitz.open(os.path.join(d, "cert_noimg.pdf"))
        px = doc[0].get_pixmap(clip=fitz.Rect(276, 40, 336, 80))
        R.check("image deleted (area white)", min(px.pixel(px.width // 2, px.height // 2)[:3]) > 240)
        doc.close()
        import pdf_edit as pe
        R.check("no ghost image box after delete", pe.extract_blocks(os.path.join(d, "cert_noimg.pdf"), 0)["images"] == [])
        close_editor(page)
        # scanned page
        page.evaluate("p => PdfEdit.open(p)", scan_pdf)
        wait(page, "document.querySelector('.pe-overlay') && !document.querySelector('.pe-scan').classList.contains('hidden')")
        R.check("scanned page notice", "scanned" in page.text_content("#pe-hint"))
        page.click(".pe-bar [data-a=mode-add]")
        page.mouse.click(*pt(page, 0, 100, 100))
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        page.keyboard.type("On a scan")
        page.mouse.click(*pt(page, 0, 300, 500)); time.sleep(0.2)
        page.fill("#pe-name", "scan_text"); page.fill("#pe-dir", d)
        page.click("#pe-save")
        wait(page, "document.querySelector('#pe-notice') && !document.querySelector('#pe-notice').classList.contains('hidden')", timeout=30)
        doc = fitz.open(os.path.join(d, "scan_text.pdf"))
        R.check("text added on scanned page", "On a scan" in doc[0].get_text())
        doc.close()
        close_editor(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
