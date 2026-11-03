"""Shared helpers for the editor E2E scripts (qa/e2e/*.py).

Start your own app instance first (never the owner's):

    set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9344
    set PYTHONUTF8=1
    python main_webview.py

then run e.g. ``python qa/e2e/annotate_e2e.py <scratch dir>``. The scripts make
their own test files inside the scratch dir and never touch real folders.
"""
from __future__ import annotations

import contextlib
import os
import sys
import time

PORT = int(os.environ.get("QA_CDP_PORT", "9344"))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


@contextlib.contextmanager
def app_page():
    """(page) of the running app over CDP."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        page = None
        for ctx in br.contexts:
            for p in ctx.pages:
                if "index.html" in p.url or p.url.startswith("file:"):
                    page = p
        if page is None:
            raise SystemExit("App page not found on the CDP port")
        page.set_default_timeout(15000)
        try:
            yield page
        finally:
            with contextlib.suppress(Exception):
                br.close()   # closes the CDP connection only, not the app


class Results:
    def __init__(self):
        self.rows = []

    def check(self, name, cond, detail=""):
        self.rows.append((name, bool(cond), str(detail)))
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
        return bool(cond)

    def summary(self):
        bad = [r for r in self.rows if not r[1]]
        print(f"\n{len(self.rows) - len(bad)}/{len(self.rows)} passed")
        return 1 if bad else 0


def wait(page, js, timeout=15.0, poll=0.1):
    """Wait until the JS expression is truthy; returns its value."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = page.evaluate(js)
        except Exception as exc:   # page navigating etc.
            last = exc
        if last and not isinstance(last, Exception):
            return last
        time.sleep(poll)
    raise TimeoutError(f"timeout waiting for {js!r} (last={last!r})")


def close_overlays(page):
    """Force-close any editor overlay left from an earlier run (no saving)."""
    page.evaluate("""() => {
      document.querySelectorAll('.an-overlay, .modal-overlay').forEach(o => {
        const x = o.querySelector('#an-x, #pe-x');
        if (!x) return;
      });
    }""")
    for _ in range(4):
        n = page.evaluate("document.querySelectorAll('.an-overlay').length")
        if not n:
            return
        page.keyboard.press("Escape")
        time.sleep(0.2)
        # a "Discard changes" confirm may be up
        btn = page.query_selector(".modal-overlay.dlg-top .btn-primary, .modal-overlay.dlg-top .btn-danger")
        if btn:
            btn.click()
            time.sleep(0.2)


def page_box(page, sel_page_el):
    """Bounding box of a page element (CSS px)."""
    return page.eval_on_selector(sel_page_el, "el => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }")
