"""doc_convert: Word / HWP(X) → PDF next to the file, original kept."""
import os

import pytest

fitz = pytest.importorskip("pymupdf")
import doc_convert as dc  # noqa: E402


def _fake(src, dst):
    d = fitz.open()
    d.new_page().insert_text((72, 72), os.path.basename(src))
    d.save(dst)
    d.close()


def test_word_and_hwpx_get_a_pdf_next_to_them_and_keep_the_original(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICEAXE_PREVIEW_CACHE", str(tmp_path / "cache"))
    for name in ("letter.docx", "form.hwpx", "old.hwp"):
        src = tmp_path / name
        src.write_bytes(b"original")
        r = dc.to_pdf(str(src), _word=_fake, _hwp=_fake)
        assert r["out_path"] == str(tmp_path / (os.path.splitext(name)[0] + ".pdf")) and r["pages"] == 1
        assert src.read_bytes() == b"original"


def test_never_overwrites_an_existing_pdf(tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(b"x")
    (tmp_path / "a.pdf").write_bytes(b"keep")
    r = dc.to_pdf(str(src), _word=_fake)
    assert os.path.basename(r["out_path"]) == "a (2).pdf"
    assert (tmp_path / "a.pdf").read_bytes() == b"keep"


def test_refuses_other_files_and_missing_hangul(tmp_path, monkeypatch):
    other = tmp_path / "a.txt"
    other.write_text("x")
    with pytest.raises(dc.ConvertError) as e:
        dc.to_pdf(str(other))
    assert e.value.code == "unsupported"
    hw = tmp_path / "b.hwpx"
    hw.write_bytes(b"x")
    monkeypatch.setattr(dc, "hwp_available", lambda: False)
    with pytest.raises(dc.ConvertError) as e:
        dc.to_pdf(str(hw))
    assert e.value.code == "no_hwp" and not (tmp_path / "b.pdf").exists()


def test_a_failed_hangul_run_leaves_no_file_next_to_the_document(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICEAXE_PREVIEW_CACHE", str(tmp_path / "cache"))
    hw = tmp_path / "c.hwpx"
    hw.write_bytes(b"x")

    def boom(src, dst):
        raise RuntimeError("crashed")
    with pytest.raises(dc.ConvertError):
        dc.to_pdf(str(hw), _hwp=boom)
    assert sorted(os.listdir(tmp_path)) == ["c.hwpx", "cache"]


def test_excel_and_powerpoint_are_converted_next_to_the_file(tmp_path):
    import pymupdf
    import doc_convert as dc

    def fake(src, dst):                           # stands in for Office's exporter
        d = pymupdf.open(); d.new_page(); d.save(dst); d.close()
    for name in ("fees.xlsx", "slides.pptx"):
        src = tmp_path / name
        src.write_bytes(b"office file")
        r = dc.to_pdf(str(src), _word=fake)
        assert r["out_path"] == str(tmp_path / (src.stem + ".pdf")) and r["pages"] == 1
        assert r["app"] == ("Excel" if name.endswith("xlsx") else "PowerPoint")
        assert src.read_bytes() == b"office file"  # original untouched
