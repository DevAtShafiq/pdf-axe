"""Regression tests from the file-tools QA pass (naming, overwrite safety,
old bridge entry points)."""
import os
import sys

import pytest

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import image_crop  # noqa: E402
import media_convert as mc  # noqa: E402


def _pdf(path, pages=3):
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} text")
    doc.save(str(path))
    return str(path)


def _bytes(p):
    with open(p, "rb") as f:
        return f.read()


@pytest.fixture
def bridge():
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    b.events = []
    b._emit = lambda ev, payload=None: b.events.append((ev, payload))
    b._thread = lambda fn, *a, **k: fn(*a, **k)
    return b


# ── one numbering style everywhere: "name (2).ext" ──────────────────────────

def test_media_convert_numbering_matches_other_tools(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"x")
    assert os.path.basename(mc.unique_path(str(p))) == "a (2).pdf"
    (tmp_path / "a (2).pdf").write_bytes(b"x")
    assert os.path.basename(mc.unique_path(str(p))) == "a (3).pdf"
    # numbering an already-numbered name does not stack "(2) (2)"
    assert os.path.basename(mc.unique_path(str(tmp_path / "a (2).pdf"))) == "a (3).pdf"
    d = tmp_path / "x_images"
    d.mkdir()
    assert os.path.basename(mc.unique_dir(str(d))) == "x_images (2)"


def test_crop_numbering_and_exclusive_write(tmp_path):
    src = tmp_path / "pic.png"
    Image.new("RGB", (40, 30), (10, 20, 30)).save(src)
    outs = [image_crop.crop_image(str(src), 0, 0, 10, 10)[1] for _ in range(3)]
    assert [os.path.basename(o) for o in outs] == ["pic_cropped.png", "pic_cropped (2).png",
                                                   "pic_cropped (3).png"]
    # an explicit out_path that exists gets a free name, the file is untouched
    keep = _bytes(outs[0])
    ok, out = image_crop.crop_image(str(src), 0, 0, 5, 5, 0, outs[0])
    assert ok and os.path.basename(out) == "pic_cropped (4).png"
    assert _bytes(outs[0]) == keep


# ── old bridge entry points must never overwrite ────────────────────────────

def test_old_build_pdf_from_pages_never_overwrites(bridge, tmp_path):
    src = _pdf(tmp_path / "s.pdf", 3)
    out = tmp_path / "out.pdf"
    out.write_bytes(b"precious")
    r = bridge.build_pdf_from_pages([{"path": src, "page": 2}, {"path": src, "page": 0}], str(out))
    assert r["ok"], r
    assert out.read_bytes() == b"precious"
    assert os.path.basename(r["out_path"]) == "out (2).pdf"
    assert fitz.open(r["out_path"]).page_count == 2
    # replace=True keeps a backup in _to_review/
    r = bridge.build_pdf_from_pages([{"path": src, "page": 1}], src, True)
    assert r["ok"] and r["replaced"] and os.path.isfile(r["backup"])
    assert "_to_review" in r["backup"] and fitz.open(r["backup"]).page_count == 3


def test_old_merge_split_extract_reorder_never_overwrite(bridge, tmp_path):
    a = _pdf(tmp_path / "a.pdf", 2)
    b = _pdf(tmp_path / "b.pdf", 1)
    out = tmp_path / "m.pdf"
    out.write_bytes(b"precious")
    r = bridge.merge_pdfs([a, b], str(out))
    assert r["ok"] and out.read_bytes() == b"precious" and r["page_count"] == 3

    sd = tmp_path / "split"
    sd.mkdir()
    (sd / "a_p1.pdf").write_bytes(b"precious")
    r = bridge.split_pdf_pages(a, str(sd))
    assert r["ok"] and (sd / "a_p1.pdf").read_bytes() == b"precious" and r["count"] == 2

    ex = tmp_path / "ex.pdf"
    ex.write_bytes(b"precious")
    r = bridge.extract_pdf_pages(a, "2", str(ex))
    assert r["ok"] and ex.read_bytes() == b"precious"
    assert os.path.basename(r["out_path"]) == "ex (2).pdf"

    before = _bytes(a)
    r = bridge.reorder_pdf_pages(a, [1, 0], a)        # same path: still a new file
    assert r["ok"] and _bytes(a) == before
    assert os.path.basename(r["out_path"]) == "a_arranged.pdf"
    d = fitz.open(r["out_path"])
    assert "Page 2" in d[0].get_text() and "Page 1" in d[1].get_text()


def test_thumbnails_missing_file_is_readable(bridge, tmp_path):
    r = bridge.pdf_thumbnails(str(tmp_path / "gone.pdf"), [0], 80)
    assert r["ok"] is False and r["error"] == "File not found: gone.pdf"


def test_delete_page_keeps_backup(bridge, tmp_path):
    p = _pdf(tmp_path / "d.pdf", 3)
    r = bridge.delete_pdf_page(p, 1)
    assert r["ok"], r
    assert fitz.open(p).page_count == 2
    assert os.path.isfile(r["backup"]) and fitz.open(r["backup"]).page_count == 3
