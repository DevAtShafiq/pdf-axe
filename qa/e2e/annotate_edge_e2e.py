"""E2E edge cases for the annotation editor: undo granularity, text edit undo,
arrow-key nudge, opacity slider, eraser drag, custom stamp, drawn signature,
Ctrl+S, save over original (backup), unsaved-close guard, rotated page.

    python qa/e2e/annotate_edge_e2e.py <scratch dir>
"""
from __future__ import annotations

import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from annotate_e2e import click, count, drag, key, open_editor, pt  # noqa: E402
from cdp import Results, app_page, close_overlays, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()


def texts(page):
    return page.evaluate("[...document.querySelectorAll('.an-t-text .an-ft')].map(e => e.textContent)")


def undo(page):
    page.click("[data-a=undo]")
    time.sleep(0.15)


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_editor(page, f["multi"])
        # 1) text box, then edit it (Enter), then undo → old text
        key(page, "t"); click(page, 0, (100, 600))
        wait(page, "document.activeElement && document.activeElement.classList.contains('an-editbox')")
        page.keyboard.type("First"); page.keyboard.press("Escape"); time.sleep(0.2)
        key(page, "v"); click(page, 0, (110, 606))
        page.focus("#an-view"); page.keyboard.press("Enter")
        wait(page, "document.activeElement && document.activeElement.classList.contains('an-editbox')")
        page.keyboard.press("Control+a"); page.keyboard.type("Second"); page.keyboard.press("Escape"); time.sleep(0.2)
        R.check("text edited", texts(page) == ["Second"], texts(page))
        undo(page)
        R.check("undo text edit → old text", texts(page) == ["First"], texts(page))
        undo(page)
        R.check("undo again → box gone", count(page) == 0, count(page))
        page.click("[data-a=redo]"); time.sleep(0.1)
        page.click("[data-a=redo]"); time.sleep(0.1)
        R.check("redo twice", texts(page) == ["Second"], texts(page))
        # 2) empty new box must not eat an undo step
        key(page, "t"); click(page, 0, (300, 700)); time.sleep(0.2)
        page.keyboard.press("Escape"); time.sleep(0.2)
        undo(page)
        R.check("empty box leaves undo history intact", texts(page) == ["First"], texts(page))
        page.click("[data-a=redo]"); time.sleep(0.1)
        # 3) arrow-key nudge: one undo step per press burst
        key(page, "r"); drag(page, 0, (100, 400), (200, 450))
        rid = page.evaluate("document.querySelector('.an-t-rect').dataset.id")
        key(page, "v"); click(page, 0, (101, 425))
        x0 = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}']`).getBBox().x", rid)
        for _ in range(3):
            page.keyboard.press("ArrowRight"); time.sleep(0.05)
        x1 = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}']`).getBBox().x", rid)
        R.check("arrow keys move", abs(x1 - x0 - 3) < 0.6, f"{x0} {x1}")
        page.keyboard.press("Shift+ArrowDown"); time.sleep(0.05)
        undo(page)
        x2 = page.evaluate("id => document.querySelector(`.an-a[data-id='${id}']`).getBBox().x", rid)
        R.check("undo arrow nudge", x2 <= x1 + 0.01, f"{x1} {x2}")
        # 4) opacity slider: dragging = one undo step
        click(page, 0, (101, 425))
        u0 = page.evaluate("document.querySelector('[data-a=undo]').disabled")
        sl = page.query_selector("#an-props input[data-prop=opacity]")
        R.check("opacity slider present", sl is not None)
        if sl:
            b = sl.bounding_box()
            page.mouse.move(b["x"] + b["width"] - 2, b["y"] + b["height"] / 2)
            page.mouse.down()
            for i in range(10):
                page.mouse.move(b["x"] + b["width"] * (0.9 - i * 0.05), b["y"] + b["height"] / 2)
            page.mouse.up(); time.sleep(0.1)
            op = page.evaluate("id => +document.querySelector(`.an-a[data-id='${id}']`).getAttribute('opacity')", rid)
            undo(page)
            op2 = page.evaluate("id => +document.querySelector(`.an-a[data-id='${id}']`).getAttribute('opacity')", rid)
            R.check("opacity change + single undo", op < 0.9 and op2 == 1, f"{op} {op2}")
        # 5) eraser drag removes several, undo restores each
        key(page, "p"); drag(page, 0, (300, 300), (350, 350))
        key(page, "p"); drag(page, 0, (360, 300), (410, 350))
        n = count(page)
        key(page, "e")
        drag(page, 0, (290, 325), (420, 325), steps=30)
        R.check("eraser drag removes 2", count(page) == n - 2, f"{n} -> {count(page)}")
        undo(page); undo(page)
        R.check("undo eraser x2", count(page) == n)
        # 6) custom stamp
        page.click("[data-tool=stamp]")
        page.click(".an-menu .menu-item[data-i='custom']")
        wait(page, "document.querySelector('.modal-overlay.dlg-top input')")
        page.fill(".modal-overlay.dlg-top input", "Checked by QA")
        page.keyboard.press("Enter"); time.sleep(0.3)
        click(page, 0, (300, 780))
        R.check("custom stamp", "CHECKED BY QA" in " ".join(page.evaluate("[...document.querySelectorAll('.an-t-stamp')].map(e=>e.textContent)")))
        # 7) drawn signature, remembered
        page.click("[data-tool=signature]")
        wait(page, "document.querySelector('.an-sig-overlay')")
        cb = page.query_selector("#an-sig-canvas").bounding_box()
        page.mouse.move(cb["x"] + 40, cb["y"] + 120); page.mouse.down()
        for i in range(20):
            page.mouse.move(cb["x"] + 40 + i * 20, cb["y"] + 120 - (i % 5) * 12)
        page.mouse.up()
        page.check("#an-sig-rem")
        page.click("#an-sig-ok"); time.sleep(0.4)
        n = count(page)
        click(page, 0, (450, 700))
        R.check("drawn signature placed", count(page) == n + 1)
        page.click("[data-tool=signature]")
        wait(page, "document.querySelector('.an-sig-overlay .an-sig-use')")
        R.check("signature remembered", page.evaluate("document.querySelectorAll('.an-sig-use').length") >= 1)
        page.click(".an-sig-forget"); time.sleep(0.4)
        page.click("#an-sig-cancel"); time.sleep(0.2)
        # 8) unsaved close guard
        page.click("#an-close")
        wait(page, "document.querySelector('.modal-overlay.dlg-top')")
        page.click(".modal-overlay.dlg-top .btn:not(.btn-primary):not(.btn-danger)")   # Cancel
        time.sleep(0.2)
        R.check("close guard keeps editor", page.evaluate("!!document.querySelector('.an-overlay')"))
        # 9) Ctrl+S save over original → backup in _to_review
        page.check("input[name=an-out][value=over]", force=True)
        page.focus("#an-view"); page.keyboard.press("Control+s")
        wait(page, "document.querySelector('.modal-overlay.dlg-top')")
        page.click(".modal-overlay.dlg-top .btn-primary, .modal-overlay.dlg-top .btn-danger")
        wait(page, "!document.querySelector('.an-overlay')", timeout=20)
        doc = fitz.open(f["multi"]); na = len(list(doc[0].annots())); doc.close()
        backups = glob.glob(os.path.join(d, "_to_review", "*multi*"))
        R.check("save over original + backup", na == n + 1 and backups, f"{na} {backups}")

        # 10) rotated page: rect drawn appears where drawn after save/reload
        from test_pdf_edit import _cert
        rot = _cert(os.path.join(d, "rot90.pdf"), rotation=90)
        open_editor(page, rot)
        key(page, "r"); drag(page, 0, (100, 100), (200, 160))
        page.fill("#an-dir", d); page.fill("#an-name", "rot90_qa")
        page.click("#an-save")
        wait(page, "!document.querySelector('.an-overlay')", timeout=20)
        import annotate as an
        a = an.load(os.path.join(d, "rot90_qa.pdf"))["annotations"][0]
        R.check("rotated page rect position", [round(v) for v in a["rect"]] == [100, 100, 200, 160], a["rect"])
        close_overlays(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
