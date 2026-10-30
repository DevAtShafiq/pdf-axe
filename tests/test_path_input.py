# -*- coding: utf-8 -*-
"""Typed / pasted paths for the editable breadcrumb and Ctrl+V (path_input.py)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import path_input as pi  # noqa: E402


def test_folder_file_quotes_and_links(tmp_path, monkeypatch):
    d = tmp_path / "Fictional class" / "15. SHILA"
    d.mkdir(parents=True)
    f = d / "offer letter.pdf"
    f.write_bytes(b"%PDF-1.4")
    r = pi.resolve(f'"{d}"')
    assert r["ok"] and r["is_dir"] and r["path"] == str(d)
    r = pi.resolve(f"  \u201c{f}\u201d  \nsecond line")
    assert r["ok"] and not r["is_dir"] and r["folder"] == str(d)
    url = "file:///" + str(f).replace("\\", "/").replace(" ", "%20")
    assert pi.resolve(url)["path"] == str(f)
    monkeypatch.setenv("OA_TEST_DIR", str(tmp_path))
    assert pi.resolve(r"%OA_TEST_DIR%\Fictional class")["path"] == str(tmp_path / "Fictional class")


def test_clear_errors():
    assert not pi.resolve("")["ok"]
    assert "full path" in pi.resolve(r"Documents\x")["error"]
    assert "Unknown variable" in pi.resolve(r"%NO_SUCH_VAR_OA%\x")["error"]
    r = pi.resolve(r"C:\definitely\not\here\oa-test")
    assert not r["ok"] and r["exists"] is False and "Couldn" in r["error"]


def test_unc_and_drive_forms():
    assert pi.clean("file://server/share/a%20b.pdf") == r"\\server\share\a b.pdf"
    assert pi.clean("C:") == "C:\\"
    assert pi.clean(r"'D:\x'") == r"D:\x"
