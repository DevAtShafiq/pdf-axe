"""E2E: the PDF text editor (ui/js/pdf-edit.js) driven over CDP.

    python qa/e2e/pdfedit_e2e.py <scratch dir>

Entry points (Ctrl+Shift+T, context menu, details Tools button, preview
button), select/edit a block, new text box, font/size/colour/bold/italic/align,
move, wrap width, delete, undo per box, zoom/page buttons, save → PyMuPDF.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

import pdf_edit as pe  # noqa: E402

R = Results()


def st(page):
    return page.evaluate("PdfEdit._state()")


def pt(page, p, x, y):
    return page.evaluate("""([p, x, y, w]) => {
        const el = document.querySelector(`.pe-page[data-p="${p}"]`);
        const view = el.closest('.an-view');
        let r = el.getBoundingClientRect(), vr = view.getBoundingClientRect();
        let cx = r.left + x / w * r.width, cy = r.top + y / w * r.width;
        if (cy < vr.top + 40 || cy > vr.bottom - 40) {
            view.scrollTop += cy - (vr.top + vr.height / 2);
            r = el.getBoundingClientRect();
            cx = r.left + x / w * r.width; cy = r.top + y / w * r.width;
        }
        return [cx, cy];
    }""", [p, x, y, PAGE_W])


PAGE_W = 612.0


def close_editor(page):
    for _ in range(4):
        if not page.evaluate("!!document.querySelector('.pe-overlay')"):
            return
        page.evaluate("document.querySelector('#pe-view') && document.querySelector('#pe-view').focus()")
        page.keyboard.press("Escape")
        time.sleep(0.2)
        b = page.query_selector(".modal-overlay.dlg-top .btn-primary, .modal-overlay.dlg-top .btn-danger")
        if b:
            b.click()
            time.sleep(0.2)


def wait_open(page):
    wait(page, "document.querySelector('.pe-overlay') && document.querySelector('.pe-page .an-bg.on') && document.querySelector('.pe-block')")
    time.sleep(0.3)


def blk(path, text):
    for b in pe.extract_blocks(path, 0)["blocks"]:
        if text in b["text"]:
            return b
    raise AssertionError(text)


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    cert = f["cert"]
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        close_editor(page)
        page.evaluate("d => App.navigate(d)", d)
        time.sleep(1.0)
        page.evaluate("p => FileTree.revealPath(p)", cert)
        time.sleep(1.0)
        sel = page.evaluate("FileTree.getSelected()")
        R.check("file selected in list", sel and sel[0].lower().endswith("cert.pdf"), sel)
        # Ctrl+Shift+T
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.keyboard.press("Control+Shift+T")
        try:
            wait_open(page)
            ok = True
        except TimeoutError:
            ok = False
        R.check("Ctrl+Shift+T opens editor", ok)
        close_editor(page)
        # details Tools button
        btn = page.query_selector("#qa-edit-text")
        vis = btn and page.evaluate("b => !b.classList.contains('hidden') && b.offsetParent !== null", btn)
        R.check("details 'Edit text' visible for PDF", vis)
        if vis:
            btn.click()
            wait_open(page)
            R.check("details button opens editor", True)
            close_editor(page)
        # preview toolbar button
        pb = page.query_selector("#preview-edit-text")
        pvis = pb and page.evaluate("b => !b.classList.contains('hidden') && b.offsetParent !== null", pb)
        R.check("preview 'Edit text' button visible", pvis)
        if pvis:
            pb.click()
            wait_open(page)
            R.check("preview button opens editor", True)
            close_editor(page)
        # context menu
        row = page.evaluate("""p => { const el = [...document.querySelectorAll('[data-path]')].find(e => e.dataset.path.toLowerCase() === p.toLowerCase());
                                      if (!el) return null; el.scrollIntoView({block:'center'}); const r = el.getBoundingClientRect(); return [r.left + 30, r.top + r.height/2]; }""", cert)
        if row:
            page.mouse.click(row[0], row[1], button="right")
            time.sleep(0.4)
            item = page.query_selector("text=Edit PDF text…")
            R.check("context menu has 'Edit PDF text…'", item is not None)
            if item:
                item.click()
                wait_open(page)
                R.check("context menu opens editor", True)
                close_editor(page)
        else:
            R.check("context menu row found", False)

        page.evaluate("p => PdfEdit.open(p)", cert)
        wait_open(page)
        # zoom buttons
        z0 = page.text_content("#pe-zoom")
        page.click("#pe-editor-dummy") if False else None
        page.click(".pe-bar [data-a=zin]"); time.sleep(0.2)
        z1 = page.text_content("#pe-zoom")
        page.click(".pe-bar [data-a=z100]"); time.sleep(0.2)
        z2 = page.text_content("#pe-zoom")
        page.click(".pe-bar [data-a=fitw]"); time.sleep(0.3)
        R.check("zoom buttons", z1 != z0 and z2 == "100%", f"{z0} {z1} {z2}")

        # 1) edit the name block
        b = blk(cert, "John Smith")
        cx, cy = pt(page, 0, (b["bbox"][0] + b["bbox"][2]) / 2, (b["bbox"][1] + b["bbox"][3]) / 2)
        page.mouse.click(cx, cy)
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        page.keyboard.press("Control+a")
        page.keyboard.type("Jane Doe")
        # change colour via the toolbar while editing (keeps editing)
        page.evaluate("""() => { const c = document.querySelector('#pe-color'); c.value = '#ff0000'; c.dispatchEvent(new Event('input')); c.dispatchEvent(new Event('change')); }""")
        page.click(".pe-bar [data-a=italic]")
        page.mouse.click(*pt(page, 0, 500, 600))   # click outside = keep
        time.sleep(0.3)
        s = st(page)
        ov = s["model"]["ov"].get("0:" + b["id"], {})
        R.check("block edit in model", ov.get("text") == "Jane Doe" and ov.get("color") == "#ff0000" and ov.get("italic") is True, ov)
        # 2) new text box
        page.click(".pe-bar [data-a=mode-add]")
        page.mouse.click(*pt(page, 0, 80, 560))
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        page.keyboard.type("Added by QA")
        page.select_option("#pe-font", "Courier New")
        page.fill("#pe-size", "16"); page.dispatch_event("#pe-size", "change")
        page.mouse.click(*pt(page, 0, 500, 650)); time.sleep(0.3)
        s = st(page)
        adds = s["model"]["adds"]
        R.check("new text box", len(adds) == 1 and adds[0]["text"] == "Added by QA" and adds[0]["font"] == "Courier New" and adds[0]["size"] == 16, adds)
        # undo per box: undo removes style, then text box
        u = s["undo"]
        page.click(".pe-bar [data-a=undo]"); time.sleep(0.15)
        page.click(".pe-bar [data-a=undo]"); time.sleep(0.15)
        page.click(".pe-bar [data-a=undo]"); time.sleep(0.15)
        s2 = st(page)
        R.check("undo steps back through the new box", len(s2["model"]["adds"]) == 0 and "0:" + b["id"] in s2["model"]["ov"], f"undo={u} adds={s2['model']['adds']}")
        for _ in range(3):
            page.click(".pe-bar [data-a=redo]"); time.sleep(0.1)
        s3 = st(page)
        R.check("redo restores new box", s3["model"]["adds"] and s3["model"]["adds"][0]["size"] == 16, s3["model"]["adds"])
        # regression: style change while typing, then ONE undo keeps the typed text
        page.click(".pe-bar [data-a=mode-add]")
        page.mouse.click(*pt(page, 0, 80, 620))
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        page.keyboard.type("Keep me")
        page.click(".pe-bar [data-a=bold]"); time.sleep(0.1)
        page.mouse.click(*pt(page, 0, 500, 650)); time.sleep(0.2)
        page.click(".pe-bar [data-a=undo]"); time.sleep(0.15)
        a2 = [a for a in st(page)["model"]["adds"] if a["rect"][1] > 600]
        R.check("undo style keeps typed text", a2 and a2[0]["text"] == "Keep me" and not a2[0]["bold"], a2)
        page.click(".pe-bar [data-a=undo]"); time.sleep(0.15)
        a3 = [a for a in st(page)["model"]["adds"] if a["rect"][1] > 600]
        R.check("second undo removes the box", not a3, a3)
        # 3) delete a block with Del
        b2 = blk(cert, "Date:")
        page.mouse.click(*pt(page, 0, b2["bbox"][0] + 5, (b2["bbox"][1] + b2["bbox"][3]) / 2))
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        page.keyboard.press("Escape")       # cancel the edit, keep selection
        time.sleep(0.15)
        page.keyboard.press("Delete"); time.sleep(0.2)
        s4 = st(page)
        R.check("Del deletes block", s4["model"]["ov"].get("0:" + b2["id"], {}).get("deleted") is True, s4["model"]["ov"])
        # 4) move a block by drag
        b3 = blk(cert, "This is to certify")
        a0 = pt(page, 0, b3["bbox"][0] + 10, (b3["bbox"][1] + b3["bbox"][3]) / 2)
        a1 = pt(page, 0, b3["bbox"][0] + 10, (b3["bbox"][1] + b3["bbox"][3]) / 2 + 30)
        page.mouse.move(*a0); page.mouse.down()
        for i in range(1, 9):
            page.mouse.move(a0[0], a0[1] + (a1[1] - a0[1]) * i / 8)
        page.mouse.up(); time.sleep(0.2)
        s5 = st(page)
        mv = s5["model"]["ov"].get("0:" + b3["id"], {}).get("bbox")
        R.check("drag moves block", mv and abs(mv[1] - b3["bbox"][1] - 30) < 2, mv)
        # 5) save as new file
        page.fill("#pe-name", "cert_qa")
        page.fill("#pe-dir", d)
        page.click("#pe-save")
        wait(page, "document.querySelector('#pe-notice') && !document.querySelector('#pe-notice').classList.contains('hidden')", timeout=30)
        out = os.path.join(d, "cert_qa.pdf")
        R.check("saved", os.path.isfile(out))
        doc = fitz.open(out)
        txt = doc[0].get_text().replace("\xa0", " ")
        wid = [w.field_value for w in doc[0].widgets()]
        doc.close()
        R.check("output text", "Jane Doe" in txt and "John Smith" not in txt and "Added by QA" in txt and "Date:" not in txt, txt[:200])
        R.check("form field kept", wid == ["Signed"], wid)
        nb = blk(out, "Jane Doe")
        R.check("style written (red italic)", nb["color"] == "#ff0000" and nb["italic"], (nb["color"], nb["italic"], nb["font"]))
        ab = blk(out, "Added by QA")
        R.check("added box font/size", ab["family"] == "Courier New" and abs(ab["size"] - 16) < 0.6, (ab["family"], ab["size"]))
        mb = blk(out, "This is to certify")
        R.check("moved block position", abs(mb["bbox"][1] - b3["bbox"][1] - 30) < 2, (b3["bbox"], mb["bbox"]))
        s6 = st(page)
        R.check("editor reloads the output", os.path.normcase(os.path.abspath(s6["src"])) == os.path.normcase(os.path.abspath(out)) and not s6["model"]["adds"], s6["src"])
        wait(page, "document.querySelector('.pe-page .an-bg.on') && document.querySelector('.pe-block')")
        time.sleep(0.5)
        page.screenshot(path=os.path.join(d, "after_save.png"))
        # 6) bold + align + wrap width on the paragraph, then save over (backup)
        b4 = blk(out, "has successfully")
        page.mouse.click(*pt(page, 0, b4["bbox"][0] + 20, b4["bbox"][1] + 4))
        wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')")
        page.keyboard.press("Escape"); time.sleep(0.1)
        page.click(".pe-bar [data-a=bold]"); time.sleep(0.1)
        page.click(".pe-bar [data-a=align-center]"); time.sleep(0.1)
        h = page.query_selector(".pe-block.is-sel .pe-handle")
        R.check("wrap handle shown", h is not None)
        if h:
            hb = h.bounding_box()
            page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2); page.mouse.down()
            for i in range(1, 9):
                page.mouse.move(hb["x"] - i * 20, hb["y"] + hb["height"] / 2)
            page.mouse.up(); time.sleep(0.2)
        ov4 = st(page)["model"]["ov"].get("0:" + b4["id"], {})
        R.check("bold/center/width in model", ov4.get("bold") is True and ov4.get("align") == "center" and ov4.get("bbox"), ov4)
        page.check("input[name=pe-out][value=over]", force=True)
        page.click("#pe-save")
        wait(page, "document.querySelector('.modal-overlay.dlg-top')")
        page.click(".modal-overlay.dlg-top .btn-primary")
        wait(page, "document.querySelector('#pe-notice') && document.querySelector('#pe-notice').textContent.includes('Saved')", timeout=30)
        time.sleep(0.5)
        nb4 = blk(out, "has successfully")
        R.check("bold paragraph saved over", nb4["bold"] and nb4["bbox"][2] - nb4["bbox"][0] < b4["bbox"][2] - b4["bbox"][0], (nb4["bbox"], b4["bbox"]))
        R.check("backup in _to_review", any("cert_qa" in n for n in os.listdir(os.path.join(d, "_to_review"))))
        # Ctrl+S with no changes → info toast, no file
        before = set(os.listdir(d))
        page.evaluate("document.querySelector('#pe-view').focus()")
        page.keyboard.press("Control+s"); time.sleep(0.5)
        R.check("Ctrl+S with no changes writes nothing", set(os.listdir(d)) == before)
        close_editor(page)
        # multipage nav + scanned-free page 3 add text
        page.evaluate("p => PdfEdit.open(p)", f["multi"])
        wait_open(page)
        page.click(".pe-bar [data-a=next]"); time.sleep(0.4)
        R.check("page next", page.input_value("#pe-pg") == "2")
        page.click(".pe-bar [data-a=prev]"); time.sleep(0.4)
        R.check("page prev", page.input_value("#pe-pg") == "1")
        close_editor(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
