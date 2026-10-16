# -*- coding: utf-8 -*-
"""
image_crop.py — full-resolution, EXIF-aware image cropping for the Crop dialog.

The dialog previews a scaled-down copy (``crop_source``) and sends back a
rectangle in FULL-resolution pixels of the *oriented* image (EXIF rotation
applied, then the optional 90° steps the user chose in the dialog). The crop
is always written to a NEW file ``<name>_cropped<ext>`` (``_cropped (2)`` ...);
the original is never overwritten.
"""

from __future__ import annotations

import base64
import io
import os
import re

from PIL import Image, ImageOps

PREVIEW_MAX = 1600

_FMT_BY_EXT = {
    ".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG", ".png": "PNG",
    ".bmp": "BMP", ".webp": "WEBP", ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF",
}
CROPPABLE_EXTS = set(_FMT_BY_EXT)


def load_oriented(path: str) -> Image.Image:
    """Open *path* with its EXIF orientation applied (the way viewers show it)."""
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img.load()
    return img


def _rotate_cw(img: Image.Image, deg: int) -> Image.Image:
    deg = int(deg or 0) % 360
    if deg == 90:
        return img.transpose(Image.Transpose.ROTATE_270)   # PIL ROTATE_* is counter-clockwise
    if deg == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if deg == 270:
        return img.transpose(Image.Transpose.ROTATE_90)
    return img


def unique_output_path(path: str, suffix: str = "_cropped", ext: str = "") -> str:
    """``<stem><suffix><ext>`` next to *path*, numbered `` (2)``, `` (3)`` ... if taken."""
    base, own_ext = os.path.splitext(path)
    ext = ext or own_ext or ".png"
    stem = f"{base}{suffix}"
    m = re.match(r"^(.*) \((\d+)\)$", stem)
    if m and not suffix:
        stem = m.group(1)
    cand = f"{stem}{ext}"
    n = 2
    while os.path.exists(cand):
        cand = f"{stem} ({n}){ext}"
        n += 1
    return cand


def crop_source(path: str, max_dim: int = PREVIEW_MAX) -> dict:
    """Preview + true size for the crop dialog.

    Returns {width, height, preview_w, preview_h, data_url, format, dpi}. ``width``/
    ``height`` are the full-resolution size AFTER EXIF orientation.
    """
    img = load_oriented(path)
    w, h = img.size
    fmt = (img.format or _FMT_BY_EXT.get(os.path.splitext(path)[1].lower(), "")).upper()
    dpi = img.info.get("dpi")
    prev = img
    if prev.mode not in ("RGB", "RGBA", "L"):
        prev = prev.convert("RGBA" if "A" in prev.mode or prev.mode == "P" else "RGB")
    if max(w, h) > max_dim:
        r = max_dim / float(max(w, h))
        prev = prev.resize((max(1, round(w * r)), max(1, round(h * r))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if prev.mode == "RGBA":
        prev.save(buf, format="PNG")
        mime = "png"
    else:
        prev.convert("RGB").save(buf, format="JPEG", quality=88)
        mime = "jpeg"
    return {
        "width": w, "height": h,
        "preview_w": prev.width, "preview_h": prev.height,
        "data_url": f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode(),
        "format": fmt,
        "dpi": [round(float(dpi[0]), 2), round(float(dpi[1]), 2)] if dpi else None,
    }


def crop_image(path: str, x: int, y: int, w: int, h: int,
               rotate: int = 0, out_path: str = "",
               out_w: int = 0, out_h: int = 0, dpi: int = 0, max_kb: int = 0) -> tuple:
    """Crop (after EXIF orientation and *rotate* degrees clockwise) and save a new file.

    (x, y, w, h) are full-resolution pixels of the oriented+rotated image and
    are clamped to its bounds. With *out_w*/*out_h* (an official photo size)
    the crop is resized to exactly that many pixels and tagged with *dpi*;
    *max_kb* lowers JPEG quality until the file fits (upload limits).
    Returns (True, saved_path) or (False, message).
    """
    try:
        if not path or not os.path.isfile(path):
            return False, f"File not found: {path}"
        src_ext = os.path.splitext(path)[1].lower()
        if src_ext not in CROPPABLE_EXTS:
            return False, f"Not a supported image type: {src_ext or '(none)'}"
        if int(rotate or 0) % 90:
            return False, "Rotation must be a multiple of 90°"
        img = load_oriented(path)
        img = _rotate_cw(img, rotate)
        W, H = img.size
        x0 = max(0, min(int(round(x)), W))
        y0 = max(0, min(int(round(y)), H))
        x1 = max(0, min(int(round(x + w)), W))
        y1 = max(0, min(int(round(y + h)), H))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return False, "Crop area is empty"

        out = img.crop((x0, y0, x1, y1))
        out_w, out_h, dpi, max_kb = int(out_w or 0), int(out_h or 0), int(dpi or 0), int(max_kb or 0)
        if out_w > 0 and out_h > 0:
            if out_w > 10000 or out_h > 10000:
                return False, "Output size is too large"
            out = out.resize((out_w, out_h), Image.LANCZOS)

        # Output path — never the original, never an existing file.
        if out_path and out_path.strip():
            cand = os.path.abspath(out_path.strip())
            # the source always exists, so this also covers out_path == path
            save_path = unique_output_path(cand, suffix="") if os.path.exists(cand) else cand
        else:
            save_path = unique_output_path(path)
        ext = os.path.splitext(save_path)[1].lower()
        fmt = _FMT_BY_EXT.get(ext)
        if not fmt:
            save_path = unique_output_path(os.path.splitext(save_path)[0] + ".png", suffix="")
            fmt = "PNG"

        kw: dict = {}
        icc = img.info.get("icc_profile")
        if icc and fmt in ("JPEG", "PNG", "WEBP", "TIFF"):
            kw["icc_profile"] = icc
        src_dpi = (dpi, dpi) if dpi > 0 else img.info.get("dpi")
        if src_dpi and fmt in ("JPEG", "PNG", "TIFF"):
            kw["dpi"] = src_dpi
        if fmt == "JPEG":
            if out.mode in ("RGBA", "LA", "P"):
                rgba = out.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                out = bg
            elif out.mode not in ("RGB", "L", "CMYK"):
                out = out.convert("RGB")
            kw.update(quality=95, subsampling=0)
            exif = img.getexif()
            if exif:
                exif[0x0112] = 1          # pixels are already upright
                kw["exif"] = exif.tobytes()
        elif fmt == "WEBP":
            kw.update(quality=95)
        elif fmt == "GIF" and out.mode not in ("P", "L"):
            out = out.convert("P", palette=Image.Palette.ADAPTIVE)
        elif fmt == "BMP" and out.mode not in ("RGB", "L", "P", "1"):
            out = out.convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format=fmt, **kw)
        if max_kb > 0 and fmt == "JPEG":
            q = kw.get("quality", 95)
            while buf.tell() > max_kb * 1024 and q > 30:
                q -= 5
                kw.update(quality=q, subsampling=2)
                buf = io.BytesIO()
                out.save(buf, format=fmt, **kw)
            if buf.tell() > max_kb * 1024:
                return False, f"Could not get the photo under {max_kb} KB"
        for _ in range(1000):                   # exclusive create: never clobbers
            try:
                with open(save_path, "xb") as f:
                    f.write(buf.getvalue())
                return True, save_path
            except FileExistsError:
                save_path = unique_output_path(save_path, suffix="")
        return False, "Could not find a free file name"
    except Exception as exc:
        return False, str(exc)
