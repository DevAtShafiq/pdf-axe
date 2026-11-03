"""docx_edit: quick text edits keep the document's formatting."""
import os
import random

import pytest

docx = pytest.importorskip("docx")
import docx_edit as de  # noqa: E402
import doc_preview  # noqa: E402


def _paras(d):
    return list(d.element.body.iter(de._W + "p"))


def _doc(tmp_path):
    d = docx.Document()
    p = d.add_paragraph("Dear ")
    r = p.add_run("Kim")
    r.bold = True
    p.add_run(", welcome.")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Salary"
    t.cell(0, 1).text = "3,000"
    d.add_paragraph("")
    path = str(tmp_path / "a.docx")
    d.save(path)
    return path


def test_replace_keeps_untouched_runs_and_puts_new_text_in_the_replaced_run():
    d = docx.Document()
    p = d.add_paragraph("Dear ")
    p.add_run("Kim").bold = True
    p.add_run(", welcome.")
    el = _paras(d)[0]
    assert de.replace_text(el, "Dear Kim, welcome.", "Dear 박지성, welcome!")
    assert [(r.text, bool(r.bold)) for r in d.paragraphs[0].runs] == [("Dear ", False), ("박지성", True), (", welcome!", False)]


def test_replace_refuses_when_the_paragraph_changed():
    d = docx.Document()
    d.add_paragraph("Hello")
    assert not de.replace_text(_paras(d)[0], "Goodbye", "x")


def test_random_edits_always_give_the_new_text():
    rnd = random.Random(7)
    for _ in range(500):
        d = docx.Document()
        p = d.add_paragraph()
        for _ in range(rnd.randint(0, 4)):
            p.add_run("".join(rnd.choice("ab\t") for _ in range(rnd.randint(0, 4)))).bold = rnd.random() < .5
        el = _paras(d)[0]
        old = de.para_text(el)
        new = "".join(rnd.choice("abc ") for _ in range(rnd.randint(0, 8)))
        assert de.replace_text(el, old, new)
        assert de._norm(de.para_text(el)) == de._norm(new)


def test_save_writes_a_new_file_and_leaves_the_original(tmp_path):
    path = _doc(tmp_path)
    html = doc_preview.docx_to_html(path, edit=True)
    assert 'data-pi="0"' in html["html"] and html["sig"]
    ps = _paras(docx.Document(path))
    cell = next(i for i, p in enumerate(ps) if de.para_text(p) == "3,000")
    empty = next(i for i, p in enumerate(ps) if de.para_text(p) == "")
    out = str(tmp_path / "a_edited.docx")
    r = de.save(path, [{"pi": cell, "old": "3,000", "new": "3,500"},
                       {"pi": empty, "old": " ", "new": "Added"}], out, html["sig"])
    assert r["changed"] == 2
    d = docx.Document(out)
    assert d.tables[0].cell(0, 1).text == "3,500" and "Added" in [p.text for p in d.paragraphs]
    assert docx.Document(path).tables[0].cell(0, 1).text == "3,000"
    with pytest.raises(de.DocxEditError):                  # never over an existing file
        de.save(path, [], out)
    with pytest.raises(de.DocxEditError):                  # never over the original
        de.save(path, [], path)


def test_save_refuses_a_file_changed_since_it_was_opened(tmp_path):
    path = _doc(tmp_path)
    with pytest.raises(de.DocxEditError) as e:
        de.save(path, [], str(tmp_path / "b.docx"), sig="1:1")
    assert e.value.code == "changed"
    assert not os.path.exists(tmp_path / "b.docx")
