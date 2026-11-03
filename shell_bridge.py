# -*- coding: utf-8 -*-
"""
shell_bridge.py — File Explorer integration in the JS bridge (mixed into SFMBridge).

* shell_menu_status / shell_menu_set(on): the Settings switch that adds or
  removes Office Axe's right-click menu entries (shell_menu.py).
* shell_deliver(groups): called by shell_ipc when Explorer hands over files;
  queued until the page is ready, then pushed as the 'shell_actions' event.
* shell_take_pending(): the page asks for actions that arrived before it was ready.
"""
from __future__ import annotations

import threading


class ShellBridgeMixin:
    _shell_pending = None
    _shell_ready = False
    _shell_lock = threading.Lock()

    def shell_deliver(self, groups: list) -> None:
        with self._shell_lock:
            if self._shell_pending is None:
                self._shell_pending = []
            if not self._shell_ready:
                self._shell_pending += groups
                return
        self._bring_to_front()
        self._emit("shell_actions", groups)

    def _bring_to_front(self) -> None:
        w = getattr(self, "_window", None)
        if not w:
            return
        try:
            w.restore()
        except Exception:
            pass
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "Office Axe")
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    # ── JS API ───────────────────────────────────────────────────────────────
    def shell_take_pending(self) -> dict:
        """Actions handed over before the page was ready (and mark it ready)."""
        with self._shell_lock:
            items, self._shell_pending = list(self._shell_pending or []), []
            self._shell_ready = True
        return {"ok": True, "actions": items}

    def shell_menu_status(self) -> dict:
        try:
            import shell_menu
            return {"ok": True, **shell_menu.status()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def shell_menu_set(self, on: bool) -> dict:
        """Add (on) or remove (off) the right-click menu entries for this Windows user."""
        try:
            import shell_menu
            n = shell_menu.register() if on else shell_menu.unregister()
            return {"ok": True, "changed": n, **shell_menu.status()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
