"""E2E: Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+D on a placed signature (and other
annotations) in Edit → Fill & sign.

    set QA_CDP_PORT=9350
    python qa/e2e/annot_copy_paste_e2e.py <scratch dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from annotate_e2e import pt, drag  # noqa: E402
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()

SIGS = """[...document.querySelectorAll('#st-host .an-t-signature')].map(g => {
  const im = g.querySelector('image'); return { id: g.dataset.id, page: +g.closest('.an-page').dataset.p,
  x: +im.getAttribute('x'), y: +im.getAttribute('y'), w: +im.getAttribute('width'), h: +im.getAttribute('height') }; })"""


def sigs(page):
    return page.evaluate(SIGS)


def key(page, k):
    page.keyboard.press(k)
    time.sleep(0.35)


def close_all(page):
    for _ in range(5):
        if not page.evaluate("!!document.querySelector('.st-overlay')"):
            return
        if page.evaluate("!!document.querySelector('.modal-overlay.dlg-top')"):
            page.evaluate("() => { const bs = document.querySelectorAll('.modal-overlay.dlg-top button'); bs[bs.length - 1].click(); }")
        else:
            page.click("#st-x")
        time.sleep(0.4)


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    src = f["multi"]
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        close_all(page)
        page.evaluate("p => Studio.open(p, {tool: 'select'})", src)
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=20)
        time.sleep(0.5)

        # draw a signature with the mouse in the pad, place it by clicking
        page.click(".st-tool[data-id=signature]")
        wait(page, "document.querySelector('.an-sig-overlay #an-sig-canvas')")
        time.sleep(0.3)
        if page.evaluate("document.querySelectorAll('.an-sig-use').length"):
            pass
        cb = page.query_selector("#an-sig-canvas").bounding_box()
        page.mouse.move(cb["x"] + 80, cb["y"] + 120); page.mouse.down()
        for i in range(40):
            page.mouse.move(cb["x"] + 80 + i * 8, cb["y"] + 120 - 30 * ((i % 8) / 8))
        page.mouse.up()
        time.sleep(0.2)
        page.uncheck("#an-sig-rem")
        page.click("#an-sig-ok")
        wait(page, "!document.querySelector('.an-sig-overlay')")
        time.sleep(0.4)
        x, y = pt(page, 0, 200, 500)
        page.mouse.move(x, y); time.sleep(0.2); page.mouse.click(x, y)
        time.sleep(0.5)
        s1 = sigs(page)
        R.check("signature placed", len(s1) == 1, s1)
        sel = page.evaluate("Studio._state() && document.querySelector('#st-host .an-a.sel, #st-host .an-a.selected') ? 1 : 0")
        # make sure it is selected (click it)
        if not sel and s1:
            cx, cy = pt(page, 0, s1[0]["x"] + s1[0]["w"] / 2, s1[0]["y"] + s1[0]["h"] / 2)
            page.mouse.click(cx, cy); time.sleep(0.3)

        key(page, "Control+c")
        key(page, "Control+v")
        s2 = sigs(page)
        R.check("Ctrl+C, Ctrl+V pastes a second signature", len(s2) == 2, len(s2))
        if len(s2) == 2:
            a, b = s2
            R.check("the copy is offset a little down-right, same size",
                    b["x"] > a["x"] and b["y"] > a["y"] and abs(b["w"] - a["w"]) < 0.5 and abs(b["h"] - a["h"]) < 0.5, s2)
        key(page, "Control+v")
        s3 = sigs(page)
        R.check("Ctrl+V again pastes another, further along", len(s3) == 3 and s3[2]["x"] > s3[1]["x"], [(s["x"], s["y"]) for s in s3])

        # paste on another page: same spot as the original
        page.evaluate("() => { const v = document.querySelector('#st-host .an-view, #an-view'); v && v.focus(); }")
        key(page, "PageDown")
        time.sleep(0.5)
        key(page, "Control+v")
        s4 = sigs(page)
        on2 = [s for s in s4 if s["page"] == 1]
        R.check("Ctrl+V on page 2 puts it on page 2", len(on2) == 1, s4)
        if on2:
            R.check("…at the same spot as the original", abs(on2[0]["x"] - s1[0]["x"]) < 0.6 and abs(on2[0]["y"] - s1[0]["y"]) < 0.6, (on2[0], s1[0]))
        # cut, undo
        key(page, "Control+x")
        R.check("Ctrl+X removes the selected signature", len(sigs(page)) == 3)
        key(page, "Control+z")
        R.check("Ctrl+Z brings it back", len(sigs(page)) == 4)
        key(page, "Control+v")
        R.check("pasting after a cut works", len(sigs(page)) == 5)
        key(page, "Control+d")
        R.check("Ctrl+D duplicates", len(sigs(page)) == 6)
        key(page, "Control+z"); key(page, "Control+z")
        R.check("each paste is one Undo step", len(sigs(page)) == 4)

        # save → 4 stamps in the PDF
        page.click("#st-save")
        wait(page, "document.querySelector('#st-go')")
        out = os.path.join(d, "out"); os.makedirs(out, exist_ok=True)
        page.fill("#st-name", "sig_copies"); page.fill("#st-dir", out)
        page.click("#st-go")
        p = os.path.join(out, "sig_copies.pdf")
        end = time.time() + 30
        while time.time() < end and not os.path.isfile(p):
            time.sleep(0.2)
        time.sleep(0.5)
        if os.path.isfile(p):
            doc = fitz.open(p)
            n = sum(1 for pg in doc for a in pg.annots() if a.type[0] == fitz.PDF_ANNOT_STAMP)
            R.check("saved PDF has all 4 signatures", n == 4, n)
            R.check("text still selectable", "quick brown fox" in doc[0].get_text())
            doc.close()
        else:
            R.check("saved PDF written", False)
        close_all(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "annot_copy")))
