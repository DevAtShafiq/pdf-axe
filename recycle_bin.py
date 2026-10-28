# -*- coding: utf-8 -*-
"""
recycle_bin.py — send files to the Windows Recycle Bin and bring them back.

Used by the app's Delete (after the user confirms) and its Ctrl+Z, like File
Explorer: Delete moves items to the Recycle Bin; Undo puts them back where
they were.

* ``send_to_recycle_bin(paths)`` — SHFileOperationW with FOF_ALLOWUNDO (the
  same call Explorer uses), one item at a time so every item reports its own
  result.
* ``restore(path, trashed_at)`` — finds the item in ``<drive>\\$Recycle.Bin``
  by its original path (the ``$I…`` info file records it) and moves the
  matching ``$R…`` item back. The ``$I…`` file is then removed, exactly as
  Explorer's "Restore" does, so the Recycle Bin no longer lists it.

$I file layout (little endian):
    v1 (Vista/7/8): int64 version=1, int64 size, int64 FILETIME, wchar[260] path
    v2 (Windows 10+): int64 version=2, int64 size, int64 FILETIME,
                      int32 nchars, wchar[nchars] path (NUL-terminated)
"""
from __future__ import annotations

import ctypes
import glob
import os
import shutil
import struct
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Optional

# FILETIME = 100 ns ticks since 1601-01-01
_EPOCH_DIFF = 11644473600


class RecycleError(Exception):
    pass


def available() -> bool:
    return sys.platform == "win32"


def has_recycle_bin(path: str) -> bool:
    """True when *path* is on a drive with a Recycle Bin.

    USB sticks, SD cards, network shares and CDs have none: there Windows'
    "delete with undo" silently deletes for good, so the app must not use it.
    Only local fixed disks qualify (their bin is created on first use).
    """
    if not available():
        return False
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive or drive.startswith("\\\\"):
        return False                                   # UNC path → network share
    try:
        kind = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
    except Exception:
        return False
    return kind == 3                                   # DRIVE_FIXED


# ── delete ────────────────────────────────────────────────────────────────────

def _shfileop_delete(path: str) -> int:
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"          # double-NUL terminated list
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc == 0 and op.fAnyOperationsAborted:
        rc = -1
    return rc


def send_to_recycle_bin(paths, delete_fn: Optional[Callable[[str], int]] = None,
                        bin_check: Optional[Callable[[str], bool]] = None) -> list:
    """Move each path to the Recycle Bin. → [{path, ok, trashed_at?, error?}]

    *trashed_at* (unix seconds, taken just before the call) lets ``restore``
    pick the right copy when the same path was deleted more than once.
    """
    delete_fn = delete_fn or _shfileop_delete
    bin_check = bin_check or has_recycle_bin
    out = []
    for p in paths or []:
        p = os.path.abspath(str(p or ""))
        try:
            if not os.path.exists(p):
                raise RecycleError("not found")
            if not bin_check(p):
                out.append({"path": p, "ok": False, "no_bin": True,
                            "error": "this drive has no Recycle Bin"})
                continue
            t = time.time() - 2          # FILETIME in $I is the deletion time; allow clock slack
            rc = delete_fn(p)
            if rc != 0 or os.path.exists(p):
                raise RecycleError(f"Windows could not move it to the Recycle Bin (code {rc}). "
                                   "It may be open in another program.")
            out.append({"path": p, "ok": True, "trashed_at": t})
        except Exception as exc:
            out.append({"path": p, "ok": False, "error": str(exc)})
    return out


# ── restore ───────────────────────────────────────────────────────────────────

def parse_info_file(data: bytes) -> Optional[dict]:
    """Decode a $I file → {path, size, deleted_at (unix s)} or None."""
    if len(data) < 24:
        return None
    version, size, ft = struct.unpack_from("<qqq", data, 0)
    if version == 1:
        raw = data[24:24 + 520]
    elif version == 2:
        if len(data) < 28:
            return None
        n = struct.unpack_from("<i", data, 24)[0]
        raw = data[28:28 + 2 * max(0, n)]
    else:
        return None
    path = raw.decode("utf-16-le", "replace").split("\0", 1)[0]
    if not path:
        return None
    return {"path": path, "size": size, "deleted_at": ft / 1e7 - _EPOCH_DIFF}


def build_info_file(path: str, size: int, deleted_at: float) -> bytes:
    """Encode a v2 $I record (used by the tests)."""
    ft = int((deleted_at + _EPOCH_DIFF) * 1e7)
    name = path + "\0"
    return struct.pack("<qqqi", 2, size, ft, len(name)) + name.encode("utf-16-le")


