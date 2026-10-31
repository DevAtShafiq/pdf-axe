# -*- coding: utf-8 -*-
"""
dragdrop_bridge.py — drag and drop between Office Axe and Windows (mixed into SFMBridge).

* dnd_drag_out(paths): a real Windows drag of the selected files, started
  when a selected row is dragged in the file list (drag_out.py). Runs on the
  window's UI thread; returns {ok, effect} once the user drops or cancels.
* dnd_attach(window): listens for files dropped onto the page (from Explorer,
  or rows dragged within the app). pywebview gives their real paths; they are
  sent to the page as the 'files_dropped' event, where file-tree.js copies or
  moves them into the folder they were dropped on.
"""
from __future__ import annotations

import logging
import threading

_log = logging.getLogger("sfm")


def dropped_paths(event) -> list:
    """Real paths from a pywebview DOM drop event (files with pywebviewFullPath)."""
    try:
        files = (event or {}).get("dataTransfer", {}).get("files", []) or []
    except AttributeError:
        return []
    out = []
    for f in files:
        p = f.get("pywebviewFullPath") if isinstance(f, dict) else None
        if isinstance(p, str) and p:
            out.append(p)
    return out


class DragDropBridgeMixin:
    _dnd_lock = threading.Lock()

    # ── out: Office Axe → other apps ─────────────────────────────────────────
    def dnd_drag_out(self, paths: list) -> dict:
        import drag_out
        paths = drag_out.clean_paths(paths)
        if not paths:
            return {"ok": False, "error": "Nothing to drag."}
        if not self._dnd_lock.acquire(blocking=False):
            return {"ok": False, "error": "A drag is already running."}
        try:
            return self._on_ui_thread(lambda: drag_out.do_drag(paths))
        except Exception as exc:
            _log.exception("drag out failed")
            return {"ok": False, "error": str(exc)}
        finally:
            self._dnd_lock.release()

    def _on_ui_thread(self, fn):
        """Run *fn* on the window's UI thread (STA, OLE ready) and return its result."""
        form = getattr(getattr(self, "_window", None), "native", None)
        if form is None or not getattr(form, "InvokeRequired", False):
            import pythoncom
            pythoncom.OleInitialize()
            return fn()
        from System import Func, Object  # type: ignore
        box = {}

        def run():
            import pythoncom
            try:
                pythoncom.OleInitialize()
            except Exception:
                pass
            box["r"] = fn()
            return None
        form.Invoke(Func[Object](run))
        return box.get("r", {"ok": False, "error": "The drag did not start."})

    # ── in: Explorer (or our own rows) → Office Axe ──────────────────────────
    def dnd_attach(self, window) -> None:
        if getattr(self, "_dnd_attached", False):
            return
        try:
            from webview.dom import DOMEventHandler
            window.dom.document.events.drop += DOMEventHandler(self._dnd_on_drop, True, False)
            self._dnd_attached = True
            _log.info("drag and drop: drop handler attached")
        except Exception as exc:                     # older pywebview / other platforms
            _log.warning("drag and drop: could not attach the drop handler: %s", exc)

    def _dnd_on_drop(self, event) -> None:
        paths = dropped_paths(event)
        if paths:
            self._emit("files_dropped", {"paths": paths})
