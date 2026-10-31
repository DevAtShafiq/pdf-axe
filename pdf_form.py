"""
pdf_form.py — fill in a PDF's own form fields (AcroForm), like Acrobat's "Fill form".

    fields(path) → {"xfa": "none"|"hybrid"|"dynamic", "pages": [{w, h}], "fields": [...]}
    fill(path, values, out_path, lock=False) → {"out_path", "filled"}

A field entry (one per widget, in page then tab order):

    {"id": "123",                # widget xref (stable for this file)
     "name": "Passport No",      # full field name (radio buttons share it)
     "type": "text" | "checkbox" | "radio" | "combo" | "list" | "signature" | "button",
     "page": 0, "rect": [x0, y0, x1, y1],   # page units as displayed (rotation applied)
     "value": "..." | true/false,           # radio: true on the chosen button
     "options": [...],                      # combo / list
     "required": bool, "readonly": bool, "multiline": bool, "password": bool,
     "maxlen": 0, "comb": bool, "font_size": 0, "format": "" | "date:dd/mm/yyyy" | "number",
     "label": "tooltip / alternate name"}

*values* maps id → new value (text: str, checkbox/radio: bool, combo/list: str).
Only the given fields change. The result stays a fillable form unless *lock*
(the fields are then flattened into the page; signatures and comments stay
separate). Korean / CJK text gets a CJK font in the field appearance.
Hybrid XFA forms (AcroForm + XFA) have their XFA part removed when filled, so
every viewer shows the AcroForm values; pure (dynamic) XFA forms cannot be
filled here and say so.
"""
from __future__ import annotations

import os
import re


class FormError(Exception):
    def __init__(self, msg: str, code: str = "error"):
        super().__init__(msg)
        self.code = code


def _fitz():
    import fitz  # PyMuPDF
    return fitz


_TYPES = {}


def _type_name(fitz, t: int) -> str:
    if not _TYPES:
        _TYPES.update({
            fitz.PDF_WIDGET_TYPE_TEXT: "text",
            fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
            fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
            fitz.PDF_WIDGET_TYPE_COMBOBOX: "combo",
            fitz.PDF_WIDGET_TYPE_LISTBOX: "list",
            fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature",
            fitz.PDF_WIDGET_TYPE_BUTTON: "button",
        })
    return _TYPES.get(t, "button")


def _open(path: str):
    fitz = _fitz()
    if not path or not os.path.isfile(path):
        raise FormError(f"File not found: {os.path.basename(str(path or ''))}", "not_found")
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise FormError(f"Could not open the PDF: {exc}", "unreadable")
    if doc.needs_pass:
        doc.close()
        raise FormError("This PDF is password-protected", "need_password")
    return doc


def _xfa_kind(doc, has_widgets: bool) -> str:
    try:
        cat = doc.pdf_catalog()
        t, _ = doc.xref_get_key(cat, "AcroForm/XFA")
        if t in ("null", None, ""):
            return "none"
        nr_t, nr_v = doc.xref_get_key(cat, "NeedsRendering")
        if nr_v == "true" or not has_widgets:
            return "dynamic"
        return "hybrid"
    except Exception:
        return "none"


def _js(doc, xref: int, key: str) -> str:
    try:
        t, v = doc.xref_get_key(xref, key)
        if t == "xref":
            t, v = doc.xref_get_key(int(v.split()[0]), "JS")
        return v or ""
    except Exception:
        return ""


def _format_hint(doc, xref: int) -> str:
    js = _js(doc, xref, "AA/F/JS") or _js(doc, xref, "AA/F")
    m = re.search(r'AFDate_(?:Format|FormatEx)\s*\(\s*["\']([^"\']+)["\']', js)
    if m:
        return "date:" + m.group(1)
    if re.search(r"AFNumber_Format|AFPercent_Format", js):
        return "number"
    return ""


def _display_rect(page, r) -> list:
    rr = (r * page.rotation_matrix) if page.rotation else r
    rr = _fitz().Rect(rr).normalize()
    return [round(rr.x0, 2), round(rr.y0, 2), round(rr.x1, 2), round(rr.y1, 2)]


