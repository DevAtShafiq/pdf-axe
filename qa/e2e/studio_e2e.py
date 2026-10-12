"""E2E: PDF Studio — entry points, Read & copy, Comment, Add text, Pages,
one Undo across versions, Save (text + form fields kept), close.

    set QA_CDP_PORT=9350
    python qa/e2e/studio_e2e.py <scratch dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402
from annotate_e2e import drag, click  # noqa: E402

R = Results()


def clip_text():
    import win32clipboard as wc
    for _ in range(10):
        try:
            wc.OpenClipboard()
            try:
                return wc.GetClipboardData(wc.CF_UNICODETEXT) if wc.IsClipboardFormatAvailable(wc.CF_UNICODETEXT) else ""
            finally:
                wc.CloseClipboard()
        except Exception:
            time.sleep(0.1)
    return ""


def studio_open(page):
    try:
        wait(page, "!!document.querySelector('.st-overlay') && !document.querySelector('#st-busy').classList.contains('hidden') === false", timeout=12)
        wait(page, "window.Studio && Studio._state() && Studio._state().session", timeout=12)
        return True
    except TimeoutError:
        return False


def state(page):
    return page.evaluate("Studio._state()")


def pill(page):
    return page.text_content("#st-ver")


def force_close(page):
    for _ in range(4):
        if not page.evaluate("!!document.querySelector('.st-overlay')"):
            return
        if not page.evaluate("!!document.querySelector('.modal-overlay.dlg-top')"):
            page.click("#st-x")
            time.sleep(0.3)
        # confirm "Close without saving" (the last button of the top dialog)
        page.evaluate("() => { const bs = document.querySelectorAll('.modal-overlay.dlg-top button'); if (bs.length) bs[bs.length - 1].click(); }")
        time.sleep(0.3)


def select(page, d, path):
    page.evaluate("d => App.navigate(d)", d)
    time.sleep(0.8)
    page.evaluate("p => FileTree.revealPath(p)", path)
    time.sleep(0.8)


def tool(page, tid):
    page.click(f".st-tool[data-id={tid}]")
    wait(page, f"Studio._state().itemId === '{tid}' && document.querySelector('#st-busy').classList.contains('hidden')", timeout=20)
    time.sleep(0.4)


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    src = f["multi"]
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append("console: " + m.text))
        force_close(page)

        # ── entry points ──────────────────────────────────────────────────
        select(page, d, src)
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.keyboard.press("Control+Shift+P")
        R.check("Ctrl+Shift+P opens Studio", studio_open(page))
        force_close(page)
        b = page.query_selector("#qa-studio")
        vis = b and page.evaluate("b => b.offsetParent !== null", b)
        R.check("details 'Edit' button visible", vis and page.text_content("#qa-studio").strip() == "Edit", vis and page.text_content("#qa-studio"))
        hidden = page.evaluate("""() => ['#qa-annotate', '#qa-edit-text', '#preview-annotate', '#preview-edit-text']
                                   .every(s => { const b = document.querySelector(s); return !b || b.offsetParent === null; })""")
        R.check("PDF: no separate Annotate / Edit text buttons", hidden)
        if vis:
            b.click(); R.check("details button opens Studio", studio_open(page)); force_close(page)
        pb = page.query_selector("#preview-studio")
        vis = pb and page.evaluate("b => b.offsetParent !== null", pb)
        R.check("preview 'PDF Studio' button visible", vis)
        if vis:
            pb.click(); R.check("preview button opens Studio", studio_open(page)); force_close(page)
        row = page.evaluate("""p => { const el = [...document.querySelectorAll('[data-path]')].find(e => e.dataset.path.toLowerCase() === p.toLowerCase());
                                  if (!el) return null; const r = el.getBoundingClientRect(); return [r.left + 30, r.top + r.height/2]; }""", src)
        if row:
            page.mouse.click(row[0], row[1], button="right"); time.sleep(0.4)
            labels = page.evaluate("[...document.querySelectorAll('.context-menu *')].filter(e => !e.children.length).map(e => e.textContent.trim())")
            R.check("context menu: no separate Annotate / Edit PDF text for PDFs",
                    not any(t.startswith("Annotate") or t.startswith("Edit PDF text") for t in labels), labels[:12])
            it = page.query_selector('text="Edit…"')
            R.check("context menu item", it is not None)
            if it:
                it.click(); R.check("context menu opens Studio", studio_open(page)); force_close(page)
            else:
                page.keyboard.press("Escape")
        select(page, d, src)
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.keyboard.press("Control+Shift+A")
        ok = studio_open(page)
        R.check("Annotate (Ctrl+Shift+A) routes to Studio Comment", ok and state(page)["mode"] == "annotate", ok and state(page))
        force_close(page)
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.keyboard.press("Control+Shift+T")
        ok = studio_open(page)
        R.check("Edit text (Ctrl+Shift+T) routes to Studio Edit", ok and state(page)["mode"] == "edit", ok and state(page))
        force_close(page)

        # ── Read & copy ───────────────────────────────────────────────────
        page.evaluate("p => Studio.open(p, {tool: 'read'})", src)
        R.check("open in Read mode", studio_open(page) and state(page)["mode"] == "read")
        wait(page, "document.querySelector('.st-read-page')", timeout=15)
        R.check("pill says Original", pill(page).startswith("Original"), pill(page))
        page.focus(".st-read-view")
        page.keyboard.press("Control+a"); time.sleep(0.3)
        page.keyboard.press("Control+c"); time.sleep(0.5)
        c = clip_text()
        R.check("Read: Ctrl+A, Ctrl+C copies page text", "Page 1 heading" in c, c[:60])
        page.click(".st-read-bar [data-a=copy-all]"); time.sleep(0.8)
        c = clip_text()
        R.check("Read: Copy all text has every page", all(f"Page {i} heading" in c for i in (1, 2, 3)), c[:80])

        # ── Comment: highlight ────────────────────────────────────────────
        tool(page, "highlight")
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=15)
        drag(page, 0, (74, 136), (250, 136))
        n = page.evaluate("document.querySelectorAll('#st-host .an-t-highlight').length")
        R.check("Comment: highlight drawn", n >= 1, n)
        R.check("pill shows unsaved", "unsaved" in pill(page), pill(page))

        # ── Add text: switching tool commits Version 1 ────────────────────
        tool(page, "add-text")
        R.check("switching tool makes Version 1", pill(page).startswith("Version 1"), pill(page))
        wait(page, "document.querySelector('#st-host .pe-page .an-bg.on')", timeout=15)
        x, y = page.evaluate("""() => { const el = document.querySelector('#st-host .pe-page[data-p="0"]'); const view = el.closest('.an-view');
                                       let r = el.getBoundingClientRect(); const vr = view.getBoundingClientRect();
                                       let cy = r.top + r.height * 0.6;
                                       view.scrollTop += cy - (vr.top + vr.height / 2);
                                       r = el.getBoundingClientRect();
                                       return [r.left + r.width * 0.15, r.top + r.height * 0.6]; }""")
        time.sleep(0.3)
        page.mouse.click(x, y); time.sleep(0.4)
        try:
            wait(page, "document.activeElement && document.activeElement.classList.contains('pe-editbox')", timeout=5)
            page.keyboard.type("Hello Studio 안녕하세요"); time.sleep(0.3)
            R.check("Add text: box typed", True)
        except TimeoutError:
            R.check("Add text: box opened", False)

        # ── Pages: commit Version 2, rotate page 2 ────────────────────────
        tool(page, "pages")
        R.check("switching to Pages makes Version 2", pill(page).startswith("Version 2"), pill(page))
        R.check("Pages: thumbnails shown", page.evaluate("document.querySelectorAll('#st-host [data-idx], #st-host .pt-thumb, #st-host .arr-item').length") >= 3)

        # ── one Undo across versions ──────────────────────────────────────
        tool(page, "read")
        page.click("#st-undo"); time.sleep(0.3)
        wait(page, "document.querySelector('#st-busy').classList.contains('hidden')", timeout=15)
        R.check("Undo steps back to Version 1", pill(page).startswith("Version 1"), pill(page))
        page.click("#st-redo"); time.sleep(0.3)
        wait(page, "document.querySelector('#st-busy').classList.contains('hidden')", timeout=15)
        R.check("Redo goes to Version 2", pill(page).startswith("Version 2"), pill(page))

        # ── Save as a new file ────────────────────────────────────────────
        page.click("#st-save")
        wait(page, "document.querySelector('#st-go')")
        page.fill("#st-name", "studio_out")
        page.fill("#st-dir", d)
        page.click("#st-go")
        out = os.path.join(d, "studio_out.pdf")
        try:
            wait(page, "document.querySelector('#st-sheet').classList.contains('hidden')", timeout=30)
        except TimeoutError:
            R.check("Save closed the sheet", False, page.text_content("#st-err"))
        R.check("Save wrote the new file", os.path.exists(out))
        R.check("pill no longer unsaved", "unsaved" not in pill(page), pill(page))
        if os.path.exists(out):
            doc = fitz.open(out)
            txt = doc[0].get_text()
            R.check("saved: original text still selectable", "quick brown fox" in txt)
            R.check("saved: added text present (Korean too)", "Hello Studio" in txt and "안녕하세요" in txt, txt[-80:])
            R.check("saved: highlight annotation kept", any(a.type[1] == "Highlight" for a in doc[0].annots()))
            w = list(doc[0].widgets())
            R.check("saved: form field kept (not flattened)", len(w) == 1 and w[0].field_value == "Form value")
            R.check("original untouched", "Hello Studio" not in fitz.open(src)[0].get_text())

        # ── close without prompt after save ───────────────────────────────
        page.click("#st-x"); time.sleep(0.5)
        R.check("closes without unsaved prompt", not page.evaluate("!!document.querySelector('.st-overlay')"))

        # ── unsaved prompt ────────────────────────────────────────────────
        page.evaluate("p => Studio.open(p, {tool: 'highlight'})", src)
        studio_open(page)
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=15)
        drag(page, 0, (74, 156), (250, 156))
        page.click("#st-x"); time.sleep(0.4)
        R.check("unsaved changes prompt shown", page.evaluate("!!document.querySelector('.modal-overlay.dlg-top')"))
        force_close(page)

        R.check("no JS errors", not errors, "; ".join(errors[:5]))
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "studio_e2e")))
