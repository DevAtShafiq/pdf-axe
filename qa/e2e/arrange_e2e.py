"""E2E: Acrobat-style page tools — Extract (click pages / type range, one file
per page, delete after extracting), Split (every N, at selected pages, every
page; cut marks), Insert (pages from a file, blank page) — in the Arrange
window and in Edit → Pages.

    set QA_CDP_PORT=9350
    python qa/e2e/arrange_e2e.py <scratch dir>
"""
from __future__ import annotations

import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402

R = Results()


def make_pdfs(d):
    import pymupdf as fitz
    os.makedirs(d, exist_ok=True)
    doc = fitz.open()
    for i in range(6):
        doc.new_page(width=595, height=842).insert_text((72, 100), f"Source page {i + 1}", fontsize=20)
    src = os.path.join(d, "report.pdf")
    doc.save(src); doc.close()
    doc = fitz.open()
    for i in range(2):
        doc.new_page(width=595, height=842).insert_text((72, 100), f"Extra page {i + 1}", fontsize=20)
    extra = os.path.join(d, "extra.pdf")
    doc.save(extra); doc.close()
    return src, extra


def texts(path):
    import pymupdf as fitz
    with fitz.open(path) as doc:
        return [p.get_text().strip() for p in doc]


def tiles(page):
    return page.evaluate("[...document.querySelectorAll('.pt-overlay .pt-tile')].map(t => ({id: t.dataset.id, sel: t.classList.contains('sel'), cut: t.classList.contains('pt-cut'), blank: !!t.querySelector('.pt-blank'), cap: t.querySelector('.pt-cap').textContent}))")


def click_tile(page, i, mod=None):
    page.evaluate("i => document.querySelectorAll('.pt-overlay .pt-tile')[i].scrollIntoView({block: 'center'})", i)
    time.sleep(0.15)
    box = page.evaluate("i => { const r = document.querySelectorAll('.pt-overlay .pt-tile')[i].querySelector('.pt-thumb').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }", i)
    if mod:
        page.keyboard.down(mod)
    page.mouse.click(*box)
    if mod:
        page.keyboard.up(mod)
    time.sleep(0.2)


def set_dir(page, d):
    # In Edit → Pages the save bar is hidden (Edit saves); outputs go next to the PDF.
    page.evaluate("d => { document.querySelector('#pt-a-dir').value = d; }", d)


def close_all(page):
    for _ in range(6):
        if not page.evaluate("!!document.querySelector('.pt-overlay, .st-overlay')"):
            return
        if page.evaluate("!!document.querySelector('.modal-overlay.dlg-top')"):
            page.evaluate("() => { const bs = document.querySelectorAll('.modal-overlay.dlg-top button'); bs[bs.length - 1].click(); }")
        elif page.evaluate("!!document.querySelector('.st-overlay')"):
            page.click("#st-x")
        else:
            page.click("#pt-a-x")
        time.sleep(0.4)


def wait_idle(page):
    wait(page, "!document.querySelector('#pt-a-save').classList.contains('is-loading')", timeout=30)
    time.sleep(0.3)


