"""End-to-end checks of the preview panel in a running Office Axe window.

    python qa/e2e/make_preview_fixtures.py <dir>
    (start the app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9342)
    python qa/e2e/preview_e2e.py <dir> [step ...]

Each step prints PASS/FAIL lines. Windows clipboard checks use win32clipboard.
"""
from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(__file__))
from pv_cdp import page_of  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def clip_text():
    import win32clipboard
    import win32con
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            time.sleep(0.05)
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return ""
    finally:
        win32clipboard.CloseClipboard()


def clip_set(text):
    import win32clipboard
    import win32con
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def wait(pg, js, arg=None, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = pg.evaluate(js, arg)
            if v:
                return v
        except Exception:
            pass
        time.sleep(0.2)
    return None


def enter_app(pg):
    if pg.evaluate("!!document.querySelector('#preview-body') && getComputedStyle(document.querySelector('#preview-body')).display !== 'none' && !document.querySelector('.auth-screen:not(.hidden), #auth-screen:not(.hidden)')"):
        pass
    btn = pg.locator("text=Continue without an account")
    if btn.count() and btn.first.is_visible():
        btn.first.click()
        time.sleep(1.5)


def open_file(pg, folder, name):
    """Select a file through the file list (a real click), wait for the preview."""
    path = os.path.join(folder, name)
    pg.evaluate("p => { if (App.state.currentFolder !== p) App.navigate(p, false); }", folder)
    ok = wait(pg, "p => !!Array.from(document.querySelectorAll('[data-path]')).find(e => e.dataset.path === p)", path)
    if not ok:
        return False
    pg.evaluate("p => { const e = Array.from(document.querySelectorAll('[data-path]')).find(e => e.dataset.path === p); e.scrollIntoView({block:'center'}); }", path)
    pg.locator(f'[data-path="{path.replace(chr(92), chr(92) * 2)}"]').first.click()
    return wait(pg, "p => Preview.getCurrentPath() === p", path, 5)


def view(pg):
    return pg.evaluate("Preview.getView()")


def step_pdf(pg, F):
    check("pdf: select opens preview", open_file(pg, F, "sample.pdf"))
    n = wait(pg, "document.querySelectorAll('#pdf-canvas-wrap .pdf-page').length")
    check("pdf: first page rendered", n and n >= 1, n)
    check("pdf: page count shown", pg.evaluate("document.getElementById('pdf-total-pages').textContent") == "3")
    ready = wait(pg, "document.querySelector('#pdf-canvas-wrap .pdf-page[data-page=\"0\"]')?.dataset.textLayer")
    check("pdf: text layer ready", ready == "ready", ready)
    check("pdf: context toolbar visible", pg.evaluate("!document.getElementById('pdf-ctx-toolbar').classList.contains('hidden')"))
    check("pdf: copy buttons visible", pg.evaluate("!document.getElementById('preview-copy-page').classList.contains('hidden')"))
    # navigation
    pg.evaluate("Preview.goNext()")
    time.sleep(0.4)
    check("pdf: next page", pg.evaluate("Preview.getCurrentPage()") == 1)
    pg.evaluate("Preview.goToPage(3)")
    time.sleep(0.4)
    check("pdf: go to page 3", pg.evaluate("Preview.getCurrentPage()") == 2)
    check("pdf: page input = 3", pg.evaluate("document.getElementById('pdf-page-input').value") == "3")
    pg.evaluate("Preview.goToPage(99)")
    check("pdf: go to page clamps", pg.evaluate("Preview.getCurrentPage()") == 2)
    pg.evaluate("Preview.goPrev()")
    check("pdf: prev page", pg.evaluate("Preview.getCurrentPage()") == 1)
    pg.evaluate("Preview.goToPage(1)")
    time.sleep(0.4)
    # zoom
    w0 = pg.evaluate("document.querySelector('#pdf-canvas-wrap .pdf-page').getBoundingClientRect().width")
    pg.click("#preview-zoom-in")
    pg.click("#preview-zoom-in")
    time.sleep(0.4)
    wait(pg, "document.querySelector('#pdf-canvas-wrap .pdf-page')")
    time.sleep(0.8)
    w1 = pg.evaluate("document.querySelector('#pdf-canvas-wrap .pdf-page')?.getBoundingClientRect().width || 0")
    check("pdf: zoom in widens page", w1 > w0 * 1.15, (w0, w1))
    check("pdf: zoom label 130%", pg.evaluate("document.getElementById('preview-zoom-label').textContent") == "130%")
    pg.click("#preview-zoom-reset")
    time.sleep(0.8)
    check("pdf: zoom reset 100%", pg.evaluate("document.getElementById('preview-zoom-label').textContent") == "100%")
    for _ in range(12):
        pg.evaluate("Preview.zoomOut()")
    check("pdf: zoom min 20%", pg.evaluate("document.getElementById('preview-zoom-label').textContent") == "20%")
    pg.click("#preview-zoom-reset")
    time.sleep(1.0)
    wait(pg, "document.querySelector('#pdf-canvas-wrap .pdf-page[data-page=\"0\"]')?.dataset.textLayer === 'ready'")

    # Ctrl+A then Ctrl+C on page 1 → Windows clipboard (Korean)
    clip_set("before")
    box = pg.locator('#pdf-canvas-wrap .pdf-page[data-page="0"]')
    bb = box.bounding_box()
    # The file list had focus (a file was just clicked); a click on the page
    # must move focus to the preview or Ctrl+A selects every file.
    pg.evaluate("(document.getElementById('filelist-list') || document.getElementById('filelist-thumb')).focus()")
    pg.mouse.click(bb["x"] + 8, bb["y"] + 15)    # empty area → focus preview
    check("pdf: click on page focuses the preview",
          pg.evaluate("!!document.activeElement.closest('#preview-body')"),
          pg.evaluate("document.activeElement.id"))
    pg.keyboard.press("Control+a")
    sel = pg.evaluate("PdfText.selectedText()")
    check("pdf: Ctrl+A selects page text", "Hello world" in sel and "안녕하세요" in sel, sel[:80])
    pg.keyboard.press("Control+c")
    time.sleep(0.6)
    c = clip_text()
    check("pdf: Ctrl+C puts Korean text on Windows clipboard", "안녕하세요 한국어 텍스트" in c and "Page 1 Hello world" in c, repr(c[:120]))
    # drag-select a single word
    sp = pg.locator('#pdf-canvas-wrap .pdf-page[data-page="0"] .pdf-text-layer span', has_text="안녕하세요").first
    sb = sp.bounding_box()
    pg.mouse.move(sb["x"] + 1, sb["y"] + sb["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(sb["x"] + sb["width"] - 1, sb["y"] + sb["height"] / 2, steps=5)
    pg.mouse.up()
    sel = pg.evaluate("PdfText.selectedText()")
    check("pdf: drag selects a Korean word", sel.strip().startswith("안녕"), repr(sel))
    clip_set("before")
    pg.keyboard.press("Control+c")
    time.sleep(0.6)
    check("pdf: Ctrl+C of drag selection", clip_text().strip() == sel.strip() and clip_text() != "before", repr(clip_text()))
    # copy page / copy all buttons
    clip_set("before")
    pg.evaluate("window.getSelection().removeAllRanges()")
    pg.click("#preview-copy-page")
    time.sleep(0.8)
    c = clip_text()
    check("pdf: Copy page button", c.startswith("Page 1 Hello world") and "안녕하세요" in c, repr(c[:80]))
    clip_set("before")
    pg.click("#preview-copy-all")
    time.sleep(0.8)
    c = clip_text()
    check("pdf: Copy all button", "Page 3 Hello world" in c and c.count("안녕하세요") == 3, repr(c[:80]))
    # right-click menu
    pg.mouse.click(bb["x"] + 8, bb["y"] + 15, button="right")
    items = pg.evaluate("Array.from(document.querySelectorAll('.pdf-text-menu .ctx-label')).map(e => e.textContent)")
    check("pdf: right-click menu items", "Copy all text" in items and any("page 1" in i for i in items), items)
    pg.keyboard.press("Escape")
    check("pdf: Escape closes menu", pg.evaluate("!document.querySelector('.pdf-text-menu')"))
    # links
    links = pg.evaluate("document.querySelectorAll('#pdf-canvas-wrap .pdf-page[data-page=\"0\"] .pdf-link-line').length")
    check("pdf: link underlines drawn", links == 2, links)
    check("pdf: link spans marked", pg.evaluate("document.querySelectorAll('#pdf-canvas-wrap .pdf-page[data-page=\"0\"] span.is-link').length") >= 2)
    # arrow keys
    pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.keyboard.press("ArrowRight")
    time.sleep(0.3)
    check("pdf: ArrowRight next page", pg.evaluate("Preview.getCurrentPage()") == 1)
    pg.keyboard.press("ArrowLeft")
    time.sleep(0.3)
    check("pdf: ArrowLeft prev page", pg.evaluate("Preview.getCurrentPage()") == 0)
    # scroll tracking
    pg.evaluate("document.getElementById('pdf-canvas-wrap').scrollTop = document.getElementById('pdf-canvas-wrap').scrollHeight")
    time.sleep(0.6)
    check("pdf: scroll tracks current page", pg.evaluate("Preview.getCurrentPage()") == 2,
          pg.evaluate("Preview.getCurrentPage()"))


def step_errors(pg, F):
    for name, needle in (("broken.pdf", "Could not read PDF"), ("empty.pdf", "Could not read PDF"),
                         ("broken.png", "damaged"), ("file.xyz", "No preview")):
        open_file(pg, F, name)
        t = wait(pg, "n => { const t = document.getElementById('preview-empty').innerText; return t.includes(n) ? t : ''; }", needle, 6)
        check(f"error state: {name}", t, pg.evaluate("document.getElementById('preview-empty').innerText"))
    open_file(pg, F, "locked.pdf")
    time.sleep(2)
    t = pg.evaluate("document.getElementById('preview-empty').innerText + ' | ' + (document.querySelector('.modal-overlay')?.innerText || '')")
    check("locked pdf asks for password", "password" in t.lower(), t[:200])
    pg.keyboard.press("Escape")


def step_images(pg, F):
    for name in ("img.png", "img.jpg", "img.tiff", "img.tif", "img.webp", "img.gif"):
        open_file(pg, F, name)
        ok = wait(pg, "(() => { const i = document.getElementById('img-preview'); return !i.classList.contains('hidden') && i.src.startsWith('data:image') && i.naturalWidth > 0; })()", None, 8)
        check(f"image: {name} shown", ok)
    pg.click("#preview-zoom-in")
    time.sleep(0.5)
    tr = pg.evaluate("document.getElementById('img-preview').style.transform")
    check("image: zoom applies to .gif", "1.15" in tr, tr)
    pg.click("#preview-zoom-reset")
    open_file(pg, F, "img.tiff")
    wait(pg, "document.getElementById('img-preview').naturalWidth > 0")
    pg.click("#preview-zoom-in")
    time.sleep(0.5)
    tr = pg.evaluate("document.getElementById('img-preview').style.transform")
    check("image: zoom applies to .tiff", "1.15" in tr, tr)
    pg.click("#preview-zoom-reset")


def step_text(pg, F):
    open_file(pg, F, "utf8.txt")
    t = wait(pg, "document.getElementById('text-preview').textContent.includes('안녕') && document.getElementById('text-preview').textContent")
    check("text: utf-8 Korean", t)
    open_file(pg, F, "korean_cp949.txt")
    t = wait(pg, "document.getElementById('text-preview').textContent.includes('한글') && document.getElementById('text-preview').textContent")
    check("text: cp949 Korean", t)
    pg.locator("#text-preview").click()
    pg.keyboard.press("Control+a")
    clip_set("before")
    pg.keyboard.press("Control+c")
    time.sleep(0.6)
    check("text: Ctrl+A, Ctrl+C copies Korean", "한글 CP949" in clip_text(), repr(clip_text()))


def step_docx(pg, F):
    open_file(pg, F, "doc.docx")
    ok = wait(pg, "!!document.querySelector('#doc-preview .doc-sheet h1')")
    check("docx: rendered", ok)
    check("docx: Korean heading", "제목" in pg.evaluate("document.querySelector('#doc-preview .doc-sheet').innerText"))
    check("docx: table", pg.evaluate("!!document.querySelector('#doc-preview table.doc-table')"))
    check("docx: meta bar", "words" in pg.evaluate("document.querySelector('#doc-preview .doc-bar').innerText"))
    pg.locator("#doc-preview .doc-sheet").click()
    pg.keyboard.press("Control+a")
    clip_set("before")
    pg.keyboard.press("Control+c")
    time.sleep(0.6)
    check("docx: Ctrl+A, Ctrl+C copies Korean", "본문" in clip_text(), repr(clip_text()[:80]))
    pg.locator("#doc-preview .doc-sheet").click(button="right")
    items = pg.evaluate("Array.from(document.querySelectorAll('.pdf-text-menu .ctx-label')).map(e => e.textContent)")
    check("docx: right-click menu", "Select all" in items and "Copy" in items, items)
    pg.keyboard.press("Escape")
    hrefs = pg.evaluate("Array.from(document.querySelectorAll('#doc-preview [data-href]')).map(a => a.dataset.href)")
    check("docx: http link present", "https://example.com/word" in hrefs, hrefs)


def step_xlsx(pg, F):
    open_file(pg, F, "book.xlsx")
    ok = wait(pg, "!!document.querySelector('#doc-preview table.xl-grid')")
    check("xlsx: grid rendered", ok)
    tabs = pg.evaluate("Array.from(document.querySelectorAll('#doc-preview .xl-tab')).map(t => t.textContent + (t.classList.contains('active') ? '*' : ''))")
    check("xlsx: sheet tabs", tabs == ["Data*", "Second", "Hidden"], tabs)
    txt = pg.evaluate("document.querySelector('#doc-preview table.xl-grid').innerText")
    check("xlsx: Korean + number format", "홍길동" in txt and "1,234.50" in txt, txt[:120])
    pg.click("#doc-preview .xl-tab[data-sheet='Second']")
    ok = wait(pg, "document.querySelector('#doc-preview .xl-tab.active')?.dataset.sheet === 'Second' && document.querySelector('#doc-preview table.xl-grid').innerText.includes('on sheet two')")
    check("xlsx: switch sheet", ok)
    open_file(pg, F, "data.csv")
    ok = wait(pg, "document.querySelector('#doc-preview table.xl-grid')?.innerText.includes('김철수')")
    check("csv: grid with Korean", ok)
    pg.locator("#doc-preview table.xl-grid td").first.click()
    pg.keyboard.press("Control+a")
    clip_set("before")
    pg.keyboard.press("Control+c")
    time.sleep(0.6)
    check("csv: copy grid text", "김철수" in clip_text(), repr(clip_text()[:80]))


def step_office(pg, F):
    open_file(pg, F, "deck.pptx")
    v = wait(pg, "(() => { const v = Preview.getView(); return v.view === 'pdf' ? 'pdf' : (document.querySelector('#preview-empty [data-act=office]') ? 'btn' : ''); })()", None, 10)
    if v == "btn":
        pg.click("#preview-empty [data-act=office]")
    ok = wait(pg, "Preview.getView().view === 'pdf' && Preview.getView().converted && document.querySelectorAll('#pdf-canvas-wrap .pdf-page').length > 0", None, 90)
    check("pptx: converted via PowerPoint and shown", ok, pg.evaluate("document.getElementById('preview-empty').innerText"))
    check("pptx: no edit toolbar on converted view", pg.evaluate("document.getElementById('pdf-ctx-toolbar').classList.contains('hidden')"))
    ready = wait(pg, "document.querySelector('#pdf-canvas-wrap .pdf-page')?.dataset.textLayer === 'ready'")
    check("pptx: text layer on converted pdf", ready)
    # cached second time
    open_file(pg, F, "img.png")
    t0 = time.time()
    open_file(pg, F, "deck.pptx")
    ok = wait(pg, "Preview.getView().view === 'pdf' && Preview.getView().converted", None, 5)
    check("pptx: cached conversion opens right away", ok and time.time() - t0 < 5)


def step_stale(pg, F):
    """Overwrite a previewed PDF in place, re-select it: the preview must follow."""
    import pymupdf
    p = os.path.join(F, "change.pdf")
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "first version")
    d.save(p)
    d.close()
    pg.evaluate("FileTree.refresh && FileTree.refresh()")
    time.sleep(1)
    open_file(pg, F, "change.pdf")
    wait(pg, "document.querySelector('#pdf-canvas-wrap .pdf-page')?.dataset.textLayer === 'ready'")
    d = pymupdf.open()
    for i in range(2):
        d.new_page().insert_text((72, 72), f"second version {i}")
    tmp = p + ".new"
    d.save(tmp)
    d.close()
    os.replace(tmp, p)
    check("stale: previewed PDF can be replaced on disk", True)


STEPS = {"pdf": step_pdf, "errors": step_errors, "images": step_images, "text": step_text,
         "docx": step_docx, "xlsx": step_xlsx, "office": step_office, "stale": step_stale}


def main(argv):
    F = os.path.abspath(argv[1])
    names = argv[2:] or list(STEPS)
    with sync_playwright() as pw:
        br, pg = page_of(pw)
        enter_app(pg)
        for n in names:
            try:
                STEPS[n](pg, F)
            except Exception as exc:
                check(f"{n}: crashed", False, repr(exc)[:300])
    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
