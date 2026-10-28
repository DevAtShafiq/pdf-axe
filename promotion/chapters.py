# -*- coding: utf-8 -*-
"""
chapters.py — the storyboard as code: one function pair per chapter.

    setup(rec, data) -> folder   invisible preparation (files, state); returns
                                 the folder to show when filming starts
    act(rec, data)               what the viewer sees: real clicks / typing,
                                 plus rec.focus(...) / rec.wide() / rec.success()
                                 marks for the editor (camera, callouts, sounds)

`data` comes from make_demo_data.build(): {"root", "app", "inbox", "students", "qr"}.
Section keys must match narration.json.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

import desktop as D

MENU = "#ctx-menu .ctx-item"


@dataclass
class Chapter:
    n: int
    key: str
    title: str
    setup: Callable
    act: Callable


def _p(data, *parts):
    return os.path.join(data["inbox"], *parts)


# ── 1 · Workspace ─────────────────────────────────────────────────────────────
def setup_workspace(rec, data):
    return data["app"]


def act_workspace(rec, data):
    rec.wide("workspace")
    rec.wait(1.6)
    rec.click_rect(rec.row("Documents received"), "open folder", double=True)
    rec.wait(1.4)
    rec.focus("file list", rec.rect("#filelist-list"))
    rec.wait(0.8)
    rec.click_sel("#btn-view-thumb", "Thumbnail view")
    rec.wait(0.6)
    rec.focus("thumbnails", rec.rect("#filelist-thumb"))
    rec.wait(2.2)
    rec.click_sel("#btn-view-list", "List view")
    rec.wait(0.8)
    rec.click_sel('#fl-sort-bar [data-sort="size"]', "Sort by size")
    rec.wait(1.2)
    rec.click_sel('#fl-sort-bar [data-sort="name"]', "Sort by name")
    rec.wait(0.8)
    rec.click_sel("#search-input", "Search")
    rec.type("photo", cps=8, name="search", rect=rec.rect("#search-input"))
    rec.wait(0.4)
    rec.focus("search results", rec.rect("#filelist-list"))
    rec.wait(1.8)
    rec.press("a", "ctrl", show=False)
    rec.press("backspace", show=False)
    rec.wait(0.8)
    rec.click_rect(rec.row("Offer letter.pdf"), "Offer letter.pdf")
    rec.wait(0.9)
    rec.focus("details", rec.rect("#pane-details") or rec.rect("#details-panel"))
    rec.wait(2.0)
    rec.click_sel("#btn-theme", "Dark theme")
    rec.wait(0.5)
    rec.wide("dark")
    rec.wait(2.0)
    rec.click_sel("#btn-theme", "Light theme")
    rec.wait(1.0)


# ── 2 · Folders from a list ───────────────────────────────────────────────────
def setup_folders(rec, data):
    return data["app"]


def act_folders(rec, data):
    p = rec.page
    rec.wait(0.6)
    rec.click_sel("#btn-new-folder-menu", "New folder menu")
    rec.wait(0.6)
    rec.click_text(MENU, "from a list")
    p.wait_for_selector("#nfl-text", state="visible")
    rec.wait(0.4)
    rec.focus("dialog", rec.rect(".modal-overlay .modal"))
    rec.click_sel("#nfl-text", "names box")
    rec.type("\n".join(data["students"]), cps=22, name="names", rect=rec.rect("#nfl-text"))
    rec.wait(0.5)
    rec.focus("preview", rec.rect("#nfl-list"))
    rec.wait(1.6)
    rec.click_sel('#nfl-num [data-num="before"]', "Numbering 01 Name")
    rec.wait(1.4)
    rec.focus("numbered", rec.rect("#nfl-list"))
    rec.wait(1.2)
    rec.click_text(".modal-overlay .modal-footer .btn-primary", "Create")
    p.wait_for_selector("#nfl-text", state="detached")
    rec.success("folders created")
    rec.wait(0.6)
    rec.focus("created", rec.rect("#filelist-list"))
    rec.wait(2.2)


# ── 3 · Preview ───────────────────────────────────────────────────────────────
def setup_preview(rec, data):
    return data["inbox"]


def act_preview(rec, data):
    for name, hold in (("Study plan.docx", 2.2), ("Fee schedule.xlsx", 2.2),
                       ("Passport photo.jpg", 1.8), ("Transcript.pdf", 1.4)):
        rec.click_rect(rec.row(name), name)
        wait_preview(rec)
        rec.focus(f"preview {name}", rec.rect("#pane-preview"))
        rec.wait(hold)
    rec.click_sel("#preview-zoom-in", "Zoom in")
    rec.wait(0.5)
    rec.click_sel("#preview-zoom-in", "Zoom in")
    rec.wait(1.4)
    rec.click_sel("#preview-zoom-out", "Zoom out")
    rec.wait(0.4)
    rec.click_sel("#preview-zoom-out", "Zoom out")
    rec.wait(1.0)


# ── 4 · Smart rename ──────────────────────────────────────────────────────────
def setup_rename(rec, data):
    return data["inbox"]


def act_rename(rec, data):
    p = rec.page
    rec.click_rect(rec.row("scan_0012.pdf"), "scan_0012.pdf")
    rec.wait(1.0)
    rec.press("f2")
    rec.wait(0.6)
    rec.press("a", "ctrl", show=False)
    rec.type("bachelor cert", cps=9, name="rename")
    rec.wait(0.9)
    dd = p.evaluate("""() => { const d = [...document.querySelectorAll('.rt-dropdown')].find(e => e.offsetParent !== null);
                               if (!d) return null; const b = d.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; }""")
    if dd:
        rec.focus("suggestions", rec._video(dd))
    rec.wait(1.6)
    rec.click_text(".rt-dropdown .rt-item", "학사 학위증명서 (아포스티유 포함)")
    rec.wait(0.5)
    if p.evaluate("!!document.querySelector('input.inline-rename:focus')"):
        rec.press("enter", show=False)
    rec.success("renamed")
    rec.wait(1.6)
    rec.click_sel("#btn-more", "More")
    rec.wait(0.7)
    rec.ev("redact", name="templates path", rect=None)  # placeholder: the rect is set once the dialog is open
    red = rec.events[-1]
    rec.click_sel("#mm-templates", "Document name templates")
    rec.wait(1.2)
    path_el = rec.js("""() => { const ov = [...document.querySelectorAll('.modal-overlay')].pop(); if (!ov) return null;
        const el = [...ov.querySelectorAll('*')].filter(e => !e.children.length && /[A-Za-z]:\\/.test(e.textContent)).pop();
        if (!el) return null; const b = (el.closest('div') || el).getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; }""")
    if path_el:                                      # the templates file path (owner's folders)
        r = rec._video(path_el)
        red["rect"] = [r[0] - 12, r[1] - 8, r[2] + 24, r[3] + 16]
    rec.focus("template manager", rec.rect(".modal-overlay .modal"))
    rec.wait(3.0)
    rec.press("esc", show=False)
    rec.wait(0.4)
    rec.ev("redact_end", name="templates path")
    rec.wait(0.8)


# ── 5 · Organise: cut / paste, undo / redo ────────────────────────────────────
def setup_organise(rec, data):
    os.makedirs(os.path.join(data["app"], "Minji Park"), exist_ok=True)
    return data["inbox"]


def act_organise(rec, data):
    rec.click_rect(rec.row("Page photo 1.jpg"), "Page photo 1.jpg")
    rec.wait(0.4)
    r = rec.row("Page photo 2.jpg")
    x, y = rec.screen(r)
    D.move(x, y)
    rec.ev("click", name="ctrl+click", x=round(x - rec.origin[0]), y=round(y - rec.origin[1]), rect=r)
    D._send(D._key(D.VK["ctrl"]))
    D.click()
    D._send(D._key(D.VK["ctrl"], flags=D.KEY_UP))
    rec.ev("key", text="Ctrl + click", show=True)
    rec.wait(0.8)
    rec.press("x", "ctrl")
    rec.wait(0.8)
    rec.click_text("#breadcrumb .breadcrumb-seg", "Applications 2026")
    rec.wait(1.0)
    rec.click_rect(rec.rect_text("#filelist-list .file-item .name, #filelist-list .file-item", "Minji Park", exact=True), "Minji Park", double=True)
    rec.wait(1.0)
    rec.press("v", "ctrl")
    rec.wait(1.2)
    rec.focus("pasted", rec.rect("#filelist-list"))
    rec.wait(1.4)
    rec.press("z", "ctrl")
    rec.wait(1.6)
    rec.press("y", "ctrl")
    rec.wait(1.6)
    rec.success("moved")


# ── shared helpers ────────────────────────────────────────────────────────────
def ctrl_click(rec, r, name=""):
    x, y = rec.screen(r)
    D.move(x, y)
    rec.ev("click", name=f"ctrl+click {name}", x=round(x - rec.origin[0]), y=round(y - rec.origin[1]), rect=r)
    D._send(D._key(D.VK["ctrl"]))
    D.click()
    D._send(D._key(D.VK["ctrl"], flags=D.KEY_UP))


def select_files(rec, *names):
    rec.click_rect(rec.row(names[0]), names[0])
    rec.wait(0.35)
    for n in names[1:]:
        ctrl_click(rec, rec.row(n), n)
        rec.wait(0.35)
    if len(names) > 1:
        rec.ev("key", text="Ctrl + click", show=True)


def context_menu(rec, file_name, item):
    rec.click_rect(rec.row(file_name), "right-click " + file_name, right=True)
    rec.wait(0.7)
    rec.focus("menu", rec.rect("#ctx-menu"))
    rec.click_text(MENU, item)


def footer_btn(rec, label, exact=True):
    """Click a button in the top-most dialog's footer by its label."""
    r = rec.js("""([t, ex]) => { const ov = [...document.querySelectorAll('.modal-overlay')].filter(o => o.isConnected).pop();
        if (!ov) return null; const btns = [...ov.querySelectorAll('.modal-footer .btn, .modal-footer button')]
          .filter(x => x.offsetParent);
        let b = btns.find(x => ex ? x.textContent.trim() === t : x.textContent.trim().includes(t));
        if (!b) b = btns.filter(x => x.classList.contains('btn-primary')).pop();    // the dialog's main action
        if (!b) return null; const r = b.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }""", [label, exact])
    if not r:
        raise RuntimeError(f"footer button {label!r} not found")
    rec.click_rect(rec._video(r), label)