def run(d):
    src, extra = make_pdfs(d)
    out = os.path.join(d, "out")
    os.makedirs(out, exist_ok=True)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append("console: " + m.text))
        close_all(page)

        # ── Extract from the details pane → page grid with the Extract bar ─
        page.evaluate("d => App.navigate(d)", d); time.sleep(0.8)
        page.evaluate("p => FileTree.revealPath(p)", src); time.sleep(0.8)
        b = page.query_selector("#qa-pdf-extract")
        vis = b and page.evaluate("b => b.offsetParent !== null", b)
        if vis:
            b.click()
        else:
            page.evaluate("p => PdfTools.openExtract(p)", src)
        wait(page, "document.querySelectorAll('.pt-overlay .pt-tile').length === 6", timeout=15)
        R.check("Extract opens the page grid (thumbnails, not a number box)", page.evaluate("!document.querySelector('#pt-a-sub-extract').classList.contains('hidden')"))
        wait(page, "document.querySelector('.pt-overlay .pt-tile img[src]')", timeout=15)
        R.check("thumbnails rendered", True)
        set_dir(page, out)
        # plain clicks pick pages; range box follows
        sel_now = [t for t in tiles(page) if t["sel"]]
        if sel_now:   # opened with the previewed page selected; clear it
            for i, t in enumerate(tiles(page)):
                if t["sel"]:
                    click_tile(page, i)
        click_tile(page, 1); click_tile(page, 3)
        R.check("plain click picks pages (2 and 4)", [i for i, t in enumerate(tiles(page)) if t["sel"]] == [1, 3])
        R.check("range box shows '2, 4'", page.input_value("#pt-x-range") == "2, 4", page.input_value("#pt-x-range"))
        click_tile(page, 3)
        R.check("clicking again un-picks", [i for i, t in enumerate(tiles(page)) if t["sel"]] == [1])
        # typing a range selects pages
        page.fill("#pt-x-range", "1-3"); page.dispatch_event("#pt-x-range", "input"); time.sleep(0.3)
        R.check("typing 1-3 selects 3 pages", [i for i, t in enumerate(tiles(page)) if t["sel"]] == [0, 1, 2])
        page.fill("#pt-x-range", "9"); page.dispatch_event("#pt-x-range", "input"); time.sleep(0.2)
        R.check("bad range is flagged and Extract disabled", page.evaluate("document.querySelector('#pt-x-range').classList.contains('is-invalid') && document.querySelector('#pt-x-go').disabled"))
        page.fill("#pt-x-range", "1-3"); page.dispatch_event("#pt-x-range", "input"); time.sleep(0.2)
        page.click("#pt-x-go"); wait_idle(page)
        f = os.path.join(out, "report_p1-3.pdf")
        R.check("Extract 1-3 → one file with pages 1-3", os.path.exists(f) and texts(f) == ["Source page 1", "Source page 2", "Source page 3"], os.listdir(out))
        # each page as a separate file
        page.fill("#pt-x-range", "5, 6"); page.dispatch_event("#pt-x-range", "input"); time.sleep(0.2)
        page.check("#pt-x-each"); time.sleep(0.1)
        R.check("info says 2 pages → 2 files", "2 files" in page.text_content("#pt-x-info"), page.text_content("#pt-x-info"))
        page.click("#pt-x-go"); wait_idle(page)
        R.check("each page → report_p5.pdf, report_p6.pdf",
                texts(os.path.join(out, "report_p5.pdf")) == ["Source page 5"] and texts(os.path.join(out, "report_p6.pdf")) == ["Source page 6"])
        # delete after extracting
        page.uncheck("#pt-x-each")
        page.fill("#pt-x-range", "6"); page.dispatch_event("#pt-x-range", "input"); time.sleep(0.2)
        page.check("#pt-x-del")
        page.click("#pt-x-go"); wait_idle(page)
        R.check("delete after extracting removes the page here", len(tiles(page)) == 5)
        R.check("…and marks unsaved", "unsaved" in page.text_content("#pt-a-status"))
        R.check("original file untouched", len(texts(src)) == 6)
        page.uncheck("#pt-x-del")
        # Escape closes the bar first
        page.focus("#pt-a-grid"); page.keyboard.press("Escape"); time.sleep(0.3)
        R.check("Esc closes the Extract bar, not the window",
                page.evaluate("document.querySelector('#pt-a-sub-extract').classList.contains('hidden') && !!document.querySelector('.pt-overlay')"))
        close_all(page)

        # ── Insert: blank page + pages from a file ─────────────────────────
        page.evaluate("p => PdfTools.openArrange([p])", src)
        wait(page, "document.querySelectorAll('.pt-overlay .pt-tile').length === 6", timeout=15)
        set_dir(page, out)
        click_tile(page, 0)
        page.click("[data-a=insert-menu]"); time.sleep(0.2)
        R.check("Insert menu opens", page.evaluate("!document.querySelector('#pt-a-insmenu').classList.contains('hidden')"))
        page.click("#pt-a-insmenu [data-a=blank]"); time.sleep(0.5)
        t = tiles(page)
        R.check("blank page inserted after page 1", len(t) == 7 and t[1]["blank"] and "blank" in t[1]["cap"], [x["cap"] for x in t])
        page.evaluate("p => { window.__realBrowse = SFM.browseForPdfsOrImages; SFM.browseForPdfsOrImages = async () => ({ok: true, paths: [p]}); }", extra)
        click_tile(page, 6)
        page.click("[data-a=insert-menu]"); time.sleep(0.2)
        page.click("#pt-a-insmenu [data-a=insert]"); time.sleep(1.0)
        page.evaluate("() => { if (window.__realBrowse) SFM.browseForPdfsOrImages = window.__realBrowse; }")
        R.check("pages from a file inserted at the end", len(tiles(page)) == 9)
        # undo / redo the insert
        page.focus("#pt-a-grid"); page.keyboard.press("Control+z"); time.sleep(0.3)
        R.check("Ctrl+Z undoes the insert", len(tiles(page)) == 7)
        page.keyboard.press("Control+y"); time.sleep(0.3)
        R.check("Ctrl+Y redoes it", len(tiles(page)) == 9)

        # ── Split ──────────────────────────────────────────────────────────
        page.click("[data-a=panel-split]"); time.sleep(0.3)
        R.check("Split bar opens and Extract bar stays closed",
                page.evaluate("!document.querySelector('#pt-a-sub-split').classList.contains('hidden') && document.querySelector('#pt-a-sub-extract').classList.contains('hidden')"))
        page.fill("#pt-sp-every", "4"); page.dispatch_event("#pt-sp-every", "input"); time.sleep(0.3)
        cuts = [i for i, t in enumerate(tiles(page)) if t["cut"]]
        R.check("every 4 pages → cut marks before pages 5 and 9", cuts == [4, 8], cuts)
        R.check("button says Split into 3 files", "3 files" in page.text_content("#pt-sp-go"), page.text_content("#pt-sp-go"))
        sd = os.path.join(out, "split_every"); os.makedirs(sd, exist_ok=True); set_dir(page, sd)
        page.click("#pt-sp-go"); wait_idle(page)
        files = sorted(glob.glob(os.path.join(sd, "*.pdf")))
        R.check("3 files written", len(files) == 3, [os.path.basename(x) for x in files])
        if len(files) == 3:
            t0 = texts(files[0])
            R.check("file 1 = page 1, blank, page 2, page 3", t0 == ["Source page 1", "", "Source page 2", "Source page 3"], t0)
            R.check("file 3 = the 9th page (inserted)", texts(files[2]) == ["Extra page 2"], texts(files[2]))
        # at selected pages
        page.check("input[name=pt-sp-mode][value=marks]"); time.sleep(0.2)
        click_tile(page, 3); click_tile(page, 6)
        cuts = [i for i, t in enumerate(tiles(page)) if t["cut"]]
        R.check("at selected pages: cuts before 4 and 7", cuts == [3, 6], cuts)
        md = os.path.join(out, "split_marks"); os.makedirs(md, exist_ok=True); set_dir(page, md)
        page.click("#pt-sp-go"); wait_idle(page)
        files = sorted(glob.glob(os.path.join(md, "*.pdf")))
        R.check("3 files at the chosen cuts", [len(texts(x)) for x in files] == [3, 3, 3], [os.path.basename(x) for x in files])
        # every page
        page.check("input[name=pt-sp-mode][value=each]"); time.sleep(0.2)
        R.check("every page → 9 files", "9 files" in page.text_content("#pt-sp-go"))
        R.check("original still untouched after splits", len(texts(src)) == 6)
        # save the arranged version (blank page + inserted pages)
        page.keyboard.press("Escape"); time.sleep(0.2)
        page.fill("#pt-a-name", "report_arranged")
        set_dir(page, out)
        page.click("#pt-a-save")
        wait(page, "!document.querySelector('.pt-overlay')", timeout=30)
        sv = os.path.join(out, "report_arranged.pdf")
        R.check("Save writes all 9 pages incl. blank + inserted", os.path.exists(sv) and texts(sv)[1] == "" and texts(sv)[-1] == "Extra page 2" and len(texts(sv)) == 9)

        # ── Edit → Pages has the same tools; names use the real file ──────
        page.evaluate("p => Studio.open(p, {tool: 'pages'})", src)
        wait(page, "document.querySelectorAll('#st-host .pt-tile').length === 6", timeout=20)
        R.check("Edit → Pages shows Insert / Extract / Split",
                page.evaluate("['insert-menu', 'panel-extract', 'panel-split'].every(a => document.querySelector(`#st-host [data-a=${a}]`))"))
        page.click("#st-host [data-a=panel-extract]"); time.sleep(0.2)
        ed = os.path.join(out, "studio"); os.makedirs(ed, exist_ok=True); set_dir(page, ed)
        page.fill("#pt-x-range", "2"); page.dispatch_event("#pt-x-range", "input"); time.sleep(0.2)
        page.click("#pt-x-go"); time.sleep(1.5)
        R.check("Edit → Pages extract is named after the PDF (report_p2.pdf)",
                os.path.exists(os.path.join(ed, "report_p2.pdf")) and texts(os.path.join(ed, "report_p2.pdf")) == ["Source page 2"], os.listdir(ed))
        close_all(page)

        R.check("no JS errors", not errors, "; ".join(errors[:5]))
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "arrange_e2e")))
