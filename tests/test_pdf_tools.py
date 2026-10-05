"""Tests for pdf_tools (merge / split / extract / arrange) and its bridge mixin."""
import os
import sys

import pytest

fitz = pytest.importorskip("fitz")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pdf_tools as pt  # noqa: E402
from pdf_tools_bridge import PdfToolsBridgeMixin  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def make_pdf(path, n, tag="P"):
    """n-page PDF; page i carries the text '<tag><i+1>' so order can be checked."""
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 60), f"{tag}{i + 1}", fontsize=24)
    doc.save(str(path))
    doc.close()
    return str(path)


def texts(path):
    doc = fitz.open(str(path))
    try:
        return [p.get_text().strip() for p in doc]
    finally:
        doc.close()


def rotations(path):
    doc = fitz.open(str(path))
    try:
        return [p.rotation for p in doc]
    finally:
        doc.close()


def digest(path):
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def a5(tmp_path):
    return make_pdf(tmp_path / "a.pdf", 5, "A")


@pytest.fixture
def b3(tmp_path):
    return make_pdf(tmp_path / "b.pdf", 3, "B")


# ── range parsing ────────────────────────────────────────────────────────────

def test_parse_groups_basic():
    assert pt.parse_page_groups("1-3,4-6,7", 10) == [[0, 1, 2], [3, 4, 5], [6]]


def test_parse_groups_open_ended_and_end_keyword():
    assert pt.parse_page_groups("8-", 10) == [[7, 8, 9]]
    assert pt.parse_page_groups("-2", 10) == [[0, 1]]
    assert pt.parse_page_groups("9-end", 10) == [[8, 9]]
    assert pt.parse_page_groups("last", 10) == [[9]]


def test_parse_groups_descending_and_spaces():
    assert pt.parse_page_groups(" 5 - 3 ; 1 ", 6) == [[4, 3, 2], [0]]


@pytest.mark.parametrize("spec", ["", "0", "11", "1-11", "abc", "1-x", ",,"])
def test_parse_groups_errors(spec):
    with pytest.raises(pt.PdfToolsError):
        pt.parse_page_groups(spec, 10)


def test_parse_page_list_keeps_order_drops_repeats():
    assert pt.parse_page_list("3,1-2,3", 5) == [2, 0, 1]


def test_pages_label():
    assert pt.pages_label([0, 1, 2, 4]) == "1-3_5"
    assert pt.pages_label([6]) == "7"


def test_split_groups_modes():
    assert pt.split_groups(5, "each") == [[0], [1], [2], [3], [4]]
    assert pt.split_groups(5, "every", 2) == [[0, 1], [2, 3], [4]]
    assert pt.split_groups(5, "ranges", ranges="1-2,5") == [[0, 1], [4]]
    assert pt.split_groups(5, "extract", ranges="5,1") == [[4, 0]]
    with pytest.raises(pt.PdfToolsError):
        pt.split_groups(5, "every", 0)


# ── never overwrite ──────────────────────────────────────────────────────────

def test_unique_path(tmp_path):
    p = tmp_path / "x.pdf"
    assert pt.unique_path(str(p)) == str(p)
    p.write_bytes(b"1")
    assert pt.unique_path(str(p)) == str(tmp_path / "x (2).pdf")
    (tmp_path / "x (2).pdf").write_bytes(b"2")
    assert pt.unique_path(str(tmp_path / "x (2).pdf")) == str(tmp_path / "x (3).pdf")


# ── merge ────────────────────────────────────────────────────────────────────

def test_merge_order_and_default_name(a5, b3, tmp_path):
    r = pt.merge([b3, a5])
    assert r["out_path"] == str(tmp_path / "b_merged.pdf")
    assert texts(r["out_path"]) == ["B1", "B2", "B3", "A1", "A2", "A3", "A4", "A5"]
    assert r["page_count"] == 8


def test_merge_bookmarks(a5, b3, tmp_path):
    r = pt.merge([a5, b3], str(tmp_path / "m.pdf"), bookmarks=True)
    doc = fitz.open(r["out_path"])
    toc = doc.get_toc()
    doc.close()
    assert [t[1:] for t in toc if t[0] == 1] == [["a", 1], ["b", 6]]
    r2 = pt.merge([a5, b3], str(tmp_path / "n.pdf"), bookmarks=False)
    doc = fitz.open(r2["out_path"])
    assert doc.get_toc() == []
    doc.close()


