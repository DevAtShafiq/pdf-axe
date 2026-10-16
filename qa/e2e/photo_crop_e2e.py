"""E2E: "Crop to photo size" (ui/js/photo-size.js + photo_size.py).

Opens the dialog from the right-click menu and the details pane, picks presets,
moves / resizes / zooms / rotates / straightens the box, saves, and checks the
output JPEGs (pixel size, DPI, KB limit, naming, 4×6 sheet) with PIL. Also
checks the old free "Crop Image…" still works.

    set QA_CDP_PORT=9373
    python qa/e2e/photo_crop_e2e.py <scratch dir> [screenshot.png]
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402

R = Results()
DLG = ".modal-overlay:has(#modal-photo-size)"


def make_photo(path, size=(1500, 2000)):
    """Portrait-like test photo: grey wall, skin oval face, dark hair, eyes, shoulders."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.effect_noise(size, 25).convert("RGB")
    bg = Image.new("RGB", size, (205, 210, 215))
    im = Image.blend(bg, im, 0.25)
    d = ImageDraw.Draw(im)
    cx, cy = size[0] // 2, int(size[1] * 0.42)
    d.ellipse([cx - 420, cy + 420, cx + 420, cy + 1400], fill=(30, 40, 70))          # shoulders
    d.ellipse([cx - 260, cy - 380, cx + 260, cy + 360], fill=(90, 60, 40))           # hair
    d.ellipse([cx - 230, cy - 300, cx + 230, cy + 340], fill=(225, 185, 155))        # face
    for ex in (cx - 95, cx + 95):
        d.ellipse([ex - 30, cy - 40, ex + 30, cy], fill=(40, 30, 30))                # eyes
    d.arc([cx - 90, cy + 140, cx + 90, cy + 230], 20, 160, fill=(150, 60, 60), width=10)
    im = im.filter(ImageFilter.GaussianBlur(1))
    im.save(path, quality=95, dpi=(72, 72))
    return path


def st(page):
    return page.evaluate(f"document.querySelector('{DLG}')._psState()")


def is_open(page):
    return page.evaluate(f"!!document.querySelector('{DLG}')")


def wait_ready(page):
    wait(page, f"(() => {{ const o = document.querySelector('{DLG}'); return o && o._psState && o._psState().ready; }})()")
    time.sleep(0.3)


def reveal(page, d, path):
    page.evaluate("d => App.navigate(d)", d)
    time.sleep(0.8)
    page.evaluate("p => FileTree.revealPath(p)", path)
    time.sleep(0.8)


def right_click_item(page, path, label):
    row = page.evaluate("""p => { const el = [...document.querySelectorAll('[data-path]')].find(e => e.dataset.path.toLowerCase() === p.toLowerCase() && e.offsetParent !== null);
                              if (!el) return null; const r = el.getBoundingClientRect(); return [r.left + 30, r.top + r.height/2]; }""", path)
    if not row:
        return False
    page.mouse.click(row[0], row[1], button="right")
    time.sleep(0.4)
    it = page.query_selector(f".ctx-item:has-text('{label}')") or page.query_selector(f"text={label}")
    if not it:
        page.keyboard.press("Escape")
        return False
    it.click()
    return True


def canvas_pt(page, fx, fy):
    """Screen point of frame px (fx, fy)."""
    b = page.query_selector(f"{DLG} canvas").bounding_box()
    s = st(page)["scale"]
    return b["x"] + fx * s, b["y"] + fy * s


def save_and_wait(page, d, before):
    page.click(f"{DLG} [id^=ps-save]")
    wait(page, f"!document.querySelector('{DLG}')", timeout=20)
    time.sleep(0.5)
    return sorted(set(os.listdir(d)) - before)


