# -*- coding: utf-8 -*-
"""
photo_size.py — "Crop to photo size": official passport / visa / ID photo sizes.

The office crops student photos to the exact size an embassy or form asks for
(Korea passport 35×45 mm, US 2×2 in, China visa 33×48 mm ...). This module
holds the preset list and does the full-resolution work for the dialog in
ui/js/photo-size.js:

    presets()                       built-in list + the office's own file
    render_photo(path, params)      crop / straighten / resize → new JPEG
    make_print_sheet(path, ...)     several copies on a 4×6 in sheet
    face_box(path, rotate)          face rectangle, only if OpenCV is installed

Geometry contract with the dialog (all floats, full-resolution pixels):
  * the image is EXIF-upright, then turned ``rotate`` degrees clockwise
    (0/90/180/270) → size W×H;
  * then straightened by ``angle`` degrees clockwise (±15) about its centre
    on a canvas just big enough to hold it ("frame", Wf×Hf, same centre);
    corners outside the photo are filled white;
  * ``rect`` (x, y, w, h) is in frame pixels.

Output is always a NEW JPEG ``<stem>_<tag>.jpg`` next to the original
(`` (2)``, `` (3)`` ... when taken) with the DPI written; nothing is ever
overwritten.

Extending the list: put a ``photo_presets.json`` next to the app (next to
OfficeAxe.exe, or next to this file when run from source). It is a JSON list
of presets in the same shape as BUILTIN_PRESETS below; an entry with the same
``key`` replaces the built-in one, ``"hidden": true`` hides it. Use
``write_presets_template()`` (dialog → "Edit list…") to create a starter file.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import sys

from PIL import Image, ImageDraw, ImageOps

import image_crop

DEFAULT_DPI = 300
MM_PER_IN = 25.4
MAX_ANGLE = 15.0
SHEET_IN = (6.0, 4.0)          # 4×6 in print sheet (landscape)

# ── Preset list ─────────────────────────────────────────────────────────────
# w/h in ``unit`` (mm | in | px). ``guide`` lines are measured from the TOP of
# the photo in the same unit: crown (top of head), eye line, chin; ``head``
# is the allowed chin-to-crown range shown as a hint. Numbers follow the
# usual ICAO-style rules of each authority; check the embassy page when a rule
# changes and override the preset in photo_presets.json.
_ICAO_35x45 = {"crown": 4.0, "eye": 19.5, "chin": 38.0, "head": [32, 36]}

BUILTIN_PRESETS: list = [
    {"key": "kr_passport", "name": "Korea passport / visa", "group": "Korea",
     "w": 35, "h": 45, "unit": "mm", "tag": "korea_passport", "guide": _ICAO_35x45,
     "note": "Head 32–36 mm (chin to crown), white background, no glasses glare."},
    {"key": "kr_id", "name": "Korean ID / resume (반명함)", "group": "Korea",
     "w": 30, "h": 40, "unit": "mm", "tag": "korea_id",
     "guide": {"crown": 4.0, "eye": 17.0, "chin": 32.5, "head": [26, 30]},
     "note": "3×4 cm half-size photo for resumes, school and ID forms."},
    {"key": "kr_arc", "name": "Korea ARC (residence card)", "group": "Korea",
     "w": 35, "h": 45, "unit": "mm", "tag": "korea_arc", "guide": _ICAO_35x45,
     "note": "Alien Registration Card: same as the passport photo."},
    {"key": "np_passport", "name": "Nepal passport", "group": "Nepal",
     "w": 35, "h": 45, "unit": "mm", "tag": "nepal_passport", "guide": _ICAO_35x45,
     "note": "Head 32–36 mm, plain light background."},
    {"key": "in_passport", "name": "India passport", "group": "South Asia",
     "w": 35, "h": 45, "unit": "mm", "tag": "india_passport", "guide": _ICAO_35x45,
     "note": "Head 32–36 mm, white background."},
    {"key": "bd_passport", "name": "Bangladesh passport", "group": "South Asia",
     "w": 35, "h": 45, "unit": "mm", "tag": "bangladesh_passport", "guide": _ICAO_35x45,
     "note": "Head 32–36 mm, white / light background."},
    {"key": "pk_passport", "name": "Pakistan passport", "group": "South Asia",
     "w": 35, "h": 45, "unit": "mm", "tag": "pakistan_passport", "guide": _ICAO_35x45,
     "note": "Head 32–36 mm, white background."},
    {"key": "us_passport", "name": "US passport / visa (2×2 in)", "group": "Other",
     "w": 2, "h": 2, "unit": "in", "tag": "us_passport",
     "guide": {"crown": 0.25, "eye": 0.81, "chin": 1.5, "head": [1.0, 1.375]},
     "note": "600×600 px; head 1–1⅜ in, eyes 1⅛–1⅜ in from the bottom."},
    {"key": "cn_visa", "name": "China visa", "group": "Other",
     "w": 33, "h": 48, "unit": "mm", "tag": "china_visa",
     "guide": {"crown": 4.0, "eye": 18.5, "chin": 34.5, "head": [28, 33]},
     "note": "Head 28–33 mm tall, 15–22 mm wide, 3–5 mm space above the head."},
    {"key": "jp_visa", "name": "Japan visa", "group": "Other",
     "w": 45, "h": 45, "unit": "mm", "tag": "japan_visa",
     "guide": {"crown": 6.0, "eye": 20.0, "chin": 37.0, "head": [27, 33]},
     "note": "Square 45×45 mm, plain background."},
    {"key": "schengen", "name": "Schengen visa", "group": "Other",
     "w": 35, "h": 45, "unit": "mm", "tag": "schengen_visa", "guide": _ICAO_35x45,
     "note": "Head 32–36 mm (70–80 % of the photo height)."},
    {"key": "uk", "name": "UK passport / visa", "group": "Other",
     "w": 35, "h": 45, "unit": "mm", "tag": "uk_photo",
     "guide": {"crown": 5.0, "eye": 20.0, "chin": 37.0, "head": [29, 34]},
     "note": "Head 29–34 mm, light grey or cream background."},
    {"key": "ca", "name": "Canada passport / visa", "group": "Other",
     "w": 50, "h": 70, "unit": "mm", "tag": "canada_photo",
     "guide": {"crown": 15.0, "eye": 30.0, "chin": 48.5, "head": [31, 36]},
     "note": "Head 31–36 mm (chin to crown), white background."},
    {"key": "au", "name": "Australia passport / visa", "group": "Other",
     "w": 35, "h": 45, "unit": "mm", "tag": "australia_photo", "guide": _ICAO_35x45,
     "note": "Head 32–36 mm, plain light background."},
]

# Pseudo-presets the dialog always offers (not stored in the list).
CUSTOM_KEY = "custom"
FREE_KEY = "free"


def app_dir() -> str:
    """Folder the office can edit: next to the EXE (frozen) or this file."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def user_presets_path() -> str:
    return os.path.join(app_dir(), "photo_presets.json")


