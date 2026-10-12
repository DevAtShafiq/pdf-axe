"""End-to-end checks for the file-browser shell (navigation, list, sorting,
selection, keyboard, rename, new folder, clipboard, Move to review, undo,
context menu, details pane, dialogs).

Usage:  python qa/e2e/shell_e2e.py <scratch root>
Creates fresh fixture folders under <scratch root>; never deletes anything.
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
saved_sort = page.evaluate("() => localStorage.getItem('oa.filelist.sort')")
# Ctrl+Enter saves a template into the user's template file(s): put them back afterwards.
_tpl_files = {}
for _lang in ("en", "ko"):
    _f = page.evaluate("l => SFM.getRenameTemplates(l).then(r => r.user_file || '')", _lang)
    if _f and _f not in _tpl_files:
        _tpl_files[_f] = open(_f, "rb").read() if os.path.exists(_f) else None


def ev(js, arg=None):
    return page.evaluate(js, arg) if arg is not None else page.evaluate(js)


def focus_list():
    ev("() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); document.getElementById('filelist-list').focus(); }")


def press(key, n=1):
    for _ in range(n):
        page.keyboard.press(key)
        time.sleep(0.08)


def fixture() -> str:
    d = fresh_dir(ROOT, "shell")
    now = time.time()
    files = [
        ("alpha.pdf", lambda p: make_pdf(p, 2), now - 5000),
        ("beta.png", make_png, now - 4000),
        ("gamma.txt", lambda p: write(p, "gamma text " * 50), now - 3000),
        ("file10.txt", lambda p: write(p, "1"), now - 2000),
        ("file2.txt", lambda p: write(p, "22"), now - 1000),
    ]
    for n, mk, t in files:
        p = os.path.join(d, n)
        mk(p)
        set_mtime(p, t)
    sub = os.path.join(d, "Sub Folder")
    os.makedirs(sub)
    write(os.path.join(sub, "inner.txt"), "inner")
    set_mtime(sub, now - 6000)
    return d


try:
    ev("() => { App.switchPanel('workspace'); FileTree.setViewMode('list'); }")
    ev("() => localStorage.removeItem('oa.filelist.sort')")
    ev("() => FileTree.setSort('mtime') || FileTree.setSort('mtime')")  # ensure some state; reset below
    # reset sort to the default newest-first
    ev("""() => { const s = JSON.parse(localStorage.getItem('oa.filelist.sort')||'{}');
                  if (s.key !== 'mtime' || s.dir !== 'desc') FileTree.setSort('mtime');
                  FileTree.setSort(null, false); }""")

    # ── boot ──────────────────────────────────────────────────────────────
    C.check("tree lists drives", ev("() => document.querySelectorAll('#tree-body .tree-drive').length") >= 1)
    C.check("empty state before a folder is opened", "Open a folder" in ev("() => document.getElementById('filelist-list').textContent") or ev("() => !!FileTree.getCurrentFolder()"))

    d = fixture()
    navigate(page, d)
    C.check("navigate: current folder set", ev("() => FileTree.getCurrentFolder()") == d)
    n = names(page)
    C.check("default sort newest first", n == ["file2.txt", "file10.txt", "gamma.txt", "beta.png", "alpha.pdf", "Sub Folder"], str(n))
    C.check("status shows item count", "6 items" in ev("() => document.getElementById('status-text').textContent"))
    C.check("breadcrumb last segment is folder", ev("() => document.querySelector('#breadcrumb .breadcrumb-seg.last').textContent") == os.path.basename(d))
    C.check("tree highlights current folder", ev("() => !!document.querySelector('#tree-body .tree-node.active')"))

    # ── sorting ───────────────────────────────────────────────────────────
    page.click("#fl-sort-bar .col-sort[data-sort='name']")
    time.sleep(0.2)
    n = names(page)
    C.check("sort by name asc (natural)", n == ["alpha.pdf", "beta.png", "file2.txt", "file10.txt", "gamma.txt", "Sub Folder"], str(n))
    page.click("#fl-sort-bar .col-sort[data-sort='name']")
    time.sleep(0.2)
    C.check("sort by name desc", names(page)[0] == "Sub Folder")
    page.click("#fl-sort-bar .col-sort[data-sort='size']")
    time.sleep(0.2)
    n = names(page)
    biggest = max((x for x in os.listdir(d) if os.path.isfile(os.path.join(d, x))), key=lambda x: os.path.getsize(os.path.join(d, x)))
    C.check("sort by size desc (folders last)", n[0] == biggest and n[-1] == "Sub Folder", str(n))
    page.click("#fl-folders-first")
    time.sleep(0.2)
    C.check("folders first", names(page)[0] == "Sub Folder")
    C.check("folders-first button pressed", ev("() => document.getElementById('fl-folders-first').getAttribute('aria-pressed')") == "true")
    page.click("#fl-folders-first")
    page.click("#fl-sort-bar .col-sort[data-sort='mtime']")
    time.sleep(0.2)
    C.check("sort by date desc again", names(page)[0] == "file2.txt", str(names(page)))
    C.check("sort remembered in localStorage", '"mtime"' in (ev("() => localStorage.getItem('oa.filelist.sort')") or ""))

    # sort keeps focus on the same FILE (F2 renamed the wrong one before)
    click_row(page, "gamma.txt")
    page.click("#fl-sort-bar .col-sort[data-sort='name']")
    time.sleep(0.2)
    C.check("sort keeps focused file", ev("() => (FileTree.getFocusedEntry()||{}).name") == "gamma.txt", str(ev("() => (FileTree.getFocusedEntry()||{}).name")))
    page.click("#fl-sort-bar .col-sort[data-sort='mtime']")  # name desc → mtime desc
    time.sleep(0.2)
    if names(page)[0] != "file2.txt":
        page.click("#fl-sort-bar .col-sort[data-sort='mtime']")
        time.sleep(0.2)

    # ── selection ─────────────────────────────────────────────────────────
    click_row(page, "file10.txt")
    C.check("click selects one", selected_names(page) == ["file10.txt"])
    C.check("details pane shows name", "file10.txt" in ev("() => document.getElementById('detail-rows').textContent"))
    click_row(page, "beta.png", ["Control"])
    C.check("ctrl+click adds", selected_names(page) == ["beta.png", "file10.txt"])
    C.check("status shows 2 selected", ev("() => document.getElementById('status-sel-text').textContent") == "2 selected")
    C.check("details: multi summary", "2 items" in ev("() => document.getElementById('detail-rows').textContent"))
    click_row(page, "beta.png", ["Control"])
    C.check("ctrl+click toggles off", selected_names(page) == ["file10.txt"])
    click_row(page, "file2.txt")
    click_row(page, "beta.png", ["Shift"])
    C.check("shift+click range", selected_names(page) == ["beta.png", "file10.txt", "file2.txt", "gamma.txt"], str(selected_names(page)))
    # ctrl-click sets the anchor for a later shift-click
    click_row(page, "file2.txt")
    click_row(page, "gamma.txt", ["Control"])
    click_row(page, "alpha.pdf", ["Shift"])
    C.check("shift-range from ctrl-click anchor", selected_names(page) == ["alpha.pdf", "beta.png", "gamma.txt"], str(selected_names(page)))
    C.check("toolbar convert enabled for pdf/img", ev("() => !document.getElementById('btn-convert').disabled"))

    # keyboard
    click_row(page, "file2.txt")
    focus_list()
    press("ArrowDown")
    C.check("ArrowDown moves selection", selected_names(page) == ["file10.txt"])
    press("Shift+ArrowDown")
    C.check("Shift+ArrowDown extends", selected_names(page) == ["file10.txt", "gamma.txt"])
    press("End")
    C.check("End selects last", selected_names(page) == ["Sub Folder"])
    press("Home")
    C.check("Home selects first", selected_names(page) == ["file2.txt"])
    press("Control+a")
    C.check("Ctrl+A selects all", len(selected_names(page)) == 6)
    press("Escape")
    press("g")
    C.check("type-ahead jumps", selected_names(page) == ["gamma.txt"])
    press("b")
    C.check("type-ahead other letter", selected_names(page) == ["beta.png"])
    press("Space")
    C.check("Space toggles selection off", selected_names(page) == [])
    press("Space")
    # Caps Lock: Shift-less upper-case letters still work for Ctrl+A
    page.keyboard.down("Control"); page.keyboard.press("A"); page.keyboard.up("Control")
    time.sleep(0.1)
    C.check("Ctrl+Shift-less 'A' (caps) selects all", len(selected_names(page)) == 6)

    # search filter
    page.fill("#search-input", "file")
    time.sleep(0.5)
    C.check("search filters list", sorted(names(page)) == ["file10.txt", "file2.txt"], str(names(page)))
    C.check("search drops hidden items from selection", selected_names(page) == ["file10.txt", "file2.txt"], str(selected_names(page)))
    page.fill("#search-input", "zzz")
    time.sleep(0.5)
    C.check("search no-match empty state", "No matches" in ev("() => document.getElementById('filelist-list').textContent"))
    page.focus("#search-input")
    press("Escape")
    time.sleep(0.4)
    C.check("Esc clears search", len(names(page)) == 6)

    # view mode
    ev("() => FileTree.setViewMode('thumb')")
    time.sleep(0.8)
    C.check("thumb view shows tiles", ev("() => document.querySelectorAll('#filelist-thumb .thumb-tile').length") == 6)
    C.check("thumb view loads image thumbnails", ev("() => document.querySelectorAll('#filelist-thumb img').length") >= 1)
    ev("() => FileTree.setViewMode('list')")

    # ── navigation ────────────────────────────────────────────────────────
    row(page, "Sub Folder").dblclick()
    time.sleep(0.7)
    sub = os.path.join(d, "Sub Folder")
    C.check("double-click folder navigates", ev("() => FileTree.getCurrentFolder()") == sub)
    C.check("back enabled", not ev("() => document.getElementById('btn-nav-back').disabled"))
    page.click("#btn-nav-back"); time.sleep(0.7)
    C.check("back goes to parent", ev("() => FileTree.getCurrentFolder()") == d)
    page.click("#btn-nav-forward"); time.sleep(0.7)
    C.check("forward", ev("() => FileTree.getCurrentFolder()") == sub)
    page.click("#btn-nav-up"); time.sleep(0.7)
    C.check("up", ev("() => FileTree.getCurrentFolder()") == d)
    # breadcrumb click then Up (Up did nothing after a breadcrumb click)
    page.locator("#breadcrumb .breadcrumb-seg").nth(-2).click(); time.sleep(0.7)
    cur = ev("() => FileTree.getCurrentFolder()")
    C.check("breadcrumb navigates to parent", cur.lower() == os.path.dirname(d).lower(), cur)
    page.click("#btn-nav-up"); time.sleep(0.7)
    cur2 = ev("() => FileTree.getCurrentFolder()")
    C.check("Up works after breadcrumb", cur2.lower() == os.path.dirname(os.path.dirname(d)).lower(), cur2)
    # drive root: Up stops at C:\ and lists the root, not drive C's cwd
    drive = os.path.splitdrive(d)[0] + "\\"
    top = os.path.join(drive, os.path.splitdrive(d)[1].strip("\\").split("\\")[0])   # e.g. C:\Users
    navigate(page, top)
    page.click("#btn-nav-up"); time.sleep(0.9)
    C.check("Up from a top folder reaches the drive root", ev("() => FileTree.getCurrentFolder()") == drive, ev("() => FileTree.getCurrentFolder()"))
    page.click("#btn-nav-up"); time.sleep(0.4)
    C.check("Up at drive root stays", ev("() => FileTree.getCurrentFolder()") == drive)
    root_names = set(names(page))
    C.check("drive root lists the root", set(os.listdir(drive)) >= root_names and len(root_names) > 0)
    navigate(page, d)
    focus_list()
    click_row(page, "gamma.txt")
    focus_list()
    press("Backspace"); time.sleep(0.7)
    C.check("Backspace goes up", ev("() => FileTree.getCurrentFolder()").lower() == os.path.dirname(d).lower())
    navigate(page, d)
    # Enter on a folder opens it
    click_row(page, "Sub Folder"); focus_list(); press("Enter"); time.sleep(0.7)
    C.check("Enter opens folder", ev("() => FileTree.getCurrentFolder()") == sub)
    page.keyboard.press("Alt+ArrowLeft"); time.sleep(0.7)
    C.check("Alt+Left = back", ev("() => FileTree.getCurrentFolder()") == d)
    page.keyboard.press("Alt+ArrowRight"); time.sleep(0.7)
    C.check("Alt+Right = forward", ev("() => FileTree.getCurrentFolder()") == sub)
    page.keyboard.press("Alt+ArrowUp"); time.sleep(0.7)
    C.check("Alt+Up = up", ev("() => FileTree.getCurrentFolder()") == d)
    C.check("recent folders recorded", ev("() => App.state.recentFolders.length") >= 2)

    # tree: expand a nested folder and keep it open across navigation
    ev("f => App.navigate(f)", d); time.sleep(0.8)
    cur_node = page.locator("#tree-body .tree-node.active").first
    C.check("current-folder node active in tree", cur_node.count() == 1)
    arrow = page.locator(f"#tree-body .tree-node[data-path='{d.replace(chr(92), chr(92)*2)}'] > .tree-line .tree-arrow").first
    if arrow.count():
        arrow.click(); time.sleep(0.7)
        C.check("tree arrow expands children", ev("f => !!document.querySelector(`.tree-children[data-parent-path=\"${CSS.escape(f)}\"] .tree-node`)", d))
        ev("f => App.navigate(f)", sub); time.sleep(0.8)
        ev("f => App.navigate(f)", d); time.sleep(0.9)
        C.check("expanded tree node stays expanded after navigation",
                ev("f => !!document.querySelector(`.tree-children[data-parent-path=\"${CSS.escape(f)}\"] .tree-node`)", d))

    # ── new folder ────────────────────────────────────────────────────────
    focus_list()
    page.keyboard.press("Control+Shift+N"); time.sleep(0.4)
    C.check("Ctrl+Shift+N opens New folder dialog", modal_count(page) == 1)
    C.check("suggested name", ev("() => document.querySelector('.modal-overlay input').value") == "New folder")
    page.keyboard.type("Bad:name"); page.keyboard.press("Enter"); time.sleep(0.3)
    C.check("invalid name is rejected", modal_count(page) == 1 and "can’t contain" in ev("() => document.querySelector('.modal-overlay .field-error').textContent"))
    page.fill(".modal-overlay input", "Sub Folder"); page.keyboard.press("Enter"); time.sleep(0.3)
    C.check("existing name is rejected", modal_count(page) == 1)
    page.fill(".modal-overlay input", "Made By Test"); page.keyboard.press("Enter"); time.sleep(1.0)
    C.check("folder created on disk", os.path.isdir(os.path.join(d, "Made By Test")))
    C.check("new folder selected", selected_names(page) == ["Made By Test"])
    page.keyboard.press("Control+Shift+KeyN"); time.sleep(0.3)
    C.check("next suggestion is New folder", ev("() => document.querySelector('.modal-overlay input').value") == "New folder")
    press("Escape"); time.sleep(0.2)
    C.check("Esc cancels New folder", modal_count(page) == 0 and not os.path.exists(os.path.join(d, "New folder")))

    # keys are not hijacked behind a dialog
    click_row(page, "gamma.txt")
    page.click("#btn-more"); time.sleep(0.3)
    C.check("More menu opens", modal_count(page) == 1)
    page.keyboard.press("Delete"); time.sleep(0.3)
    C.check("Delete behind a dialog does nothing", modal_count(page) == 1 and os.path.exists(os.path.join(d, "gamma.txt")))
    page.keyboard.press("ArrowDown"); time.sleep(0.2)
    C.check("arrows behind a dialog don't move selection", selected_names(page) == ["gamma.txt"])
    page.keyboard.press("F2"); time.sleep(0.2)
    C.check("F2 behind a dialog does nothing", not ev("() => FileTree.isRenaming()"))
    close_modals(page)

    # ── inline rename ─────────────────────────────────────────────────────
    click_row(page, "file10.txt"); focus_list()
    page.keyboard.press("F2"); time.sleep(0.6)
    C.check("F2 starts inline rename", ev("() => FileTree.isRenaming()"))
    sel = ev("() => { const i = document.querySelector('.inline-rename'); return [i.selectionStart, i.selectionEnd, i.value]; }")
    C.check("stem selected (not extension)", sel == [0, 6, "file10.txt"], str(sel))
    C.check("suggestion list open", ev("() => !!document.querySelector('.inline-rename-suggestions:not(.hidden)')"))
    page.keyboard.type("renamed10")
    page.keyboard.press("Escape"); time.sleep(0.2)
    page.keyboard.press("Enter"); time.sleep(0.8)
    C.check("Enter renames on disk", os.path.exists(os.path.join(d, "renamed10.txt")) and not os.path.exists(os.path.join(d, "file10.txt")))
    C.check("renamed item stays selected", selected_names(page) == ["renamed10.txt"])
    # Tab moves to next file
    nl = names(page)
    nxt = nl[nl.index("renamed10.txt") + 1] if "renamed10.txt" in nl else None
    page.keyboard.press("F2"); time.sleep(0.5)
    page.keyboard.press("Tab"); time.sleep(0.6)
    C.check("Tab commits and edits the next file", ev("() => FileTree.isRenaming()") and
            ev("() => document.querySelector('.inline-rename').value") == nxt, str(nxt))
    page.keyboard.press("Escape"); page.keyboard.press("Escape"); time.sleep(0.3)
    C.check("Esc Esc cancels rename", not ev("() => FileTree.isRenaming()"))
    # Ctrl+Z undoes the rename
    focus_list()
    page.keyboard.press("Control+z"); time.sleep(0.9)
    C.check("Ctrl+Z undoes rename", os.path.exists(os.path.join(d, "file10.txt")))
    page.keyboard.press("Control+y"); time.sleep(0.9)
    C.check("Ctrl+Y redoes rename", os.path.exists(os.path.join(d, "renamed10.txt")))
    # invalid character blocked
    click_row(page, "renamed10.txt"); focus_list(); page.keyboard.press("F2"); time.sleep(0.4)
    page.keyboard.press("End"); page.keyboard.type("?*")
    v = ev("() => document.querySelector('.inline-rename').value")
    C.check("invalid chars stripped while typing", "?" not in v and "*" not in v, v)
    C.check("balloon tip shown", ev("() => !!document.querySelector('.inline-rename-tip.show')"))
    page.keyboard.press("Escape"); page.keyboard.press("Escape"); time.sleep(0.2)
    # name clash on a file offers "(2)"
    click_row(page, "renamed10.txt"); focus_list(); page.keyboard.press("F2"); time.sleep(0.4)
    page.keyboard.press("Control+a"); page.keyboard.type("file2.txt")
    page.keyboard.press("Escape"); time.sleep(0.1)
    page.keyboard.press("Enter"); time.sleep(0.6)
    C.check("clash asks to use (2)", modal_count(page) == 1 and "file2 (2).txt" in ev("() => document.querySelector('.modal-overlay').textContent"))
    page.keyboard.press("Enter"); time.sleep(0.8)
    C.check("clash renamed to file2 (2).txt", os.path.exists(os.path.join(d, "file2 (2).txt")))
    # extension change asks
    click_row(page, "file2 (2).txt"); focus_list(); page.keyboard.press("F2"); time.sleep(0.4)
    page.keyboard.press("Control+a"); page.keyboard.type("ext.md")
    page.keyboard.press("Escape"); time.sleep(0.1)
    page.keyboard.press("Enter"); time.sleep(0.5)
    C.check("extension change asks first", modal_count(page) == 1 and "extension" in ev("() => document.querySelector('.modal-overlay').textContent"))
    page.click(".modal-overlay [data-modal-btn='No']"); time.sleep(0.4)
    C.check("No keeps editing", ev("() => FileTree.isRenaming()") and os.path.exists(os.path.join(d, "file2 (2).txt")))
    page.keyboard.press("Escape"); page.keyboard.press("Escape"); time.sleep(0.2)
    # template pick via suggestions
    click_row(page, "beta.png"); focus_list(); page.keyboard.press("F2"); time.sleep(0.5)
    page.keyboard.press("Control+a"); page.keyboard.type("passport"); time.sleep(0.8)
    C.check("typing filters templates", ev("() => document.querySelectorAll('.inline-rename-suggestions .rt-item').length") >= 1)
    page.keyboard.press("Enter"); time.sleep(0.9)
    after = [x for x in os.listdir(d) if x.lower().endswith(".png")]
    C.check("Enter applies the top template", after and "passport" in after[0].lower(), str(after))
    # Ctrl+Enter = rename + save template
    png = after[0] if after else "beta.png"
    click_row(page, png); focus_list(); page.keyboard.press("F2"); time.sleep(0.5)
    page.keyboard.press("Control+a"); page.keyboard.type("QA Custom Doc"); time.sleep(0.5)
    page.keyboard.press("Control+Enter"); time.sleep(1.0)
    C.check("Ctrl+Enter renames", os.path.exists(os.path.join(d, "QA Custom Doc.png")))
    C.check("Ctrl+Enter saved template", any("QA Custom Doc" in (r.get("en") or "") + (r.get("local") or "")
                                             for r in ev("() => SFM.getRenameTemplates().then(r => r.rows)")))

    # ── clipboard ─────────────────────────────────────────────────────────
    clear_toasts(page)
    click_row(page, "gamma.txt"); focus_list()
    page.keyboard.press("Control+c"); time.sleep(0.5)
    cb = ev("() => SFM.getClipboardFiles()")
    C.check("Ctrl+C puts file on Windows clipboard", cb["ok"] and [os.path.basename(p) for p in cb["paths"]] == ["gamma.txt"] and not cb["cut"], str(cb))
    page.keyboard.press("Control+v"); time.sleep(1.0)
    C.check("Ctrl+V pastes a numbered copy", os.path.exists(os.path.join(d, "gamma (2).txt")))
    C.check("pasted copy selected", selected_names(page) == ["gamma (2).txt"], str(selected_names(page)))
    # paste into subfolder
    row(page, "Sub Folder").dblclick(); time.sleep(0.7)
    focus_list(); page.keyboard.press("Control+v"); time.sleep(1.0)
    C.check("paste into another folder", os.path.exists(os.path.join(sub, "gamma.txt")))
    page.keyboard.press("Alt+ArrowLeft"); time.sleep(0.7)
    # Caps-lock style upper-case C
    click_row(page, "alpha.pdf"); focus_list()
    page.keyboard.down("Control"); page.keyboard.press("C"); page.keyboard.up("Control"); time.sleep(0.5)
    cb = ev("() => SFM.getClipboardFiles()")
    C.check("Ctrl+C with Caps Lock copies files", [os.path.basename(p) for p in cb["paths"]] == ["alpha.pdf"], str(cb))
    # cut + paste moves
    click_row(page, "gamma (2).txt"); focus_list()
    page.keyboard.press("Control+x"); time.sleep(0.5)
    C.check("Ctrl+X marks cut on clipboard", ev("() => SFM.getClipboardFiles()")["cut"] is True)
    row(page, "Sub Folder").dblclick(); time.sleep(0.7)
    focus_list(); page.keyboard.press("Control+v"); time.sleep(1.0)
    C.check("cut+paste moves the file", os.path.exists(os.path.join(sub, "gamma (2).txt")) and not os.path.exists(os.path.join(d, "gamma (2).txt")))
    # copy a folder into itself is refused
    page.keyboard.press("Alt+ArrowLeft"); time.sleep(0.7)
    click_row(page, "Sub Folder"); focus_list(); page.keyboard.press("Control+c"); time.sleep(0.4)
    row(page, "Sub Folder").dblclick(); time.sleep(0.7)
    clear_toasts(page)
    focus_list(); page.keyboard.press("Control+v"); time.sleep(1.0)
    C.check("pasting a folder into itself is refused", not os.path.exists(os.path.join(sub, "Sub Folder")) and any("itself" in t for t in toasts(page)), str(toasts(page)))
    page.keyboard.press("Alt+ArrowLeft"); time.sleep(0.7)
    # Ctrl+C on a text selection in the preview copies text, not files
    ev("() => SFM.setClipboard('sentinel')")
    click_row(page, "gamma.txt"); time.sleep(1.0)
    tp = page.locator("#text-preview")
    if tp.count() and tp.is_visible():
        box = tp.bounding_box()
        page.mouse.click(box["x"] + 10, box["y"] + 10)        # last click outside the list
        ev("""() => { const n = document.getElementById('text-preview'); const r = document.createRange();
                      r.selectNodeContents(n); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
        page.keyboard.press("Control+c"); time.sleep(0.4)
        win32 = __import__("win32clipboard")
        win32.OpenClipboard()
        try:
            t = win32.GetClipboardData(win32.CF_UNICODETEXT)
        finally:
            win32.CloseClipboard()
        C.check("Ctrl+C with preview text selected copies text, not files",
                ev("() => SFM.getClipboardFiles()")["paths"] == [] and "gamma text" in t, t[:60])
    else:
        C.check("text preview visible for .txt", False)

    # ── Move to review + undo ─────────────────────────────────────────────
    click_row(page, "gamma.txt"); focus_list()
    page.keyboard.press("Delete"); time.sleep(0.4)
    txt = ev("() => (document.querySelector('.modal-overlay')||{}).textContent || ''")
    C.check("Delete asks to move to review", "gamma.txt" in txt and "_to_review" in txt)
    page.keyboard.press("Enter"); time.sleep(0.9)
    rv = os.path.join(d, "_to_review")
    C.check("file moved into _to_review", not os.path.exists(os.path.join(d, "gamma.txt")) and any(x.endswith("gamma.txt") for x in os.listdir(rv)))
    focus_list(); page.keyboard.press("Control+z"); time.sleep(1.0)
    C.check("Ctrl+Z restores the file", os.path.exists(os.path.join(d, "gamma.txt")))
    page.keyboard.press("Control+y"); time.sleep(1.0)
    C.check("Ctrl+Y moves it to review again", not os.path.exists(os.path.join(d, "gamma.txt")))
    page.keyboard.press("Control+z"); time.sleep(1.0)
    C.check("second undo restores again", os.path.exists(os.path.join(d, "gamma.txt")))
    # cancel keeps the file
    click_row(page, "gamma.txt"); focus_list(); page.keyboard.press("Delete"); time.sleep(0.3)
    page.keyboard.press("Escape"); time.sleep(0.3)
    C.check("Esc cancels Move to review", os.path.exists(os.path.join(d, "gamma.txt")) and modal_count(page) == 0)

    # ── context menu ──────────────────────────────────────────────────────
    row(page, "gamma.txt").click(button="right"); time.sleep(0.3)
    items = ev("() => [...document.querySelectorAll('#ctx-menu:not(.hidden) .ctx-label')].map(e => e.textContent)")
    C.check("context menu opens", "Rename" in items and "Move to Review" in items and "Copy Path" in items, str(items))
    C.check("Open shortcut says Enter", ev("() => [...document.querySelectorAll('#ctx-menu .ctx-item')].find(e => e.textContent.includes('Open') && !e.textContent.includes('with')).querySelector('.ctx-shortcut').textContent") == "Enter")
    page.locator("#ctx-menu .ctx-item", has_text="Copy Path").click(); time.sleep(0.4)
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        clip = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    C.check("Copy Path copies the path", clip == os.path.join(d, "gamma.txt"), clip)
    row(page, "gamma.txt").click(button="right"); time.sleep(0.2)
    page.locator("#ctx-menu .ctx-item", has_text="Copy Name").click(); time.sleep(0.4)
    win32clipboard.OpenClipboard()
    try:
        clip = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    C.check("Copy Name copies the name", clip == "gamma.txt", clip)
    row(page, "gamma.txt").click(button="right"); time.sleep(0.2)
    page.locator("#ctx-menu .ctx-item", has_text="Rename").click(); time.sleep(0.5)
    C.check("context Rename starts inline rename", ev("() => FileTree.isRenaming()") and ev("() => document.querySelector('.inline-rename').value") == "gamma.txt")
    page.keyboard.press("Escape"); page.keyboard.press("Escape"); time.sleep(0.2)
    row(page, "gamma.txt").click(button="right"); time.sleep(0.2)
    page.locator("#ctx-menu .ctx-item", has_text="Move to Review").click(); time.sleep(0.4)
    txt = ev("() => (document.querySelector('.modal-overlay .confirm-message')||{}).textContent || ''")
    C.check("context Move to Review names the file, not its path", "“gamma.txt”" in txt, txt)
    close_modals(page)
    row(page, "Sub Folder").click(button="right"); time.sleep(0.2)
    page.locator("#ctx-menu .ctx-item", has_text="Open").first.click(); time.sleep(0.8)
    C.check("context Open on a folder navigates into it", ev("() => FileTree.getCurrentFolder()") == sub)
    page.keyboard.press("Alt+ArrowLeft"); time.sleep(0.7)
    page.keyboard.press("Escape")

    # ── details pane quick actions ────────────────────────────────────────
    click_row(page, "alpha.pdf"); time.sleep(1.2)
    rows_txt = ev("() => document.getElementById('detail-rows').textContent")
    C.check("details shows PDF pages", "Pages" in rows_txt and "2" in rows_txt, rows_txt)
    C.check("details badge PDF", "PDF" in ev("() => document.getElementById('file-type-badge').textContent"))
    page.click("#qa-rename"); time.sleep(0.5)
    C.check("details Rename starts inline rename", ev("() => FileTree.isRenaming()"))
    page.keyboard.press("Escape"); page.keyboard.press("Escape"); time.sleep(0.2)
    page.click("#qa-copy-path"); time.sleep(0.4)
    win32clipboard.OpenClipboard()
    try:
        clip = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    C.check("details Copy path", clip == os.path.join(d, "alpha.pdf"), clip)
    page.click("#qa-delete"); time.sleep(0.4)
    C.check("details Move to review asks", modal_count(page) == 1)
    close_modals(page)
    C.check("details cancel keeps file", os.path.exists(os.path.join(d, "alpha.pdf")))
    click_row(page, "Sub Folder"); time.sleep(0.4)
    C.check("details for folder: Open with hidden", ev("() => document.getElementById('qa-openwith').classList.contains('hidden')"))
    page.click("#qa-open"); time.sleep(0.8)
    C.check("details Open on a folder navigates", ev("() => FileTree.getCurrentFolder()") == sub)
    page.keyboard.press("Alt+ArrowLeft"); time.sleep(0.7)

    # ── dialogs ───────────────────────────────────────────────────────────
    write(os.path.join(d, "case me.txt"), "c")
    ev("() => FileTree.refresh()"); time.sleep(0.6)
    click_row(page, "case me.txt")
    ev("() => Dialogs.openUppercase()"); time.sleep(0.3)
    page.click("#uc-title"); time.sleep(0.1)
    prev = ev("() => document.querySelector('#uc-preview .uc-row').dataset.new")
    C.check("Change case keeps the extension", prev == "Case Me.txt", prev)
    page.click(".modal-overlay [data-modal-btn='Rename']"); time.sleep(0.8)
    C.check("Change case renames on disk", "Case Me.txt" in os.listdir(d))
    ev("() => Dialogs.openSettings()"); time.sleep(0.5)
    C.check("Settings opens", ev("() => !!document.getElementById('modal-settings')"))
    C.check("Settings API key box never holds a raw key", ev("() => document.getElementById('s-apikey').value") == "")
    close_modals(page)
    close_modals(page)
    ev("() => Dialogs.openMoreMenu()"); time.sleep(0.3)
    C.check("More menu items", ev("() => document.querySelectorAll('#modal-more .more-item').length") >= 6,
            str(ev("() => [document.querySelectorAll('.modal-overlay').length, document.querySelectorAll('#modal-more .more-item').length]")))
    page.click("#mm-templates"); time.sleep(1.0)
    C.check("Templates manager opens from More", ev("() => !!document.querySelector('.rt-manager')"))
    close_modals(page)
    # Copy to / Move to dialogs
    click_row(page, "file2.txt")
    ev("() => Dialogs.openCopyTo(App.state.selectedPaths)"); time.sleep(0.3)
    page.fill("#ct-dest", sub); page.click(".modal-overlay [data-modal-btn='Copy']"); time.sleep(0.9)
    C.check("Copy to… copies", os.path.exists(os.path.join(sub, "file2.txt")) and os.path.exists(os.path.join(d, "file2.txt")))
    click_row(page, "file2.txt")
    ev("() => Dialogs.openMoveTo(App.state.selectedPaths)"); time.sleep(0.3)
    clear_toasts(page)
    page.fill("#mv-dest", sub); page.click(".modal-overlay [data-modal-btn='Move']"); time.sleep(0.9)
    C.check("Move to… never replaces (gets (2))", os.path.exists(os.path.join(sub, "file2 (2).txt")) and not os.path.exists(os.path.join(d, "file2.txt")))
    ev("() => Dialogs.openMoveTo(['" + os.path.join(d, "nope.txt").replace("\\", "\\\\") + "'])"); time.sleep(0.3)
    clear_toasts(page)
    page.fill("#mv-dest", sub); page.click(".modal-overlay [data-modal-btn='Move']"); time.sleep(0.8)
    C.check("Move of a missing file reports failure", any("failed" in t.lower() for t in toasts(page)), str(toasts(page)))

    # theme toggle
    before = ev("() => document.documentElement.getAttribute('data-theme')")
    page.click("#btn-theme"); time.sleep(0.3)
    C.check("theme toggles", ev("() => document.documentElement.getAttribute('data-theme')") != before)
    page.click("#btn-theme"); time.sleep(0.3)

    # other pages block file shortcuts
    click_row(page, "alpha.pdf")
    ev("() => App.switchPanel('settings')"); time.sleep(0.3)
    page.keyboard.press("Delete"); time.sleep(0.3)
    C.check("Delete on another page does nothing", modal_count(page) == 0)
    ev("() => App.switchPanel('workspace')")

    C.check("no uncaught page errors", not errors, "; ".join(errors[:5]))
finally:
    try:
        close_modals(page)
        page.evaluate("s => s == null ? localStorage.removeItem('oa.filelist.sort') : localStorage.setItem('oa.filelist.sort', s)", saved_sort)
    except Exception:
        pass
    for _f, _data in _tpl_files.items():
        if _data is not None:
            with open(_f, "wb") as fh:
                fh.write(_data)
        elif os.path.exists(_f):
            # the file did not exist before: empty it is not allowed (no deletions) — leave
            # only the header the app writes, and say so
            print("NOTE: test created", _f)
    rc = C.summary()
    pw.stop()
sys.exit(rc)
