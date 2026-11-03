# -*- coding: utf-8 -*-
"""Drag and drop with Windows (drag_out.py, dragdrop_bridge.py): the parts
that don't need a real mouse."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drag_out as do  # noqa: E402
from dragdrop_bridge import dropped_paths  # noqa: E402


def test_clean_paths_keeps_existing_unique_items(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"x")
    d = tmp_path / "folder"
    d.mkdir()
    got = do.clean_paths([str(a), str(a).upper() if os.name == "nt" else str(a), str(d),
                          str(tmp_path / "missing.pdf"), "", None, 5])
    assert got == [str(a), str(d)]


def test_effect_and_drag_state():
    assert do.effect_name(do.DROPEFFECT_COPY) == "copy"
    assert do.effect_name(do.DROPEFFECT_MOVE | do.DROPEFFECT_COPY) == "move"
    assert do.effect_name(do.DROPEFFECT_LINK) == "link"
    assert do.effect_name(0) == "none"
    assert do.query_continue(True, do.MK_LBUTTON) == do.DRAGDROP_S_CANCEL      # Esc
    assert do.query_continue(False, 0) == do.DRAGDROP_S_DROP                   # button released
    assert do.query_continue(False, do.MK_LBUTTON) == 0                         # still dragging


def test_dropped_paths_from_a_pywebview_event():
    ev = {"dataTransfer": {"files": [
        {"name": "a.pdf", "pywebviewFullPath": r"C:\x\a.pdf"},
        {"name": "b.txt"},                                   # no real path (e.g. from a browser)
        {"name": "c", "pywebviewFullPath": r"C:\x\c"}]}}
    assert dropped_paths(ev) == [r"C:\x\a.pdf", r"C:\x\c"]
    assert dropped_paths({}) == [] and dropped_paths(None) == [] and dropped_paths("x") == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell data object")
def test_shell_data_object_carries_the_files(tmp_path):
    import pythoncom
    import win32con
    pythoncom.OleInitialize()
    (tmp_path / "one").mkdir()
    a = tmp_path / "one" / "a.txt"
    a.write_text("x")
    b = tmp_path / "b.txt"
    b.write_text("y")
    fe = (win32con.CF_HDROP, None, pythoncom.DVASPECT_CONTENT, -1, pythoncom.TYMED_HGLOBAL)
    for paths in ([str(a)], [str(a), str(b)]):                  # one folder / several folders
        obj = do.data_object(paths)
        data = obj.GetData(fe).data
        text = data[20:].decode("utf-16-le", "ignore")         # DROPFILES header, then wide paths
        assert all(os.path.basename(p) in text for p in paths)


def test_drag_out_refuses_nothing_to_drag():
    r = do.do_drag([]) if sys.platform == "win32" else do.do_drag(["x"])
    assert not r["ok"]
