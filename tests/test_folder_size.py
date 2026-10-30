# -*- coding: utf-8 -*-
"""Folder sizes for the file list's Size column (folder_size.py)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import folder_size as fs  # noqa: E402


def _tree(root):
    (root / "A" / "B").mkdir(parents=True)
    (root / "a.bin").write_bytes(b"x" * 1000)
    (root / "A" / "b.bin").write_bytes(b"x" * 2000)
    (root / "A" / "B" / "c.bin").write_bytes(b"x" * 3000)


def test_sums_every_file_below(tmp_path):
    _tree(tmp_path)
    r = fs.measure(str(tmp_path))
    assert r == {"bytes": 6000, "files": 3, "folders": 2, "complete": True}


def test_stops_at_the_limit_and_says_so(tmp_path):
    _tree(tmp_path)
    r = fs.measure(str(tmp_path), max_items=2)
    assert r["complete"] is False and r["bytes"] < 6000
    r = fs.measure(str(tmp_path), cancelled=lambda: True)
    assert r["complete"] is False


def test_missing_folder_is_just_empty(tmp_path):
    assert fs.measure(str(tmp_path / "nope")) == {"bytes": 0, "files": 0, "folders": 0, "complete": True}


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junctions_are_not_followed(tmp_path):
    _tree(tmp_path / "real")
    (tmp_path / "top").mkdir()
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "top" / "link"), str(tmp_path / "real")],
                       capture_output=True)
    if r.returncode:
        pytest.skip("could not make a junction")
    assert fs.measure(str(tmp_path / "top"))["bytes"] == 0


def test_bridge_emits_one_event_per_folder(tmp_path, monkeypatch):
    _tree(tmp_path)
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    events = []
    monkeypatch.setattr(b, "_emit", lambda ev, payload=None: events.append((ev, payload)))
    monkeypatch.setattr(b, "_thread", lambda fn, *a, **k: fn(*a, **k))
    assert b.folder_sizes([str(tmp_path / "A"), str(tmp_path)], "j1")["ok"]
    sizes = [(p["path"], p["bytes"], p["size_str"]) for e, p in events if e == "folder_size"]
    assert sizes == [(str(tmp_path / "A"), 5000, "4.9 KB"), (str(tmp_path), 6000, "5.9 KB")]
    assert events[-1] == ("folder_sizes_done", {"job_id": "j1"})
