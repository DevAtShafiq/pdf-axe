# -*- coding: utf-8 -*-
"""
shell_menu.py — Office Axe entries in the File Explorer right-click menu.

Classic (Windows 10 style) context-menu verbs, registered for the current
Windows user only (HKCU\\Software\\Classes — no admin rights, no HKLM). On
Windows 11 they appear under "Show more options" (Shift+right-click).

    Open in Office Axe              any file, any folder, folder background
    Convert to PDF with Office Axe  Word / Excel / PowerPoint / 한글 / images
    Compress with Office Axe        PDF
    Merge into one PDF (Office Axe) PDF (select several, then right-click)

Each verb runs   <app> --open|--convert-pdf|--compress|--merge "<path>".
When Explorer starts it once per selected file, shell_ipc.py hands the
paths to the Office Axe window that is already running.

Every key this module creates carries the value  OfficeAxe = "1",  and
unregister() removes only keys that carry it, so nothing else is touched.
"""
from __future__ import annotations

import os
import sys

ROOT = r"Software\Classes"
MARK = "OfficeAxe"

DOC_EXTS = [".docx", ".doc", ".docm", ".dotx", ".dot", ".dotm", ".rtf", ".odt", ".hwp", ".hwpx",
            ".xlsx", ".xlsm", ".xls", ".xlsb", ".ods", ".pptx", ".ppt", ".pptm", ".pps", ".ppsx", ".odp"]
IMAGE_EXTS = [".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff", ".heic", ".heif"]

_HERE = os.path.dirname(os.path.abspath(__file__))


def app_command() -> list:
    """argv prefix that starts Office Axe (frozen EXE, or pythonw + main_webview.py)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return [pyw if os.path.isfile(pyw) else exe, os.path.join(_HERE, "main_webview.py")]


def app_icon() -> str:
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    ico = os.path.join(_HERE, "icon.ico")
    return ico if os.path.isfile(ico) else ""


def _cmd(prefix: list, flag: str, arg: str = '"%1"') -> str:
    return " ".join(f'"{p}"' for p in prefix) + f" {flag} {arg}"


def entries(prefix: list | None = None, icon: str | None = None) -> list:
    """[(key path under HKCU\\Software\\Classes, label, command, extra values)]"""
    prefix = prefix or app_command()
    icon = app_icon() if icon is None else icon
    out = []

    def add(key, label, flag, arg='"%1"', multi=False):
        extra = {"Icon": icon} if icon else {}
        if multi:
            extra["MultiSelectModel"] = "Player"
        out.append((key, label, _cmd(prefix, flag, arg), extra))

    add(r"*\shell\OfficeAxe.Open", "Open in Office Axe", "--open")
    add(r"Directory\shell\OfficeAxe.Open", "Open in Office Axe", "--open")
    add(r"Directory\Background\shell\OfficeAxe.Open", "Open in Office Axe", "--open", arg='"%V"')
    for ext in DOC_EXTS + IMAGE_EXTS:
        add(rf"SystemFileAssociations\{ext}\shell\OfficeAxe.ToPdf", "Convert to PDF with Office Axe",
            "--convert-pdf", multi=True)
    add(r"SystemFileAssociations\.pdf\shell\OfficeAxe.Compress", "Compress with Office Axe", "--compress", multi=True)
    add(r"SystemFileAssociations\.pdf\shell\OfficeAxe.Merge", "Merge into one PDF (Office Axe)", "--merge", multi=True)
    return out


def _winreg():
    import winreg
    return winreg


def register(prefix: list | None = None, icon: str | None = None, reg=None) -> int:
    """Create / refresh every entry. Returns how many verbs were written."""
    wr = reg or _winreg()
    n = 0
    for key, label, command, extra in entries(prefix, icon):
        full = ROOT + "\\" + key
        with wr.CreateKeyEx(wr.HKEY_CURRENT_USER, full, 0, wr.KEY_WRITE) as k:
            wr.SetValueEx(k, "", 0, wr.REG_SZ, label)
            wr.SetValueEx(k, MARK, 0, wr.REG_SZ, "1")
            for name, val in extra.items():
                wr.SetValueEx(k, name, 0, wr.REG_SZ, val)
        with wr.CreateKeyEx(wr.HKEY_CURRENT_USER, full + r"\command", 0, wr.KEY_WRITE) as k:
            wr.SetValueEx(k, "", 0, wr.REG_SZ, command)
        n += 1
    _notify()
    return n


def _ours(wr, full) -> bool:
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, full, 0, wr.KEY_READ) as k:
            return wr.QueryValueEx(k, MARK)[0] == "1"
    except OSError:
        return False


def unregister(reg=None) -> int:
    """Remove exactly the keys register() made (only those marked OfficeAxe=1)."""
    wr = reg or _winreg()
    n = 0
    for key, *_ in entries(prefix=["x"], icon=""):
        full = ROOT + "\\" + key
        if not _ours(wr, full):
            continue
        for sub in (full + r"\command", full):
            try:
                wr.DeleteKey(wr.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
        n += 1
    _notify()
    return n


def status(reg=None) -> dict:
    """{installed: all verbs present, count, total, command_ok: commands point at this app}"""
    wr = reg or _winreg()
    ents = entries()
    present, current = 0, 0
    for key, _label, command, _extra in ents:
        full = ROOT + "\\" + key
        if not _ours(wr, full):
            continue
        present += 1
        try:
            with wr.OpenKey(wr.HKEY_CURRENT_USER, full + r"\command", 0, wr.KEY_READ) as k:
                if wr.QueryValueEx(k, "")[0] == command:
                    current += 1
        except OSError:
            pass
    return {"installed": present == len(ents), "count": present, "total": len(ents),
            "command_ok": current == len(ents)}


def _notify():
    """Tell Explorer that associations changed (best effort)."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
    except Exception:
        pass
