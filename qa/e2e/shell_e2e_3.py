"""Shell E2E part 3: open / full view / combine shortcuts, preview zoom keys,
Settings dialog persistence, theme save ordering.

Usage:  python qa/e2e/shell_e2e_3.py <scratch root>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (Checker, connect, make_pdf, make_png, write, fresh_dir,  # noqa: E402
                     navigate, click_row, modal_count, close_modals)

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "oa_e2e"))
C = Checker()
pw, browser, page = connect()
errors: list[str] = []
page.on("pageerror", lambda e: errors.append(str(e)))


def ev(js, arg=None):
    return page.evaluate(js, arg) if arg is not None else page.evaluate(js)


def focus_list():
    ev("() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); document.getElementById('filelist-list').focus(); }")


orig = ev("() => SFM.getSettings().then(r => r.settings)")
try:
    ev("() => { App.switchPanel('workspace'); FileTree.setViewMode('list'); }")
    d = fresh_dir(ROOT, "open")
    make_pdf(os.path.join(d, "doc.pdf"), 3)
    make_pdf(os.path.join(d, "doc2.pdf"), 1)
    make_png(os.path.join(d, "pic.png"))
    write(os.path.join(d, "note.txt"), "hello note")
    navigate(page, d)

    click_row(page, "doc.pdf"); focus_list()
    page.keyboard.press("Enter"); time.sleep(1.2)
    C.check("Enter on a PDF opens full view", ev("() => !!document.getElementById('modal-fullview')"))
    C.check("full view shows page count", ev("() => document.getElementById('fv-total').textContent") == "3")
    page.click("#fv-next"); time.sleep(0.6)
    C.check("full view next page", ev("() => document.getElementById('fv-page-input').value") == "2")
    page.keyboard.press("Escape"); time.sleep(0.3)
    C.check("Esc closes full view", modal_count(page) == 0)
    click_row(page, "pic.png"); focus_list()
    page.keyboard.press("Enter"); time.sleep(1.0)
    C.check("Enter on an image opens full view", ev("() => !!document.querySelector('#fullview-inner img')"))
    close_modals(page)
    click_row(page, "note.txt"); focus_list()
    page.keyboard.press("Enter"); time.sleep(0.6)
    C.check("Enter on a text file previews it (no modal)", modal_count(page) == 0 and "hello note" in ev("() => document.getElementById('text-preview').textContent"))

    click_row(page, "doc.pdf"); focus_list()
    page.keyboard.press("Control+Enter"); time.sleep(1.0)
    C.check("Ctrl+Enter on one PDF opens Arrange", modal_count(page) >= 1)
    close_modals(page)
    click_row(page, "doc.pdf"); page.locator("#filelist-list .file-item", has_text="doc2.pdf").click(modifiers=["Control"])
    focus_list(); page.keyboard.press("Control+Enter"); time.sleep(1.0)
    C.check("Ctrl+Enter on 2 PDFs opens Merge", modal_count(page) >= 1)
    close_modals(page)
    click_row(page, "note.txt"); focus_list(); page.keyboard.press("Control+Enter"); time.sleep(0.4)
    C.check("Ctrl+Enter on a text file only warns", modal_count(page) == 0)

    # preview zoom keys
    click_row(page, "pic.png"); time.sleep(0.8)
    z0 = ev("() => document.getElementById('preview-zoom-label') ? document.getElementById('preview-zoom-label').textContent : ''")
    focus_list(); page.keyboard.press("Control+="); time.sleep(0.3)
    z1 = ev("() => document.getElementById('preview-zoom-label') ? document.getElementById('preview-zoom-label').textContent : ''")
    C.check("Ctrl+= zooms the preview", bool(z0) and z0 != z1, f"{z0}->{z1}")
    page.keyboard.press("Control+0"); time.sleep(0.3)

    # theme: quick double toggle must store the final theme
    t0 = ev("() => document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'")
    for _ in range(3):
        page.click("#btn-theme")
    time.sleep(1.0)
    final = ev("() => document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'")
    C.check("theme toggled", final != t0)
    C.check("saved theme matches the screen after quick toggles", ev("() => SFM.getSettings().then(r => r.settings.theme)") == final)
    page.click("#btn-theme"); time.sleep(0.6)

    # Settings dialog persistence
    ev("() => Dialogs.openSettings()"); time.sleep(0.6)
    page.fill("#s-outfolder", d)
    page.select_option("#s-ocrlang", "kor")
    page.click(".modal-overlay [data-modal-btn='Save']"); time.sleep(0.6)
    s = ev("() => SFM.getSettings().then(r => r.settings)")
    C.check("Settings save output folder + OCR language", s.get("output_folder") == d and s.get("ocr_lang") == "kor", str(s))
    ev("() => Dialogs.openSettings()"); time.sleep(0.6)
    C.check("Settings reopen shows saved values", ev("() => document.getElementById('s-outfolder').value") == d
            and ev("() => document.getElementById('s-ocrlang').value") == "kor")
    page.click(".modal-overlay [data-modal-btn='Save']"); time.sleep(0.6)
    C.check("saving without touching the key keeps has_api_key", ev("() => SFM.getSettings().then(r => r.settings.has_api_key)") == bool(orig.get("has_api_key")))
    C.check("no uncaught page errors", not errors, "; ".join(errors[:5]))
finally:
    try:
        close_modals(page)
        keep = {"output_folder": orig.get("output_folder") or "", "ocr_lang": orig.get("ocr_lang") or "eng",
                "theme": orig.get("theme") or "dark"}
        ev("s => SFM.saveSettings(s)", keep)
        ev("t => App.applyTheme(t)", orig.get("theme") or "dark")
    except Exception:
        pass
    rc = C.summary()
    pw.stop()
sys.exit(rc)