def to_px(value: float, unit: str, dpi: float = DEFAULT_DPI) -> int:
    """Length in mm / in / px → whole pixels at *dpi*."""
    unit = (unit or "mm").lower()
    v = float(value)
    if unit == "px":
        return max(1, int(round(v)))
    if unit == "in":
        return max(1, int(round(v * dpi)))
    return max(1, int(round(v / MM_PER_IN * dpi)))


def _slug(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "_", str(s or "")).strip("_").lower()
    return s[:40] or "photo"


def normalize(p: dict) -> dict | None:
    """Validate one preset and add px_w / px_h / dpi / guide fractions."""
    try:
        key = str(p.get("key") or "").strip()
        if not key or key in (CUSTOM_KEY, FREE_KEY):
            return None
        unit = str(p.get("unit") or "mm").lower()
        if unit not in ("mm", "in", "px"):
            return None
        dpi = float(p.get("dpi") or DEFAULT_DPI)
        w, h = float(p["w"]), float(p["h"])
        if w <= 0 or h <= 0 or not (50 <= dpi <= 2400):
            return None
        out = {
            "key": key,
            "name": str(p.get("name") or key),
            "group": str(p.get("group") or "Other"),
            "w": w, "h": h, "unit": unit, "dpi": dpi,
            "px_w": to_px(w, unit, dpi), "px_h": to_px(h, unit, dpi),
            "tag": _slug(p.get("tag") or p.get("name") or key),
            "note": str(p.get("note") or ""),
            "max_kb": float(p["max_kb"]) if p.get("max_kb") else 0,
            "guide": None,
        }
        g = p.get("guide")
        if isinstance(g, dict) and all(k in g for k in ("crown", "chin")):
            frac = {k: float(g[k]) / h for k in ("crown", "eye", "chin") if g.get(k) is not None}
            if "head" in g and isinstance(g["head"], (list, tuple)) and len(g["head"]) == 2:
                frac["head"] = [float(g["head"][0]) / h, float(g["head"][1]) / h]
            out["guide"] = frac
        return out
    except (KeyError, TypeError, ValueError):
        return None


