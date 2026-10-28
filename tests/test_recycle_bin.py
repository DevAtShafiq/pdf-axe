# -*- coding: utf-8 -*-
"""Recycle Bin delete + restore (Ctrl+Z), and undoing moves.

Nothing is really deleted here: the Windows call is replaced by a stub that
*moves* the file into a fake $Recycle.Bin folder and writes the $I record the
way Windows does, so restore() is exercised against the real file layout.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recycle_bin as rb  # noqa: E402


def _fake_bin(tmp_path):
    d = tmp_path / "$Recycle.Bin" / "S-1-5-21-test"
    d.mkdir(parents=True)
    counter = {"n": 0}

    def delete_fn(path):
        counter["n"] += 1
        tag = f"ABC{counter['n']:03d}{os.path.splitext(path)[1]}"
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        os.replace(path, d / ("$R" + tag))
        (d / ("$I" + tag)).write_bytes(rb.build_info_file(path, size, time.time()))
        return 0
    return d, delete_fn


def test_info_file_roundtrip_v2_and_v1():
    data = rb.build_info_file(r"C:\Users\a\문서\report.pdf", 1234, 1_700_000_000)
    info = rb.parse_info_file(data)
    assert info["path"] == r"C:\Users\a\문서\report.pdf" and info["size"] == 1234
    assert abs(info["deleted_at"] - 1_700_000_000) < 1
    import struct
    v1 = struct.pack("<qqq", 1, 5, 0) + "D:\\x.txt".encode("utf-16-le").ljust(520, b"\0")
    assert rb.parse_info_file(v1)["path"] == "D:\\x.txt"
    assert rb.parse_info_file(b"junk") is None


def test_delete_then_restore(tmp_path):
    d, delete_fn = _fake_bin(tmp_path)
    f = tmp_path / "work" / "report.pdf"
    f.parent.mkdir()
    f.write_bytes(b"%PDF-1.4 hello")
    res = rb.send_to_recycle_bin([str(f)], delete_fn=delete_fn, bin_check=lambda p: True)
    assert res[0]["ok"] and not f.exists()

    removed = []
    r = rb.restore(str(f), res[0]["trashed_at"], dirs=[str(d)], remove_info=removed.append)
    assert r["ok"] and f.read_bytes() == b"%PDF-1.4 hello" and r["dst"] == str(f)
    assert len(removed) == 1 and os.path.basename(removed[0]).startswith("$I")


def test_restore_picks_newest_and_never_overwrites(tmp_path):
    d, delete_fn = _fake_bin(tmp_path)
    f = tmp_path / "a.txt"
    f.write_text("old")
    first = rb.send_to_recycle_bin([str(f)], delete_fn=delete_fn, bin_check=lambda p: True)[0]
    time.sleep(0.05)
    f.write_text("new")
    second = rb.send_to_recycle_bin([str(f)], delete_fn=delete_fn, bin_check=lambda p: True)[0]
    f.write_text("someone made a new a.txt")        # the name is taken again
    r = rb.restore(str(f), second["trashed_at"], dirs=[str(d)], remove_info=lambda p: None)
    assert r["ok"] and os.path.basename(r["dst"]) == "a (2).txt"
    assert open(r["dst"]).read() == "new" and f.read_text() == "someone made a new a.txt"
    assert first["ok"]


def test_restore_when_gone_from_bin(tmp_path):
    d, _ = _fake_bin(tmp_path)
    r = rb.restore(str(tmp_path / "nope.txt"), 0, dirs=[str(d)])
    assert not r["ok"] and "no longer in the Recycle Bin" in r["error"]


def test_no_bin_drive_is_refused_not_deleted(tmp_path):
    f = tmp_path / "usb.txt"
    f.write_text("x")
    called = []
    res = rb.send_to_recycle_bin([str(f)], delete_fn=lambda p: called.append(p) or 0, bin_check=lambda p: False)
    assert res[0]["no_bin"] and not res[0]["ok"] and f.exists() and not called


def test_bridge_falls_back_to_review_without_bin(tmp_path, monkeypatch):
    import sfm_bridge
    monkeypatch.setattr(rb, "has_recycle_bin", lambda p: False)
    b = sfm_bridge.SFMBridge()
    f = tmp_path / "doc.txt"
    f.write_text("keep me")
    r = b.recycle_delete([str(f)])
    item = r["results"][0]
    assert r["ok"] and item["fallback"] == "review" and not f.exists() and os.path.exists(item["moved_to"])
    u = b.restore_recycled([{"path": item["path"], "moved_to": item["moved_to"]}])
    assert u["ok"] and f.read_text() == "keep me"


def test_undo_moves(tmp_path):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    src_dir, dst_dir = tmp_path / "from", tmp_path / "to"
    src_dir.mkdir(); dst_dir.mkdir()
    f = src_dir / "x.pdf"
    f.write_text("1")
    r = b.move_files([str(f)], str(dst_dir))
    moved = r["results"][0]
    assert r["ok"] and not f.exists()
    u = b.undo_moves([{"src": moved["src"], "dst": moved["dst"]}])
    assert u["ok"] and f.exists() and not os.path.exists(moved["dst"])
    # redo = the same call with src/dst swapped
    again = b.undo_moves([{"src": moved["dst"], "dst": str(f)}])
    assert again["ok"] and os.path.exists(moved["dst"]) and not f.exists()
