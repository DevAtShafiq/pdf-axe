"""Screenshot the QA app: python qa/e2e/shot.py out.png"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import app_page, toasts  # noqa: E402

with app_page() as page:
    page.screenshot(path=sys.argv[1])
    print(toasts(page))
    print(page.evaluate("() => Array.from(document.querySelectorAll('.modal-overlay')).map(o => o.textContent.trim().slice(0, 200))"))
