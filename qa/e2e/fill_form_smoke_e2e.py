"""E2E smoke: Edit → Fill form on a fillable PDF (text field incl. Korean),
Save, check the value with PyMuPDF and that the field is still fillable.

    set QA_CDP_PORT=9350
    python qa/e2e/fill_form_smoke_e2e.py <scratch dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    out = os.path.join(d, "out"); os.makedirs(out, exist_ok=True)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.evaluate("p => Studio.open(p)", f["multi"])
        wait(page, "Studio._state() && Studio._state().session", timeout=20)
        time.sleep(1.5)
        st = page.evaluate("Studio._state()")
        R.check("PDF with fields opens on Fill form", st.get("mode") == "form" or st.get("itemId") == "form", st)
        if st.get("itemId") != "form":
            page.click(".st-tool[data-id=form]"); time.sleep(1.5)
        inp = page.query_selector("#st-host .pf-root input[type=text], #st-host .pf-root textarea")
        R.check("field shown as a real input box", inp is not None)
        if inp:
            inp.click(); page.keyboard.press("Control+a"); page.keyboard.type("홍길동 Hong Gildong"); page.keyboard.press("Tab")
            time.sleep(0.4)
        page.click("#st-save")
        wait(page, "document.querySelector('#st-go')")
        page.evaluate("() => { const r = document.querySelector('input[name=st-out][value=new]'); if (r) { r.checked = true; r.dispatchEvent(new Event('change')); } }")
        page.fill("#st-name", "filled"); page.fill("#st-dir", out)
        page.click("#st-go")
        p = os.path.join(out, "filled.pdf")
        end = time.time() + 30
        while time.time() < end and not os.path.isfile(p):
            time.sleep(0.2)
        time.sleep(0.6)
        R.check("saved", os.path.isfile(p))
        if os.path.isfile(p):
            doc = fitz.open(p)
            ws = list(doc[0].widgets())
            R.check("field still a fillable field", len(ws) == 1, len(ws))
            if ws:
                R.check("value saved (Korean + English)", ws[0].field_value == "홍길동 Hong Gildong", ws[0].field_value)
            R.check("page text still selectable", "quick brown fox" in doc[0].get_text())
            doc.close()
        page.click("#st-x"); time.sleep(0.4)
        if page.evaluate("!!document.querySelector('.modal-overlay.dlg-top')"):
            page.evaluate("() => { const bs = document.querySelectorAll('.modal-overlay.dlg-top button'); bs[bs.length - 1].click(); }")
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "fill_form")))
