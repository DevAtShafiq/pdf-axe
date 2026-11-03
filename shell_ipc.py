# -*- coding: utf-8 -*-
"""
shell_ipc.py — command-line actions from File Explorer, one app window.

    Office Axe --open "C:\\path"          select / open it in the app
    Office Axe --convert-pdf "file"       make a PDF (originals kept)
    Office Axe --compress "file.pdf"      open Compress for it
    Office Axe --merge "file.pdf"         open Merge (with every selected PDF)

Explorer starts the app once per selected file. The first process becomes
the app and listens on a local socket (127.0.0.1, per-user auth key); every
later process sends its action there and exits. Actions arriving close
together (one right-click on several files) are grouped into one batch.
"""
from __future__ import annotations

import getpass
import hashlib
import os
import threading
import time
from typing import Callable, Optional

PORT = 48613
FLAGS = {"--open": "open", "--convert-pdf": "convert", "--compress": "compress", "--merge": "merge"}
GROUP_WINDOW_S = 1.0


def _authkey() -> bytes:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return hashlib.sha256(f"officeaxe-shell:{user}".encode()).digest()


def parse_args(argv: list) -> list:
    """argv → [{"action", "path"}] (unknown flags and missing paths are ignored)."""
    out, i = [], 0
    while i < len(argv):
        act = FLAGS.get(argv[i])
        if act and i + 1 < len(argv):
            p = argv[i + 1].strip().strip('"')
            if p:
                out.append({"action": act, "path": os.path.abspath(p)})
            i += 2
            continue
        i += 1
    return out


def group(actions: list) -> list:
    """Merge actions of the same kind into one: [{"action", "paths": [...]}]."""
    order, by = [], {}
    for a in actions:
        k = a["action"]
        if k not in by:
            by[k] = []
            order.append(k)
        for p in a.get("paths") or [a["path"]]:
            if p not in by[k]:
                by[k].append(p)
    return [{"action": k, "paths": by[k]} for k in order]


def send(actions: list, port: int = PORT, timeout: float = 4.0) -> bool:
    """Give *actions* to the running app. True when it took them."""
    from multiprocessing.connection import Client
    end = time.time() + timeout
    while True:
        try:
            try:
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)   # let the app come to the front
            except Exception:
                pass
            with Client(("127.0.0.1", port), authkey=_authkey()) as c:
                c.send({"actions": actions})
                return bool(c.recv())
        except (ConnectionRefusedError, OSError, EOFError):
            if time.time() >= end:
                return False
            time.sleep(0.25)


class Server:
    """Listens for actions from later processes and delivers them in groups."""

    def __init__(self, deliver: Callable[[list], None], port: int = PORT):
        from multiprocessing.connection import Listener
        self.listener = Listener(("127.0.0.1", port), authkey=_authkey())   # raises if taken
        self.deliver = deliver
        self._pending: list = []
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        threading.Thread(target=self._loop, daemon=True).start()

    def add(self, actions: list) -> None:
        with self._lock:
            self._pending += actions
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(GROUP_WINDOW_S, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            batch, self._pending = self._pending, []
        if batch:
            try:
                self.deliver(group(batch))
            except Exception:
                pass

    def _loop(self) -> None:
        while True:
            try:
                with self.listener.accept() as c:
                    msg = c.recv()
                    self.add(list(msg.get("actions") or []))
                    c.send(True)
            except Exception:
                time.sleep(0.1)


def start_or_forward(argv: list, deliver: Callable[[list], None], port: int = PORT):
    """Called at app start. If another Office Axe already runs, forward this
    process's actions to it and return (None, True) — the caller exits.
    Otherwise become the server: returns (server, False); this process's own
    actions are queued on it."""
    actions = parse_args(argv)
    if actions and send(actions, port, timeout=0.6):
        return None, True
    try:
        server = Server(deliver, port)
    except OSError:
        # another process won the race to listen: hand over to it
        if actions and send(actions, port, timeout=6.0):
            return None, True
        return None, False
    if actions:
        server.add(actions)
    return server, False