def run(d, shot=""):
    from PIL import Image
    d = os.path.normpath(os.path.abspath(d))
    os.makedirs(d, exist_ok=True)
    src = make_photo(os.path.join(d, "student.jpg"))
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.evaluate("try { localStorage.removeItem('sfm.photoSize.last') } catch (_) {}")
        reveal(page, d, src)

        # 1 ── right-click → Crop to photo size…
        R.check("context menu has 'Crop to photo size…'", right_click_item(page, src, "Crop to photo size"))
        wait_ready(page)
        R.check("dialog opens from the right-click menu", is_open(page))
        opts = page.evaluate(f"[...document.querySelectorAll('{DLG} [id^=ps-preset] option')].map(o => o.value)")
        need = {"kr_passport", "kr_id", "kr_arc", "np_passport", "in_passport", "bd_passport", "pk_passport",
                "us_passport", "cn_visa", "jp_visa", "schengen", "uk", "ca", "au", "custom", "free"}
        R.check("all official presets + Custom + Free listed", need <= set(opts), sorted(need - set(opts)))

        page.select_option(f"{DLG} [id^=ps-preset]", "kr_passport")
        time.sleep(0.3)
        s0 = st(page)
        R.check("Korea passport box has the 35:45 aspect", abs(s0["rect"]["w"] / s0["rect"]["h"] - 413 / 531) < 0.003, s0["rect"])
        spec = page.inner_text(f"{DLG} [id^=ps-spec]")
        R.check("spec shows 35 × 45 mm · 413 × 531 px · 300 DPI", "35 × 45 mm" in spec and "413 × 531" in spec and "300 DPI" in spec, spec)
        R.check("face guide on by default for presets",
                page.evaluate(f"document.querySelector('{DLG} [id^=ps-guide]').checked"))

        # 2 ── wheel zoom (box gets smaller = zoom in)
        r = s0["rect"]
        x, y = canvas_pt(page, r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
        page.mouse.move(x, y)
        for _ in range(5):
            page.mouse.wheel(0, -120)
            time.sleep(0.05)
        s1 = st(page)
        R.check("wheel up zooms in (box smaller, same aspect)",
                s1["rect"]["w"] < r["w"] * 0.85 and abs(s1["rect"]["w"] / s1["rect"]["h"] - 413 / 531) < 0.003,
                (round(r["w"]), round(s1["rect"]["w"])))

        # 3 ── drag to move
        r = s1["rect"]
        x, y = canvas_pt(page, r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
        page.mouse.move(x, y); page.mouse.down()
        page.mouse.move(x - 30, y - 20, steps=6); page.mouse.up()
        s2 = st(page)
        R.check("drag moves the box", s2["rect"]["x"] < r["x"] - 10 and s2["rect"]["y"] < r["y"] - 5 and abs(s2["rect"]["w"] - r["w"]) < 0.01)

        # 4 ── corner handle keeps the aspect
        r = s2["rect"]
        x, y = canvas_pt(page, r["x"] + r["w"], r["y"] + r["h"])
        page.mouse.move(x, y); page.mouse.down()
        page.mouse.move(x + 25, y + 5, steps=6); page.mouse.up()
        s3 = st(page)
        R.check("corner resize keeps 35:45", s3["rect"]["w"] > r["w"] + 5 and abs(s3["rect"]["w"] / s3["rect"]["h"] - 413 / 531) < 0.003,
                (round(r["w"]), round(s3["rect"]["w"])))

        # 5 ── rotate right then left, straighten
        page.click(f"{DLG} [id^=ps-rr]"); time.sleep(0.3)
        s4 = st(page)
        R.check("rotate right 90° swaps the frame", s4["rot"] == 90 and s4["W"] == 2000 and s4["H"] == 1500, (s4["rot"], s4["W"], s4["H"]))
        R.check("box keeps the preset aspect after rotating", abs(s4["rect"]["w"] / s4["rect"]["h"] - 413 / 531) < 0.003)
        page.click(f"{DLG} [id^=ps-rl]"); time.sleep(0.3)
        R.check("rotate left returns to 0°", st(page)["rot"] == 0)
        page.evaluate(f"(() => {{ const r = document.querySelector('{DLG} input[type=range][id^=ps-angle-]'); r.value = '4.5'; r.dispatchEvent(new Event('input', {{bubbles:true}})); }})()")
        time.sleep(0.3)
        s5 = st(page)
        R.check("straighten 4.5° enlarges the frame", s5["angle"] == 4.5 and s5["Wf"] > 1500 and s5["Hf"] > 2000, (s5["angle"], round(s5["Wf"])))
        R.check("straighten label shows 4.5°", "4.5°" in page.inner_text(f"{DLG} [id^=ps-anglev]"))

        # 6 ── KB limit + print sheet, save
        page.fill(f"{DLG} [id^=ps-kb]", "40")
        page.check(f"{DLG} [id^=ps-sheet]")
        time.sleep(0.2)
        R.check("copies row shows how many fit", "up to 8" in page.inner_text(f"{DLG} [id^=ps-fit-n]"))
        if shot:
            page.query_selector(f"{DLG} .modal").screenshot(path=shot)
            R.check("dialog screenshot saved", os.path.isfile(shot), shot)
        before = set(os.listdir(d))
        new = save_and_wait(page, d, before)
        R.check("saved student_korea_passport.jpg + 4×6 sheet",
                new == ["student_korea_passport.jpg", "student_korea_passport_4x6.jpg"], new)
        out = os.path.join(d, "student_korea_passport.jpg")
        if os.path.isfile(out):
            with Image.open(out) as im:
                R.check("output 413×531 px", im.size == (413, 531), im.size)
                R.check("output DPI 300", tuple(round(v) for v in im.info.get("dpi", (0, 0))) == (300, 300), im.info.get("dpi"))
                R.check("output is JPEG", im.format == "JPEG")
            R.check("output ≤ 40 KB", os.path.getsize(out) <= 40 * 1024, os.path.getsize(out))
        sheet = os.path.join(d, "student_korea_passport_4x6.jpg")
        if os.path.isfile(sheet):
            with Image.open(sheet) as im:
                R.check("sheet is 4×6 in @ 300 DPI", im.size in ((1800, 1200), (1200, 1800)) and round(im.info["dpi"][0]) == 300, im.size)
        R.check("original untouched", os.path.getsize(src) > 0 and Image.open(src).size == (1500, 2000))

        # 7 ── details pane "Photo size…" reopens with the last preset remembered
        reveal(page, d, src)
        btn = page.query_selector("#ai-photo-size")
        vis = btn and page.evaluate("b => b.offsetParent !== null", btn)
        R.check("details pane shows 'Photo size…' for images", bool(vis))
        if vis:
            btn.click()
            wait_ready(page)
            R.check("details button opens the dialog", is_open(page))
            R.check("last used preset remembered", st(page)["key"] == "kr_passport", st(page)["key"])
            R.check("last KB limit remembered", page.input_value(f"{DLG} [id^=ps-kb]") == "40")
            page.select_option(f"{DLG} [id^=ps-preset]", "us_passport"); time.sleep(0.2)
            page.fill(f"{DLG} [id^=ps-kb]", "")
            page.uncheck(f"{DLG} [id^=ps-sheet]")
            s6 = st(page)
            R.check("US 2×2 in box is square", abs(s6["rect"]["w"] - s6["rect"]["h"]) < 0.01)
            page.click(f"{DLG} button[id^=ps-fit-]"); time.sleep(0.2)
            for i in range(2):
                if i:
                    reveal(page, d, src); page.click("#ai-photo-size"); wait_ready(page)
                before = set(os.listdir(d))
                new = save_and_wait(page, d, before)
                want = "student_us_passport.jpg" if i == 0 else "student_us_passport (2).jpg"
                R.check(f"US save #{i + 1} → {want}", new == [want], new)
                p = os.path.join(d, want)
                if os.path.isfile(p):
                    with Image.open(p) as im:
                        R.check(f"US #{i + 1} is 600×600 @ 300 DPI", im.size == (600, 600) and round(im.info["dpi"][0]) == 300, im.size)

        # 8 ── Custom mm + Esc closes without saving
        reveal(page, d, src)
        page.click("#ai-photo-size"); wait_ready(page)
        page.select_option(f"{DLG} [id^=ps-preset]", "custom")
        page.fill(f"{DLG} [id^=ps-cw]", "20"); page.fill(f"{DLG} [id^=ps-ch]", "25")
        time.sleep(0.2)
        R.check("custom 20×25 mm box aspect", abs(st(page)["ratio"] - 236 / 295) < 0.003, st(page)["ratio"])
        before = set(os.listdir(d))
        new = save_and_wait(page, d, before)
        R.check("custom saved as student_20x25mm.jpg", new == ["student_20x25mm.jpg"], new)
        if new:
            with Image.open(os.path.join(d, new[0])) as im:
                R.check("custom is 236×295 px", im.size == (236, 295), im.size)
        page.click("#ai-photo-size"); wait_ready(page)
        before = set(os.listdir(d))
        page.keyboard.press("Escape"); time.sleep(0.4)
        R.check("Esc closes, nothing written", not is_open(page) and set(os.listdir(d)) == before)

        # 9 ── old free crop still works
        reveal(page, d, src)
        R.check("context menu still has 'Crop Image…'", right_click_item(page, src, "Crop Image"))
        wait(page, "!!document.querySelector('#modal-crop-image canvas')")
        time.sleep(0.5)
        before = set(os.listdir(d))
        page.click("#modal-crop-image .btn-primary")
        wait(page, "!document.querySelector('#modal-crop-image')", timeout=15)
        time.sleep(0.4)
        new = sorted(set(os.listdir(d)) - before)
        R.check("old Crop Image saves student_cropped.jpg", new == ["student_cropped.jpg"], new)
        R.check("no page errors", not errors, errors[:3])
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
