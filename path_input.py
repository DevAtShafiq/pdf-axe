# -*- coding: utf-8 -*-
"""
path_input.py — understand a path the user typed or pasted.

Used by the editable breadcrumb (click empty space / Ctrl+L / Alt+D / F4) and
by Ctrl+V in the file list when the clipboard holds a path as text.

    resolve("\"%USERPROFILE%\\Documents\"")  → {ok, path, exists, is_dir}
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse


def clean(text: str) -> str:
    """The path inside *text*: quotes, file:/// links, %VARS%, ~ handled."""
    s = str(text or "").strip()
    # only the first line of a multi-line paste
    s = s.splitlines()[0].strip() if s else ""
    # surrounding quotes, including the curly ones Word adds
    while len(s) >= 2 and s[0] in "\"'“”‘’" and s[-1] in "\"'“”‘’":
        s = s[1:-1].strip()
    if s.lower().startswith("file:"):
        u = urlparse(s)
        p = unquote(u.path or "")
        if u.netloc and u.netloc.lower() != "localhost":
            s = "\\\\" + u.netloc + p.replace("/", "\\")          # file://server/share → UNC
        else:
            s = re.sub(r"^/(?=[A-Za-z]:)", "", p)                  # /C:/x → C:/x
    s = os.path.expandvars(s)
    s = os.path.expanduser(s)
    if re.fullmatch(r"[A-Za-z]:", s):
        s += "\\"                                                 # "C:" means the drive root
    return s


def resolve(text: str) -> dict:
    s = clean(text)
    if not s:
        return {"ok": False, "error": "Type or paste a folder or file path."}
    if "%" in s and re.search(r"%[^%\\/]+%", s):
        return {"ok": False, "error": f"Unknown variable in “{s}”.", "path": s}
    if not (os.path.isabs(s) or s.startswith("\\\\")):
        return {"ok": False, "error": "Use a full path, like C:\\Users\\Name\\Documents.", "path": s}
    path = os.path.normpath(s)
    if not os.path.exists(path):
        return {"ok": False, "error": f"Couldn’t find “{path}”. Check the path and try again.",
                "path": path, "exists": False}
    return {"ok": True, "path": path, "exists": True, "is_dir": os.path.isdir(path),
            "folder": path if os.path.isdir(path) else os.path.dirname(path)}
