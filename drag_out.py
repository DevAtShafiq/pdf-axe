# -*- coding: utf-8 -*-
"""
drag_out.py — drag files from Office Axe into any Windows app (Explorer, the
desktop, WhatsApp, Telegram, a browser upload box, Outlook, Word…).

A real Windows (OLE) drag: the shell builds the data object for the chosen
files (exactly what Explorer itself hands out), and DoDragDrop runs the drag
with the normal cursors. The target decides what happens: copy by default,
Shift = move, Alt = shortcut, like dragging from Explorer.

Must run on an STA thread with OLE initialised — in the app that's the
window's UI thread (see dragdrop_bridge.py).
"""
from __future__ import annotations

import os
import sys

DROPEFFECT_NONE, DROPEFFECT_COPY, DROPEFFECT_MOVE, DROPEFFECT_LINK = 0, 1, 2, 4
EFFECT_NAMES = {DROPEFFECT_NONE: "none", DROPEFFECT_COPY: "copy", DROPEFFECT_MOVE: "move", DROPEFFECT_LINK: "link"}

MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002
DRAGDROP_S_DROP = 0x00040100
DRAGDROP_S_CANCEL = 0x00040101
DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102


def clean_paths(paths) -> list:
    """Existing, absolute, de-duplicated paths (order kept)."""
    out, seen = [], set()
    for p in paths or []:
        if not isinstance(p, str) or not p.strip():
            continue
        p = os.path.abspath(p)
        k = os.path.normcase(p)
        if k in seen or not os.path.exists(p):
            continue
        seen.add(k)
        out.append(p)
    return out


def effect_name(effect: int) -> str:
    """The single effect the target chose (move wins over copy)."""
    for bit in (DROPEFFECT_MOVE, DROPEFFECT_COPY, DROPEFFECT_LINK):
        if effect & bit:
            return EFFECT_NAMES[bit]
    return "none"


def query_continue(escape_pressed: bool, key_state: int, button: int = MK_LBUTTON) -> int:
    """IDropSource.QueryContinueDrag: Esc cancels, releasing the button drops."""
    if escape_pressed:
        return DRAGDROP_S_CANCEL
    if not key_state & button:
        return DRAGDROP_S_DROP
    return 0


class _DropSource:
    _com_interfaces_ = None            # set in _make_source (needs pythoncom)
    _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]

    def __init__(self, button: int):
        self.button = button

    def QueryContinueDrag(self, escape_pressed, key_state):
        return query_continue(bool(escape_pressed), int(key_state), self.button)

    def GiveFeedback(self, effect):
        return DRAGDROP_S_USEDEFAULTCURSORS


def _make_source(button: int):
    import pythoncom
    from win32com.server.util import wrap
    _DropSource._com_interfaces_ = [pythoncom.IID_IDropSource]
    return wrap(_DropSource(button), pythoncom.IID_IDropSource)


def data_object(paths: list):
    """The shell's IDataObject for *paths* (CF_HDROP, shell IDList array, …)."""
    import pythoncom
    from win32com.shell import shell
    desktop = shell.SHGetDesktopFolder()
    pidls = [shell.SHParseDisplayName(p, 0)[0] for p in paths]
    parents = {os.path.normcase(os.path.dirname(p)) for p in paths}
    if len(parents) == 1:
        # one folder (the usual case): relative ids from that folder, like Explorer
        parent = os.path.dirname(paths[0])
        folder = desktop.BindToObject(shell.SHParseDisplayName(parent, 0)[0], None, shell.IID_IShellFolder)
        children = [folder.ParseDisplayName(0, None, os.path.basename(p))[1] for p in paths]
        res = folder.GetUIObjectOf(0, children, pythoncom.IID_IDataObject, 0)
    else:
        # items from several folders (opened folders in the list): absolute ids
        res = desktop.GetUIObjectOf(0, pidls, pythoncom.IID_IDataObject, 0)
    obj = res[1] if isinstance(res, tuple) else res
    _prefer(obj, DROPEFFECT_COPY)
    return obj


def _prefer(obj, effect: int) -> None:
    """Tell the target what to do by default ("Preferred DropEffect"). Explorer
    would otherwise MOVE files dragged to the same drive; here a plain drag
    copies and Shift moves — the original stays where it was."""
    import struct
    import pythoncom
    import win32clipboard
    try:
        cf = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")
        medium = pythoncom.STGMEDIUM()
        medium.set(pythoncom.TYMED_HGLOBAL, struct.pack("<I", effect))
        obj.SetData((cf, None, pythoncom.DVASPECT_CONTENT, -1, pythoncom.TYMED_HGLOBAL), medium, True)
    except Exception:
        pass        # the drag still works; the target just picks its own default


def do_drag(paths, button: int = MK_LBUTTON) -> dict:
    """Run the drag. Returns {ok, effect: 'copy'|'move'|'link'|'none', paths}."""
    if sys.platform != "win32":
        return {"ok": False, "error": "Dragging out works on Windows only."}
    paths = clean_paths(paths)
    if not paths:
        return {"ok": False, "error": "Nothing to drag."}
    import pythoncom
    obj = data_object(paths)
    source = _make_source(button)
    allowed = DROPEFFECT_COPY | DROPEFFECT_MOVE | DROPEFFECT_LINK
    try:
        effect = pythoncom.DoDragDrop(obj, source, allowed)
    except pythoncom.com_error as exc:
        return {"ok": False, "error": str(exc)}
    if isinstance(effect, tuple):          # some pywin32 builds return (hr, effect)
        effect = effect[-1]
    return {"ok": True, "effect": effect_name(int(effect or 0)), "paths": paths}
