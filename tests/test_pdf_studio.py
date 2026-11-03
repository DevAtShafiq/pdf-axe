"""Tests for pdf_studio.py — the working-copy engine behind PDF Studio."""
from __future__ import annotations

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fitz = pytest.importorskip("pymupdf")

import pdf_studio as ps  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    """Keep sessions inside the test's temp folder."""
    root = tmp_path / "sysTemp"
    root.mkdir()
    monkeypatch.setattr(ps.tempfile, "gettempdir", lambda: str(root))
    yield


def _pdf(path, pages=2, text="Hello Studio", image=False):
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"{text} page {i + 1}", fontsize=14)
        if image:
            from PIL import Image
            import random
            rnd = random.Random(i)
            im = Image.new("RGB", (600, 600))
            im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                        for _ in range(600 * 600)])
            buf = io.BytesIO()
            im.save(buf, "PNG")
            pg.insert_image(fitz.Rect(72, 150, 500, 578), stream=buf.getvalue())
    doc.save(str(path))
    doc.close()
    return str(path)


def _text(path, password=""):
    doc = fitz.open(path)
    if password:
        assert doc.authenticate(password)
    try:
        return "".join(p.get_text() for p in doc)
    finally:
        doc.close()


def _edit_step(session, work, extra="Added"):
    """What a Studio tool does: read the current step, write a NEW step file."""
    nxt = ps.next_step(session)["path"]
    doc = fitz.open(work)
    doc[0].insert_text((72, 200), extra, fontsize=12)
    doc.save(nxt)
    doc.close()
    return nxt


def test_begin_copies_into_session(tmp_path):
    src = _pdf(tmp_path / "a.pdf", pages=3)
    before = open(src, "rb").read()
    r = ps.begin(src)
    assert r["page_count"] == 3 and r["has_text"] and not r["protected"]
    assert r["name"] == "a.pdf" and r["source"] == os.path.abspath(src)
    assert os.path.basename(r["work"]) == "step_000.pdf"
    assert os.path.dirname(r["work"]) == r["session"]
    assert ps._inside_root(r["work"])
    assert open(src, "rb").read() == before            # original untouched


def test_begin_rejects_missing_and_non_pdf(tmp_path):
    with pytest.raises(ps.StudioError) as e:
        ps.begin(str(tmp_path / "nope.pdf"))
    assert e.value.code == "not_found"
    txt = tmp_path / "x.txt"
    txt.write_text("hi")
    with pytest.raises(ps.StudioError) as e:
        ps.begin(str(txt))
    assert e.value.code == "not_pdf"


