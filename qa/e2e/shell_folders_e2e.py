"""End-to-end checks: removed OCR / Smart Split features, New folder,
New folders from a list, and the Copy to / Move to folder tree.

Start your OWN app instance first (never the owner's), e.g.
    set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9361
    set PYTHONUTF8=1
    python main_webview.py
then:
    set OA_CDP_PORT=9361
    python qa/e2e/shell_folders_e2e.py <scratch root> [<screenshot dir>]

Creates fresh fixture folders under <scratch root>; never deletes anything.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (Checker, connect, make_pdf, make_png, write, fresh_dir,  # noqa: E402
                     navigate, names, selected_names, row, click_row, modal_count,
                     close_modals, toasts, clear_toasts)

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "oa_e2e"))
SHOTS = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
C = Checker()
pw, browser, page = connect()
errors: list[str] = []
page.on("pageerror", lambda e: errors.append(str(e)))
REMOVED = ("Smart Split", "Doc Type", "by OCR", "Split & Rename", "OCR language")


def ev(js, arg=None):
    return page.evaluate(js, arg) if arg is not None else page.evaluate(js)


def shot(name):
    page.screenshot(path=os.path.join(SHOTS, name + ".png"))


def focus_list():
    ev("() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); document.getElementById('filelist-list').focus(); }")


def menu_labels():
    return ev("() => [...document.querySelectorAll('#ctx-menu:not(.hidden) .ctx-label')].map(e => e.textContent)")


def click_menu(label):
    page.locator("#ctx-menu:not(.hidden) .ctx-item", has=page.locator(".ctx-label", has_text=label)).first.click()
    time.sleep(0.3)


def inline_value():
    return ev("() => (document.querySelector('#filelist-list input.inline-rename') || {}).value")


def picker_ready():
    page.wait_for_selector(".fp-modal")
    page.wait_for_function("() => (document.querySelector('#modal-folderpick')?.closest('.modal-overlay')?.dataset.ready) === '1'")
    time.sleep(0.2)


def fp_row(path):
    css = path.replace("\\", "\\\\").replace('"', '\\"')   # backslashes are CSS escapes
    return page.locator(f'#fp-drives .fp-node > .fp-row[data-path="{css}"]').first   # tree rows, not Recent


ROW_JS = "(sel, p) => [...document.querySelectorAll(sel)].find(r => r.dataset.path.toLowerCase() === p.toLowerCase())"


def fp_selected():
    return ev("() => (document.querySelector('#fp-tree .fp-row.selected') || {dataset:{}}).dataset.path || ''")


def fp_ok_button():
    return ev("""() => { const b = document.querySelector('#modal-folderpick .modal-footer .btn-primary');
                         return b ? { disabled: b.disabled, text: b.textContent.trim() } : null; }""")


def fixture() -> str:
    d = fresh_dir(ROOT, "folders")
    make_pdf(os.path.join(d, "scan.pdf"), 2)
    make_png(os.path.join(d, "photo.png"))
    write(os.path.join(d, "notes.txt"), "notes")
    write(os.path.join(d, "Existing", "keep.txt"), "keep me")
    write(os.path.join(d, "Target", "notes.txt"), "already here")
    os.makedirs(os.path.join(d, "Target", "Deep", "Deeper-qx7"))
    os.makedirs(os.path.join(d, "Archive 2024"))
    return d


try:
    d = fixture()
    ev("() => { App.switchPanel('workspace'); FileTree.setViewMode('list'); }")
    navigate(page, d)
    close_modals(page)

    # ══ 1. removed features are gone everywhere ════════════════════════════
    C.check("Dialogs: no Smart Split / OCR rename dialogs",
            ev("() => ['openSmartSplitProgress','openOcrRenameProgress','openSplitRenameOcrProgress'].every(k => !(k in Dialogs))"))
    C.check("bridge.js: no wrappers", ev("() => ['smartSplit','ocrRename','ocrRenameProgress','splitAndRenameOcr'].every(k => !(k in SFM))"))
    C.check("Python bridge: no methods",
            ev("() => ['smart_split_rename','ocr_rename','ocr_rename_with_progress','split_and_rename_ocr'].every(k => typeof window.pywebview.api[k] !== 'function')"))
    C.check("toolbar: no Smart Split button", ev("() => !document.getElementById('btn-smart-split')"))
    click_row(page, "scan.pdf")
    time.sleep(0.6)
    row(page, "scan.pdf").click(button="right")
    time.sleep(0.3)
    labels = menu_labels()
    C.check("PDF context menu: removed items gone", labels and not any(r in l for l in labels for r in REMOVED), str(labels))
    C.check("PDF context menu: Split / Extract still there", "Split PDF…" in labels and "Extract Pages…" in labels, str(labels))
    shot("01_pdf_context_menu")
    page.keyboard.press("Escape")
    details_text = ev("() => (document.getElementById('pane-details') || document.body).innerText")
    C.check("details pane: no removed actions", not any(r in details_text for r in REMOVED))
    ev("() => Dialogs.openMoreMenu()"); time.sleep(0.3)
    more = ev("() => document.getElementById('modal-more').innerText")
    C.check("More menu: no removed actions, has New folders from a list", not any(r in more for r in REMOVED) and "New folders from a list" in more)
    close_modals(page)
    ev("() => Dialogs.openSettings()"); time.sleep(0.6)
    st = ev("() => document.getElementById('modal-settings').innerText")
    C.check("Settings: no OCR language / Smart Split text", not any(r in st for r in REMOVED) and not ev("() => !!document.getElementById('s-ocrlang')"))
    close_modals(page)
    C.check("whole page: no removed feature text", not any(r in ev("() => document.body.innerText") for r in ("Smart Split", "Rename by Doc", "Split & Rename by OCR")))

    # ══ 2. New folder ══════════════════════════════════════════════════════
    C.check("toolbar New folder button is labelled", "New folder" in ev("() => document.getElementById('btn-new-folder').textContent"))
    C.check("file-list header has a New folder button", ev("() => !!document.getElementById('fl-new-folder')"))
    page.click("#btn-new-folder"); time.sleep(0.8)
    C.check("toolbar New folder creates and edits inline", os.path.isdir(os.path.join(d, "New folder")) and inline_value() == "New folder")
    shot("02_new_folder_inline")
    page.keyboard.press("Control+A"); page.keyboard.type("Clients"); page.keyboard.press("Enter"); time.sleep(0.9)
    C.check("typed name kept", os.path.isdir(os.path.join(d, "Clients")) and not os.path.exists(os.path.join(d, "New folder")))
    C.check("new folder selected", selected_names(page) == ["Clients"])

    # empty-space menu of the file list
    box = page.locator("#filelist-list").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] - 12, button="right"); time.sleep(0.3)
    labels = menu_labels()
    C.check("empty-space menu offers New folder + from a list + Paste",
            ev("() => document.getElementById('ctx-menu').dataset.kind") == "background"
            and "New folder" in labels and "New folders from a list…" in labels and "Paste" in labels, str(labels))
    shot("03_empty_space_menu")
    click_menu("New folder"); time.sleep(0.6)
    C.check("empty-space New folder edits inline", inline_value() == "New folder")
    page.keyboard.press("Escape"); page.keyboard.press("Escape"); time.sleep(0.4)
    C.check("Esc keeps 'New folder'", os.path.isdir(os.path.join(d, "New folder")) and inline_value() is None)
    focus_list()
    page.keyboard.press("Control+Shift+N"); time.sleep(0.8)
    C.check("Ctrl+Shift+N picks the next free name", inline_value() == "New folder (2)" and os.path.isdir(os.path.join(d, "New folder (2)")))
    page.keyboard.press("Enter"); time.sleep(0.6)

    # toolbar drop-down
    page.click("#btn-new-folder-menu"); time.sleep(0.3)
    labels = menu_labels()
    C.check("toolbar drop-down lists both ways", labels == ["New folder", "New folders from a list…"], str(labels))
    page.keyboard.press("Escape")

    # ══ 3. New folders from a list ═════════════════════════════════════════
    focus_list()
    page.keyboard.press("Control+Shift+L"); time.sleep(0.5)
    C.check("Ctrl+Shift+L opens New folders from a list", ev("() => !!document.getElementById('modal-newfolders')"))
    pasted = ("Alice Kim\tBob Lee\t\r\n\r\n  Carol Park  \r\n Dan \r\nalice kim\r\nBad:Name\r\nCON\r\n"
              "Trailing.\r\nExisting\r\n\r\n")
    page.fill("#nfl-text", pasted)
    page.wait_for_function("() => document.querySelectorAll('#nfl-list .nfl-row').length === 9")
    st = ev("() => [...document.querySelectorAll('#nfl-list .nfl-row')].map(r => [r.querySelector('.nfl-name').textContent, r.className.match(/is-(\\w+)/)[1]])")
    want = [["Alice Kim", "ok"], ["Bob Lee", "ok"], ["Carol Park", "ok"], ["Dan", "ok"], ["alice kim", "duplicate"],
            ["Bad:Name", "invalid"], ["CON", "invalid"], ["Trailing.", "invalid"], ["Existing", "exists"]]
    C.check("preview: tabs, blank lines, spaces, duplicates, invalid, existing", st == want, str(st))
    C.check("preview: summary badges", ev("() => document.getElementById('nfl-summary').innerText.replace(/\\s+/g,' ')")
            .strip() == "4 to create 1 already exists 1 duplicate 3 invalid", ev("() => document.getElementById('nfl-summary').innerText"))
    C.check("Create button shows the count", ev("() => document.querySelector('#modal-newfolders .btn-primary').textContent.trim()") == "Create 4 folders")
    shot("04_new_folders_from_list")
    page.fill("#nfl-prefix", "P - ")
    page.click("#nfl-num [data-num='before']")
    page.wait_for_function("() => (document.querySelector('#nfl-list .nfl-name') || {}).textContent === '1 P - Alice Kim'")
    C.check("prefix + numbering preview", ev("() => [...document.querySelectorAll('#nfl-list .nfl-name')].slice(0,2).map(e=>e.textContent)") == ["1 P - Alice Kim", "2 P - Bob Lee"])
    page.select_option("#nfl-sep", " - "); page.fill("#nfl-start", "10")
    page.wait_for_function("() => (document.querySelector('#nfl-list .nfl-name') || {}).textContent === '10 - P - Alice Kim'")
    C.check("start number + separator", True)
    shot("05_new_folders_numbering")
    page.fill("#nfl-prefix", ""); page.click("#nfl-num [data-num='none']")
    page.wait_for_function("() => document.querySelector('#modal-newfolders .btn-primary').textContent.trim() === 'Create 4 folders'")
    page.click("#modal-newfolders .btn-primary"); time.sleep(1.2)
    made = ["Alice Kim", "Bob Lee", "Carol Park", "Dan"]
    C.check("folders created", all(os.path.isdir(os.path.join(d, n)) for n in made))
    C.check("problem names skipped", not any(os.path.exists(os.path.join(d, n)) for n in ("Bad", "Trailing", "CON")))
    C.check("existing folder untouched", sorted(os.listdir(os.path.join(d, "Existing"))) == ["keep.txt"])
    C.check("created folders are selected", selected_names(page) == sorted(made), str(selected_names(page)))
    shot("06_folders_created_selected")
    focus_list(); page.keyboard.press("Control+Z"); time.sleep(1.0)
    C.check("Ctrl+Z moves them to _to_review (nothing deleted)",
            not os.path.exists(os.path.join(d, "Alice Kim")) and os.path.isdir(os.path.join(d, "_to_review")))
    focus_list(); page.keyboard.press("Control+Y"); time.sleep(1.0)
    C.check("Ctrl+Y brings them back", all(os.path.isdir(os.path.join(d, n)) for n in made))

    # ══ 4. Copy to / Move to — folder tree ═════════════════════════════════
    clear_toasts(page)
    click_row(page, "notes.txt")
    row(page, "notes.txt").click(button="right"); time.sleep(0.3)
    click_menu("Copy To…")
    picker_ready()
    C.check("picker: current folder highlighted + selected",
            ev(f"f => !!({ROW_JS})('#fp-tree .fp-row.is-current', f)", d) and fp_selected().lower() == d.lower())
    drive = d[:3]
    C.check("picker: root drive expanded down to the current folder",
            ev(f"p => !!({ROW_JS})('#fp-tree .fp-row.is-drive', p)?.closest('.fp-node.expanded')", drive))
    C.check("picker: current folder's subfolders listed",
            set(ev("f => [...document.querySelectorAll('#fp-tree .fp-row')].filter(r => r.dataset.path.toLowerCase().startsWith(f.toLowerCase() + '\\\\')).map(r => r.querySelector('.fp-label').textContent)", d))
            >= {"Alice Kim", "Archive 2024", "Target"})
    C.check("picker: recent folders section", ev("() => !document.getElementById('fp-recent-sec').hidden && document.querySelectorAll('#fp-recent .fp-row').length >= 1"))
    shot("07_copy_picker")
    target = os.path.join(d, "Target")
    deep = os.path.join(target, "Deep")
    fp_row(target).locator(".fp-twist").click(); time.sleep(0.6)
    C.check("expand loads children lazily", fp_row(deep).is_visible())
    fp_row(target).locator(".fp-twist").click(); time.sleep(0.2)
    C.check("collapse hides them", not fp_row(deep).is_visible())
    page.fill("#fp-filter", "deeper-qx7"); time.sleep(0.4)
    page.wait_for_function("() => document.querySelectorAll('#fp-results .fp-result-row').length >= 1", timeout=15000)
    C.check("search finds the folder", ev("() => document.querySelector('#fp-results .fp-result-row .fp-label').textContent") == "Deeper-qx7")
    shot("08_picker_search")
    page.click("#fp-results .fp-result-row")
    C.check("search result selects", fp_selected().lower() == os.path.join(deep, "Deeper-qx7").lower())
    page.focus("#fp-filter"); page.keyboard.press("Escape"); time.sleep(0.3)
    C.check("Esc clears the search, dialog stays", modal_count(page) == 1 and ev("() => !document.getElementById('fp-tree-sec').hidden"))
    fp_row(target).click(); time.sleep(0.2)
    page.click("#fp-newfolder"); time.sleep(0.6)
    C.check("New folder in picker: inline name", ev("() => document.querySelector('.fp-new-input')?.value") == "New folder")
    shot("09_picker_new_folder")
    page.fill(".fp-new-input", "From Picker"); page.keyboard.press("Enter"); time.sleep(0.8)
    made_in_picker = os.path.join(target, "From Picker")
    C.check("New folder in picker: created on disk and selected", os.path.isdir(made_in_picker) and fp_selected().lower() == made_in_picker.lower())
    fp_row(target).click(); time.sleep(0.2)
    C.check("Copy button names the action", fp_ok_button()["text"] == "Copy here")
    page.click("#modal-folderpick .modal-footer .btn-primary"); time.sleep(1.0)
    C.check("copy keeps the original", os.path.isfile(os.path.join(d, "notes.txt")))
    C.check("conflict gets ' (2)', existing file untouched",
            open(os.path.join(target, "notes (2).txt"), encoding="utf-8").read() == "notes"
            and open(os.path.join(target, "notes.txt"), encoding="utf-8").read() == "already here")
    C.check("toast says what was renamed", any("notes (2).txt" in t for t in toasts(page)), str(toasts(page)))

    # Move via the tree
    clear_toasts(page)
    click_row(page, "photo.png")
    row(page, "photo.png").click(button="right"); time.sleep(0.3)
    click_menu("Move To…")
    picker_ready()
    C.check("move: current folder is not a valid destination", fp_ok_button()["disabled"]
            and "already in this folder" in ev("() => document.getElementById('fp-dest').innerText"))
    fp_row(target).locator(".fp-twist").click(); time.sleep(0.6)
    fp_row(deep).click(); time.sleep(0.2)
    shot("10_move_picker")
    page.click("#modal-folderpick .modal-footer .btn-primary")
    time.sleep(1.0)
    C.check("move moved the file", os.path.isfile(os.path.join(deep, "photo.png")) and not os.path.exists(os.path.join(d, "photo.png")))

    # keyboard: arrows + Enter
    click_row(page, "scan.pdf")
    ev("() => Dialogs.openCopyTo(App.state.selectedPaths)")
    picker_ready()
    page.keyboard.press("ArrowDown"); time.sleep(0.2)
    C.check("keyboard: arrow moves the selection", fp_selected() != "" and fp_selected().lower() != d.lower())
    close_modals(page)

    # a folder can't go into itself
    click_row(page, "Target")
    ev("() => Dialogs.openMoveTo(App.state.selectedPaths)")
    picker_ready()
    ev("p => document.querySelector('#modal-folderpick').closest('.modal-overlay')._reveal(p)", deep); time.sleep(0.4)
    C.check("move folder into itself is blocked", fp_ok_button()["disabled"]
            and "into itself" in ev("() => document.getElementById('fp-dest').innerText"))
    close_modals(page)
    C.check("no uncaught page errors", not errors, "; ".join(errors[:5]))
finally:
    try:
        close_modals(page)
        ev("() => ContextMenu.hide()")
    except Exception:
        pass
    rc = C.summary()
    pw.stop()
sys.exit(rc)
