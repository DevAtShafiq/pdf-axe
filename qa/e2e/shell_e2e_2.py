"""Shell E2E part 2: inline-rename edge cases, template language + manager,
toolbar odds and ends (refresh, recent folders, thumbnail zoom, resizers).

Usage:  python qa/e2e/shell_e2e_2.py <scratch root>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (Checker, connect, make_pdf, make_png, write, set_mtime, fresh_dir,  # noqa: E402
                     navigate, names, selected_names, row, click_row, modal_count,
                     close_modals, toasts, clear_toasts)

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "oa_e2e"))
C = Checker()
pw, browser, page = connect()
errors: list[str] = []
page.on("pageerror", lambda e: errors.append(str(e)))


def ev(js, arg=None):
    return page.evaluate(js, arg) if arg is not None else page.evaluate(js)


def focus_list():
    ev("() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); document.getElementById('filelist-list').focus(); }")


def renaming():
    return ev("() => FileTree.isRenaming()")


def edit_value():
    return ev("() => (document.querySelector('.inline-rename')||{}).value")


def start_f2(name):
    click_row(page, name)
    focus_list()
    page.keyboard.press("F2")
    time.sleep(0.5)


def type_name(text, close_list=True):
    page.keyboard.press("Control+a")
    page.keyboard.type(text)
    time.sleep(0.3)
    if close_list:
        page.keyboard.press("Escape")       # close suggestions so Enter uses the typed text
        time.sleep(0.1)


def cancel():
    for _ in range(2):
        if renaming():
            page.keyboard.press("Escape")
            time.sleep(0.15)


tpl_files = {}
orig_lang = ev("() => SFM.getRenameLang().then(r => r.lang)")
for lang in ("en", "ko"):
    f = ev("l => SFM.getRenameTemplates(l).then(r => r.user_file || '')", lang)
    if f and f not in tpl_files:
        tpl_files[f] = open(f, "rb").read() if os.path.exists(f) else None

try:
    ev("() => { App.switchPanel('workspace'); FileTree.setViewMode('list'); }")
    d = fresh_dir(ROOT, "rename")
    now = time.time()
    for i, n in enumerate(["one.txt", "two.txt", "three.txt", "gamma.txt", "scan.pdf"]):
        p = os.path.join(d, n)
        (make_pdf(p) if n.endswith(".pdf") else write(p, n))
        set_mtime(p, now - 100 * (i + 1))
    os.makedirs(os.path.join(d, "Folder A"))
    os.makedirs(os.path.join(d, "Folder B"))
    set_mtime(os.path.join(d, "Folder A"), now - 1000)
    set_mtime(os.path.join(d, "Folder B"), now - 1100)
    navigate(page, d)
    order = names(page)

    # folder rename: whole name selected, Enter renames
    start_f2("Folder A")
    s = ev("() => { const i = document.querySelector('.inline-rename'); return [i.selectionStart, i.selectionEnd]; }")
    C.check("folder rename selects the whole name", s == [0, len("Folder A")], str(s))
    type_name("Folder Renamed")
    page.keyboard.press("Enter"); time.sleep(0.8)
    C.check("folder renamed on disk", os.path.isdir(os.path.join(d, "Folder Renamed")))
    # folder clash keeps editing with a tip
    start_f2("Folder Renamed")
    type_name("Folder B")
    page.keyboard.press("Enter"); time.sleep(0.5)
    C.check("folder name clash keeps editing + tip", renaming() and ev("() => !!document.querySelector('.inline-rename-tip.show')"))
    cancel()
    C.check("folder unchanged after clash", os.path.isdir(os.path.join(d, "Folder Renamed")))
    # reserved device name
    start_f2("one.txt")
    type_name("CON.txt")
    page.keyboard.press("Enter"); time.sleep(0.4)
    C.check("reserved name refused", renaming() and "reserved" in ev("() => document.querySelector('.inline-rename-tip').textContent"))
    cancel()
    # trailing dots / spaces dropped
    start_f2("one.txt")
    type_name("uno.txt...  ")
    page.keyboard.press("Enter"); time.sleep(0.7)
    C.check("trailing dots/spaces dropped", os.path.exists(os.path.join(d, "uno.txt")), str(os.listdir(d)))
    # case-only rename
    start_f2("gamma.txt")
    type_name("Gamma.txt")
    page.keyboard.press("Enter"); time.sleep(0.7)
    C.check("case-only rename", "Gamma.txt" in os.listdir(d), str(os.listdir(d)))
    # empty name reverts
    start_f2("two.txt")
    page.keyboard.press("Control+a"); page.keyboard.press("Delete"); time.sleep(0.2)
    page.keyboard.press("Escape"); page.keyboard.press("Enter"); time.sleep(0.5)
    C.check("empty name keeps the old name", os.path.exists(os.path.join(d, "two.txt")) and not renaming())
    # Shift+Tab goes to the previous item
    order = names(page)
    i = order.index("three.txt")
    start_f2("three.txt")
    page.keyboard.press("Shift+Tab"); time.sleep(0.6)
    C.check("Shift+Tab edits the previous item", renaming() and edit_value() == order[i - 1], f"{edit_value()} vs {order[i - 1]}")
    cancel()
    # Tab on the last item just commits
    last = names(page)[-1]
    start_f2(last)
    page.keyboard.press("Tab"); time.sleep(0.5)
    C.check("Tab on the last item ends editing", not renaming())
    # click elsewhere commits the typed name
    start_f2("two.txt")
    type_name("dos.txt")
    page.click("#breadcrumb"); time.sleep(0.8)
    C.check("clicking away commits", os.path.exists(os.path.join(d, "dos.txt")), str(os.listdir(d)))
    # rename survives a refresh while editing (F5 is blocked in the box, use the API)
    start_f2("three.txt")
    page.keyboard.type("x")
    ev("() => FileTree.refresh()"); time.sleep(0.7)
    C.check("a list refresh keeps the edit in progress", renaming() and edit_value() == "x.txt", str(edit_value()))
    cancel()
    # thumbnail view rename
    ev("() => FileTree.setViewMode('thumb')"); time.sleep(0.6)
    page.locator("#filelist-thumb .thumb-tile", has=page.locator(".thumb-name", has_text="three.txt")).first.click()
    focus_list(); ev("() => document.getElementById('filelist-thumb').focus()")
    page.keyboard.press("F2"); time.sleep(0.5)
    C.check("thumb view rename uses a textarea", ev("() => document.activeElement.tagName") == "TEXTAREA")
    type_name("tres.txt")
    page.keyboard.press("Enter"); time.sleep(0.8)
    C.check("thumb view rename commits", os.path.exists(os.path.join(d, "tres.txt")), str(os.listdir(d)))
    ev("() => FileTree.setViewMode('list')"); time.sleep(0.4)
    # rename then search: F2 acts on the visible focused file
    page.fill("#search-input", "dos"); time.sleep(0.5)
    click_row(page, "dos.txt"); focus_list()
    page.keyboard.press("F2"); time.sleep(0.4)
    C.check("F2 in a filtered list edits the right file", edit_value() == "dos.txt")
    cancel()
    page.focus("#search-input"); page.keyboard.press("Escape"); time.sleep(0.4)
    # initialText (QR → name) pre-fill
    ev("() => FileTree.startRename(FileTree.getEntry ? null : null)")
    path = os.path.join(d, "scan.pdf")
    ev("p => FileTree.startRename(p, 'QR:Value/With*Bad')", path); time.sleep(0.5)
    C.check("pre-filled rename strips bad characters", edit_value() == "QR Value With Bad" or "*" not in (edit_value() or ""), str(edit_value()))
    cancel()

    # ── template language ─────────────────────────────────────────────────
    ev("() => RenameTemplates.setLang('ko')"); time.sleep(0.2)
    start_f2("scan.pdf")
    page.keyboard.press("Control+a"); page.keyboard.type("passport"); time.sleep(0.8)
    tgt = ev("() => (document.querySelector('.inline-rename-suggestions .rt-item .rt-target')||{}).textContent || ''")
    C.check("Korean mode suggests KR-EN names", any("가" <= ch <= "힣" for ch in tgt), tgt)
    sel = page.locator(".inline-rename-suggestions #rt-lang-inline")
    sel.select_option("en"); time.sleep(0.8)
    tgt = ev("() => (document.querySelector('.inline-rename-suggestions .rt-item .rt-target')||{}).textContent || ''")
    C.check("switching to International gives English names", tgt and not any("가" <= ch <= "힣" for ch in tgt), tgt)
    C.check("language choice is saved", ev("() => SFM.getRenameLang().then(r => r.lang)") == "en")
    C.check("focus returns to the rename box after language change", ev("() => document.activeElement.classList.contains('inline-rename')"))
    page.keyboard.press("ArrowDown"); page.keyboard.press("ArrowDown"); time.sleep(0.1)
    second = ev("() => (document.querySelector('.inline-rename-suggestions .rt-item.active .rt-target')||{}).textContent || ''")
    page.keyboard.press("Enter"); time.sleep(0.8)
    C.check("arrow-picked template applied", second.strip() in os.listdir(d), f"{second} / {os.listdir(d)}")
    cancel()

    # ── templates manager ─────────────────────────────────────────────────
    ev("() => RenameTemplates.openManager()"); time.sleep(0.8)
    C.check("manager opens", ev("() => !!document.querySelector('.rt-manager')"))
    n0 = ev("() => document.querySelectorAll('#rt-list .rt-list-row').length")
    page.fill("#rt-search", "passport"); time.sleep(0.2)
    C.check("manager search filters", 0 < ev("() => document.querySelectorAll('#rt-list .rt-list-row').length") < n0)
    page.fill("#rt-search", "")
    page.fill("#rt-en", "QA Manager Row")
    page.click("#rt-add"); time.sleep(0.2)
    C.check("Add as new puts the row first", "QA Manager Row" in ev("() => document.querySelector('#rt-list .rt-list-row').textContent"))
    page.fill("#rt-en", "QA Manager Row 2")
    page.click("#rt-apply"); time.sleep(0.2)
    C.check("Apply changes edits the custom row", "QA Manager Row 2" in ev("() => document.querySelector('#rt-list .rt-list-row').textContent"))
    page.fill("#rt-en", "bad/name"); page.click("#rt-add"); time.sleep(0.2)
    C.check("bad characters refused", "bad/name" not in ev("() => document.getElementById('rt-list').textContent"))
    page.click("#rt-close"); time.sleep(0.4)
    C.check("closing with unsaved changes asks", modal_count(page) >= 2)
    page.keyboard.press("Escape"); time.sleep(0.3)
    C.check("cancel keeps the manager open", ev("() => !!document.querySelector('.rt-manager')"))
    page.click("#rt-save"); time.sleep(0.6)
    rows = ev("() => SFM.getRenameTemplates('en').then(r => r.rows.filter(x => !x.builtin).map(x => x.en))")
    C.check("Save to file stores the row", "QA Manager Row 2" in rows, str(rows))
    page.locator("#rt-list .rt-list-row", has_text="QA Manager Row 2").click()
    page.click("#rt-remove"); page.click("#rt-save"); time.sleep(0.6)
    rows = ev("() => SFM.getRenameTemplates('en').then(r => r.rows.filter(x => !x.builtin).map(x => x.en))")
    C.check("Remove + save drops the row", "QA Manager Row 2" not in rows, str(rows))
    page.click("#rt-close"); time.sleep(0.3)
    C.check("manager closes", not ev("() => !!document.querySelector('.rt-manager')"))

    # ── toolbar odds and ends ─────────────────────────────────────────────
    write(os.path.join(d, "external.txt"), "made outside")
    focus_list(); page.keyboard.press("F5"); time.sleep(0.7)
    C.check("F5 refresh shows new files", "external.txt" in names(page))
    page.click("#btn-open-folder", button="right"); time.sleep(0.3)
    C.check("right-click Open folder lists recent folders", ev("() => !!document.getElementById('recent-dropdown')"))
    page.mouse.click(5, 5); time.sleep(0.2)
    ev("() => FileTree.setViewMode('thumb')"); time.sleep(0.3)
    w0 = ev("() => document.querySelector('#filelist-thumb .img-wrap').offsetWidth")
    page.click("#thumb-zoom-in"); time.sleep(0.3)
    w1 = ev("() => document.querySelector('#filelist-thumb .img-wrap').offsetWidth")
    C.check("thumbnail zoom in", w1 > w0, f"{w0}->{w1}")
    page.click("#thumb-zoom-out"); page.click("#thumb-zoom-out"); time.sleep(0.3)
    C.check("thumbnail zoom out", ev("() => document.querySelector('#filelist-thumb .img-wrap').offsetWidth") < w1)
    ev("() => FileTree.setViewMode('list')")
    rz = page.locator("#resizer-tree-filelist")
    if rz.count():
        bx = rz.bounding_box(); wt = ev("() => document.getElementById('pane-tree').offsetWidth")
        page.mouse.move(bx["x"] + 2, bx["y"] + 50); page.mouse.down(); page.mouse.move(bx["x"] + 60, bx["y"] + 50, steps=5); page.mouse.up()
        C.check("pane resizer drags", ev("() => document.getElementById('pane-tree').offsetWidth") > wt)
    C.check("status bar shows folder name", ev("() => document.getElementById('status-folder-name').textContent") == os.path.basename(d))
    C.check("no uncaught page errors", not errors, "; ".join(errors[:5]))
finally:
    try:
        close_modals(page)
        ev("l => SFM.setRenameLang(l)", orig_lang)
    except Exception:
        pass
    for f, data in tpl_files.items():
        if data is not None:
            with open(f, "wb") as fh:
                fh.write(data)
    rc = C.summary()
    pw.stop()
sys.exit(rc)