def test_merge_never_overwrites(a5, b3, tmp_path):
    out = tmp_path / "m.pdf"
    out.write_bytes(b"keep me")
    r = pt.merge([a5, b3], str(out))
    assert r["out_path"] == str(tmp_path / "m (2).pdf")
    assert out.read_bytes() == b"keep me"


def test_merge_with_page_spec(a5, b3, tmp_path):
    r = pt.merge([{"path": a5, "pages": "5,1"}, b3], str(tmp_path / "m.pdf"))
    assert texts(r["out_path"]) == ["A5", "A1", "B1", "B2", "B3"]


def test_merge_includes_images(a5, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    img = tmp_path / "scan.png"
    Image.new("RGB", (300, 150), (200, 10, 10)).save(img, dpi=(150, 150))
    r = pt.merge([str(img), a5], str(tmp_path / "m.pdf"))
    assert r["page_count"] == 6
    doc = fitz.open(r["out_path"])
    first = doc[0].rect
    doc.close()
    assert round(first.width) == 144 and round(first.height) == 72   # 300px @150dpi = 2in


def test_merge_encrypted_gives_clear_error(a5, tmp_path):
    enc = tmp_path / "secret.pdf"
    doc = fitz.open(a5)
    doc.save(str(enc), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    with pytest.raises(pt.PdfToolsError, match="password-protected"):
        pt.merge([a5, str(enc)], str(tmp_path / "m.pdf"))
    assert not (tmp_path / "m.pdf").exists()


def test_merge_rejects_other_files(a5, tmp_path):
    t = tmp_path / "notes.txt"
    t.write_text("x")
    with pytest.raises(pt.PdfToolsError):
        pt.merge([a5, str(t)])


# ── split ────────────────────────────────────────────────────────────────────

def test_split_each_page(a5, tmp_path):
    r = pt.split(a5, "each")
    assert r["out_dir"] == str(tmp_path / "a_split")
    assert [os.path.basename(f) for f in r["files"]] == [f"a_p{i}.pdf" for i in range(1, 6)]
    assert texts(r["files"][3]) == ["A4"]


def test_split_every_n(a5):
    r = pt.split(a5, "every", every=2)
    assert [os.path.basename(f) for f in r["files"]] == ["a_p1-2.pdf", "a_p3-4.pdf", "a_p5.pdf"]
    assert texts(r["files"][1]) == ["A3", "A4"]


def test_split_ranges(a5):
    r = pt.split(a5, "ranges", ranges="1-3,4-5")
    assert [texts(f) for f in r["files"]] == [["A1", "A2", "A3"], ["A4", "A5"]]


def test_split_never_overwrites(a5, tmp_path):
    first = pt.split(a5, "each")
    before = digest(first["files"][0])
    second = pt.split(a5, "each")
    assert os.path.basename(second["files"][0]) == "a_p1 (2).pdf"
    assert digest(first["files"][0]) == before


def test_split_reports_progress(a5):
    seen = []
    pt.split(a5, "each", progress=lambda d, t, m: seen.append((d, t)))
    assert seen[-1] == (5, 5)


# ── extract ──────────────────────────────────────────────────────────────────

def test_extract_order_and_name(a5, tmp_path):
    r = pt.extract(a5, "4,1-2")
    assert os.path.basename(r["out_path"]) == "a_p4_1-2.pdf"
    assert texts(r["out_path"]) == ["A4", "A1", "A2"]
    assert r["pages"] == [4, 1, 2]


def test_extract_never_overwrites(a5, tmp_path):
    out = tmp_path / "e.pdf"
    out.write_bytes(b"x")
    r = pt.extract(a5, "1", str(out))
    assert r["out_path"] == str(tmp_path / "e (2).pdf")
    assert out.read_bytes() == b"x"


def test_extract_bad_range(a5):
    with pytest.raises(pt.PdfToolsError, match="out of range"):
        pt.extract(a5, "9")


# ── build / arrange ──────────────────────────────────────────────────────────

def slots(path, order, rot=None):
    rot = rot or {}
    return [{"path": path, "page": p, "rotate": rot.get(i, 0)} for i, p in enumerate(order)]


def test_build_reorder_rotate_delete_duplicate(a5, tmp_path):
    # reorder (3,1), delete page 2/4/5, duplicate page 1, rotate the 2nd slot
    pages = slots(a5, [2, 0, 0], rot={1: 90})
    r = pt.build_pdf(pages, str(tmp_path / "a_arranged.pdf"))
    assert texts(r["out_path"]) == ["A3", "A1", "A1"]
    assert rotations(r["out_path"]) == [0, 90, 0]
    assert r["replaced"] is False


def test_build_insert_from_other_pdf(a5, b3, tmp_path):
    pages = slots(a5, [0]) + slots(b3, [2]) + slots(a5, [1])
    r = pt.build_pdf(pages, str(tmp_path / "x.pdf"))
    assert texts(r["out_path"]) == ["A1", "B3", "A2"]


def test_build_new_file_never_overwrites(a5, tmp_path):
    out = tmp_path / "a_arranged.pdf"
    out.write_bytes(b"old")
    r = pt.build_pdf(slots(a5, [1]), str(out))
    assert r["out_path"] == str(tmp_path / "a_arranged (2).pdf")
    assert out.read_bytes() == b"old"


def test_build_replace_keeps_backup(a5, tmp_path):
    original = digest(a5)
    r = pt.build_pdf(slots(a5, [4, 3]), a5, replace=True)
    assert r["replaced"] is True and r["out_path"] == os.path.abspath(a5)
    assert texts(a5) == ["A5", "A4"]
    assert os.path.dirname(r["backup"]) == str(tmp_path / "_to_review")
    assert digest(r["backup"]) == original
    assert texts(r["backup"]) == ["A1", "A2", "A3", "A4", "A5"]


def test_build_rotation_is_relative(tmp_path):
    p = make_pdf(tmp_path / "r.pdf", 1)
    doc = fitz.open(p)
    doc[0].set_rotation(90)
    doc.saveIncr()
    doc.close()
    r = pt.build_pdf([{"path": p, "page": 0, "rotate": 270}], str(tmp_path / "o.pdf"))
    assert rotations(r["out_path"]) == [0]


def test_build_errors(a5, tmp_path):
    with pytest.raises(pt.PdfToolsError):
        pt.build_pdf([], str(tmp_path / "o.pdf"))
    with pytest.raises(pt.PdfToolsError, match="does not exist"):
        pt.build_pdf(slots(a5, [7]), str(tmp_path / "o.pdf"))
    with pytest.raises(pt.PdfToolsError):
        pt.build_pdf(slots(a5, [0]), str(tmp_path / "missing.pdf"), replace=True)


# ── thumbnails ───────────────────────────────────────────────────────────────

def test_thumbnails_and_cache(a5):
    t = pt.thumbnails(a5, [0, 4, 99], 80)
    assert set(t) == {0, 4}
    assert t[0].startswith("data:image/")
    again = pt.thumbnails(a5, [0], 80)
    assert again[0] is t[0]          # served from cache


# ── bridge mixin ─────────────────────────────────────────────────────────────

class _Bridge(PdfToolsBridgeMixin):
    def __init__(self):
        self.events = []

    def _emit(self, event, payload=None):
        self.events.append((event, payload))


def test_bridge_ok_and_error_shapes(a5, b3, tmp_path):
    b = _Bridge()
    r = b.pdf_merge([a5, b3], "", True, "job1")
    assert r["ok"] and os.path.isfile(r["out_path"])
    assert any(e == "pdf_tools_progress" and p["job"] == "job1" for e, p in b.events)
    bad = b.pdf_extract(a5, "1-99")
    assert bad["ok"] is False and "out of range" in bad["error"]
    info = b.pdf_info(a5)
    assert info["ok"] and info["page_count"] == 5
    pr = b.pdf_parse_ranges("1-2,5", 5)
    assert pr["ok"] and pr["groups"] == [[1, 2], [5]]
    th = b.pdf_thumbnails(a5, [1], 60)
    assert th["ok"] and "1" in th["thumbs"]
    infos = b.pdf_infos([a5, str(tmp_path / "nope.pdf")])
    assert [i["ok"] for i in infos["items"]] == [True, False]