def _read_user_presets() -> tuple:
    path = user_presets_path()
    if not os.path.isfile(path):
        return [], ""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("presets", [])
        if not isinstance(data, list):
            return [], "photo_presets.json must be a list of presets"
        return [d for d in data if isinstance(d, dict)], ""
    except (OSError, ValueError) as exc:
        return [], f"photo_presets.json: {exc}"


def presets() -> dict:
    """{"presets": [...], "user_file": path, "warning": str}."""
    user, warn = _read_user_presets()
    by_key: dict = {}
    order: list = []
    for p in BUILTIN_PRESETS + user:
        k = str(p.get("key") or "")
        if p.get("hidden"):
            by_key.pop(k, None)
            continue
        n = normalize(p)
        if not n:
            continue
        if k not in by_key:
            order.append(k)
        by_key[k] = n
    return {"presets": [by_key[k] for k in order if k in by_key],
            "user_file": user_presets_path(), "warning": warn}


def find_preset(key: str) -> dict | None:
    return next((p for p in presets()["presets"] if p["key"] == key), None)


def write_presets_template() -> str:
    """Create photo_presets.json (with one example) if missing; return its path."""
    path = user_presets_path()
    if not os.path.exists(path):
        example = [{
            "key": "example_visa", "name": "Example visa (edit me)", "group": "Office",
            "w": 35, "h": 45, "unit": "mm", "dpi": 300, "tag": "example_visa",
            "max_kb": 240,
            "guide": {"crown": 4, "eye": 19.5, "chin": 38, "head": [32, 36]},
            "note": "Copy this block for each new size. unit: mm, in or px. "
                    "Use the key of a built-in preset to change it, or add \"hidden\": true to hide it.",
        }]
        with open(path, "x", encoding="utf-8", newline="\n") as f:
            json.dump(example, f, ensure_ascii=False, indent=2)
    return path


# ── Geometry ────────────────────────────────────────────────────────────────

def frame_size(w: float, h: float, angle: float) -> tuple:
    a = math.radians(float(angle or 0))
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return w * c + h * s, w * s + h * c


def _flatten_white(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P", "PA"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        return bg
    return img.convert("RGB") if img.mode != "RGB" else img


def oriented(path: str, rotate: int = 0) -> Image.Image:
    img = image_crop.load_oriented(path)
    img = _flatten_white(img)
    return image_crop._rotate_cw(img, rotate)


def crop_straight(img: Image.Image, rect, angle: float = 0.0) -> Image.Image:
    """Cut *rect* (frame px) out of *img* straightened by *angle*° clockwise.

    Returns the cut at its native resolution (round(w)×round(h)); areas
    outside the photo are white.
    """
    x, y, w, h = (float(v) for v in rect)
    if w < 1 or h < 1:
        raise ValueError("Crop area is empty")
    a = math.radians(max(-MAX_ANGLE, min(MAX_ANGLE, float(angle or 0))))
    W, H = img.size
    Wf, Hf = frame_size(W, H, math.degrees(a))
    sw, sh = max(1, int(round(w))), max(1, int(round(h)))
    kx, ky = w / sw, h / sh
    cos, sin = math.cos(a), math.sin(a)
    cx, cy, cfx, cfy = W / 2.0, H / 2.0, Wf / 2.0, Hf / 2.0
    # source = c + R(-a)·(frame − cf); frame = (x + U·kx, y + V·ky)
    coeffs = (cos * kx, sin * ky, cx + cos * (x - cfx) + sin * (y - cfy),
              -sin * kx, cos * ky, cy - sin * (x - cfx) + cos * (y - cfy))
    return img.transform((sw, sh), Image.Transform.AFFINE, coeffs,
                         resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))


