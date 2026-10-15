"""E2E: the "Draw signature" pad (ui/js/annotate.js signaturePad) with a
simulated pen tablet.

    set QA_CDP_PORT=9363
    python qa/e2e/signature_pad_e2e.py <scratch dir>

A pen tablet in absolute mode maps its whole surface to the whole screen, so
the ink lands anywhere on the window. The script signs with real CDP pen
events (Input.dispatchMouseEvent pointerType "pen" + force = pressure) in far
corners of the window, huge and tiny, plus touch and mouse, and checks:

* the ink shown in the pad is centred (within a few px) and fitted;
* the same signature gives the same result wherever / however big it is signed;
* a small far-away mark is left out; Undo stroke / Clear work;
* a stroke that starts on a button is ink, not a click;
* pressure changes the line width; blue ink is blue;
* the saved PNG is tightly cropped, transparent, hi-res, ink centred;
* placing it (click or drawn box) centres it, keeping its aspect ratio, and
  in the saved PDF (PyMuPDF) the ink is centred in the drawn box;
* remembered signatures: saved, listed, used, forgotten.
"""
from __future__ import annotations

import base64
import io
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from annotate_e2e import drag, open_editor, pt, save_new  # noqa: E402
from cdp import Results, app_page, close_overlays, wait  # noqa: E402
from fixtures import make_all  # noqa: E402

R = Results()
PAD_W, PAD_H, PADX, PADY = 560, 190, 30, 26


# ── a made-up cursive signature in unit coords (width 1) ──────────────────────
def signature_shape():
    main = []
    for i in range(140):
        t = i / 139
        x = t + 0.05 * math.sin(t * 40)
        y = 0.16 * math.sin(t * 22) * (1 - 0.4 * t) + 0.08 * math.cos(t * 9)
        main.append((x, y))
    under = [(0.1 + 0.8 * i / 30, 0.24 + 0.02 * math.sin(i / 4)) for i in range(31)]
    dot = [(0.42, -0.24), (0.425, -0.235)]
    return [main, under, dot]


def placed(shape, x0, y0, w):
    return [[(x0 + x * w, y0 + y * w) for x, y in s] for s in shape]


# ── input over CDP ─────────────────────────────────────────────────────────────
class Pen:
    def __init__(self, page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)

    def stroke(self, pts, pressure=0.6, kind="pen"):
        c = self.cdp
        if kind == "touch":
            c.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": pts[0][0], "y": pts[0][1]}]})
            for x, y in pts[1:]:
                c.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
            c.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            time.sleep(0.05)
            return
        base = {"pointerType": kind, "force": pressure} if kind == "pen" else {}
        x, y = pts[0]
        c.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "buttons": 0, **base})
        c.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1, **base})
        for x, y in pts[1:]:
            c.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1, **base})
        c.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1, **base})
        time.sleep(0.05)

    def sign(self, strokes, pressure=0.6, kind="pen", pause=True):
        for s in strokes:
            self.stroke(s, pressure, kind)
        # the pad shows the centred signature after a short writing pause
        time.sleep(1.35 if pause else 0.12)


# ── measurements in the page ──────────────────────────────────────────────────
PAD_INK = """() => {
  const cv = document.querySelector('#an-sig-canvas'); if (!cv) return null;
  const D = cv.width / 560, d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
  let x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1, n = 0, r = 0, g = 0, b = 0;
  for (let y = 0; y < cv.height; y++) for (let x = 0; x < cv.width; x++) {
    const i = (y * cv.width + x) * 4;
    if (d[i + 3] > 40) { n++; r += d[i]; g += d[i + 1]; b += d[i + 2];
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  }
  if (x1 < 0) return { n: 0 };
  return { n: n / (D * D), box: [x0 / D, y0 / D, (x1 + 1) / D, (y1 + 1) / D], rgb: [r / n, g / n, b / n] };
}"""


def pad_ink(page):
    time.sleep(0.08)
    return page.evaluate(PAD_INK)


