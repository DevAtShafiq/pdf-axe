"""E2E: how the editors / recorder are reached — Ctrl+Shift+A, details Tools,
preview button and context menu for Annotate (PDF + image); the recorder's
More-actions "Capture" section and the Settings "Recordings folder" row.

    python qa/e2e/entry_points_e2e.py <scratch dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, close_overlays, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()


def opened(page):
    try:
        wait(page, "!!document.querySelector('.an-overlay .an-page')", timeout=10)
        return True
    except TimeoutError:
        return False


def select(page, d, path):
    page.evaluate("d => App.navigate(d)", d)
    time.sleep(0.8)
    page.evaluate("p => FileTree.revealPath(p)", path)
    time.sleep(0.8)


def run(d):
    f = make_all(d)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        close_overlays(page)
        for kind in ("cert", "png"):
            path = f[kind]
            select(page, d, path)
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.keyboard.press("Control+Shift+A")
            R.check(f"{kind}: Ctrl+Shift+A", opened(page))
            close_overlays(page)
            b = page.query_selector("#qa-annotate")
            if b and page.evaluate("b => b.offsetParent !== null", b):
                b.click()
                R.check(f"{kind}: details Annotate", opened(page))
                close_overlays(page)
            else:
                R.check(f"{kind}: details Annotate visible", False)
            pb = page.query_selector("#preview-annotate")
            if pb and page.evaluate("b => b.offsetParent !== null", pb):
                pb.click()
                R.check(f"{kind}: preview Annotate button", opened(page))
                close_overlays(page)
            else:
                R.check(f"{kind}: preview Annotate visible", False)
            row = page.evaluate("""p => { const el = [...document.querySelectorAll('[data-path]')].find(e => e.dataset.path.toLowerCase() === p.toLowerCase());
                                      if (!el) return null; const r = el.getBoundingClientRect(); return [r.left + 30, r.top + r.height/2]; }""", path)
            if row:
                page.mouse.click(row[0], row[1], button="right")
                time.sleep(0.4)
                it = page.query_selector("text=Annotate…")
                R.check(f"{kind}: context menu item", it is not None)
                if it:
                    it.click()
                    R.check(f"{kind}: context menu opens", opened(page))
                    close_overlays(page)
                else:
                    page.keyboard.press("Escape")
        # non-annotatable selection: Ctrl+Shift+A just informs
        txt = os.path.join(d, "notes.txt")
        open(txt, "w").write("x")
        select(page, d, txt)
        page.keyboard.press("Control+Shift+A")
        time.sleep(0.5)
        R.check("txt: no editor", not page.evaluate("!!document.querySelector('.an-overlay')"))
        # More actions → Capture section
        page.evaluate("Dialogs.openMoreMenu && Dialogs.openMoreMenu()")
        time.sleep(0.5)
        R.check("More menu has Capture section", page.evaluate("!!document.querySelector('#modal-more .rec-more [data-rec=record]')"))
        page.keyboard.press("Escape"); time.sleep(0.3)
        page.evaluate("Dialogs.openSettings && Dialogs.openSettings()")
        try:
            wait(page, "!!document.querySelector('#modal-settings #s-recfolder')", timeout=8)
            ok = True
        except TimeoutError:
            ok = False
        R.check("Settings has Recordings folder row", ok)
        page.keyboard.press("Escape"); time.sleep(0.3)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
