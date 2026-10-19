"""E2E: Edit PDF — every object on the page is editable (pictures, shapes,
lines, table grids), driven with real mouse / keyboard events over CDP.

Start your own app instance first (never the owner's):

    set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9362
    set PYTHONUTF8=1
    python main_webview.py

    set QA_CDP_PORT=9362
    python qa/e2e/edit_objects_e2e.py <scratch dir>

Checks: hover outline, click selects (handles), drag moves, corner handle
resizes keeping proportions, arrows nudge (one undo step), Delete removes a
table grid, line / fill colour of a shape, rotate, crop, add a picture, text
blocks still editable, one Undo per change, Studio Save → the output is
verified with PyMuPDF (positions, colours, text selectable, form field kept).
Screenshots go to <scratch dir>/shots/.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402

from test_pdf_edit_objects import TEXTS, _make  # noqa: E402

R = Results()
SHOTS = None


def shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name + ".png"))


def st(page):
    return page.evaluate("PdfEdit._state()")


def pt(page, x, y, p=0):
    """Page units → screen point (scrolls the page into view when needed)."""
    return page.evaluate("""([p, x, y]) => {
        const el = document.querySelector(`.pe-page[data-p="${p}"]`);
        const view = el.closest('.an-view');
        const W = +getComputedStyle(el).width.replace('px', '');
        const pw = el.getBoundingClientRect().width;
        let r = el.getBoundingClientRect(), vr = view.getBoundingClientRect();
        const k = r.width / (PdfEdit._state() ? PdfEdit._state().pageW : 612);
        let cx = r.left + x * k, cy = r.top + y * k;
        if (cy < vr.top + 40 || cy > vr.bottom - 40) {
            view.scrollTop += cy - (vr.top + vr.height / 2);
            r = el.getBoundingClientRect();
            cx = r.left + x * k; cy = r.top + y * k;
        }
        return [cx, cy];
    }""", [p, x, y])


def drag(page, a, b, steps=10, shift=False):
    x0, y0 = pt(page, *a)
    x1, y1 = pt(page, *b)
    x0, y0 = pt(page, *a)
    page.mouse.move(x0, y0)
    time.sleep(0.05)
    if shift:
        page.keyboard.down("Shift")
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(0.02)
    page.mouse.up()
    if shift:
        page.keyboard.up("Shift")
    time.sleep(0.3)


def drag_screen(page, x0, y0, x1, y1, steps=10):
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(0.02)
    page.mouse.up()
    time.sleep(0.3)


def click(page, x, y):
    cx, cy = pt(page, x, y)
    page.mouse.click(cx, cy)
    time.sleep(0.25)


def handle(page, hd):
    return page.evaluate("""hd => { const h = document.querySelector(`.pe-selbox .pe-oh-${hd}`);
        if (!h) return null; const r = h.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }""", hd)


def key(page, k, n=1):
    for _ in range(n):
        page.keyboard.press(k)
        time.sleep(0.05)
    time.sleep(0.2)


def sel_kind(page):
    s = st(page)
    return s and s["sel"] and s["sel"]["kind"]


def obj_model(page):
    return st(page)["model"]["objs"]


def settle(page, t=8):
    """Wait until the page picture shows every pending object edit."""
    try:
        wait(page, "PdfEdit._state() && PdfEdit._state().bgInSync", timeout=t)
        return True
    except TimeoutError:
        return False


def run(d):
    global SHOTS
    SHOTS = os.path.join(d, "shots")
    os.makedirs(SHOTS, exist_ok=True)
    src = _make(os.path.join(d, "objects.pdf"))
    logo_png = os.path.join(d, "logo.png")
    im = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    for x in range(200):
        for y in range(100):
            if (x - 100) ** 2 / 100 ** 2 + (y - 50) ** 2 / 50 ** 2 <= 1:
                im.putpixel((x, y), (250, 140, 0, 255))
    im.save(logo_png)

    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append("console: " + m.text))
        page.evaluate("""() => document.querySelectorAll('.st-overlay, .an-overlay').forEach(o => o.remove())""")
        page.evaluate("p => Studio.open(p, {tool: 'edit'})", src)
        wait(page, "document.querySelectorAll('.pe-obj').length >= 5", timeout=20)
        time.sleep(1.0)
        shot(page, "01_open")
        n = page.evaluate("document.querySelectorAll('.pe-obj').length")
        R.check("objects shown on the page", n == 5, n)
        R.check("rail item renamed", page.text_content(".st-tool[data-id=edit]").strip() == "Edit")

        # ── hover + click selects a picture ───────────────────────────────
        x, y = pt(page, 120, 100)
        page.mouse.move(x, y)
        time.sleep(0.3)
        hov = page.evaluate("[...document.querySelectorAll('.pe-obj')].some(o => o.matches(':hover'))")
        R.check("hover marks the object", hov)
        shot(page, "02_hover")
        page.mouse.click(x, y)
        time.sleep(0.3)
        R.check("click selects the picture", sel_kind(page) == "obj", st(page)["sel"])
        R.check("8 handles", page.evaluate("document.querySelectorAll('.pe-selbox .pe-oh').length") == 8)
        R.check("object toolbar says Picture", page.text_content("#pe-objkind") == "Picture", page.text_content("#pe-objkind"))
        shot(page, "03_selected")
        pic_key = st(page)["sel"]["key"]

        # ── drag moves it (one undo step) ─────────────────────────────────
        u0 = st(page)["undo"]
        drag(page, (120, 100), (420, 280))
        m = obj_model(page).get(pic_key, {})
        R.check("drag moved the picture", m.get("bbox") and abs(m["bbox"][0] - 360) < 3 and abs(m["bbox"][1] - 240) < 3, m)
        R.check("one undo step for the drag", st(page)["undo"] == u0 + 1, st(page)["undo"] - u0)
        R.check("page picture re-rendered without the old copy", settle(page))
        shot(page, "04_moved")

        # ── corner handle resizes keeping proportions ─────────────────────
        h = handle(page, "se")
        if h:
            k = page.evaluate("document.querySelector('.pe-page').getBoundingClientRect().width / 612")
            drag_screen(page, h[0], h[1], h[0] + 60 * k, h[1] + 10 * k)
        m = obj_model(page).get(pic_key, {})
        b = m.get("bbox") or [0, 0, 0, 0]
        R.check("corner handle resized", abs((b[2] - b[0]) - 180) < 3, b)
        R.check("…keeping the proportions", abs((b[2] - b[0]) / max(1, b[3] - b[1]) - 1.5) < 0.03, b)
        settle(page)
        shot(page, "05_resized")

        # ── arrows nudge, one undo step for the series ────────────────────
        u0 = st(page)["undo"]
        page.focus("#pe-view")
        key(page, "ArrowRight", 3)
        key(page, "Shift+ArrowDown")
        b2 = obj_model(page)[pic_key]["bbox"]
        R.check("arrows nudge 1 pt, Shift = 10 pt", abs(b2[0] - b[0] - 3) < 0.01 and abs(b2[1] - b[1] - 10) < 0.01, (b, b2))
        R.check("nudges in a row = one undo step", st(page)["undo"] == u0 + 1, st(page)["undo"] - u0)
        page.keyboard.press("Control+z")
        time.sleep(0.3)
        b3 = obj_model(page)[pic_key]["bbox"]
        R.check("Ctrl+Z undoes the nudges", all(abs(p - q) < 0.01 for p, q in zip(b3, b)), b3)
        key(page, "Escape")
        R.check("Esc deselects", st(page)["sel"] is None)

        # ── shape: fill / line colour, line width ─────────────────────────
        click(page, 360, 130)
        R.check("click selects the yellow box", sel_kind(page) == "obj" and page.text_content("#pe-objkind") == "Shape",
                page.text_content("#pe-objkind"))
        box_key = st(page)["sel"]["key"]
        page.evaluate("""() => { const i = document.querySelector('#pe-ofill'); i.value = '#ff0000'; i.dispatchEvent(new Event('change', {bubbles: true})); }""")
        page.evaluate("""() => { const i = document.querySelector('#pe-ostroke'); i.value = '#00aa00'; i.dispatchEvent(new Event('change', {bubbles: true})); }""")
        page.fill("#pe-owidth", "5")
        page.press("#pe-owidth", "Enter")
        page.focus("#pe-view")
        time.sleep(0.3)
        m = obj_model(page).get(box_key, {})
        R.check("fill / line / width in the model", m.get("fill") == "#ff0000" and m.get("stroke") == "#00aa00" and m.get("width") == 5, m)
        settle(page)
        shot(page, "06_recoloured")
        px = page.evaluate("""() => new Promise(res => { const img = document.querySelector('.pe-page .an-bg');
            const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
            const g = c.getContext('2d'); g.drawImage(img, 0, 0); const k = img.naturalWidth / 612;
            res([...g.getImageData(360 * k, 130 * k, 1, 1).data]); })""")
        R.check("page picture shows the new fill", px[0] > 200 and px[1] < 60, px)

        # ── table grid: click a line, Delete ──────────────────────────────
        click(page, 120, 450)
        R.check("clicking a grid line selects the grid", page.text_content("#pe-objkind").startswith("Graphic"), page.text_content("#pe-objkind"))
        grid_key = st(page)["sel"]["key"]
        click(page, 120, 475)        # inside an empty cell: not part of the grid
        R.check("an empty table cell does not select the grid", not (st(page)["sel"] and st(page)["sel"]["key"] == grid_key))
        click(page, 120, 450)
        key(page, "Delete")
        R.check("Delete removes the grid", obj_model(page).get(grid_key, {}).get("delete") is True)
        settle(page)
        shot(page, "07_grid_deleted")

        # ── logo: Shift-drag moves straight ───────────────────────────────
        click(page, 110, 330)
        logo_key = st(page)["sel"]["key"]
        drag(page, (110, 330), (260, 338), shift=True)
        b = obj_model(page).get(logo_key, {}).get("bbox")
        R.check("Shift-drag keeps the line straight", b and abs(b[1] - 312) < 0.6 and abs(b[0] - 242) < 3, b)

        # ── rotate the clipped (Word-like) picture ────────────────────────
        click(page, 450, 470)
        clip_key = st(page)["sel"]["key"]
        key(page, "r")
        m = obj_model(page).get(clip_key, {})
        R.check("R rotates 90°", m.get("rotate") == 90 and abs((m["bbox"][2] - m["bbox"][0]) - 80) < 0.5, m)
        settle(page)
        shot(page, "08_rotated")
        key(page, "Escape")

        # ── crop the moved red picture ────────────────────────────────────
        pb = obj_model(page)[pic_key]["bbox"]
        click(page, (pb[0] + pb[2]) / 2, (pb[1] + pb[3]) / 2)
        key(page, "c")
        R.check("crop frame shown", page.evaluate("!!document.querySelector('.pe-selbox.is-crop')"))
        h = handle(page, "e")
        k = page.evaluate("document.querySelector('.pe-page').getBoundingClientRect().width / 612")
        if h:
            drag_screen(page, h[0], h[1], h[0] - 60 * k, h[1])
        shot(page, "09_cropping")
        key(page, "Enter")
        m = obj_model(page)[pic_key]
        R.check("Enter applies the crop", m.get("crop") and abs((m["bbox"][2] - m["bbox"][0]) - (pb[2] - pb[0] - 60)) < 2, m)
        settle(page)
        shot(page, "10_cropped")

        # ── add a picture (file dialog stubbed) ───────────────────────────
        page.evaluate("p => { window.__pick = SFM.annotPickPhoto; SFM.annotPickPhoto = async () => ({ok: true, path: p}); }", logo_png)
        page.click("[data-a=add-image]")
        wait(page, "PdfEdit._state().model.addImgs.length === 1", timeout=10)
        page.evaluate("() => { SFM.annotPickPhoto = window.__pick; }")
        time.sleep(0.4)
        R.check("Add image places a selected picture", sel_kind(page) == "aimg")
        a = st(page)["model"]["addImgs"][0]
        tgt = [440, 180]
        drag(page, ((a["rect"][0] + a["rect"][2]) / 2, (a["rect"][1] + a["rect"][3]) / 2), tgt)
        a = st(page)["model"]["addImgs"][0]
        R.check("the new picture can be dragged", abs((a["rect"][0] + a["rect"][2]) / 2 - tgt[0]) < 3, a["rect"])
        shot(page, "11_added")

        # ── text is still editable next to the objects ────────────────────
        click(page, 70, 36)
        R.check("text block still opens for editing", st(page)["editing"])
        page.keyboard.press("End")
        page.keyboard.type(" OK")
        key(page, "Escape") if False else None
        page.mouse.click(*pt(page, 580, 760))
        time.sleep(0.3)

        # ── Undo / Redo of an object change ───────────────────────────────
        u = st(page)["undo"]
        page.focus("#pe-view")
        page.keyboard.press("Control+z")
        time.sleep(0.2)
        page.keyboard.press("Control+y")
        time.sleep(0.2)
        R.check("undo / redo keeps the history", st(page)["undo"] == u)

        # ── Save through the Studio ───────────────────────────────────────
        page.click("#st-save")
        wait(page, "!!document.querySelector('#st-go')", timeout=5)
        page.fill("#st-name", "objects_out")
        page.fill("#st-dir", d)
        page.click("#st-go")
        out = os.path.join(d, "objects_out.pdf")
        try:
            wait(page, f"Studio._state() && Studio._state().savedAt > 0", timeout=30)
        except TimeoutError:
            pass
        time.sleep(0.5)
        shot(page, "12_saved")
        R.check("saved", os.path.isfile(out), out)
        if os.path.isfile(out):
            verify(out)
        # Studio Undo goes back to the original version, then Redo
        R.check("no JS errors", not errors, errors[:3])
        page.evaluate("() => { const b = document.querySelector('#st-x'); b && b.click(); }")
        time.sleep(0.5)


def verify(out):
    import pdf_edit as pe
    doc = fitz.open(out)
    pg = doc[0]
    t = pg.get_text("text").replace(" ", " ")
    R.check("output: text still selectable", all(s in t for s in TEXTS[1:]), t[:120])
    R.check("output: edited text", "Neighbour text stays here OK" in t)
    ws = list(pg.widgets())
    R.check("output: form field kept", len(ws) == 1 and ws[0].field_value == "Rahim")
    objs = pe.extract_objects(out, 0)["objects"]
    imgs = [o for o in objs if o["kind"] == "image"]
    R.check("output: 3 pictures (moved, rotated, added)", len(imgs) == 3, [o["bbox"] for o in imgs])
    reds = [x for x in pg.get_drawings() if x.get("fill") and x["fill"][0] > 0.95 and x["fill"][1] < 0.05]
    R.check("output: recoloured box", len(reds) == 1 and abs(reds[0]["width"] - 5) < 0.3 and reds[0]["color"][1] > 0.6,
            reds and (reds[0]["fill"], reds[0]["color"], reds[0]["width"]))
    R.check("output: grid deleted", not any(o["kind"] == "path" and o["paths"] == 6 for o in objs))
    logo = [o for o in objs if o["kind"] == "path" and o["fill"] == "#009900"]
    R.check("output: logo moved straight", logo and abs(logo[0]["bbox"][1] - 312) < 1 and logo[0]["bbox"][0] > 230,
            logo and logo[0]["bbox"])
    infos = pg.get_image_info()
    rot = [i for i in infos if abs(i["transform"][0]) < 1e-3]
    R.check("output: rotated picture", len(rot) == 1, [i["transform"] for i in infos])
    doc.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run(os.path.abspath(sys.argv[1]))
    sys.exit(R.summary())