# ── Encoding ────────────────────────────────────────────────────────────────

def _jpeg(img: Image.Image, quality: int, dpi: float) -> bytes:
    # Baseline (not progressive) JPEG: some embassy upload portals refuse
    # progressive files. optimize=True only shrinks the Huffman tables.
    buf = io.BytesIO()
    kw = {"quality": int(quality), "optimize": True, "dpi": (dpi, dpi)}
    if quality >= 90:
        kw["subsampling"] = 0
    img.save(buf, format="JPEG", **kw)
    return buf.getvalue()


def encode_jpeg(img: Image.Image, dpi: float = DEFAULT_DPI, max_kb: float = 0) -> tuple:
    """(bytes, quality, target_met). With *max_kb* the best quality that fits is
    searched (like the app's compress-to-KB) but the pixel size is never
    changed — official photos must keep their size."""
    if not max_kb or max_kb <= 0:
        return _jpeg(img, 95, dpi), 95, None
    target = int(float(max_kb) * 1024)
    lo, hi, best, smallest = 10, 95, None, None
    while lo <= hi:
        q = (lo + hi) // 2
        data = _jpeg(img, q, dpi)
        if len(data) <= target:
            best, lo = (data, q), q + 1
        else:
            hi = q - 1
            if smallest is None or len(data) < len(smallest[0]):
                smallest = (data, q)
    if best:
        return best[0], best[1], True
    for q in (8, 5, 3, 1):           # last resort before giving up
        data = _jpeg(img, q, dpi)
        if len(data) <= target:
            return data, q, True
        if len(data) < len(smallest[0]):
            smallest = (data, q)
    return smallest[0], smallest[1], False


def _write_new(data: bytes, want: str) -> str:
    save_path = want if not os.path.exists(want) else image_crop.unique_output_path(want, suffix="")
    for _ in range(1000):                       # exclusive create: never clobbers
        try:
            with open(save_path, "xb") as f:
                f.write(data)
            return save_path
        except FileExistsError:
            save_path = image_crop.unique_output_path(save_path, suffix="")
    raise OSError("Could not find a free file name")


def output_path_for(path: str, tag: str, suffix: str = "") -> str:
    stem = os.path.splitext(path)[0]
    return image_crop.unique_output_path(f"{stem}_{_slug(tag)}{suffix}.jpg", suffix="")


def _resolve_size(params: dict, native: tuple) -> tuple:
    """(px_w, px_h, dpi, tag) for the chosen preset / custom / free."""
    key = str(params.get("preset") or FREE_KEY)
    dpi = float(params.get("dpi") or DEFAULT_DPI)
    if key == FREE_KEY:
        return native[0], native[1], dpi, "photo"
    if key == CUSTOM_KEY:
        unit = str(params.get("unit") or "mm").lower()
        w, h = float(params.get("w") or 0), float(params.get("h") or 0)
        if w <= 0 or h <= 0:
            raise ValueError("Enter the custom width and height")
        pw, ph = to_px(w, unit, dpi), to_px(h, unit, dpi)
        if pw > 10000 or ph > 10000:
            raise ValueError("Custom size is too large")
        nice = (f"{w:g}x{h:g}{unit}")
        return pw, ph, dpi, nice
    p = find_preset(key)
    if not p:
        raise ValueError(f"Unknown photo size: {key}")
    return p["px_w"], p["px_h"], p["dpi"], p["tag"]