def test_begin_encrypted(tmp_path):
    src = _pdf(tmp_path / "lock.pdf")
    doc = fitz.open(src)
    enc = str(tmp_path / "locked.pdf")
    doc.save(enc, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    with pytest.raises(ps.StudioError) as e:
        ps.begin(enc)
    assert e.value.code == "need_password"
    with pytest.raises(ps.StudioError) as e:
        ps.begin(enc, "wrong")
    assert e.value.code == "wrong_password"
    r = ps.begin(enc, "pw")
    assert r["protected"]
    assert not fitz.open(r["work"]).needs_pass        # working copy is unlocked
    assert "Hello Studio" in _text(r["work"])


def test_next_step_numbers_and_never_reuses(tmp_path):
    r = ps.begin(_pdf(tmp_path / "a.pdf"))
    s1 = ps.next_step(r["session"])
    assert s1["index"] == 1 and s1["path"].endswith("step_001.pdf")
    assert not os.path.exists(s1["path"])
    open(s1["path"], "wb").write(b"%PDF-1.4")
    s2 = ps.next_step(r["session"])
    assert s2["index"] == 2


def test_session_paths_are_confined(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ps.StudioError) as e:
        ps.next_step(str(outside))
    assert e.value.code == "no_session"
    f = _pdf(tmp_path / "b.pdf")
    with pytest.raises(ps.StudioError) as e:
        ps.step_info(f)
    assert e.value.code == "no_work"
    with pytest.raises(ps.StudioError):
        ps.finish(f, f)


def test_step_info(tmp_path):
    r = ps.begin(_pdf(tmp_path / "a.pdf", pages=4))
    info = ps.step_info(r["work"])
    assert info["page_count"] == 4 and info["size"] > 0 and info["has_text"]


def test_finish_new_file_keeps_original_and_steps(tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    before = open(src, "rb").read()
    r = ps.begin(src)
    w1 = _edit_step(r["session"], r["work"], "First")
    w2 = _edit_step(r["session"], w1, "Second")
    out = ps.finish(w2, src)
    assert out["out_path"] == str(tmp_path / "doc_edited.pdf")
    assert not out["replaced"] and out["backup"] == ""
    t = _text(out["out_path"])
    assert "First" in t and "Second" in t and "Hello Studio" in t   # still real text
    assert open(src, "rb").read() == before
    # every step remains on disk (Undo = go back a step)
    assert os.path.isfile(r["work"]) and os.path.isfile(w1) and os.path.isfile(w2)
    # second save never overwrites the first
    out2 = ps.finish(w2, src)
    assert out2["out_path"] == str(tmp_path / "doc_edited (2).pdf")


def test_finish_custom_path_and_bad_folder(tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    r = ps.begin(src)
    out = ps.finish(r["work"], src, out_path=str(tmp_path / "custom"))
    assert out["out_path"] == str(tmp_path / "custom.pdf")
    with pytest.raises(ps.StudioError) as e:
        ps.finish(r["work"], src, out_path=str(tmp_path / "missing" / "x.pdf"))
    assert e.value.code == "bad_folder"


def test_finish_signed_into_new_signature_folder(tmp_path):
    """Signed PDFs: '<folder of the PDF>\\signature\\<stem>_signature.pdf',
    the folder created on demand (one level only), never overwritten."""
    src = _pdf(tmp_path / "form.pdf")
    r = ps.begin(src)
    target = tmp_path / "signature" / "form_signature.pdf"
    out = ps.finish(r["work"], src, out_path=str(target), make_dir=True)
    assert out["out_path"] == str(target) and os.path.isfile(target)
    assert "Hello Studio" in _text(out["out_path"])
    out2 = ps.finish(r["work"], src, out_path=str(target), make_dir=True)
    assert out2["out_path"] == str(tmp_path / "signature" / "form_signature (2).pdf")
    # only one missing level is created
    with pytest.raises(ps.StudioError) as e:
        ps.finish(r["work"], src, out_path=str(tmp_path / "a" / "b" / "x.pdf"), make_dir=True)
    assert e.value.code == "bad_folder" and not (tmp_path / "a").exists()
    # without make_dir a missing folder is still an error
    with pytest.raises(ps.StudioError):
        ps.finish(r["work"], src, out_path=str(tmp_path / "other" / "x.pdf"))
    assert not (tmp_path / "other").exists()


def test_finish_over_original_backs_up(tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    before = open(src, "rb").read()
    r = ps.begin(src)
    w1 = _edit_step(r["session"], r["work"], "Changed")
    released = []
    out = ps.finish(w1, src, replace=True, release_handles=released.append)
    assert out["replaced"] and out["out_path"] == os.path.abspath(src)
    assert released == [os.path.abspath(src)]
    assert "Changed" in _text(src)
    assert os.path.dirname(out["backup"]) == str(tmp_path / "_to_review")
    assert open(out["backup"], "rb").read() == before
    leftovers = [f for f in os.listdir(tmp_path) if ".saving" in f]
    assert leftovers == []


def test_finish_over_missing_original(tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    r = ps.begin(src)
    os.rename(src, str(tmp_path / "moved.pdf"))
    with pytest.raises(ps.StudioError) as e:
        ps.finish(r["work"], src, replace=True)
    assert e.value.code == "not_found"


def test_finish_with_password(tmp_path):
    src = _pdf(tmp_path / "doc.pdf")
    r = ps.begin(src)
    out = ps.finish(r["work"], src, password="secret")
    assert out["protected"]
    doc = fitz.open(out["out_path"])
    assert doc.needs_pass
    doc.close()
    assert "Hello Studio" in _text(out["out_path"], "secret")


def test_finish_with_target_size(tmp_path):
    src = _pdf(tmp_path / "big.pdf", pages=2, image=True)
    size = os.path.getsize(src)
    r = ps.begin(src)
    target_kb = max(1, size // 1024 // 3)
    out = ps.finish(r["work"], src, target_kb=target_kb)
    assert out["compress"] is not None
    assert out["size"] < size
    assert "Hello Studio" in _text(out["out_path"])      # text survives compression
