"""Print basic state of the running app (debug helper)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import connect  # noqa: E402

pw, browser, page = connect()
expr = sys.argv[1] if len(sys.argv) > 1 else (
    "() => ({url: location.href, folder: FileTree.getCurrentFolder(), sort: localStorage.getItem('oa.filelist.sort'),"
    " drives: document.querySelectorAll('.tree-drive').length, panel: App.state.currentPanel})")
print(json.dumps(page.evaluate(expr), indent=1, ensure_ascii=False))
pw.stop()
