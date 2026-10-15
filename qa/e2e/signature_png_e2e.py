"""E2E: signature pad → "Save as PNG…" (asks for a name, saves into the
Signature folder). Start the test app with SFM_SIGNATURE_DIR=<scratch>\\Signature
so nothing is written to a real drive root.

    set QA_CDP_PORT=9350
    python qa/e2e/signature_png_e2e.py <scratch dir> <signature dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()


def draw(page):
    cb = page.query_selector("#an-sig-canvas").bounding_box()
    page.mouse.move(cb["x"] + 80, cb["y"] + 120); page.mouse.down()
    for i in range(40):
        page.mouse.move(cb["x"] + 80 + i * 8, cb["y"] + 120 - 30 * ((i % 8) / 8))
    page.mouse.up()
    time.sleep(0.2)


def ask_open(page):
    return page.evaluate("!!document.querySelector('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text')")


def run(d, sigdir):
    from PIL import Image
    f = make_all(d)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.evaluate("p => Studio.open(p, {tool: 'select'})", f["multi"])
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=20)
        page.click(".st-tool[data-id=signature]")
        wait(page, "document.querySelector('.an-sig-overlay #an-sig-canvas')")
        time.sleep(0.3)

        page.click("#an-sig-png"); time.sleep(0.3)
        R.check("empty pad: asks to draw first, no name box", not ask_open(page))
        draw(page)
        page.click("#an-sig-png")
        wait(page, "document.activeElement && document.activeElement.matches('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text')")
        hint = page.evaluate("document.querySelector('.modal-overlay.dlg-top:not(.an-sig-overlay) .field-hint').textContent")
        R.check("name box shows where it is saved", os.path.normcase(sigdir) in os.path.normcase(hint), hint)
        page.keyboard.press("Control+a"); page.keyboard.type("Test Sign")
        page.keyboard.press("Enter")
        time.sleep(0.8)
        p1 = os.path.join(sigdir, "Test Sign.png")
        R.check("Enter saves Test Sign.png in the Signature folder", os.path.isfile(p1), os.listdir(sigdir) if os.path.isdir(sigdir) else "no folder")
        R.check("pad stays open after saving (Enter did not 'Use' the signature)", page.evaluate("!!document.querySelector('.an-sig-overlay')"))
        if os.path.isfile(p1):
            with Image.open(p1) as im:
                R.check("PNG is transparent and cropped", im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0 and im.size[0] > 200, im.size)
        page.click("#an-sig-png")
        wait(page, "document.activeElement && document.activeElement.matches('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text')")
        page.keyboard.press("Control+a"); page.keyboard.type("Test Sign")
        page.keyboard.press("Enter"); time.sleep(0.8)
        R.check("same name again → 'Test Sign (2).png' (never overwritten)", os.path.isfile(os.path.join(sigdir, "Test Sign (2).png")))
        page.click("#an-sig-png")
        wait(page, "document.activeElement && document.activeElement.matches('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text')")
        page.keyboard.press("Escape"); time.sleep(0.4)
        R.check("Esc closes only the name box", not ask_open(page) and page.evaluate("!!document.querySelector('.an-sig-overlay')"))
        # typed signature: name defaults to what was typed
        page.click(".an-sig-overlay [data-mode=type]"); time.sleep(0.2)
        page.fill("#an-sig-type", "Shafiq Ahmed")
        page.click("#an-sig-png")
        wait(page, "document.activeElement && document.activeElement.matches('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text')")
        v = page.evaluate("document.querySelector('.modal-overlay.dlg-top:not(.an-sig-overlay) input.input-text').value")
        R.check("typed signature: name defaults to the typed name", v == "Shafiq Ahmed", v)
        page.keyboard.press("Enter"); time.sleep(0.8)
        R.check("typed signature saved", os.path.isfile(os.path.join(sigdir, "Shafiq Ahmed.png")))
        page.click("#an-sig-cancel"); time.sleep(0.3)
        page.click("#st-x"); time.sleep(0.4)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2]))
