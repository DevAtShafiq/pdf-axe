"""Tiny CDP helper for driving the running Office Axe window (preview QA).

    python qa/e2e/pv_cdp.py js "<js expression>"      → prints JSON result
    python qa/e2e/pv_cdp.py shot out.png                → screenshot
    python qa/e2e/pv_cdp.py script file.js              → evaluates the file's JS

Connects to the app started with
WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9342 (PV_CDP_PORT).
"""
from __future__ import annotations

import json
import os
import sys

from playwright.sync_api import sync_playwright

PORT = os.environ.get("PV_CDP_PORT", "9342")


def page_of(pw):
    br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    for ctx in br.contexts:
        for pg in ctx.pages:
            if "index.html" in pg.url or pg.url.startswith("http"):
                return br, pg
    return br, br.contexts[0].pages[0]


def run(js: str):
    with sync_playwright() as pw:
        br, pg = page_of(pw)
        return pg.evaluate(js)


def main(argv):
    cmd = argv[1]
    with sync_playwright() as pw:
        br, pg = page_of(pw)
        if cmd == "js":
            print(json.dumps(pg.evaluate(argv[2]), ensure_ascii=False, indent=1))
        elif cmd == "script":
            with open(argv[2], encoding="utf-8") as f:
                print(json.dumps(pg.evaluate(f.read()), ensure_ascii=False, indent=1))
        elif cmd == "shot":
            pg.screenshot(path=argv[2])
            print("saved", argv[2])
        elif cmd == "reload":                # fresh JS/CSS after an edit
            cdp = pg.context.new_cdp_session(pg)
            cdp.send("Network.clearBrowserCache")
            pg.reload()
            import time
            time.sleep(4)
            print("reloaded")
        elif cmd == "keys":                 # keys "Control+a" ...
            for k in argv[2:]:
                pg.keyboard.press(k)
            print("pressed", argv[2:])


if __name__ == "__main__":
    main(sys.argv)
