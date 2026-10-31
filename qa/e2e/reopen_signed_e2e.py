"""E2E: sign + add photo, Save, then open the SAVED PDF again: the signature
and the photo must still be separate objects you can select, move, copy/paste
and delete — from the Fill & sign / Comment tools AND from the Edit tool.

    set QA_CDP_PORT=9350
    python qa/e2e/reopen_signed_e2e.py <scratch dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from annotate_e2e import pt, drag  # noqa: E402
from cdp import Results, app_page, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()

OBJS = """[...document.querySelectorAll('#st-host .an-t-signature, #st-host .an-t-photo')].map(g => {
  const im = g.querySelector('image'); return { id: g.dataset.id, type: g.classList.contains('an-t-photo') ? 'photo' : 'signature',
  page: +g.closest('.an-page').dataset.p, x: +im.getAttribute('x'), y: +im.getAttribute('y'), w: +im.getAttribute('width'), h: +im.getAttribute('height') }; })"""


def objs(page):
    return page.evaluate(OBJS)


def close_all(page):
    for _ in range(5):
        if not page.evaluate("!!document.querySelector('.st-overlay')"):
            return
        if page.evaluate("!!document.querySelector('.modal-overlay.dlg-top')"):
            page.evaluate("() => { const bs = document.querySelectorAll('.modal-overlay.dlg-top button'); bs[bs.length - 1].click(); }")
        else:
            page.click("#st-x")
        time.sleep(0.4)


def save_as(page, folder, name):
    page.click("#st-save")
    wait(page, "document.querySelector('#st-go')")
    page.evaluate("() => { const r = document.querySelector('input[name=st-out][value=new]'); if (r) { r.checked = true; r.dispatchEvent(new Event('change')); } }")
    page.fill("#st-name", name); page.fill("#st-dir", folder)
    page.click("#st-go")
    p = os.path.join(folder, name + ".pdf")
    end = time.time() + 30
    while time.time() < end and not os.path.isfile(p):
        time.sleep(0.2)
    time.sleep(0.6)
    return p


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    out = os.path.join(d, "out"); os.makedirs(out, exist_ok=True)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        close_all(page)
        page.evaluate("p => Studio.open(p, {tool: 'select'})", f["multi"])
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=20)
        time.sleep(0.4)
        # signature
        page.click(".st-tool[data-id=signature]")
        wait(page, "document.querySelector('.an-sig-overlay #an-sig-canvas')"); time.sleep(0.3)
        cb = page.query_selector("#an-sig-canvas").bounding_box()
        page.mouse.move(cb["x"] + 80, cb["y"] + 120); page.mouse.down()
        for i in range(40):
            page.mouse.move(cb["x"] + 80 + i * 8, cb["y"] + 120 - 30 * ((i % 8) / 8))
        page.mouse.up(); time.sleep(0.2)
        page.uncheck("#an-sig-rem"); page.click("#an-sig-ok")
        wait(page, "!document.querySelector('.an-sig-overlay')"); time.sleep(0.4)
        x, y = pt(page, 0, 200, 520); page.mouse.move(x, y); time.sleep(0.2); page.mouse.click(x, y); time.sleep(0.5)
        # photo (native picker stubbed)
        page.evaluate("p => { SFM.annotPickPhoto = async () => ({ok: true, path: p}); }", f["photo"])
        page.click(".st-tool[data-id=photo]"); time.sleep(1.0)
        drag(page, 0, (400, 120), (500, 250)); time.sleep(0.6)
        before = objs(page)
        R.check("signature + photo placed", sorted(o["type"] for o in before) == ["photo", "signature"], before)
        p1 = save_as(page, out, "signed1")
        R.check("saved", os.path.isfile(p1))
        close_all(page)

        # ── reopen the SAVED PDF ───────────────────────────────────────────
        page.evaluate("p => Studio.open(p, {tool: 'select'})", p1)
        wait(page, "document.querySelector('#st-host .an-page .an-bg.on')", timeout=20)
        time.sleep(0.6)
        again = objs(page)
        R.check("reopened: signature and photo are still separate objects", sorted(o["type"] for o in again) == ["photo", "signature"], again)
        sig = next((o for o in again if o["type"] == "signature"), None)
        if sig:
            cx, cy = pt(page, 0, sig["x"] + sig["w"] / 2, sig["y"] + sig["h"] / 2)
            page.mouse.click(cx, cy); time.sleep(0.3)
            page.keyboard.press("Control+c"); time.sleep(0.2); page.keyboard.press("Control+v"); time.sleep(0.4)
            R.check("reopened signature: copy/paste works", len([o for o in objs(page) if o["type"] == "signature"]) == 2)
            page.keyboard.press("Delete"); time.sleep(0.3)
            R.check("reopened signature: delete works", len([o for o in objs(page) if o["type"] == "signature"]) == 1)
            drag(page, 0, (sig["x"] + sig["w"] / 2, sig["y"] + sig["h"] / 2), (sig["x"] + sig["w"] / 2 + 60, sig["y"] + sig["h"] / 2 + 40)); time.sleep(0.4)
            moved = next(o for o in objs(page) if o["type"] == "signature")
            R.check("reopened signature: drag moves it", abs(moved["x"] - sig["x"] - 60) < 3 and abs(moved["y"] - sig["y"] - 40) < 3, (sig, moved))
        ph = next((o for o in again if o["type"] == "photo"), None)
        if ph:
            cx, cy = pt(page, 0, ph["x"] + ph["w"] / 2, ph["y"] + ph["h"] / 2)
            page.mouse.click(cx, cy); time.sleep(0.3)
            page.keyboard.press("Control+c"); time.sleep(0.2); page.keyboard.press("Control+v"); time.sleep(0.4)
            R.check("reopened photo: copy/paste works", len([o for o in objs(page) if o["type"] == "photo"]) == 2)
        p2 = save_as(page, out, "signed2")
        doc = fitz.open(p2)
        subs = sorted((a.info.get("subject") or "") for a in doc[0].annots() if a.type[0] == fitz.PDF_ANNOT_STAMP)
        R.check("second save keeps them as objects (1 signature, 2 photos)", subs.count("Photo") == 2 and len(subs) == 3, subs)
        R.check("text still selectable", "quick brown fox" in doc[0].get_text())
        doc.close()
        close_all(page)

        # ── the Edit tool: clicking a signature picks it up too ────────────
        page.evaluate("p => Studio.open(p, {tool: 'edit'})", p2)
        wait(page, "document.querySelector('#st-host .pe-page .an-bg.on')", timeout=20)
        time.sleep(0.8)
        doc = fitz.open(p2)
        pg0 = doc[0]
        r = [fitz.Rect(a.rect) for a in pg0.annots() if a.type[0] == fitz.PDF_ANNOT_STAMP and (a.info.get('subject') or '') != 'Photo'][0]
        pw = doc[0].rect.width
        doc.close()
        x, y = page.evaluate("""([x, y, PW]) => { const el = document.querySelector('#st-host .pe-page[data-p="0"]'); const v = el.closest('.an-view');
            let b = el.getBoundingClientRect(); const k = b.width / PW;
            const vr = v.getBoundingClientRect(); v.scrollTop += (b.top + y * k) - (vr.top + vr.height / 2);
            b = el.getBoundingClientRect(); return [b.left + x * k, b.top + y * k]; }""", [(r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, pw])
        time.sleep(0.3)
        page.mouse.click(x, y); time.sleep(0.8)
        st_state = page.evaluate("Studio._state()")
        sel = page.evaluate("!!document.querySelector('#st-host .an-t-signature.is-sel')")
        R.check("Edit tool: clicking the signature selects it (as a signature)", st_state and st_state.get("mode") == "annotate" and sel, st_state)
        close_all(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "reopen_signed")))
