"""Arrange: inserting a blank page, and pdf_info page sizes (used for its size)."""
import os

import pymupdf
import pytest

import pdf_tools as pt


@pytest.fixture
def src(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "Letter page one")
    doc.new_page(width=842, height=595).insert_text((72, 100), "Landscape page two")
    p = str(tmp_path / "src.pdf")
    doc.save(p)
    doc.close()
    return p


def test_pdf_info_page_sizes(src):
    info = pt.pdf_info(src)
    assert info["page_sizes"] == [[612.0, 792.0], [842.0, 595.0]]


def test_build_with_blank_page(src, tmp_path):
    pages = [{"path": src, "page": 0, "rotate": 0},
             {"blank": True, "width": 612, "height": 792, "rotate": 0},
             {"path": src, "page": 1, "rotate": 90}]
    r = pt.build_pdf(pages, str(tmp_path / "out.pdf"))
    assert r["page_count"] == 3
    doc = pymupdf.open(r["out_path"])
    assert "Letter page one" in doc[0].get_text()
    assert doc[1].get_text().strip() == "" and round(doc[1].rect.width) == 612
    assert "Landscape page two" in doc[2].get_text() and doc[2].rotation == 90
    doc.close()


def test_build_blank_default_a4_and_bad_size(src, tmp_path):
    r = pt.build_pdf([{"blank": True}], str(tmp_path / "a4.pdf"))
    doc = pymupdf.open(r["out_path"])
    assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (595, 842)
    doc.close()
    with pytest.raises(pt.PdfToolsError):
        pt.build_pdf([{"blank": True, "width": 1, "height": 1}], str(tmp_path / "bad.pdf"))
    assert not os.path.exists(tmp_path / "bad.pdf")
