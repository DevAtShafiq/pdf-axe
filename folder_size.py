# -*- coding: utf-8 -*-
"""
folder_size.py — how big a folder is, for the file list's Size column.

    measure(path) → {bytes, files, folders, complete}

Read-only. Walks with os.scandir, never follows symlinks or junctions, and
silently skips folders it can't read. Stops at *max_items* / *max_seconds*
(complete=False then, and the UI shows "> 1.2 GB").
"""
from __future__ import annotations

import os
import stat
import time
from typing import Callable, Optional

MAX_ITEMS = 200_000
MAX_SECONDS = 4.0
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)    # junctions on Windows
    except OSError:
        return True


def measure(path: str, max_items: int = MAX_ITEMS, max_seconds: float = MAX_SECONDS,
            cancelled: Optional[Callable[[], bool]] = None) -> dict:
    total = files = folders = items = 0
    complete = True
    t0 = time.monotonic()
    stack = [path]
    while stack:
        if cancelled and cancelled():
            complete = False
            break
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            continue                                  # no permission / vanished: skip
        with it:
            for entry in it:
                items += 1
                if items > max_items or (items % 512 == 0 and time.monotonic() - t0 > max_seconds):
                    complete = False
                    stack.clear()
                    break
                try:
                    if _is_link(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders += 1
                        stack.append(entry.path)
                    else:
                        files += 1
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return {"bytes": total, "files": files, "folders": folders, "complete": complete}
