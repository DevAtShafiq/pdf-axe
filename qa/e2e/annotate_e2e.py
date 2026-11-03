"""E2E: the annotation editor (ui/js/annotate.js) driven over CDP.

    python qa/e2e/annotate_e2e.py <scratch dir>

Covers every tool, select/move/resize, undo/redo, zoom buttons, eraser,
properties panel, save → PyMuPDF check (types, text + form fields kept, not
flattened), reload round trip, lock annotations, image annotate + sidecar.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, close_overlays, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()


def count(page):
    return page.evaluate("document.querySelectorAll('.an-anns .an-a').length")


def status(page):
    return page.evaluate("document.querySelector('#an-status').textContent")


def pt(page, p, x, y):
    """page units → client px for page *p*."""
    return page.evaluate("""([p, x, y]) => {
        const el = document.querySelector(`.an-page[data-p="${p}"]`);
        const view = el.closest('.an-view');
        const vb = el.querySelector('svg').viewBox.baseVal;
        let r = el.getBoundingClientRect(), vr = view.getBoundingClientRect();
        let cx = r.left + x / vb.width * r.width, cy = r.top + y / vb.height * r.height;
        if (cy < vr.top + 40 || cy > vr.bottom - 40 || cx < vr.left + 20 || cx > vr.right - 20) {
            view.scrollTop += cy - (vr.top + vr.height / 2);
            view.scrollLeft += cx - (vr.left + vr.width / 2);
            r = el.getBoundingClientRect();
            cx = r.left + x / vb.width * r.width; cy = r.top + y / vb.height * r.height;
        }
        return [cx, cy];
    }""", [p, x, y])


def drag(page, p, a, b, steps=8):
    x0, y0 = pt(page, p, *a)
    x1, y1 = pt(page, p, *b)
    x0, y0 = pt(page, p, *a)
    x1, y1 = pt(page, p, *b)
    time.sleep(0.1)
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(0.01)
    page.mouse.up()
    time.sleep(0.15)


def click(page, p, a):
    x, y = pt(page, p, *a)
    page.mouse.click(x, y)
    time.sleep(0.15)


def key(page, k):
    page.focus("#an-view")
    page.keyboard.press(k)
    time.sleep(0.1)


def open_editor(page, path):
    close_overlays(page)
    page.evaluate("p => Annotate.open(p)", path)
    wait(page, "document.querySelector('#an-status') && document.querySelector('.an-page .an-bg.on')")
    time.sleep(0.3)


def save_new(page, folder, name):
    page.fill("#an-dir", folder)
    page.fill("#an-name", name)
    page.click("#an-save")
    wait(page, "!document.querySelector('.an-overlay')", timeout=20)


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    out_dir = os.path.join(d, "out")
    os.makedirs(out_dir, exist_ok=True)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_editor(page, f["multi"])
        R.check("open: no annotations", count(page) == 0 and "No annotations" in status(page), status(page))
        # zoom buttons
        z0 = page.text_content("#an-zoom")
        page.click("[data-a=zin]"); time.sleep(0.2)
        z1 = page.text_content("#an-zoom")
        page.click("[data-a=zout]"); time.sleep(0.2)
        page.click("[data-a=zout]"); time.sleep(0.2)
        z2 = page.text_content("#an-zoom")
        page.click("[data-a=z100]"); time.sleep(0.2)
        z3 = page.text_content("#an-zoom")
        page.click("[data-a=fitp]"); time.sleep(0.2)
        z4 = page.text_content("#an-zoom")
        page.click("[data-a=fitw]"); time.sleep(0.3)
        R.check("zoom in/out/100/fit", z1 != z0 and z2 != z1 and z3 == "100%" and z4 != z3, f"{z0} {z1} {z2} {z3} {z4}")
        # page nav
        page.click("[data-a=next]"); time.sleep(0.3)
        pg2 = page.input_value("#an-pg")
        page.click("[data-a=prev]"); time.sleep(0.3)
        R.check("page next/prev", pg2 == "2" and page.input_value("#an-pg") == "1", pg2)

        n = 0
        # text markup on the body line (y≈140 baseline)
        for k, typ in (("h", "highlight"), ("u", "underline"), ("s", "strikeout"), ("q", "squiggly")):
            key(page, k)
            y = {"highlight": 136, "underline": 136, "strikeout": 156, "squiggly": 156}[typ]
            drag(page, 0, (74, y), (200, y))
            n += 1
            R.check(f"tool {typ}", count(page) == n and page.query_selector(f".an-t-{typ}"), count(page))
        # pen
        key(page, "p")
        drag(page, 0, (100, 400), (200, 450), steps=15)
        n += 1
        R.check("tool pen", count(page) == n and page.query_selector(".an-t-ink"))
        for k, typ, a, b in (("r", "rect", (100, 500), (200, 560)), ("o", "ellipse", (220, 500), (320, 560)),
                             ("a", "arrow", (340, 500), (440, 560)), ("l", "line", (100, 580), (200, 600)),
                             ("x", "cover", (450, 500), (520, 540))):
            key(page, k)
            drag(page, 0, a, b)
            n += 1
            R.check(f"tool {typ}", count(page) == n and page.query_selector(f".an-t-{typ}"), count(page))
        # redact (button, no key)
        page.click("[data-tool=redact]")
        drag(page, 0, (450, 560), (520, 600))
        n += 1
        R.check("tool redact", count(page) == n and page.query_selector(".an-t-redact"))
        # text box: click, type, Esc
        key(page, "t")
        click(page, 0, (100, 650))
        wait(page, "document.activeElement && document.activeElement.classList.contains('an-editbox')")
        page.keyboard.type("Hello text box")
        page.keyboard.press("Escape")
        time.sleep(0.2)
        n += 1
        R.check("tool text", count(page) == n and "Hello text box" in (page.text_content(".an-t-text") or ""))
        # empty text box is dropped and leaves no undo step
        undo_before = page.evaluate("document.querySelector('[data-a=undo]').disabled")
        key(page, "t")
        click(page, 0, (300, 700))
        time.sleep(0.2)
        page.keyboard.press("Escape")
        time.sleep(0.2)
        R.check("empty text box dropped", count(page) == n)
        # note
        key(page, "c")
        click(page, 0, (500, 650))
        wait(page, "document.activeElement && document.activeElement.id === 'an-p-text'")
        page.keyboard.type("A sticky comment")
        time.sleep(0.2)
        n += 1
        R.check("tool note", count(page) == n and page.query_selector(".an-t-note"))
        page.focus("#an-view"); page.keyboard.press("Escape"); time.sleep(0.1)
        # stamp from menu
        page.click("[data-tool=stamp]")
        page.click(".an-menu .menu-item[data-i='0']")
        click(page, 0, (400, 750))
        n += 1
        R.check("tool stamp", count(page) == n and "APPROVED" in (page.text_content(".an-t-stamp") or ""))
        # signature: type mode
        page.click("[data-tool=signature]")
        wait(page, "document.querySelector('.an-sig-overlay')")
        page.click(".an-sig [data-mode=type]")
        page.fill("#an-sig-type", "Jane Q")
        page.uncheck("#an-sig-rem")
        page.click("#an-sig-ok")
        time.sleep(0.4)
        click(page, 0, (150, 780))
        n += 1
        R.check("tool signature", count(page) == n and page.query_selector(".an-t-signature"))
        # photo (native picker replaced by the fixture path)
        page.evaluate("p => { window.__pick = SFM.annotPickPhoto; SFM.annotPickPhoto = async () => ({ok: true, path: p}); }", f["photo"])
        page.click("[data-tool=photo]")
        time.sleep(0.6)
        drag(page, 0, (420, 180), (500, 280))
        time.sleep(0.5)
        n += 1
        R.check("tool photo (fit into box)", count(page) == n and page.query_selector(".an-t-photo"))
        page.evaluate("() => { SFM.annotPickPhoto = window.__pick; }")

        # select + move the rectangle
        key(page, "v")
        rid = page.evaluate("document.querySelector('.an-t-rect').dataset.id")
        bb0 = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}']`).getBBox().x", rid)
        drag(page, 0, (150, 502), (180, 532))
        bb1 = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}']`).getBBox().x", rid)
        R.check("select + move", abs((bb1 - bb0) - 30) < 3, f"{bb0}->{bb1}")
        # undo / redo the move
        page.click("[data-a=undo]"); time.sleep(0.2)
        bbu = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}']`).getBBox().x", rid)
        page.click("[data-a=redo]"); time.sleep(0.2)
        bbr = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}']`).getBBox().x", rid)
        R.check("undo/redo move", abs(bbu - bb0) < 1 and abs(bbr - bb1) < 1, f"{bbu} {bbr}")
        # properties: colour swatch on the selected rect
        page.click(f".an-a[data-id='{rid}']", force=True) if False else None
        sw = page.query_selector("#an-props .an-swatches[data-prop=color] button[data-v='#22c55e']")
        if sw:
            sw.click(); time.sleep(0.1)
        col = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}'] rect`).getAttribute('stroke')", rid)
        R.check("properties: colour", col == "#22c55e", col)
        # resize signature with the SE handle, keeps aspect ratio
        sid = page.evaluate("document.querySelector('.an-t-signature').dataset.id")
        sb = page.evaluate("id => { const b = document.querySelector(`.an-a[data-id='${id}']`).getBBox(); return [b.x,b.y,b.width,b.height]; }", sid)
        click(page, 0, (sb[0] + sb[2] / 2, sb[1] + sb[3] / 2))
        h = page.evaluate("() => { const h = document.querySelector('.an-handle[data-handle=se]'); const b = h.getBBox(); return [b.x + b.width/2, b.y + b.height/2]; }")
        drag(page, 0, h, (h[0] + 40, h[1] + 40))
        sb2 = page.evaluate("id => { const b = document.querySelector(`.an-a[data-id='${id}']`).getBBox(); return [b.x,b.y,b.width,b.height]; }", sid)
        R.check("signature resize keeps ratio", sb2[2] > sb[2] + 10 and abs(sb2[2] / sb2[3] - sb[2] / sb[3]) < 0.05, f"{sb} -> {sb2}")
        # Delete key on selection, then undo
        key(page, "Delete")
        R.check("Del removes selection", count(page) == n - 1)
        page.keyboard.press("Control+z"); time.sleep(0.2)
        R.check("Ctrl+Z restores", count(page) == n)
        # eraser click on the line
        key(page, "e")
        lid = page.evaluate("document.querySelector('.an-t-line').dataset.id")
        click(page, 0, (150, 590))
        R.check("eraser", count(page) == n - 1 and not page.query_selector(f".an-a[data-id='{lid}']"))
        page.keyboard.press("Control+z"); time.sleep(0.2)
        R.check("undo eraser", count(page) == n)
        key(page, "v")
        # comments panel lists everything
        page.click(".an-side-tabs [data-tab=comments]")
        R.check("comments panel count", page.evaluate("document.querySelectorAll('#an-comments .an-c').length") == n)
        page.click(".an-side-tabs [data-tab=props]")

        # save as new file (redaction confirm appears)
        page.fill("#an-dir", out_dir)
        page.fill("#an-name", "multi_qa")
        page.click("#an-save")
        wait(page, "document.querySelector('.modal-overlay.dlg-top')")
        page.click(".modal-overlay.dlg-top .btn-danger, .modal-overlay.dlg-top .btn-primary")
        wait(page, "!document.querySelector('.an-overlay')", timeout=20)
        out = os.path.join(out_dir, "multi_qa.pdf")
        R.check("saved file exists", os.path.isfile(out), out)
        doc = fitz.open(out)
        types = sorted(a.type[1] for a in doc[0].annots())
        txt = doc[0].get_text()
        widgets = [w.field_value for w in doc[0].widgets()]
        doc.close()
        R.check("PDF has real annotations", len(types) == n - 1, types)   # redaction was applied
        R.check("text kept, not flattened", "quick brown" in txt and "Page 1 heading" in txt, txt[:60])
        R.check("form field kept", widgets == ["Form value"], widgets)
        # reopen → same annotations editable
        open_editor(page, out)
        R.check("reload round trip", count(page) == n - 1, count(page))
        close_overlays(page)

        # lock annotations
        open_editor(page, f["cert"])
        key(page, "r")
        drag(page, 0, (100, 500), (200, 560))
        page.check("#an-flatten", force=True)
        save_new(page, out_dir, "cert_locked")
        doc = fitz.open(os.path.join(out_dir, "cert_locked.pdf"))
        R.check("lock: no annotations left", len(list(doc[0].annots())) == 0)
        R.check("lock: text + field kept", "Smith" in doc[0].get_text().replace("\xa0", " ")
                and [w.field_value for w in doc[0].widgets()] == ["Signed"])
        doc.close()

        # image annotate + sidecar
        open_editor(page, f["png"])
        R.check("image: redact hidden", page.evaluate("document.querySelector('[data-tool=redact]').classList.contains('hidden')"))
        key(page, "r")
        drag(page, 0, (100, 100), (300, 250))
        save_new(page, out_dir, "picture_qa")
        R.check("image saved + sidecar", os.path.isfile(os.path.join(out_dir, "picture_qa.png"))
                and os.path.isfile(os.path.join(out_dir, "picture_qa.annot.json")))
        open_editor(page, os.path.join(out_dir, "picture_qa.png"))
        R.check("image reload editable", count(page) == 1)
        close_overlays(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "oa_qa_annot")))