def render_photo(path: str, params: dict) -> dict:
    """Crop to the preset and save ``<stem>_<tag>.jpg``.

    params: preset, rect [x,y,w,h], rotate, angle, max_kb, (custom: w, h, unit, dpi),
            print_sheet (bool), copies (0 = as many as fit).
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    if os.path.splitext(path)[1].lower() not in image_crop.CROPPABLE_EXTS:
        raise ValueError("Not a supported image type")
    rotate = int(params.get("rotate") or 0)
    if rotate % 90:
        raise ValueError("Rotation must be a multiple of 90°")
    angle = float(params.get("angle") or 0)
    if abs(angle) > MAX_ANGLE + 1e-6:
        raise ValueError(f"Straighten is limited to ±{MAX_ANGLE:g}°")
    img = oriented(path, rotate)
    rect = params.get("rect")
    if not rect or len(rect) != 4:
        Wf, Hf = frame_size(img.width, img.height, angle)
        rect = [0, 0, Wf, Hf]
    cut = crop_straight(img, rect, angle)
    pw, ph, dpi, tag = _resolve_size(params, cut.size)
    if cut.size != (pw, ph):
        cut = cut.resize((pw, ph), Image.Resampling.LANCZOS)
    max_kb = float(params.get("max_kb") or 0)
    data, quality, met = encode_jpeg(cut, dpi, max_kb)
    out = _write_new(data, output_path_for(path, tag))
    res = {"out": out, "width": pw, "height": ph, "dpi": dpi, "bytes": len(data),
           "quality": quality, "target_met": met, "tag": tag}
    if params.get("print_sheet"):
        sheet = make_print_sheet(cut, dpi, int(params.get("copies") or 0))
        sdata, _, _ = encode_jpeg(sheet["image"], dpi, 0)
        res["sheet"] = _write_new(sdata, output_path_for(path, tag, "_4x6"))
        res["sheet_copies"] = sheet["copies"]
    return res


# ── Print sheet ─────────────────────────────────────────────────────────────

def sheet_layout(pw: int, ph: int, dpi: float = DEFAULT_DPI, copies: int = 0) -> dict:
    """Grid of pw×ph photos on a 4×6 in sheet (whichever orientation fits more)."""
    # Prefer a printer-safe edge and a 2 mm cutting gap; fall back to edge-to-edge
    # when that fits more (e.g. six 2×2 in photos fill a 6×4 in sheet exactly).
    roomy = (int(round(0.12 * dpi)), int(round(2 / MM_PER_IN * dpi)))
    best = None
    for margin, gap in (roomy, (0, 0)):
        for sw, sh in ((SHEET_IN[0], SHEET_IN[1]), (SHEET_IN[1], SHEET_IN[0])):
            SW, SH = int(round(sw * dpi)), int(round(sh * dpi))
            cols = max(0, (SW - 2 * margin + gap) // (pw + gap))
            rows = max(0, (SH - 2 * margin + gap) // (ph + gap))
            if best is None or cols * rows > best["cols"] * best["rows"]:
                best = {"W": SW, "H": SH, "cols": cols, "rows": rows, "gap": gap, "margin": margin}
    n = best["cols"] * best["rows"]
    if n < 1:
        raise ValueError("The photo is bigger than a 4×6 in sheet")
    if copies and copies > 0:
        n = min(n, int(copies))
    best["count"] = n
    return best


def make_print_sheet(photo: Image.Image, dpi: float = DEFAULT_DPI, copies: int = 0) -> dict:
    pw, ph = photo.size
    L = sheet_layout(pw, ph, dpi, copies)
    cols, rows, gap = L["cols"], L["rows"], L["gap"]
    used_rows = math.ceil(L["count"] / cols)
    gw = cols * pw + (cols - 1) * gap
    gh = rows * ph + (rows - 1) * gap
    x0, y0 = (L["W"] - gw) // 2, (L["H"] - gh) // 2
    sheet = Image.new("RGB", (L["W"], L["H"]), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    k = 0
    for r in range(used_rows):
        for c in range(cols):
            if k >= L["count"]:
                break
            x, y = x0 + c * (pw + gap), y0 + r * (ph + gap)
            sheet.paste(photo, (x, y))
            d.rectangle([x - 1, y - 1, x + pw, y + ph], outline=(200, 200, 200))  # cut line
            k += 1
    return {"image": sheet, "copies": k, "cols": cols, "rows": rows}


# ── Optional face detection (never installed by us) ─────────────────────────

def face_detector() -> str:
    """Name of an installed detector, or ""."""
    try:
        import cv2  # noqa: F401
        return "opencv" if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data") else ""
    except Exception:
        return ""


def face_box(path: str, rotate: int = 0) -> dict | None:
    """Largest face as {x, y, w, h} in px of the image turned *rotate*° (before
    straightening), or None. Needs OpenCV; returns None without it."""
    if not face_detector():
        return None
    import cv2
    import numpy as np
    img = oriented(path, rotate)
    scale = 1.0
    if max(img.size) > 1200:
        scale = 1200.0 / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    casc = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    faces = casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return {"x": x / scale, "y": y / scale, "w": w / scale, "h": h / scale}