def wait_preview(rec, timeout=25):
    """Wait until the preview pane has finished loading (no spinner)."""
    rec.wait(0.5)
    try:
        rec.page.wait_for_function("""() => { const p = document.querySelector('#pane-preview');
            return p && ![...p.querySelectorAll('.loading-spinner, .spinner, .loading-overlay')].some(e => e.offsetParent)
                   && !/Reading document|Loading/.test(p.innerText); }""", timeout=timeout * 1000)
    except Exception:
        pass
    rec.wait(0.4)


def wait_file(path, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.3)
    return False


def wait_new_file(folder, before, pattern="", timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        new = [f for f in os.listdir(folder) if f not in before and pattern.lower() in f.lower()]
        if new:
            time.sleep(0.6)
            return new
        time.sleep(0.4)
    return []


def page_point(rec, sel, fx, fy):
    """A point on a rendered page (fractions of its box) in video coordinates."""
    r = rec.rect(sel)
    if not r:
        raise RuntimeError(f"page not found: {sel}")
    return r, (r[0] + r[2] * fx, r[1] + r[3] * fy)


def drag_frac(rec, sel, f0, f1, name, duration=0.9):
    r = rec.rect(sel)
    if not r:
        raise RuntimeError(f"page not found: {sel}")
    p0 = [r[0] + r[2] * f0[0], r[1] + r[3] * f0[1], 1, 1]
    p1 = [r[0] + r[2] * f1[0], r[1] + r[3] * f1[1], 1, 1]
    rec.drag(p0, p1, name=name, duration=duration)


# ── 6 · Convert ───────────────────────────────────────────────────────────────
def setup_convert(rec, data):
    # Chapter 5 moves the two page photos away; make them again if needed.
    import make_demo_data as M
    pairs = (("scan_0012.pdf", "Page photo 1.jpg", -2.0), ("Offer letter.pdf", "Page photo 2.jpg", 1.6))
    for src, name, angle in pairs:
        if not os.path.exists(_p(data, name)):
            src = src if os.path.exists(_p(data, src)) else "Transcript.pdf"    # chapter 4 renames scan_0012
            M.page_photo(_p(data, src), _p(data, name), angle=angle)
    return data["inbox"]


def act_convert(rec, data):
    p = rec.page
    inbox = data["inbox"]
    select_files(rec, "Study plan.docx", "Recommendation letter.docx")
    rec.wait(0.5)
    context_menu(rec, "Recommendation letter.docx", "to PDF")
    p.wait_for_selector(".task-card", timeout=15000)
    rec.wait(0.2)
    rec.focus("task card", rec.rect(".task-card"))
    p.wait_for_selector(".task-card.is-done, .task-card.is-failed", timeout=180000)
    rec.focus("task done", rec.rect(".task-card"))
    rec.success("pdfs created")
    rec.wait(1.8)
    # two phone photos of pages → one PDF
    select_files(rec, "Page photo 1.jpg", "Page photo 2.jpg")
    rec.wait(0.5)
    context_menu(rec, "Page photo 2.jpg", "Combine Images into One PDF")
    p.wait_for_selector("#i2p-size", state="visible")
    rec.wait(0.5)
    rec.focus("images to pdf", rec.rect(".modal-overlay .modal"))
    rec.wait(1.8)
    before = set(os.listdir(inbox))
    footer_btn(rec, "Convert")
    wait_new_file(inbox, before, ".pdf", 60)
    rec.success("combined pdf")
    rec.wait(1.6)
    # PDF pages → JPG images
    before = set(os.listdir(inbox))
    context_menu(rec, "Visa checklist.pdf", "Convert to Images")
    p.wait_for_selector('[data-seg-for="p2i-fmt"]', state="visible")
    rec.wait(0.6)
    rec.focus("pdf to images", rec.rect(".modal-overlay .modal"))
    rec.click_sel('[data-seg-for="p2i-fmt"] [data-v="jpg"]', "JPG")
    rec.wait(1.0)
    footer_btn(rec, "Convert")
    wait_new_file(inbox, before, "", 60)
    rec.success("images")
    rec.wait(1.8)


# ── 7 · Compress ──────────────────────────────────────────────────────────────
def setup_compress(rec, data):
    return data["inbox"]


def act_compress(rec, data):
    p = rec.page
    inbox = data["inbox"]
    rec.click_rect(rec.row("Bank statement.pdf"), "Bank statement.pdf")
    rec.wait(1.2)
    rec.click_sel("#btn-compress-pdf", "Compress")
    p.wait_for_selector("#cmp-targets", state="visible")
    rec.wait(0.4)
    rec.focus("compress dialog", rec.rect(".modal-overlay .modal"))
    try:
        p.wait_for_function("() => /≈/.test((document.querySelector('#pc-est-ebook') || {}).textContent || '')", timeout=60000)
    except Exception:
        pass
    rec.wait(1.2)
    rec.click_text(".cmp-target", "1 MB")
    rec.wait(1.4)
    before = set(os.listdir(inbox))
    footer_btn(rec, "Compress", exact=False)
    wait_new_file(inbox, before, "compressed", 180)
    rec.wait(0.8)
    rec.focus("compressed", rec.rect(".modal-overlay .modal") or rec.rect("#filelist-list"))
    rec.success("compressed")
    rec.wait(2.6)
    rec.press("esc", show=False)
    rec.wait(0.8)


# ── 8 · Merge, split, extract ─────────────────────────────────────────────────
def setup_merge(rec, data):
    return data["inbox"]


def act_merge(rec, data):
    p = rec.page
    inbox = data["inbox"]
    select_files(rec, "Offer letter.pdf", "Visa checklist.pdf")
    rec.wait(0.5)
    rec.click_sel("#btn-combine-pdf", "Merge")
    p.wait_for_function("() => document.querySelector('#pt-m-go') && !document.querySelector('#pt-m-go').disabled", timeout=20000)
    rec.wait(0.5)
    rec.focus("merge dialog", rec.rect(".pt-overlay .modal, .modal-overlay .modal") or rec.rect("#pt-m-go"))
    rec.wait(1.8)
    before = set(os.listdir(inbox))
    rec.click_sel("#pt-m-go", "Merge")
    wait_new_file(inbox, before, ".pdf", 60)
    rec.success("merged")
    rec.wait(1.6)
    # split the transcript every 2 pages
    rec.click_rect(rec.row("Transcript.pdf"), "Transcript.pdf")
    rec.wait(1.2)
    rec.click_sel("#qa-pdf-split", "Split")
    p.wait_for_selector("#pt-s-every", state="attached")
    rec.wait(0.6)
    rec.click_sel('.pt-mode[data-mode="every"]', "Every N pages")
    rec.wait(0.4)
    rec.click_sel("#pt-s-every", "pages per file")
    rec.press("a", "ctrl", show=False)
    rec.type("2", cps=4, name="every")
    rec.wait(1.0)
    rec.focus("split preview", rec.rect("#pt-s-prev") or rec.rect(".modal-overlay .modal"))
    rec.wait(1.4)
    rec.click_sel("#pt-s-go", "Split")
    rec.wait(2.0)
    rec.success("split")
    rec.wait(1.2)
    # extract two pages by clicking them (splitting selected the new folder)
    rec.click_rect(rec.row("Transcript.pdf"), "Transcript.pdf")
    rec.wait(1.0)
    rec.click_sel("#qa-pdf-extract", "Extract")
    p.wait_for_selector(".pt-overlay .pt-tile .pt-thumb img, .pt-overlay .pt-tile .pt-thumb canvas", timeout=20000)
    rec.wait(1.0)
    rec.focus("page grid", rec.rect_union(".pt-overlay .pt-tile"))
    rec.click_rect(rec.rect(".pt-overlay .pt-tile .pt-thumb", 0), "page 1")
    rec.wait(0.4)
    rec.click_rect(rec.rect(".pt-overlay .pt-tile .pt-thumb", 4), "page 5")
    rec.wait(1.0)
    before = set(os.listdir(inbox))
    rec.click_sel("#pt-x-go", "Extract")
    wait_new_file(inbox, before, ".pdf", 30)
    rec.success("extracted")
    rec.wait(1.4)
    rec.press("esc", show=False)
    rec.wait(0.6)
    rec.press("esc", show=False)
    rec.wait(0.6)


# ── 9 · Arrange pages ─────────────────────────────────────────────────────────
def setup_arrange(rec, data):
    return data["inbox"]


def act_arrange(rec, data):
    p = rec.page
    rec.click_rect(rec.row("Transcript.pdf"), "Transcript.pdf")
    rec.wait(1.2)
    rec.click_sel("#qa-pdf-arrange", "Arrange")
    p.wait_for_selector(".pt-overlay .pt-tile .pt-thumb img, .pt-overlay .pt-tile .pt-thumb canvas", timeout=20000)
    rec.wait(1.2)
    rec.focus("page grid", rec.rect_union(".pt-overlay .pt-tile"))
    tile = lambda i: rec.rect(".pt-overlay .pt-tile .pt-thumb", i)
    rec.click_rect(tile(5), "blank page")
    rec.wait(0.5)
    rec.press("delete", label="Del")
    rec.wait(1.3)
    rec.press("z", "ctrl")
    rec.wait(1.4)
    rec.click_rect(tile(5), "blank page again")
    rec.press("delete", label="Del")
    rec.wait(1.0)
    rec.click_rect(tile(2), "sideways page")
    rec.wait(0.5)
    rec.press("l", label="L  rotate left")
    rec.wait(1.3)
    rec.drag(tile(4), tile(0), name="cover letter to front", f1=(0.15, 0.5), duration=1.2)
    rec.wait(1.4)
    rec.focus("arranged", rec.rect_union(".pt-overlay .pt-tile"))
    rec.click_sel("#pt-a-save", "Save")
    p.wait_for_selector(".pt-overlay", state="detached", timeout=30000)
    rec.success("saved")
    rec.wait(2.0)


# ── 10 · PDF Edit studio: fill, sign, photo ───────────────────────────────────
def setup_studio(rec, data):
    return data["inbox"]


def _zoom_out(rec, overlay, n):
    for _ in range(n):
        r = rec.js("""s => { const b = [...document.querySelectorAll(s + ' button')].filter(x => x.offsetParent)
            .find(x => (x.getAttribute('aria-label') || x.title || '') === 'Zoom out');
            if (!b) return null; const r = b.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }""", overlay)
        if not r:
            return
        rec.click_rect(rec._video(r), "Zoom out", sound=False)
        rec.wait(0.25)


def _sign(rec):
    """Draw a short cursive signature with the real mouse."""
    c = rec.rect("#an-sig-canvas")
    x0, y0, w, h = rec.screen(c, 0, 0)[0], rec.screen(c, 0, 0)[1], c[2], c[3]
    strokes = [
        [(0.18, 0.62), (0.21, 0.35), (0.25, 0.62), (0.29, 0.36), (0.32, 0.63)],                  # M
        [(0.36, 0.55), (0.40, 0.48), (0.44, 0.56), (0.47, 0.50), (0.50, 0.58), (0.54, 0.47),
         (0.58, 0.57), (0.62, 0.50)],                                                            # inji
        [(0.67, 0.64), (0.68, 0.35), (0.76, 0.40), (0.69, 0.50)],                                # P
        [(0.72, 0.58), (0.80, 0.52), (0.86, 0.58), (0.92, 0.50)],                                # ark
    ]
    rec.ev("drag", name="signature", rect=c, frm=[c[0], c[1]], to=[c[0] + c[2], c[1] + c[3]])
    for s in strokes:
        pts = [(x0 + w * fx, y0 + h * fy) for fx, fy in s]
        D.move(*pts[0], duration=0.25)
        D._send(D._mouse(D.MOUSE_LEFTDOWN))
        for a, b in zip(pts, pts[1:]):
            D.move(*b, duration=0.12)
        D._send(D._mouse(D.MOUSE_LEFTUP))
        time.sleep(0.12)


def act_studio(rec, data):
    p = rec.page
    inbox = data["inbox"]
    rec.click_rect(rec.row("Application form.pdf"), "Application form.pdf")
    rec.wait(1.2)
    rec.click_sel("#qa-studio", "Edit")
    p.wait_for_selector(".st-overlay", state="visible")
    p.wait_for_function("() => document.querySelectorAll('#st-host input[type=text]').length >= 4", timeout=30000)
    rec.wait(1.0)
    _zoom_out(rec, ".st-overlay", 6)
    rec.wait(0.8)
    values = ["Minji Park", "M12345678", "14 March 2003", "Master of International Business"]
    for i, v in enumerate(values):
        r = rec.rect("#st-host input[type=text]", i)
        rec.click_rect(r, f"field {i + 1}")
        rec.type(v, cps=16, name=f"field {i + 1}", rect=r)
        rec.wait(0.3)
    boxes = rec.js("() => [...document.querySelectorAll('#st-host input[type=checkbox]')].filter(e => e.offsetParent).length")
    rec.click_rect(rec.rect("#st-host input[type=checkbox]", boxes - 1), "confirm")
    rec.wait(1.0)
    rec.focus("form filled", rec.rect("#st-host img.pf-img") or rec.rect("#st-host"))
    rec.wait(1.2)
    # signature
    rec.click_sel('.st-tool[data-id="signature"]', "Signature")
    p.wait_for_selector("#an-sig-canvas", state="visible")
    rec.wait(0.8)
    rec.focus("signature pad", rec.rect(".an-sig-overlay .modal") or rec.rect("#an-sig-canvas"))
    _sign(rec)
    rec.wait(0.6)
    rec.click_sel("#an-sig-ok", "Use signature")
    rec.wait(1.0)
    _zoom_out(rec, ".st-overlay", 4)
    rec.wait(0.8)
    drag_frac(rec, "#st-host .an-page", (48 / 595, 652 / 842), (300 / 595, 702 / 842), "place signature")
    rec.wait(1.2)
    # passport photo into the photo box
    rec.click_sel('.st-tool[data-id="photo"]', "Photo")
    rec.file_dialog(os.path.join(inbox, "Passport photo.jpg"))
    rec.wait(1.2)
    drag_frac(rec, "#st-host .an-page", (342 / 595, 652 / 842), (448 / 595, 788 / 842), "place photo")
    rec.wait(1.4)
    rec.focus("signed", rec.rect("#st-host .an-page"))
    rec.wait(1.4)
    rec.click_sel("#st-save", "Save")
    p.wait_for_selector("#st-go", state="visible")
    rec.wait(0.8)
    before = set(os.listdir(inbox))
    rec.click_sel("#st-go", "Save")
    wait_new_file(inbox, before, ".pdf", 60)
    rec.success("saved")
    rec.wait(1.8)


# ── 11 · Annotate an image ────────────────────────────────────────────────────
def setup_annotate(rec, data):
    return data["inbox"]


def act_annotate(rec, data):
    p = rec.page
    rec.click_rect(rec.row("Page photo 2.jpg"), "Page photo 2.jpg")
    rec.wait(1.2)
    rec.click_sel("#qa-annotate", "Annotate")
    p.wait_for_selector(".an-overlay .an-page", state="visible")
    rec.wait(1.2)
    pg = ".an-overlay .an-page"
    rec.focus("annotate", rec.rect(pg))
    rec.click_sel('.an-overlay [data-tool="highlight"]', "Highlight")
    drag_frac(rec, pg, (0.15, 0.38), (0.53, 0.41), "highlight")
    rec.wait(0.8)
    rec.click_sel('.an-overlay [data-tool="rect"]', "Rectangle")
    drag_frac(rec, pg, (0.13, 0.18), (0.44, 0.25), "box")
    rec.wait(0.8)
    rec.click_sel('.an-overlay [data-tool="arrow"]', "Arrow")
    drag_frac(rec, pg, (0.78, 0.56), (0.56, 0.42), "arrow")
    rec.wait(1.4)
    rec.focus("annotated", rec.rect(pg))
    rec.wait(1.0)
    r = rec.js("""() => { const b = [...document.querySelectorAll('.an-overlay button')].filter(x => x.offsetParent)
        .find(x => x.textContent.trim() === 'Save'); if (!b) return null; const r = b.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }""")
    rec.click_rect(rec._video(r), "Save")
    p.wait_for_selector(".an-overlay", state="detached", timeout=30000)
    rec.success("saved")
    rec.wait(1.6)


# ── 12 · Passport / visa photo ────────────────────────────────────────────────
def setup_photo(rec, data):
    return data["inbox"]


def act_photo(rec, data):
    p = rec.page
    inbox = data["inbox"]
    context_menu(rec, "Passport photo.jpg", "Crop to photo size")
    p.wait_for_selector("#modal-photo-size", state="visible")
    p.wait_for_function("(() => { const o = document.querySelector('.modal-overlay:has(#modal-photo-size)'); return o && o._psState && o._psState().ready; })()", timeout=30000)
    rec.wait(0.6)
    rec.focus("photo dialog", rec.rect(".modal-overlay:has(#modal-photo-size) .modal"))
    rec.wait(1.6)
    rec.click_sel("[id^=ps-zi]", "Zoom in")
    rec.wait(0.8)
    rec.click_sel("[id^=ps-kb]", "Max file size")
    rec.type("100", cps=6, name="kb")
    rec.wait(0.6)
    rec.click_sel("[id^=ps-sheet]", "4x6 sheet")
    rec.wait(1.0)
    before = set(os.listdir(inbox))
    rec.click_sel("[id^=ps-save]", "Save photo")
    wait_new_file(inbox, before, ".jpg", 30)
    rec.success("photo saved")
    rec.wait(1.4)
    rec.focus("photo files", rec.rect("#filelist-list"))
    rec.wait(1.8)


# ── 13 · AI suit & tie ────────────────────────────────────────────────────────
def setup_ai(rec, data):
    return data["inbox"]


def act_ai(rec, data):
    p = rec.page
    rec.click_rect(rec.row("Casual photo.jpg"), "Casual photo.jpg")
    rec.wait(1.5)
    p.wait_for_selector("#ai-photo-section:not(.hidden)", timeout=10000)
    rec.focus("ai panel", rec.rect("#ai-photo-section"))
    rec.wait(1.6)
    sw = rec.rect("#ai-suit-swatches button, #ai-suit-swatches [role=radio]", 1)
    if sw:
        rec.click_rect(sw, "Navy suit")
        rec.wait(0.8)
    if not rec.capture:
        print("  (rehearsal: the paid AI call is skipped)", flush=True)
        rec.wait(1.0)
        return
    rec.click_sel("#ai-wear-suit", "Wear suit & tie")
    rec.ev("wait_start", name="ai")
    p.wait_for_function("() => { const b = document.querySelector('#ai-compare'); return b && !b.classList.contains('hidden'); }", timeout=300000)
    rec.ev("wait_end", name="ai")
    rec.success("ai done")
    rec.wait(1.4)
    rec.focus("ai result", rec.rect("#pane-preview"))
    rec.wait(1.8)
    rec.click_sel("#ai-compare", "Compare with original")
    rec.wait(1.5)
    r = rec.rect(".modal-overlay .modal")
    rec.focus("compare", r)
    if r:
        rec.drag([r[0] + r[2] * 0.5, r[1] + r[3] * 0.5, 1, 1], [r[0] + r[2] * 0.25, r[1] + r[3] * 0.5, 1, 1],
                 name="compare slider", duration=0.9)
        rec.wait(0.4)
        rec.drag([r[0] + r[2] * 0.25, r[1] + r[3] * 0.5, 1, 1], [r[0] + r[2] * 0.75, r[1] + r[3] * 0.5, 1, 1],
                 name="compare slider", duration=1.2)
    rec.wait(1.4)
    rec.press("esc", show=False)
    rec.wait(0.6)


# ── 14 · QR codes ─────────────────────────────────────────────────────────────
def setup_qr(rec, data):
    return data["inbox"]


def act_qr(rec, data):
    p = rec.page
    rec.click_rect(rec.row("Payment receipt.pdf"), "Payment receipt.pdf")
    p.wait_for_selector("#pdf-canvas-wrap .pdf-page", state="visible")
    rec.wait(1.8)
    pg, (qx, qy) = page_point(rec, "#pdf-canvas-wrap .pdf-page", 148 / 595, 500 / 842)
    rec.focus("receipt", pg)
    rec.wait(0.8)
    rec.click_sel("#btn-qr", "QR")
    rec.wait(2.0)                                   # the picker covers the screen
    rec.click_rect([qx - 5, qy - 5, 10, 10], "click the QR code")
    rec.wait(0.5)
    p.wait_for_selector(".modal-overlay .qr-pick-text", timeout=30000)
    rec.wait(0.6)
    rec.success("qr read")
    rec.focus("qr text", rec.rect(".modal-overlay .modal"))
    rec.wait(3.0)
    rec.press("esc", show=False)
    rec.wait(0.8)


# ── 15 · ZIP and passwords ────────────────────────────────────────────────────
def setup_zip(rec, data):
    return data["inbox"]


def act_zip(rec, data):
    p = rec.page
    inbox = data["inbox"]
    select_files(rec, "Offer letter.pdf", "Transcript.pdf", "Fee schedule.xlsx")
    rec.wait(0.6)
    rec.click_sel("#btn-zip", "Zip")
    p.wait_for_selector("#ax-zip-name", state="visible")
    rec.wait(0.6)
    rec.focus("zip dialog", rec.rect(".modal-overlay .modal"))
    rec.click_sel("#ax-zip-name", "ZIP name")
    rec.press("a", "ctrl", show=False)
    rec.type("Minji Park documents", cps=14, name="zip name")
    rec.wait(0.4)
    rec.click_rect(rec.rect("label:has(#ax-pw-on)") or rec.rect("#ax-pw-on"), "Password")
    p.wait_for_selector("#ax-zip-pw", state="visible")
    rec.click_sel("#ax-zip-pw", "password")
    rec.type("Demo#2026", cps=10, name="password")
    rec.click_sel("#ax-zip-pw2", "repeat password")
    rec.type("Demo#2026", cps=12, name="password")
    rec.wait(0.8)
    before = set(os.listdir(inbox))
    footer_btn(rec, "Create ZIP")
    wait_new_file(inbox, before, ".zip", 30)
    rec.success("zip")
    rec.wait(1.4)
    # protect a PDF with an open password
    context_menu(rec, "Offer letter.pdf", "Protect with password")
    p.wait_for_selector("#ax-pp-user", state="visible")
    rec.wait(0.6)
    rec.focus("protect dialog", rec.rect(".modal-overlay .modal"))
    rec.click_sel("#ax-pp-user", "password")
    rec.type("Demo#2026", cps=10, name="password")
    rec.click_sel("#ax-pp-user2", "repeat")
    rec.type("Demo#2026", cps=12, name="password")
    rec.wait(0.8)
    before = set(os.listdir(inbox))
    footer_btn(rec, "Protect PDF")
    wait_new_file(inbox, before, "protected", 30)
    rec.success("protected")
    rec.wait(1.8)


# ── 16 · Screen recorder & screenshot ─────────────────────────────────────────
def setup_recorder(rec, data):
    return data["inbox"]


def act_recorder(rec, data):
    p = rec.page
    rec.click_sel("#btn-record", "Record")
    p.wait_for_selector("[id^=rec-start]", state="visible")
    rec.wait(0.6)
    rec.focus("recorder", rec.rect(".modal-overlay .modal"))
    rec.wait(3.0)
    hide = rec.js("() => { const c = document.querySelector('[id^=rec-hide]'); return c ? c.checked : false; }")
    if hide:
        rec.click_rect(rec.rect("label:has([id^=rec-hide])") or rec.rect("[id^=rec-hide]"), "keep the app visible")
        rec.wait(0.6)
    rec.click_sel("[id^=rec-shotreg]", "Screenshot: region")
    rec.wait(2.2)                                   # region picker covers the screen
    pane = rec.rect("#filelist-list")
    rec.drag([pane[0] + 10, pane[1] + 5, 1, 1], [pane[0] + pane[2] - 20, pane[1] + 330, 1, 1],
             name="screenshot region", duration=1.0)
    rec.wait(2.5)
    rec.success("screenshot")
    rec.focus("recent", rec.rect(".modal-overlay .modal"))
    rec.wait(2.5)
    if p.evaluate("!!document.querySelector('[id^=rec-close]')"):
        rec.click_sel("[id^=rec-close]", "Close")
    rec.wait(0.8)


# ── 17 · Subscription & settings ──────────────────────────────────────────────
def setup_account(rec, data):
    return data["inbox"]


def act_account(rec, data):
    p = rec.page
    rec.click_sel('.sidebar-btn[data-panel="subscription"]', "Subscription")
    rec.wait(1.2)
    rec.focus("subscription", rec.rect("#sub-page-body"))
    rec.wait(3.0)
    rec.click_sel('[data-pact="extend"]', "Extend plan")
    rec.wait(1.2)
    rec.focus("plans", rec.rect("#sub-gate .sub-plans"))
    rec.wait(2.6)
    rec.press("esc", show=False)
    rec.wait(0.8)
    rec.click_sel('.sidebar-btn[data-panel="settings"]', "Settings")
    rec.wait(0.8)
    rec.click_sel("#open-settings-dialog", "Open settings")
    p.wait_for_selector("#s-apikey", state="visible")
    rec.wait(0.3)
    key_box = rec.rect("#s-apikey")
    if key_box:                                      # never show any part of the real API key
        rec.ev("redact", name="api key", rect=[key_box[0] - 40, key_box[1] - 6, key_box[2] + 80, key_box[3] + 12])
    rec.focus("settings", rec.rect(".modal-overlay .modal"))
    rec.wait(3.0)
    footer_btn(rec, "Cancel")
    rec.wait(0.5)
    rec.ev("redact_end", name="api key")
    rec.click_sel('.sidebar-btn[data-panel="workspace"]', "Files")
    rec.wait(1.2)


CHAPTERS = [
    Chapter(1, "workspace", "The workspace", setup_workspace, act_workspace),
    Chapter(2, "folders", "A folder for every student", setup_folders, act_folders),
    Chapter(3, "preview", "Instant preview", setup_preview, act_preview),
    Chapter(4, "rename", "Smart rename", setup_rename, act_rename),
    Chapter(5, "organise", "Organise with undo", setup_organise, act_organise),
    Chapter(6, "convert", "Convert anything to PDF", setup_convert, act_convert),
    Chapter(7, "compress", "Compress to a target size", setup_compress, act_compress),
    Chapter(8, "merge", "Merge, split and extract", setup_merge, act_merge),
    Chapter(9, "arrange", "Arrange pages", setup_arrange, act_arrange),
    Chapter(10, "studio", "Fill, sign and add a photo", setup_studio, act_studio),
    Chapter(11, "annotate", "Mark up images", setup_annotate, act_annotate),
    Chapter(12, "photo", "Passport and visa photos", setup_photo, act_photo),
    Chapter(13, "ai", "AI suit and tie", setup_ai, act_ai),
    Chapter(14, "qr", "Read QR codes", setup_qr, act_qr),
    Chapter(15, "zip", "ZIP and passwords", setup_zip, act_zip),
    Chapter(16, "recorder", "Screen recorder", setup_recorder, act_recorder),
    Chapter(17, "account", "Subscription and settings", setup_account, act_account),
]
