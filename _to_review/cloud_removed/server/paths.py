"""
paths.py — Validation of cloud / shared-drive paths.

Every path stored by the server is a virtual 'a/b/c.pdf' string; files on
disk are named by random blob ids, so a client-supplied path never becomes a
disk path. These checks still reject anything that could escape a user's or
office's space, and any name Windows could not save, because the desktop app
recreates these paths locally when downloading.
"""
from __future__ import annotations

from fastapi import HTTPException

_BAD_CHARS = set('<>:"|?*')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def clean_remote_path(path: str) -> str:
    """Normalise a cloud path to 'a/b/c.pdf'; reject anything escaping the user's
    space or that could not be saved as a file name on Windows."""
    p = (path or "").replace("\\", "/").strip().strip("/")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if not parts:
        raise HTTPException(400, "Invalid file path")
    for s in parts:
        check_name(s)
    joined = "/".join(parts)
    if len(joined) > 1000:
        raise HTTPException(400, "File path too long")
    return joined


def check_name(s: str) -> str:
    """Validate one path segment (a file or folder name)."""
    if s in ("", ".", ".."):
        raise HTTPException(400, "Invalid name")
    if "/" in s or "\\" in s:
        raise HTTPException(400, "Names cannot contain / or \\")
    if any(c in _BAD_CHARS or ord(c) < 32 for c in s):
        raise HTTPException(400, 'Names cannot contain < > : " | ? * or control characters')
    if s != s.rstrip(" ."):
        raise HTTPException(400, "Names cannot end with a space or a dot")
    if s.split(".")[0].lower() in _RESERVED:
        raise HTTPException(400, f'"{s}" is a reserved name on Windows')
    if len(s) > 255:
        raise HTTPException(400, "File name too long")
    return s


def clean_name(name: str) -> str:
    """A single file/folder name, trimmed and validated."""
    s = str(name or "").strip()
    if not s:
        raise HTTPException(400, "Enter a name")
    return check_name(s)


def optional_path(path: str) -> str:
    """Like clean_remote_path, but '' (the top level) is allowed."""
    p = (path or "").replace("\\", "/").strip().strip("/")
    return clean_remote_path(p) if p else ""