def centred_and_fitted(ink, tol=3.0):
    if not ink or not ink.get("box"):
        return False, "no ink"
    x0, y0, x1, y1 = ink["box"]
    cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    centred = abs(cx - PAD_W / 2) <= tol and abs(cy - PAD_H / 2) <= tol
    inside = x0 >= PADX - 8 and x1 <= PAD_W - PADX + 8 and y0 >= PADY - 8 and y1 <= PAD_H - PADY + 8
    fills = w >= PAD_W - 2 * PADX - 2 or h >= PAD_H - 2 * PADY - 2
    return centred and inside and fills, f"centre=({cx:.1f},{cy:.1f}) size={w:.0f}x{h:.0f}"


def clear(page):
    page.evaluate("document.querySelector('#an-sig-clear').click()")
    time.sleep(0.05)


def open_pad(page):
    page.click("[data-tool=signature]")
    wait(page, "document.querySelector('.an-sig-overlay #an-sig-canvas')")
    time.sleep(0.3)


def png_info(data_url):
    from PIL import Image
    raw = base64.b64decode(data_url.split(",", 1)[1])
    im = Image.open(io.BytesIO(raw))
    im.load()
    a = im.convert("RGBA")
    bb = a.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    corners = [a.getpixel(p)[3] for p in ((0, 0), (a.width - 1, 0), (0, a.height - 1), (a.width - 1, a.height - 1))]
    px = [p for p in zip(*[iter(a.tobytes())] * 4) if p[3] > 200]
    avg = [sum(p[k] for p in px) / max(1, len(px)) for k in range(3)]
    return {"mode": im.mode, "size": a.size, "bbox": bb, "corners": corners, "rgb": avg, "bytes": len(raw)}


def save_pdf(page, folder, name):
    """Save as a new file — from PDF Studio (PDFs open there) or the plain editor."""
    if page.query_selector("#st-save") and page.is_visible("#st-save"):
        page.click("#st-save")
        wait(page, "document.querySelector('#st-go')")
        page.fill("#st-name", name)
        page.fill("#st-dir", folder)
        page.click("#st-go")
        out = os.path.join(folder, name + ".pdf")
        end = time.time() + 30
        while time.time() < end and not os.path.isfile(out):
            time.sleep(0.2)
        time.sleep(0.5)
    else:
        save_new(page, folder, name)


