"""E2E: signing saves next to the signed PDF.

* signature pad → "Save as PNG…" asks for a name and saves
  '<folder of the PDF>\\signature\\<name>_signature.png' (folder created,
  never overwritten, the suffix is never doubled);
* opened from an image (no PDF) → the image's folder;
* Edit window (PDF Studio): after placing a signature, Save defaults to
  '<folder of the PDF>\\signature\\<stem>_signature.pdf', creates the folder
  and writes there — the stamp is in the PDF (PyMuPDF) and the text is still
  real, selectable text.

Every test file is made inside <scratch dir>; nothing is written elsewhere
(do NOT set SFM_SIGNATURE_DIR for this script — it overrides the folder).

    set QA_CDP_PORT=9371
    python qa/e2e/signature_png_e2e.py <scratch dir>
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from annotate_e2e import click, open_editor  # noqa: E402
from cdp import Results, app_page, close_overlays, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()
ASK = "document.activeElement && document.activeElement.matches('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text')"


def draw(page):
    cb = page.query_selector("#an-sig-canvas").bounding_box()
    page.mouse.move(cb["x"] + 80, cb["y"] + 120); page.mouse.down()
    for i in range(40):
        page.mouse.move(cb["x"] + 80 + i * 8, cb["y"] + 120 - 30 * ((i % 8) / 8))
    page.mouse.up()
    time.sleep(0.2)


def ask_open(page):
    return page.evaluate("!!document.querySelector('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text')")


def save_png(page, name):
    page.click("#an-sig-png")
    wait(page, ASK)
    page.keyboard.press("Control+a"); page.keyboard.type(name)
    page.keyboard.press("Enter"); time.sleep(0.8)


def listing(d):
    return os.listdir(d) if os.path.isdir(d) else "no folder"


def run(d):
    import pymupdf as fitz
    from PIL import Image
    assert not os.environ.get("SFM_SIGNATURE_DIR"), "unset SFM_SIGNATURE_DIR for this script"
    d = os.path.abspath(d)
    work = os.path.join(d, "sig_png_case")
    os.makedirs(work, exist_ok=True)
    f = make_all(work)
    pdf = f["multi"]                          # <work>\multi.pdf
    sigdir = os.path.join(work, "signature")
    imgdir = os.path.join(work, "img")
    os.makedirs(imgdir, exist_ok=True)
    img = shutil.copy2(f["png"], os.path.join(imgdir, "scan.png"))
    for p in (sigdir, os.path.join(imgdir, "signature")):
        assert not os.path.exists(p), f"leftover from an earlier run: {p} (use a fresh scratch dir)"

    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        close_overlays(page)
        page.evaluate("p => Studio.open(p, {tool: 'select'})", pdf)
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=20)
        page.click(".st-tool[data-id=signature]")
        wait(page, "document.querySelector('.an-sig-overlay #an-sig-canvas')")
        time.sleep(0.3)

        # ── Save as PNG ──────────────────────────────────────────────────────
        page.click("#an-sig-png"); time.sleep(0.3)
        R.check("empty pad: asks to draw first, no name box", not ask_open(page))
        draw(page)
        page.click("#an-sig-png")
        wait(page, ASK)
        hint = page.evaluate("document.querySelector('.modal-overlay.dlg-top:not(.an-sig-overlay) .field-hint').textContent")
        R.check("name box shows '<PDF folder>\\signature'", os.path.normcase(sigdir) in os.path.normcase(hint), hint)
        R.check("name box says the folder is created", "created" in hint, hint)
        R.check("name box mentions the _signature suffix", "_signature" in hint, hint)
        page.keyboard.press("Control+a"); page.keyboard.type("Test Sign")
        page.keyboard.press("Enter"); time.sleep(0.8)
        p1 = os.path.join(sigdir, "Test Sign_signature.png")
        R.check("Enter saves 'Test Sign_signature.png' in <PDF folder>\\signature", os.path.isfile(p1), listing(sigdir))
        R.check("pad stays open after saving (Enter did not 'Use' the signature)", page.evaluate("!!document.querySelector('.an-sig-overlay')"))
        if os.path.isfile(p1):
            with Image.open(p1) as im:
                R.check("PNG is transparent and cropped", im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0 and im.size[0] > 200, im.size)
        save_png(page, "Test Sign")
        R.check("same name again → 'Test Sign_signature (2).png' (never overwritten)",
                os.path.isfile(os.path.join(sigdir, "Test Sign_signature (2).png")), listing(sigdir))
        save_png(page, "Mine_signature")
        R.check("name ending in _signature → suffix not doubled", os.path.isfile(os.path.join(sigdir, "Mine_signature.png")), listing(sigdir))
        page.click("#an-sig-png")
        wait(page, ASK)
        page.keyboard.press("Escape"); time.sleep(0.4)
        R.check("Esc closes only the name box", not ask_open(page) and page.evaluate("!!document.querySelector('.an-sig-overlay')"))
        # typed signature: the name defaults to what was typed
        page.click(".an-sig-overlay [data-mode=type]"); time.sleep(0.2)
        page.fill("#an-sig-type", "Shafiq Ahmed")
        page.click("#an-sig-png")
        wait(page, ASK)
        v = page.evaluate("document.querySelector('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text').value")
        R.check("typed signature: name defaults to the typed name", v == "Shafiq Ahmed", v)
        page.keyboard.press("Enter"); time.sleep(0.8)
        R.check("typed signature saved as 'Shafiq Ahmed_signature.png'", os.path.isfile(os.path.join(sigdir, "Shafiq Ahmed_signature.png")), listing(sigdir))
        R.check("nothing written next to the scratch dir's parent / drive root",
                not os.path.exists(os.path.join(os.path.splitdrive(d)[0] + os.sep, "Signature", "Test Sign_signature.png")))

        # ── place the signature, then Save → <PDF folder>\signature\multi_signature.pdf
        page.click(".an-sig-overlay [data-mode=draw]"); time.sleep(0.2)
        page.click("#an-sig-ok")
        wait(page, "!document.querySelector('.an-sig-overlay')")
        time.sleep(0.4)
        click(page, 0, (150, 700))
        time.sleep(0.4)
        st = page.evaluate("Studio._state()")
        R.check("signature placed on the page", st and st["dirty"], st)
        page.click("#st-save")
        wait(page, "document.querySelector('#st-sheet #st-name')")
        time.sleep(0.4)
        name = page.input_value("#st-sheet #st-name")
        folder = page.input_value("#st-sheet #st-dir")
        R.check("Save defaults to name 'multi_signature'", name == "multi_signature", name)
        R.check("Save defaults to '<PDF folder>\\signature'", os.path.normcase(folder) == os.path.normcase(sigdir), folder)
        note = page.evaluate("(document.querySelector('#st-sign-note') || {}).textContent || ''")
        R.check("sheet shows the target folder", os.path.normcase(sigdir) in os.path.normcase(note), note)
        page.click("#st-go")
        out = os.path.join(sigdir, "multi_signature.pdf")
        try:
            wait(page, f"(() => {{ const s = document.querySelector('#st-sheet'); return s && s.classList.contains('hidden'); }})()", timeout=20)
        except TimeoutError:
            pass
        time.sleep(0.5)
        R.check("saved to <PDF folder>\\signature\\multi_signature.pdf", os.path.isfile(out), listing(sigdir))
        st = page.evaluate("Studio._state()")
        pill = page.text_content("#st-ver")
        R.check("after Save nothing is left unsaved (and no pad popped up)",
                st and not st["dirty"] and st["at"] == st["savedAt"] and "unsaved" not in pill
                and not page.evaluate("!!document.querySelector('.an-sig-overlay')"), (pill, st))
        if os.path.isfile(out):
            with fitz.open(out) as doc:
                kinds = [a.type for a in doc[0].annots()]
                stamps = [k for k in kinds if k[0] == fitz.PDF_ANNOT_STAMP]
                text = doc[0].get_text()
            R.check("signature stamp is in the saved PDF", len(stamps) >= 1, kinds)
            R.check("text is still selectable", "Page 1 heading text" in text, text[:80])
        # second save never overwrites
        page.click("#st-save")
        wait(page, "document.querySelector('#st-sheet #st-name')")
        time.sleep(0.3)
        page.click("#st-go"); time.sleep(1.5)
        R.check("saving again → 'multi_signature (2).pdf'", os.path.isfile(os.path.join(sigdir, "multi_signature (2).pdf")), listing(sigdir))
        # the signature now lives in a committed version: still a signed save
        page.click(".st-tool[data-id=read]")
        wait(page, "Studio._state().itemId === 'read' && document.querySelector('#st-busy').classList.contains('hidden')", timeout=20)
        page.click("#st-save")
        wait(page, "document.querySelector('#st-sheet #st-name')")
        time.sleep(0.3)
        R.check("after switching tool: default still 'multi_signature' in signature folder",
                page.input_value("#st-sheet #st-name") == "multi_signature"
                and os.path.normcase(page.input_value("#st-sheet #st-dir")) == os.path.normcase(sigdir),
                page.input_value("#st-sheet #st-name"))
        page.click("#st-cancel"); time.sleep(0.2)
        with fitz.open(pdf) as doc:
            R.check("original PDF untouched (no signature stamp)", not list(doc[0].annots()))
        page.click("#st-x"); time.sleep(0.6)
        close_overlays(page)

        # ── unsigned Studio save keeps '<stem>_edited' next to the PDF ─────────
        page.evaluate("p => Studio.open(p, {tool: 'select'})", pdf)
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=20)
        page.click("#st-save")
        wait(page, "document.querySelector('#st-sheet #st-name')")
        time.sleep(0.3)
        R.check("no signature → default stays '<stem>_edited' next to the PDF",
                page.input_value("#st-sheet #st-name") == "multi_edited"
                and os.path.normcase(page.input_value("#st-sheet #st-dir")) == os.path.normcase(work)
                and not page.evaluate("!!document.querySelector('#st-sign-note')"),
                (page.input_value("#st-sheet #st-name"), page.input_value("#st-sheet #st-dir")))
        page.click("#st-cancel"); time.sleep(0.2)
        page.click("#st-x"); time.sleep(0.6)
        close_overlays(page)

        # ── opened from an image → the image's folder ─────────────────────────
        open_editor(page, img)
        page.click(".an-tool[data-tool=signature]")
        wait(page, "document.querySelector('.an-sig-overlay #an-sig-canvas')")
        time.sleep(0.3)
        draw(page)
        save_png(page, "Img")
        R.check("image: saved in '<image folder>\\signature\\Img_signature.png'",
                os.path.isfile(os.path.join(imgdir, "signature", "Img_signature.png")), listing(os.path.join(imgdir, "signature")))
        page.click("#an-sig-cancel"); time.sleep(0.3)
        page.evaluate("() => { const x = document.querySelector('#an-x'); if (x) x.click(); }")
        time.sleep(0.5)
        close_overlays(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
