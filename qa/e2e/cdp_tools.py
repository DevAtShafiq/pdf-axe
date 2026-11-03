"""Tiny helper: attach Playwright to a QA instance of Office Axe.

Start the app first (from the worktree):
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=9343'
    $env:PYTHONUTF8='1'; python main_webview.py
Then:  python qa/e2e/<script>.py  (port via QA_CDP_PORT, default 9343)
"""
import contextlib
import os
import time

from playwright.sync_api import sync_playwright

PORT = int(os.environ.get("QA_CDP_PORT", "9343"))


@contextlib.contextmanager
def app_page():
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        page = None
        for _ in range(60):
            pages = [p for c in browser.contexts for p in c.pages]
            page = next((p for p in pages if "index.html" in p.url or p.url.startswith("http")), None)
            if page:
                break
            time.sleep(0.5)
        assert page, "app page not found"
        page.wait_for_function("() => typeof SFM !== 'undefined' && typeof App !== 'undefined' && window.pywebview && window.pywebview.api", timeout=30000)
        yield page
        browser.close()   # detaches; the app keeps running


def wait_toast(page, text, timeout=30000):
    page.wait_for_function(
        "t => Array.from(document.querySelectorAll('.toast')).some(e => e.textContent.includes(t))",
        arg=text, timeout=timeout)


def toasts(page):
    return page.evaluate("() => Array.from(document.querySelectorAll('.toast')).map(e => e.textContent.trim())")


def open_folder(page, folder):
    page.evaluate("f => FileTree.loadFolder(f)", folder)
    page.wait_for_timeout(800)
