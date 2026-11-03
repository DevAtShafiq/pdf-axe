"""E2E: "Sign" and "Add photo" in the right-hand Tools (details pane), the
file right-click menu and Ctrl+Shift+S.

    set QA_CDP_PORT=9372
    python qa/e2e/right_tools_e2e.py <scratch dir>

Checks:
* a selected PDF shows Sign + Add photo; an image shows Sign only; a folder
  or another file shows neither;
* Sign opens Edit on Fill & sign > Signature with the signature pad already
  open; drawing with the mouse, Use signature, a click on the page places it;
  Save as a new file; PyMuPDF sees the signature in the saved PDF;
* the context menu "Sign..." and Ctrl+Shift+S open the pad the same way;
* Add photo opens the Photo tool with the picker already called (the native
  picker is stubbed); dragging a box places the photo; saved and verified;
* Sign on an image opens Annotate with the pad open;
* a screenshot of the details pane is written to <scratch>/details_pane.png.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from annotate_e2e import drag, click  # noqa: E402
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402
from studio_e2e import force_close, select, studio_open, state  # noqa: E402

R = Results()


def visible(page, sel):
    return page.evaluate("s => { const b = document.querySelector(s); return !!b && b.offsetParent !== null; }", sel)


def pad_open(page, timeout=20):
    try:
        wait(page, "!!document.querySelector('.an-sig-overlay #an-sig-canvas')", timeout=timeout)
        return True
    except TimeoutError:
        return False


def draw_signature(page):
    box = page.eval_on_selector("#an-sig-canvas", "c => { const r = c.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }")
    x0, y0, w, h = box
    import math
    page.mouse.move(x0 + w * 0.2, y0 + h * 0.5)
    page.mouse.down()
    for i in range(40):
        t = i / 39
        page.mouse.move(x0 + w * (0.2 + 0.6 * t), y0 + h * (0.5 + 0.2 * math.sin(t * 12)))
        time.sleep(0.005)
    page.mouse.up()
    time.sleep(0.2)


def use_signature(page):
    page.evaluate("() => { const c = document.querySelector('#an-sig-rem'); if (c) c.checked = false; }")  # don't remember test ink
    page.click("#an-sig-ok")
    wait(page, "!document.querySelector('.an-sig-overlay')", timeout=10)
    time.sleep(0.3)


def cancel_pad(page):
    if page.evaluate("!!document.querySelector('.an-sig-overlay')"):
        page.click("#an-sig-cancel")
        time.sleep(0.3)


def count(page):
    return page.evaluate("document.querySelectorAll('.an-anns .an-a').length")


def studio_save(page, d, name):
    page.click("#st-save")
    wait(page, "document.querySelector('#st-go')")
    page.fill("#st-name", name)
    page.fill("#st-dir", d)
    page.click("#st-go")
    wait(page, "document.querySelector('#st-sheet').classList.contains('hidden')", timeout=30)
    time.sleep(0.5)
    return os.path.join(d, name + ".pdf")


def page_images(path):
    import pymupdf as fitz
    with fitz.open(path) as doc:
        pg = doc[0]
        annots = [a.type[1] for a in pg.annots()]
        imgs = len(pg.get_images(full=True))
        # stamp annotations with an appearance image count too
        return annots, imgs


def right_click_row(page, path):
    row = page.evaluate("""p => { const el = [...document.querySelectorAll('[data-path]')].find(e => e.dataset.path.toLowerCase() === p.toLowerCase());
                              if (!el) return null; const r = el.getBoundingClientRect(); return [r.left + 30, r.top + r.height/2]; }""", path)
    if not row:
        return False
    page.mouse.click(row[0], row[1], button="right")
    time.sleep(0.4)
    return True


def run(d):
    f = make_all(d)
    src = f["multi"]
    sub = os.path.join(d, "a_folder")
    os.makedirs(sub, exist_ok=True)
    txt = os.path.join(d, "notes.txt")
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("hello")
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        force_close(page)

        # ── which files get the buttons ──────────────────────────────────
        select(page, d, src)
        R.check("PDF: Sign visible", visible(page, "#qa-sign"))
        R.check("PDF: Add photo visible", visible(page, "#qa-add-photo"))
        tip = page.get_attribute("#qa-sign", "title") or ""
        R.check("Sign tooltip shows Ctrl+Shift+S", "Ctrl+Shift+S" in tip, tip)
        R.check("Sign/Add photo are the first Tools", page.evaluate(
            "[...document.querySelectorAll('#quick-actions .qa-grid .qa-btn')].slice(0, 2).map(b => b.id).join(',')") == "qa-sign,qa-add-photo")
        page.locator("#details-panel, #pane-details, .details-pane, #quick-actions").first.screenshot(path=os.path.join(d, "details_pane.png"))
        try:
            pane = page.evaluate("() => { const q = document.querySelector('#quick-actions'); let e = q; while (e && e.parentElement && e.parentElement.offsetWidth < 500) e = e.parentElement; return e.id || e.className; }")
            print("details pane container:", pane)
        except Exception:
            pass
        select(page, d, sub)
        R.check("folder: no Sign / Add photo", not visible(page, "#qa-sign") and not visible(page, "#qa-add-photo"))
        select(page, d, txt)
        R.check("text file: no Sign / Add photo", not visible(page, "#qa-sign") and not visible(page, "#qa-add-photo"))
        select(page, d, f["png"])
        R.check("image: Sign visible, Add photo hidden", visible(page, "#qa-sign") and not visible(page, "#qa-add-photo"))

        # ── Sign from the details pane → place → save → verify ───────────
        select(page, d, src)
        page.click("#qa-sign")
        ok = studio_open(page)
        R.check("Sign opens Edit", ok)
        R.check("Sign opens the pad at once", pad_open(page))
        R.check("Edit is on Fill & sign > Signature", state(page)["itemId"] == "signature", state(page))
        R.check("rail shows Signature active", page.evaluate(
            "!!document.querySelector('.st-tool[data-id=signature].active, .st-tool[data-id=signature][aria-pressed=true], .st-tool[data-id=signature].is-active')"))
        draw_signature(page)
        use_signature(page)
        n0 = count(page)
        click(page, 0, (300, 600))
        time.sleep(0.4)
        R.check("click places the signature", count(page) == n0 + 1, (n0, count(page)))
        out = studio_save(page, d, "signed_out")
        R.check("saved signed PDF", os.path.isfile(out), out)
        if os.path.isfile(out):
            annots, imgs = page_images(out)
            R.check("saved PDF holds the signature (PyMuPDF)", annots or imgs, (annots, imgs))
        force_close(page)

        # ── context menu ──────────────────────────────────────────────────
        select(page, d, src)
        if right_click_row(page, src):
            labels = page.evaluate("[...document.querySelectorAll('.context-menu *')].filter(e => !e.children.length).map(e => e.textContent.trim()).filter(Boolean)")
            R.check("context menu has Sign… and Add Photo…", "Sign…" in labels and "Add Photo…" in labels, labels[:14])
            i_edit = labels.index("Edit…") if "Edit…" in labels else -1
            R.check("Sign… sits next to Edit…", i_edit >= 0 and "Sign…" in labels[i_edit:i_edit + 4], labels[:14])
            it = page.query_selector('.context-menu >> text="Sign…"')
            if it:
                it.click()
                R.check("context Sign… opens Edit + pad", studio_open(page) and pad_open(page))
                cancel_pad(page)
            else:
                R.check("context Sign… item", False)
                page.keyboard.press("Escape")
        force_close(page)
        # folder: none
        if right_click_row(page, sub):
            labels = page.evaluate("[...document.querySelectorAll('.context-menu *')].filter(e => !e.children.length).map(e => e.textContent.trim())")
            R.check("folder context menu: no Sign", "Sign…" not in labels)
            page.keyboard.press("Escape")
            time.sleep(0.2)

        # ── Ctrl+Shift+S ─────────────────────────────────────────────────
        select(page, d, src)
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.keyboard.press("Control+Shift+S")
        R.check("Ctrl+Shift+S opens Edit + pad", studio_open(page) and pad_open(page))
        cancel_pad(page)
        # Ctrl+Shift+S inside Edit = Save (Studio keeps its own Ctrl+S)
        force_close(page)

        # ── Add photo (native picker stubbed) ────────────────────────────
        page.evaluate("p => { window.__pick = 0; SFM.__origPick = SFM.__origPick || SFM.annotPickPhoto; SFM.annotPickPhoto = async () => { window.__pick++; return { ok: true, path: p }; }; }", f["photo"])
        select(page, d, src)
        page.click("#qa-add-photo")
        R.check("Add photo opens Edit", studio_open(page))
        try:
            wait(page, "window.__pick > 0", timeout=20)
            picked = True
        except TimeoutError:
            picked = False
        R.check("Add photo calls the picker at once", picked)
        R.check("Edit is on Fill & sign > Photo", state(page)["itemId"] == "photo", state(page))
        try:
            wait(page, "document.querySelector('.an-editor') && document.querySelector('.an-editor').dataset.tool === 'photo'", timeout=10)
            R.check("photo tool armed", True)
        except TimeoutError:
            R.check("photo tool armed", False)
        time.sleep(0.5)
        n0 = count(page)
        drag(page, 0, (380, 120), (500, 280))
        time.sleep(0.5)
        R.check("drag places the photo", count(page) == n0 + 1, (n0, count(page)))
        out = studio_save(page, d, "photo_out")
        R.check("saved PDF with photo", os.path.isfile(out))
        if os.path.isfile(out):
            annots, imgs = page_images(out)
            R.check("saved PDF holds the photo (PyMuPDF)", annots or imgs, (annots, imgs))
        force_close(page)
        page.evaluate("() => { if (SFM.__origPick) SFM.annotPickPhoto = SFM.__origPick; }")

        # ── Sign on an image → Annotate with the pad ─────────────────────
        select(page, d, f["png"])
        page.click("#qa-sign")
        R.check("image Sign opens Annotate + pad", pad_open(page) and page.evaluate("!!document.querySelector('.an-overlay')"))
        cancel_pad(page)
        for _ in range(3):
            if not page.evaluate("!!document.querySelector('.an-overlay')"):
                break
            page.evaluate("() => { const x = document.querySelector('#an-x'); if (x) x.click(); }")
            time.sleep(0.3)
            page.evaluate("() => { const bs = document.querySelectorAll('.modal-overlay.dlg-top button'); if (bs.length) bs[bs.length - 1].click(); }")
            time.sleep(0.3)

        select(page, d, src)
        page.locator("#quick-actions").screenshot(path=os.path.join(d, "details_tools.png"))
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "qa_right_tools")))