def fields(path: str) -> dict:
    doc = _open(path)
    try:
        out, pages, n_widgets = [], [], 0
        fitz = _fitz()
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            pages.append({"w": round(page.rect.width, 2), "h": round(page.rect.height, 2)})
            for w in page.widgets() or []:
                n_widgets += 1
                t = _type_name(fitz, w.field_type)
                flags = int(w.field_flags or 0)
                f = {"id": str(w.xref), "name": w.field_name or "", "type": t, "page": pno,
                     "rect": _display_rect(page, w.rect),
                     "required": bool(flags & 2), "readonly": bool(flags & 1),
                     "label": (w.field_label or "").strip(),
                     "font_size": float(w.text_fontsize or 0)}
                if t in ("checkbox", "radio"):
                    on = w.on_state()
                    v = w.field_value
                    f["value"] = bool(v not in (None, False, "", "Off")) and (v is True or v == on or str(v) == str(on))
                    f["on"] = str(on) if on not in (None, True) else "Yes"
                elif t in ("combo", "list"):
                    opts = []
                    for o in w.choice_values or []:
                        opts.append(o[1] if isinstance(o, (list, tuple)) and len(o) > 1 else (o[0] if isinstance(o, (list, tuple)) else o))
                    f["options"] = [str(o) for o in opts]
                    v = w.field_value
                    f["value"] = "" if v in (None, "Off") else (v[0] if isinstance(v, (list, tuple)) and v else str(v))
                    f["editable"] = bool(flags & (1 << 18))
                else:
                    v = w.field_value
                    f["value"] = "" if v in (None, "Off") or t != "text" else str(v)
                    f["multiline"] = bool(flags & (1 << 12))
                    f["password"] = bool(flags & (1 << 13))
                    f["comb"] = bool(flags & (1 << 24))
                    f["maxlen"] = int(w.text_maxlen or 0)
                    f["format"] = _format_hint(doc, w.xref) if t == "text" else ""
                out.append(f)
        return {"xfa": _xfa_kind(doc, n_widgets > 0), "pages": pages, "fields": out}
    finally:
        doc.close()


def page_png(path: str, page: int = 0, dpi: int = 150) -> dict:
    """The page as a PNG data URL without its form fields (the Fill form tool
    draws live inputs there); comments, signatures and stamps stay."""
    import base64
    doc = _open(path)
    try:
        if not 0 <= int(page) < doc.page_count:
            raise FormError("No such page", "bad_args")
        pg = doc.load_page(int(page))
        for w in list(pg.widgets() or []):
            pg.delete_widget(w)          # in memory only; the file is not written
        pix = pg.get_pixmap(dpi=max(50, min(400, int(dpi or 150))), annots=True)
        return {"data_url": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii"),
                "width": pix.width, "height": pix.height}
    finally:
        doc.close()


def _set(fitz, w, t: str, v) -> bool:
    """Write *v* into widget *w*; True when something changed."""
    if t == "text":
        v = "" if v is None else str(v)
        if w.text_maxlen and len(v) > w.text_maxlen:
            v = v[: w.text_maxlen]
        if (w.field_value or "") == v:
            return False
        w.field_value = v
    elif t == "checkbox":
        on = bool(v)
        cur = w.field_value not in (None, False, "", "Off")
        if cur == on:
            return False
        w.field_value = w.on_state() if on else "Off"
    elif t == "radio":
        on = bool(v)
        cur = w.field_value not in (None, False, "", "Off")
        if cur == on:
            return False
        w.field_value = True if on else False
    elif t in ("combo", "list"):
        v = "" if v is None else str(v)
        cur = w.field_value
        cur = cur[0] if isinstance(cur, (list, tuple)) and cur else (cur or "")
        if str(cur) == v:
            return False
        w.field_value = v
    else:
        return False
    w.update()
    return True


def fill(path: str, values: dict, out_path: str, lock: bool = False) -> dict:
    if not out_path:
        raise FormError("No output file given", "bad_args")
    if os.path.abspath(out_path) == os.path.abspath(path):
        raise FormError("The filled form must be saved as a new file", "bad_args")
    values = {str(k): v for k, v in (values or {}).items()}
    fitz = _fitz()
    doc = _open(path)
    try:
        n_widgets = sum(1 for p in doc for _ in (p.widgets() or []))
        if _xfa_kind(doc, n_widgets > 0) == "dynamic":
            raise FormError("This is an Adobe XFA form. It can only be filled in Adobe Acrobat or Reader.", "xfa")
        filled = 0
        # radios first switched off, then the chosen ones on (a group never ends with two)
        order = []
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            for w in page.widgets() or []:
                if str(w.xref) in values:
                    order.append((pno, w.xref, _type_name(fitz, w.field_type)))
        order.sort(key=lambda o: (o[2] == "radio" and bool(values[str(o[1])])))
        for pno, xref, t in order:
            page = doc.load_page(pno)
            w = next((x for x in page.widgets() or [] if x.xref == xref), None)
            if w is None or (int(w.field_flags or 0) & 1):
                continue
            if _set(fitz, w, t, values[str(xref)]):
                filled += 1
        if filled and _xfa_kind(doc, True) == "hybrid":
            # the XFA copy of the data would win in Acrobat: drop it so the
            # AcroForm values (what was typed here) show everywhere
            doc.xref_set_key(doc.pdf_catalog(), "AcroForm/XFA", "null")
        if lock:
            doc.bake(annots=False, widgets=True)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        doc.save(out_path, garbage=1, deflate=True)
        return {"out_path": out_path, "filled": filled, "locked": bool(lock)}
    finally:
        doc.close()
