"""Reload the UI of a running Office Axe instance (after editing ui/ files).

    set QA_CDP_PORT=9362
    python qa/e2e/reload_app.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import app_page, wait  # noqa: E402

if __name__ == "__main__":
    with app_page() as page:
        page.evaluate("() => location.reload()")
        time.sleep(1.5)
        wait(page, "window.pywebview && window.pywebview.api && typeof Studio !== 'undefined' && typeof App !== 'undefined'",
             timeout=40)
        print("reloaded")
