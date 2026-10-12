"""Regression tests for preview-panel bugs found in the QA pass (qa/preview.md)."""
from __future__ import annotations

import os
import sys

import pytest

pymupdf = pytest.importorskip("pymupdf")

import doc_preview as dp  # noqa: E402


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")
def test_office_gets_normalised_windows_paths(tmp_path, monkeypatch):
    # PowerPoint's SaveAs fails ("can't save ^0 to ^1") on forward slashes.
    monkeypatch.setenv("OFFICEAXE_PREVIEW_CACHE", str(tmp_path / "cache").replace("\\", "/"))
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK fake pptx")
    seen = []

    def conv(app, s, d):
        seen.append((s, d))
        doc = pymupdf.open()
        doc.new_page()
        doc.save(d)

    r = dp.office_to_pdf(str(src).replace("\\", "/"), _converter=conv)
    assert r["pages"] == 1
    s, d = seen[0]
    assert "/" not in s and "/" not in d
    assert "/" not in dp.cached_pdf_path(str(src))


def test_broken_image_preview_has_a_readable_error(tmp_path):
    from sfm_bridge import SFMBridge
    p = tmp_path / "broken.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nnope")
    r = SFMBridge.get_image_preview(object.__new__(SFMBridge), str(p), 400)
    assert r["ok"] is False and "NoneType" not in r["error"] and "damaged" in r["error"]
    r = SFMBridge.get_image_preview(object.__new__(SFMBridge), str(tmp_path / "gone.png"), 400)
    assert r["ok"] is False and "not found" in r["error"]


def test_broken_docx_error_does_not_dump_the_path(tmp_path):
    p = tmp_path / "broken.docx"
    p.write_bytes(b"PK\x03\x04garbage")
    with pytest.raises(dp.PreviewError) as ei:
        dp.docx_to_html(str(p))
    assert ei.value.code == "bad_file"
    assert str(tmp_path) not in str(ei.value) and "damaged" in str(ei.value)
