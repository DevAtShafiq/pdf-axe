"""
qa/e2e/cdp.py — tiny helper to drive running Office Axe windows over CDP.

Start the app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>,
then:
    python qa/e2e/cdp.py <port> run "<js>"   |   cdp.py <port> file <script.js>
    python qa/e2e/cdp.py <port> shot <file.png>
    python qa/e2e/cdp.py <port> text <css selector>
The JS may `await` (it runs inside an async function; use `return`).
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright


def page_for(pw, port: int):
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    for ctx in browser.contexts:
        for p in ctx.pages:
            if not p.url.startswith("devtools://"):
                return browser, p
    raise SystemExit("no page")


def run_js(page, js: str):
    return page.evaluate(f"(async () => {{ {js} }})()")


def main() -> None:
    port, cmd = int(sys.argv[1]), sys.argv[2]
    with sync_playwright() as pw:
        _b, page = page_for(pw, port)
        if cmd in ("run", "file"):
            src = open(sys.argv[3], encoding="utf-8").read() if cmd == "file" else sys.argv[3]
            out = run_js(page, src)
            print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        elif cmd == "shot":
            page.screenshot(path=sys.argv[3])
            print(sys.argv[3])
        elif cmd == "text":
            print(page.locator(sys.argv[3]).first.inner_text())


if __name__ == "__main__":
    main()
