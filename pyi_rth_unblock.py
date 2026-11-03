# PyInstaller runtime hook — runs before the main script (frozen builds only).
"""Unblock the app's own DLLs before pywebview loads .NET (see motw.py)."""
from __future__ import annotations

import os
import sys

if getattr(sys, "frozen", False) and sys.platform == "win32":
    try:
        from motw import unblock_tree
        unblock_tree(getattr(sys, "_MEIPASS", "") or os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        pass
