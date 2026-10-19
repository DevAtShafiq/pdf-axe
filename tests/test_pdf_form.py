"""pdf_form: read and fill a PDF's own form fields."""
import os

import pytest

fitz = pytest.importorskip("fitz")
import pdf_form  # noqa: E402


def _form(path):
    d = fitz.open()
    pg = d.new_page(width=595, height=842)

    def add(kind, name, rect, **kw):
        w = fitz.Widget()
        w.field_type, w.field_name, w.rect = kind, name, fitz.Rect(*rect)
        for k, v in kw.items():
            setattr(w, k, v)
        pg.add_widget(w)

    add(fitz.PDF_WIDGET_TYPE_TEXT, "Name", (100, 100, 300, 120), field_flags=2)
    add(fitz.PDF_WIDGET_TYPE_TEXT, "Date", (100, 130, 300, 150))
    add(fitz.PDF_WIDGET_TYPE_TEXT, "Fixed", (100, 330, 300, 350), field_flags=1, field_value="keep")
    add(fitz.PDF_WIDGET_TYPE_CHECKBOX, "Agree", (100, 160, 116, 176))
    for i in range(2):
        add(fitz.PDF_WIDGET_TYPE_RADIOBUTTON, "Pick", (100 + 40 * i, 190, 116 + 40 * i, 206), field_value=False)
    add(fitz.PDF_WIDGET_TYPE_COMBOBOX, "City", (100, 220, 300, 240), choice_values=["Seoul", "Busan"])
    d.save(path)
    d.close()
    d = fitz.open(path)
    x = next(w.xref for w in d[0].widgets() if w.field_name == "Date")
    d.xref_set_key(x, "AA", '<</F <</S /JavaScript /JS (AFDate_FormatEx\\("dd/mm/yyyy"\\);)>>>>')
    d.saveIncr()
    d.close()
    return path


def _by_name(info):
    out = {}
    for f in info["fields"]:
        out.setdefault(f["name"], []).append(f)
    return out


def test_fields_reads_types_flags_and_formats(tmp_path):
    p = _form(str(tmp_path / "f.pdf"))
    info = pdf_form.fields(p)
    assert info["xfa"] == "none" and len(info["pages"]) == 1
    f = _by_name(info)
    assert f["Name"][0]["type"] == "text" and f["Name"][0]["required"]
    assert f["Date"][0]["format"] == "date:dd/mm/yyyy"
    assert f["Fixed"][0]["readonly"] and f["Fixed"][0]["value"] == "keep"
    assert f["Agree"][0]["type"] == "checkbox" and f["Agree"][0]["value"] is False
    assert [r["type"] for r in f["Pick"]] == ["radio", "radio"]
    assert f["City"][0]["options"] == ["Seoul", "Busan"]


def test_fill_keeps_form_fillable_and_handles_korean(tmp_path):
    p = _form(str(tmp_path / "f.pdf"))
    f = _by_name(pdf_form.fields(p))
    out = str(tmp_path / "out.pdf")
    vals = {f["Name"][0]["id"]: "홍길동", f["Agree"][0]["id"]: True, f["Pick"][1]["id"]: True,
            f["City"][0]["id"]: "Busan", f["Fixed"][0]["id"]: "changed"}
    r = pdf_form.fill(p, vals, out)
    assert r["filled"] == 4 and not r["locked"]
    g = _by_name(pdf_form.fields(out))
    assert g["Name"][0]["value"] == "홍길동"
    assert g["Agree"][0]["value"] is True
    assert [x["value"] for x in g["Pick"]] == [False, True]
    assert g["City"][0]["value"] == "Busan"
    assert g["Fixed"][0]["value"] == "keep"          # read-only fields are never changed
    assert os.path.getsize(p) > 0                     # source untouched, new file written


def test_lock_flattens_values_into_the_page(tmp_path):
    p = _form(str(tmp_path / "f.pdf"))
    f = _by_name(pdf_form.fields(p))
    out = str(tmp_path / "locked.pdf")
    pdf_form.fill(p, {f["Name"][0]["id"]: "Alice"}, out, lock=True)
    d = fitz.open(out)
    assert not list(d[0].widgets() or []) and "Alice" in d[0].get_text()
    d.close()


def test_fill_refuses_same_file_and_page_png_hides_fields(tmp_path):
    p = _form(str(tmp_path / "f.pdf"))
    with pytest.raises(pdf_form.FormError):
        pdf_form.fill(p, {}, p)
    png = pdf_form.page_png(p, 0, 50)
    assert png["data_url"].startswith("data:image/png;base64,")
    d = fitz.open(p)
    assert len(list(d[0].widgets())) == 7            # rendering never writes the file
    d.close()


def test_dynamic_xfa_is_refused(tmp_path):
    p = str(tmp_path / "x.pdf")
    d = fitz.open()
    d.new_page()
    cat = d.pdf_catalog()
    d.xref_set_key(cat, "AcroForm", "<</Fields [] /XFA (dummy)>>")
    d.xref_set_key(cat, "NeedsRendering", "true")
    d.save(p)
    d.close()
    assert pdf_form.fields(p)["xfa"] == "dynamic"
    with pytest.raises(pdf_form.FormError) as e:
        pdf_form.fill(p, {}, str(tmp_path / "o.pdf"))
    assert e.value.code == "xfa"
