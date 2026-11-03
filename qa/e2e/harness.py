"""Shared helpers for driving a running Office Axe window over CDP.

Start the app first (from the repo root):
    set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9341
    set PYTHONUTF8=1
    python main_webview.py
Then run a script in this folder, e.g. ``python qa/e2e/shell_e2e.py <scratch dir>``.
Only ever point it at a throw-away folder.
"""
from __future__ import annotations

import os
import shutil
import sys
import time

from playwright.sync_api import sync_playwright

PORT = int(os.environ.get("OA_CDP_PORT", "9341"))


class Checker:
    def __init__(self):
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok, detail: str = "") -> bool:
        ok = bool(ok)
        self.results.append((name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""), flush=True)
        return ok

    def summary(self) -> int:
        bad = [r for r in self.results if not r[1]]
        print(f"\n{len(self.results) - len(bad)}/{len(self.results)} passed")
        for n, _, d in bad:
            print("  FAILED:", n, d)
        return 1 if bad else 0


def connect():
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    page = None
    for ctx in browser.contexts:
        for p in ctx.pages:
            if "index.html" in p.url:
                page = p
    if page is None:
        raise SystemExit("Office Axe page not found on port %d" % PORT)
    page.wait_for_function("window.pywebview && window.pywebview.api && typeof FileTree !== 'undefined' && typeof App !== 'undefined'", timeout=30000)
    return pw, browser, page


def make_pdf(path: str, pages: int = 1, text: str = "Hello") -> str:
    import fitz
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"{text} page {i + 1}")
    doc.save(path)
    doc.close()
    return path


def make_png(path: str, color=(200, 30, 30)) -> str:
    from PIL import Image
    Image.new("RGB", (64, 48), color).save(path)
    return path


def write(path: str, text: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def set_mtime(path: str, t: float) -> None:
    os.utime(path, (t, t))


def fresh_dir(root: str, name: str) -> str:
    """A new, empty folder root/name-<n> (never deletes anything)."""
    os.makedirs(root, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(root, f"{name}-{n}")):
        n += 1
    d = os.path.join(root, f"{name}-{n}")
    os.makedirs(d)
    return d


def navigate(page, folder: str, settle: float = 0.6) -> None:
    page.evaluate("f => App.navigate(f)", folder)
    page.wait_for_function("f => FileTree.getCurrentFolder().toLowerCase() === f.toLowerCase()",
                           arg=App_norm(folder), timeout=10000)
    time.sleep(settle)


def App_norm(p: str) -> str:
    s = p.replace("/", "\\")
    if len(s) == 2 and s[1] == ":":
        return s + "\\"
    return s.rstrip("\\") if len(s) > 3 else s


def names(page) -> list[str]:
    return page.evaluate("() => [...document.querySelectorAll('#filelist-list .file-item .name')].map(e => e.textContent)")


def selected_names(page) -> list[str]:
    return page.evaluate("() => FileTree.getSelected().map(p => p.split(/[\\\\/]/).pop()).sort()")


def row(page, name: str):
    return page.locator("#filelist-list .file-item", has=page.locator(".name", has_text=name)).first


def click_row(page, name: str, modifiers=None):
    row(page, name).click(modifiers=modifiers or [])
    time.sleep(0.15)


def modal_count(page) -> int:
    return page.evaluate("() => document.querySelectorAll('.modal-overlay').length")


def close_modals(page):
    for _ in range(5):
        if not modal_count(page):
            return
        page.keyboard.press("Escape")
        time.sleep(0.2)


def toasts(page) -> list[str]:
    return page.evaluate("() => [...document.querySelectorAll('#toast-container .toast')].map(t => t.textContent.trim())")


def clear_toasts(page):
    page.evaluate("() => document.querySelectorAll('#toast-container .toast').forEach(t => t.remove())")