def run(d):
    import pymupdf as fitz
    f = make_all(d)
    out_dir = os.path.join(d, "out")
    os.makedirs(out_dir, exist_ok=True)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        vw, vh = page.evaluate("[innerWidth, innerHeight]")
        print(f"window {vw}x{vh}")
        open_editor(page, f["multi"])
        page.evaluate("window.__pt = []; addEventListener('pointerdown', e => __pt.push([e.pointerType, e.pressure]), true)")
        open_pad(page)
        pen = Pen(page)
        shape = signature_shape()
        R.check("hint says sign anywhere", "Sign anywhere" in page.inner_text("#an-sig-hint"))
        page.screenshot(path=os.path.join(d, "sig_0_empty.png"))

        # 1) top-left corner of the window (far outside the pad)
        pen.sign(placed(shape, 14, 75, 220), 0.6)
        R.check("pen events arrive as pen with pressure",
                page.evaluate("__pt.some(p => p[0] === 'pen' && p[1] > 0.5 && p[1] < 0.7)"), page.evaluate("__pt.slice(-2)"))
        ink_tl = pad_ink(page)
        ok, info = centred_and_fitted(ink_tl)
        R.check("top-left signature centred + fitted in pad", ok, info)
        page.screenshot(path=os.path.join(d, "sig_1_topleft.png"))
        R.check("after the pause the writing layer is hidden (only the centred copy shows)", page.evaluate(
            "(() => { const o = document.querySelector('.an-sig-overlay'); return !o.classList.contains('is-writing') && getComputedStyle(document.querySelector('#an-sig-trace')).opacity === '0' })()"))

        # 1b) while writing: ONE copy (where the pen is), earlier strokes stay visible
        clear(page)
        strokes = placed(shape, 14, 75, 220)
        TRACE_N = """(() => { const t = document.querySelector('#an-sig-trace'); const d = t.getContext('2d').getImageData(0, 0, t.width, t.height).data;
                       let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 40) n++; return n; })()"""
        pen.stroke(strokes[0], 0.6); time.sleep(0.15)
        n1 = page.evaluate(TRACE_N)
        writing = page.evaluate("document.querySelector('.an-sig-overlay').classList.contains('is-writing')")
        R.check("while writing: ink shown where the pen is", writing and n1 > 50, n1)
        R.check("while writing: pad shows no second copy", (pad_ink(page) or {}).get("n", 0) == 0)
        for s in strokes[1:]:
            pen.stroke(s, 0.6)
        time.sleep(0.15)
        n2 = page.evaluate(TRACE_N)
        R.check("earlier strokes stay visible for the next letter", n2 > n1 and page.evaluate("document.querySelector('.an-sig-overlay').classList.contains('is-writing')"), f"{n1} -> {n2}")
        page.screenshot(path=os.path.join(d, "sig_1b_writing.png"))
        time.sleep(1.35)
        ok, info = centred_and_fitted(pad_ink(page))
        R.check("after a pause: centred copy appears in the pad", ok, info)

        # 1c) pen comes back (hovering, not touching): the earlier strokes reappear in place
        pen.cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": vw * 0.5, "y": vh * 0.25, "buttons": 0, "pointerType": "pen"})
        time.sleep(0.15)
        back = page.evaluate("document.querySelector('.an-sig-overlay').classList.contains('is-writing')")
        R.check("pen hovering again shows the earlier strokes where they were written", back and page.evaluate(TRACE_N) >= n2 * 0.9, page.evaluate(TRACE_N))
        time.sleep(1.35)
        R.check("…and after the pen rests, the centred copy is back", not page.evaluate("document.querySelector('.an-sig-overlay').classList.contains('is-writing')"))

        # 2) far right / bottom, huge: same shape → same pad result
        clear(page)
        w_big = vw * 0.62
        pen.sign(placed(shape, vw - w_big - 10, vh - 0.32 * w_big, w_big), 0.6)
        ink_big = pad_ink(page)
        ok, info = centred_and_fitted(ink_big)
        R.check("huge bottom-right signature centred + fitted", ok, info)
        same = all(abs(a - b) <= 3 for a, b in zip(ink_tl["box"], ink_big["box"]))
        R.check("same size/place regardless of where/how big signed", same, f"{ink_tl['box']} vs {ink_big['box']}")

        # 3) tiny signature far right top
        clear(page)
        pen.sign(placed(shape, vw - 70, 30, 50), 0.6)
        ok, info = centred_and_fitted(pad_ink(page), tol=3.5)
        R.check("tiny signature centred + fitted", ok, info)

        # 4) stray tap far away (pen touching while moving to the tablet) — before and after
        clear(page)
        pen.stroke([(vw - 12, vh - 12), (vw - 11, vh - 12)], 0.4)            # tap in the far corner first
        pen.sign(placed(shape, vw * 0.3, vh * 0.35, 300), 0.6)
        pen.stroke([(8, vh - 10), (10, vh - 9)], 0.4)                        # and another one at the end
        time.sleep(1.35)
        ink_stray = pad_ink(page)
        ok, info = centred_and_fitted(ink_stray)
        R.check("stray far marks ignored (still centred)", ok and all(abs(a - b) <= 3 for a, b in zip(ink_stray["box"], ink_tl["box"])), info)
        R.check("stray note shown", page.evaluate("!document.querySelector('#an-sig-note').classList.contains('hidden')"),
                page.inner_text("#an-sig-note"))
        page.screenshot(path=os.path.join(d, "sig_2_stray.png"))
        page.click("#an-sig-undo")
        page.click("#an-sig-undo")
        time.sleep(0.1)
        # 2 undos: the end tap, then the signature's dot → still centred
        ok, info = centred_and_fitted(pad_ink(page))
        R.check("undo stroke keeps it centred", ok, info)
        n1 = pad_ink(page)["n"]
        page.keyboard.press("Control+z")          # removes the underline
        time.sleep(0.1)
        n2 = pad_ink(page)["n"]
        R.check("Ctrl+Z undoes a stroke, pad still open",
                page.evaluate("!!document.querySelector('.an-sig-overlay')") and n2 != n1, f"{n1:.0f} -> {n2:.0f}")

        # 5) a pen stroke starting on a button is ink, not a click
        clear(page)
        bb = page.query_selector("#an-sig-cancel").bounding_box()
        sx, sy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
        pen.stroke([(sx - i * 6, sy - 40 * math.sin(i / 6)) for i in range(60)], 0.6)
        time.sleep(1.35)
        R.check("stroke from Cancel button = ink, dialog stays open",
                page.evaluate("!!document.querySelector('.an-sig-overlay')") and (pad_ink(page) or {}).get("n", 0) > 50)
        # a plain click on a swatch still works (blue)
        clear(page)
        page.click("#an-sig-cols [data-c='#1e3a8a']")
        R.check("swatch click still a click", page.evaluate("document.querySelector('#an-sig-cols .active').dataset.c") == "#1e3a8a")

        # 6) pressure → line width
        clear(page)
        pen.sign(placed(shape, 40, 60, 400), 0.15)
        light = pad_ink(page)
        clear(page)
        pen.sign(placed(shape, 40, 60, 400), 1.0)
        heavy = pad_ink(page)
        R.check("pressure changes line width", heavy["n"] > light["n"] * 1.5, f"{light['n']:.0f} vs {heavy['n']:.0f} ink px")
        R.check("blue ink", heavy["rgb"][2] > heavy["rgb"][0] + 40, heavy["rgb"])

        # 7) touch and mouse work too
        clear(page)
        pen.sign(placed(shape, vw * 0.5, vh * 0.1, 260), kind="touch")
        ok, info = centred_and_fitted(pad_ink(page))
        R.check("touch signature centred", ok and page.evaluate("__pt.some(p => p[0] === 'touch')"), info)
        clear(page)
        cb = page.query_selector("#an-sig-canvas").bounding_box()
        for s in placed(shape, cb["x"] + 20, cb["y"] + 40, 120):
            page.mouse.move(*s[0]); page.mouse.down()
            for p in s[1:]:
                page.mouse.move(*p)
            page.mouse.up()
        time.sleep(1.35)
        ok, info = centred_and_fitted(pad_ink(page))
        R.check("mouse signature (in a pad corner) centred", ok, info)

        # 8) final: pen, bottom-left of the window, blue; remember it; Use
        clear(page)
        pen.sign(placed(shape, 20, vh - 140, 340), 0.7)
        page.screenshot(path=os.path.join(d, "sig_3_final.png"))
        page.check("#an-sig-rem")
        page.click("#an-sig-ok")
        wait(page, "!document.querySelector('.an-sig-overlay')")
        time.sleep(0.4)

        # place by dragging a box (page units) → centred in the box, aspect kept
        box = (100, 560, 420, 700)
        drag(page, 0, box[:2], box[2:])
        time.sleep(0.3)
        a = page.evaluate("""() => { const g = [...document.querySelectorAll('.an-t-signature')].pop(); if (!g) return null;
            const im = g.querySelector('image'); return { id: g.dataset.id, href: im.getAttribute('href'),
            x: +im.getAttribute('x'), y: +im.getAttribute('y'), w: +im.getAttribute('width'), h: +im.getAttribute('height') }; }""")
        R.check("signature placed by box", a is not None)
        info = png_info(a["href"])
        print("PNG", info)
        bw, bh = info["size"]
        R.check("PNG is RGBA with transparent background", info["mode"] == "RGBA" and info["corners"] == [0, 0, 0, 0], info)
        x0, y0, x1, y1 = info["bbox"]
        ml, mr, mt, mb = x0, bw - x1, y0, bh - y1
        R.check("PNG cropped tight with even margins (ink centred)",
                max(ml, mr, mt, mb) <= 30 and abs(ml - mr) <= 3 and abs(mt - mb) <= 3, f"margins l{ml} r{mr} t{mt} b{mb}")
        R.check("PNG high resolution", max(bw, bh) >= 1100 and min(bw, bh) >= 300, f"{bw}x{bh}, {info['bytes']} bytes")
        R.check("PNG ink is blue", info["rgb"][2] > info["rgb"][0] + 40, info["rgb"])
        cx, cy = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
        bcx, bcy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        fits = a["w"] <= box[2] - box[0] + 0.6 and a["h"] <= box[3] - box[1] + 0.6 and \
            (abs(a["w"] - (box[2] - box[0])) < 0.6 or abs(a["h"] - (box[3] - box[1])) < 0.6)
        R.check("box placement centred in the box", abs(cx - bcx) < 0.6 and abs(cy - bcy) < 0.6, (cx, cy))
        R.check("box placement fits + keeps aspect", fits and abs(a["w"] / a["h"] - bw / bh) < 0.02, {k: v for k, v in a.items() if k != "href"})

        # place again by clicking (from the remembered list) → centred on the click
        open_pad(page)
        R.check("remembered signature listed", page.evaluate("document.querySelectorAll('.an-sig-use').length") >= 1)
        page.click(".an-sig-use")
        wait(page, "!document.querySelector('.an-sig-overlay')")
        time.sleep(0.3)
        x, y = pt(page, 0, 300, 300)
        page.mouse.click(x, y)
        time.sleep(0.3)
        c = page.evaluate("""() => { const im = [...document.querySelectorAll('.an-t-signature image')].pop();
            return [+im.getAttribute('x') + +im.getAttribute('width') / 2, +im.getAttribute('y') + +im.getAttribute('height') / 2]; }""")
        R.check("click placement centred on the click", abs(c[0] - 300) < 0.6 and abs(c[1] - 300) < 0.6, c)
        page.screenshot(path=os.path.join(d, "sig_4_placed.png"))

        # forget the remembered signature
        open_pad(page)
        n0 = page.evaluate("document.querySelectorAll('.an-sig-use').length")
        page.click(".an-sig-forget")
        time.sleep(0.4)
        R.check("forget signature", page.evaluate("document.querySelectorAll('.an-sig-use').length") == n0 - 1)
        page.click("#an-sig-cancel")
        time.sleep(0.2)

        # save → PyMuPDF: annotation rect + rendered ink centred in the drawn box
        save_pdf(page, out_dir, "sig_qa")
        doc = fitz.open(os.path.join(out_dir, "sig_qa.pdf"))
        pg = doc[0]
        stamps = [an for an in pg.annots() if an.type[0] == fitz.PDF_ANNOT_STAMP]
        R.check("two signature stamps saved", len(stamps) == 2, len(stamps))
        r = min(stamps, key=lambda s: abs(s.rect.y0 - box[1])).rect
        R.check("PDF annotation centred in box", abs((r.x0 + r.x1) / 2 - bcx) < 1 and abs((r.y0 + r.y1) / 2 - bcy) < 1, r)
        from PIL import Image
        zoom = 3
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(*box), annots=True)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        ink = im.convert("L").point(lambda v: 255 if v < 160 else 0).getbbox()
        if ink:
            icx = box[0] + (ink[0] + ink[2]) / 2 / zoom
            icy = box[1] + (ink[1] + ink[3]) / 2 / zoom
            R.check("rendered PDF ink centred in the box", abs(icx - bcx) < 3 and abs(icy - bcy) < 3,
                    f"ink centre ({icx:.1f},{icy:.1f}) vs box centre ({bcx},{bcy})")
        else:
            R.check("rendered PDF ink centred in the box", False, "no ink rendered")
        doc.close()
        close_overlays(page)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