def bin_dirs(path: str) -> list:
    """The current user's Recycle Bin folder(s) on *path*'s drive."""
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    root = os.path.join(drive + os.sep, "$Recycle.Bin")
    out = []
    try:
        for d in os.listdir(root):
            full = os.path.join(root, d)
            if os.path.isdir(full) and os.access(full, os.R_OK):
                out.append(full)
    except OSError:
        pass
    return out


def find_in_bin(path: str, trashed_at: float = 0.0, dirs: Optional[list] = None) -> Optional[tuple]:
    """(r_path, i_path, info) of the newest bin entry for *path* deleted at or
    after *trashed_at*, or None."""
    want = os.path.normcase(os.path.abspath(path))
    best = None
    for d in (dirs if dirs is not None else bin_dirs(path)):
        for ipath in glob.glob(os.path.join(d, "$I*")):
            try:
                with open(ipath, "rb") as fh:
                    info = parse_info_file(fh.read(4096))
            except OSError:
                continue
            if not info or os.path.normcase(info["path"]) != want:
                continue
            if trashed_at and info["deleted_at"] < trashed_at:
                continue
            rpath = os.path.join(d, "$R" + os.path.basename(ipath)[2:])
            if not os.path.exists(rpath):
                continue
            if best is None or info["deleted_at"] > best[2]["deleted_at"]:
                best = (rpath, ipath, info)
    return best


def _free_name(path: str) -> str:
    if not os.path.exists(path):
        return path
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    if os.path.isdir(path):
        stem, ext = name, ""
    n = 2
    while True:
        cand = os.path.join(folder, f"{stem} ({n}){ext}")
        if not os.path.exists(cand):
            return cand
        n += 1


def _shell_restore(path: str) -> bool:
    """Fallback: ask Explorer itself to "Restore" the item (Shell.Application,
    Recycle Bin namespace, verb "undelete"). Only used when *path* is free,
    because Explorer would ask before replacing. True when it came back."""
    if not available() or os.path.exists(path):
        return False
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return False
    pythoncom.CoInitialize()
    try:
        want_dir = os.path.normcase(os.path.dirname(os.path.abspath(path)))
        want_name = os.path.basename(path).lower()
        want_stem = os.path.splitext(want_name)[0]
        bin_ns = win32com.client.Dispatch("Shell.Application").NameSpace(10)
        best = None
        for item in bin_ns.Items():
            try:
                from_dir = os.path.normcase(str(item.ExtendedProperty("System.Recycle.DeletedFrom") or ""))
                name = str(item.Name or "").lower()             # extension may be hidden
                if from_dir != want_dir or name not in (want_name, want_stem):
                    continue
                when = item.ExtendedProperty("System.Recycle.DateDeleted")
                key = float(when.timestamp()) if hasattr(when, "timestamp") else 0.0
                if best is None or key > best[0]:
                    best = (key, item)
            except Exception:
                continue
        if best is None:
            return False
        best[1].InvokeVerb("undelete")
        for _ in range(40):                                    # the verb returns before the move ends
            if os.path.exists(path):
                return True
            time.sleep(0.05)
        return os.path.exists(path)
    except Exception:
        return False
    finally:
        pythoncom.CoUninitialize()


def restore(path: str, trashed_at: float = 0.0, dirs: Optional[list] = None,
            remove_info: Optional[Callable[[str], None]] = None,
            shell_fallback: bool = True) -> dict:
    """Put a recycled item back at *path* (or "name (2)" when the name is
    taken again). → {path, dst, ok} / {path, ok: False, error}."""
    try:
        hit = find_in_bin(path, trashed_at, dirs)
        if not hit:
            if shell_fallback and dirs is None and _shell_restore(path):
                return {"path": path, "dst": os.path.abspath(path), "ok": True}
            raise RecycleError("it is no longer in the Recycle Bin")
        rpath, ipath, _info = hit
        parent = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(parent):
            raise RecycleError("its folder no longer exists")
        dst = _free_name(os.path.abspath(path))
        shutil.move(rpath, dst)
        try:
            (remove_info or os.remove)(ipath)      # the bin's record of the item we just restored
        except OSError:
            pass
        return {"path": path, "dst": dst, "ok": True}
    except Exception as exc:
        return {"path": path, "ok": False, "error": str(exc)}
