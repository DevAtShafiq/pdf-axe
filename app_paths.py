"""Where Office Axe keeps its own files (settings, templates, temp).

A portable copy (the folder unzipped anywhere) and a dev run keep using the
folder they always used. An installed copy (Program Files, or any folder the
Setup installer put an uninstaller in) can't write there, so its files go to
%LOCALAPPDATA%\\OfficeAxe instead.
"""
from __future__ import annotations

import os
import sys

APP_DIR_NAME = "OfficeAxe"


def exe_dir() -> str:
    return os.path.dirname(os.path.abspath(sys.executable))


def _under(path: str, root: str | None) -> bool:
    if not root:
        return False
    p = os.path.normcase(os.path.abspath(path))
    r = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)


def is_installed(folder: str | None = None) -> bool:
    """True for a frozen app put in place by the Setup installer."""
    if folder is None:
        if not getattr(sys, "frozen", False):
            return False
        folder = exe_dir()
    if os.path.isfile(os.path.join(folder, "unins000.exe")):
        return True
    return any(_under(folder, os.environ.get(k))
               for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"))


def local_data_dir() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(root, APP_DIR_NAME)


def data_dir(portable_dir: str) -> str:
    """Folder for the app's own files: *portable_dir* unless installed."""
    if not is_installed():
        return portable_dir
    d = local_data_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d
