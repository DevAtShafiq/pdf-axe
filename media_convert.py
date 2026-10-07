"""
media_convert.py — image <-> PDF conversion and PDF / image compression.

Pure functions (no UI, no pywebview) used by sfm_bridge.SFMBridge:

    images_to_pdf(paths, out_path, page_size, orientation, margin_mm, quality, order)
    pdf_to_images(path, fmt, dpi, pages, out_dir, quality)
    convert_image(path, fmt, quality, out_path)
    compress_pdf(path, preset, out_path, target_kb, save_smallest, progress)
    compress_image(path, quality, max_edge, fmt, out_path, target_bytes, save_smallest)

Rules shared by every function here:
  * The source file is never modified.
  * An existing file is never overwritten: output names get "-2", "-3"... .
  * Results are built in memory and only written when they are worth keeping
    (a "compressed" file that came out bigger is never written), so nothing
    has to be cleaned up afterwards.
  * Failures raise ConvertError with a message meant for the user.

Optional HEIC/HEIF support: used automatically when the ``pillow_heif``
plugin is installed, otherwise HEIC files give a clear error.
"""
from __future__ import annotations

import io
import os
import re
from typing import Callable, Iterable

import file_ops as _fo

ProgressCb = Callable[[int, int, str], None]   # (done, total, label)


class ConvertError(RuntimeError):
    """User-facing conversion / compression failure."""


# ── formats ──────────────────────────────────────────────────────────────────

HEIC_EXT = {".heic", ".heif"}
IMAGE_EXT = set(_fo.IMAGE_EXT) | HEIC_EXT

# key -> (Pillow format, extension)
IMG_FORMATS = {
    "jpg": ("JPEG", ".jpg"), "jpeg": ("JPEG", ".jpg"),
    "png": ("PNG", ".png"),
    "webp": ("WEBP", ".webp"),
    "bmp": ("BMP", ".bmp"),
    "tif": ("TIFF", ".tiff"), "tiff": ("TIFF", ".tiff"),
}
PDF_IMAGE_FORMATS = ("png", "jpg", "webp")
PDF_DPI_CHOICES = (72, 96, 150, 200, 300)

# Points (1/72 in).  "fit" = page takes the image's own size.
PAGE_SIZES = {
    "fit": None,
    "a4": (595.28, 841.89),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
}

# PDF compression presets.  dpi_threshold/dpi_target drive image
# downsampling (images above the threshold are resampled to the target);
# quality is the JPEG quality used when images are recompressed.
PDF_PRESETS = {
    "screen":   {"label": "Smallest (screen, 72 dpi)",  "dpi_threshold": 100, "dpi_target": 72,  "quality": 40},
    "ebook":    {"label": "Balanced (ebook, 150 dpi)",  "dpi_threshold": 170, "dpi_target": 150, "quality": 60},
    "printer":  {"label": "High quality (print, 300 dpi)", "dpi_threshold": 330, "dpi_target": 300, "quality": 80},
    "lossless": {"label": "Lossless (clean-up only)",   "dpi_threshold": 0,   "dpi_target": 0,   "quality": 0},
}
_PRESET_ALIASES = {
    "low": "screen", "small": "screen", "smallest": "screen",
    "medium": "ebook", "balanced": "ebook", "default": "ebook",
    "high": "printer", "print": "printer",
    "prepress": "lossless", "max": "lossless", "maximum": "lossless",
}


def capabilities() -> dict:
    """What this machine can do (reported to the UI)."""
    return {
        "heic": heic_available(),
        "pdf": _fo.get_fitz() is not None,
        "pillow": _fo.get_pillow() is not None,
        "image_exts": sorted(IMAGE_EXT),
        "pdf_presets": {k: v["label"] for k, v in PDF_PRESETS.items()},
        "page_sizes": list(PAGE_SIZES),
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _pil():
    Image = _fo.get_pillow()
    if not Image:
        raise ConvertError("Pillow is not installed")
    return Image


def _fitz():
    fitz = _fo.get_fitz()
    if not fitz:
        raise ConvertError("PyMuPDF is not installed")
    return fitz


_heic_state: bool | None = None


def heic_available() -> bool:
    """Register the pillow_heif opener once; True when HEIC can be read."""
    global _heic_state
    if _heic_state is None:
        try:
            import pillow_heif  # type: ignore
            pillow_heif.register_heif_opener()
            _heic_state = True
        except Exception:
            _heic_state = False
    return _heic_state


def _ext(path: str) -> str:
    return os.path.splitext(path or "")[1].lower()


def is_image(path: str) -> bool:
    return _ext(path) in IMAGE_EXT


def unique_path(path: str) -> str:
    """``path`` if free, else ``stem-2.ext``, ``stem-3.ext``..."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while True:
        cand = f"{base}-{n}{ext}"
        if not os.path.exists(cand):
            return cand
        n += 1


def unique_dir(path: str) -> str:
    """``path`` if free, else ``path-2``, ``path-3``..."""
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists(f"{path}-{n}"):
        n += 1
    return f"{path}-{n}"


def _write_new(path: str, data: bytes) -> str:
    """Write ``data`` to a path that does not exist yet (exclusive create)."""
    path = unique_path(os.path.abspath(path))
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    while True:
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:          # lost a race — pick the next name
            path = unique_path(path)


def _natural_key(path: str):
    name = os.path.basename(path).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def fmt_size(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _open_image(path: str):
    """Open an image with a clear message for missing files / HEIC."""
    Image = _pil()
    if not os.path.isfile(path):
        raise ConvertError(f"File not found: {os.path.basename(path)}")
    ext = _ext(path)
    if ext in HEIC_EXT and not heic_available():
        raise ConvertError(
            "HEIC/HEIF photos need the 'pillow-heif' plugin, which is not installed. "
            "Convert the photo to JPG on the phone/PC first, or install pillow-heif.")
    try:
        return Image.open(path)
    except Exception as exc:
        raise ConvertError(f"Cannot read image {os.path.basename(path)}: {exc}") from exc


def _flatten(img, bg=(255, 255, 255)):
    """Any mode -> RGB (or L for plain grayscale), transparency composited on white."""
    Image = _pil()
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, bg)
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    if img.mode in ("RGB", "L"):
        return img
    if img.mode in ("1", "I;16", "I;16B", "I;16L", "I"):
        return img.convert("L")
    return img.convert("RGB")


def _iter_frames(path: str):
    """Yield (frame, dpi) for every frame/page of an image, EXIF-rotated."""
    from PIL import ImageOps, ImageSequence
    im = _open_image(path)
    try:
        dpi = im.info.get("dpi")
        n = getattr(im, "n_frames", 1) or 1
        frames = ImageSequence.Iterator(im) if n > 1 else [im]
        for fr in frames:
            out = ImageOps.exif_transpose(fr) if n == 1 else fr.copy()
            out.load()
            yield out, dpi
    finally:
        im.close()


def _load_oriented(path: str):
    """First frame, EXIF orientation applied. Returns (image, dpi)."""
    from PIL import ImageOps
    im = _open_image(path)
    try:
        dpi = im.info.get("dpi")
        out = ImageOps.exif_transpose(im)
        out.load()
        if out is im:
            out = im.copy()
        return out, dpi
    finally:
        im.close()


def _encode(img, pil_fmt: str, quality: int, dpi=None) -> bytes:
    buf = io.BytesIO()
    _fo._save_image_as(img, buf, pil_fmt, int(quality), dpi)
    return buf.getvalue()


def _sane_dpi(dpi) -> float:
    try:
        v = float(dpi[0] if isinstance(dpi, (tuple, list)) else dpi)
        if 50 <= v <= 1200:
            return v
    except Exception:
        pass
    return 96.0


# ── image(s) -> PDF ──────────────────────────────────────────────────────────

def _frame_stream(img, quality: int, src_path: str, single_frame_untouched: bool):
    """Bytes to embed in the PDF for one frame."""
    # "Original" quality: embed an untouched JPEG as-is (no re-encode).
    if (quality >= 100 and single_frame_untouched
            and _ext(src_path) in (".jpg", ".jpeg", ".jfif")
            and img.mode in ("RGB", "L")):
        with open(src_path, "rb") as f:
            return f.read()
    flat = _flatten(img)
    buf = io.BytesIO()
    if quality >= 100:
        flat.save(buf, format="PNG", optimize=False)
    else:
        flat.save(buf, format="JPEG", quality=max(10, min(95, int(quality))),
                  optimize=True)
    return buf.getvalue()


def images_to_pdf(paths: Iterable[str], out_path: str = "", page_size: str = "fit",
                  orientation: str = "auto", margin_mm: float = 0,
                  quality: int = 85, order: str = "selection",
                  progress: ProgressCb | None = None) -> dict:
    """Combine images (all frames of multi-page TIFF/GIF) into one PDF.

    page_size   : "fit" (page = image size), "a4", "letter", "legal".
    orientation : "auto" (follows each image), "portrait", "landscape".
    margin_mm   : white border around each image.
    quality     : JPEG quality 10-95; 100 = original/lossless (no recompression).
    order       : "selection" keeps the given order, "name" sorts naturally.
    Returns {"out", "pages", "images", "skipped": [{"path","error"}], "size"}.
    """
    fitz = _fitz()
    _pil()
    paths = [p for p in (paths or []) if p]
    if not paths:
        raise ConvertError("No images selected")
    if order == "name":
        paths = sorted(paths, key=_natural_key)
    key = (page_size or "fit").lower()
    if key not in PAGE_SIZES:
        raise ConvertError(f"Unknown page size: {page_size}")
    margin = max(0.0, min(float(margin_mm or 0), 50.0)) * 72.0 / 25.4
    quality = int(quality or 85)

    if not out_path:
        first = paths[0]
        stem = os.path.splitext(os.path.basename(first))[0]
        name = stem + (".pdf" if len(paths) == 1 else "_combined.pdf")
        out_path = os.path.join(os.path.dirname(first), name)

    doc = fitz.open()
    skipped, used = [], 0
    total = len(paths)
    try:
        for i, p in enumerate(paths):
            if progress:
                progress(i, total, os.path.basename(p))
            if not is_image(p):
                skipped.append({"path": p, "error": "Not an image"})
                continue
            try:
                frames = list(_iter_frames(p))
            except ConvertError as exc:
                skipped.append({"path": p, "error": str(exc)})
                continue
            except Exception as exc:
                skipped.append({"path": p, "error": f"Cannot read image: {exc}"})
                continue
            untouched = len(frames) == 1 and _exif_orientation(p) in (0, 1)
            for img, dpi in frames:
                w_px, h_px = img.size
                if key == "fit":
                    scale = 72.0 / _sane_dpi(dpi)
                    pw, ph = w_px * scale + 2 * margin, h_px * scale + 2 * margin
                else:
                    pw, ph = PAGE_SIZES[key]
                    landscape = (orientation == "landscape"
                                 or (orientation == "auto" and w_px > h_px))
                    if landscape:
                        pw, ph = ph, pw
                page = doc.new_page(width=pw, height=ph)
                box_w, box_h = pw - 2 * margin, ph - 2 * margin
                s = min(box_w / w_px, box_h / h_px)
                iw, ih = w_px * s, h_px * s
                x0, y0 = (pw - iw) / 2, (ph - ih) / 2
                rect = fitz.Rect(x0, y0, x0 + iw, y0 + ih)
                page.insert_image(rect, stream=_frame_stream(img, quality, p, untouched))
            used += 1
        if progress:
            progress(total, total, "Saving PDF")
        if used == 0:
            reasons = "; ".join(f"{os.path.basename(s['path'])}: {s['error']}" for s in skipped[:3])
            raise ConvertError("No image could be converted" + (f" — {reasons}" if reasons else ""))
        data = doc.tobytes(garbage=3, deflate=True)
        pages = doc.page_count
    finally:
        doc.close()
    final = _write_new(out_path, data)
    return {"out": final, "pages": pages, "images": used,
            "skipped": skipped, "size": len(data)}


def _exif_orientation(path: str) -> int:
    try:
        im = _open_image(path)
        try:
            return int(im.getexif().get(0x0112, 0) or 0)
        finally:
            im.close()
    except Exception:
        return 0


def image_to_pdf_each(paths: Iterable[str], **opts) -> list:
    """One PDF per image (beside each image). Returns per-file result dicts."""
    out = []
    for p in paths or []:
        try:
            stem = os.path.splitext(p)[0]
            r = images_to_pdf([p], out_path=stem + ".pdf", **opts)
            out.append({"path": p, "ok": True, **r})
        except Exception as exc:
            out.append({"path": p, "ok": False, "error": str(exc)})
    return out


# ── PDF -> images ────────────────────────────────────────────────────────────

def parse_pages(spec: str, page_count: int) -> list[int]:
    """'1-3,5' -> [0,1,2,4] (0-based). Empty / 'all' -> every page."""
    spec = (spec or "").strip().lower()
    if not spec or spec == "all":
        return list(range(page_count))
    ok, res = _fo.parse_pdf_page_range_input(spec, page_count)
    if not ok:
        raise ConvertError(str(res))
    return list(res)


def pdf_to_images(path: str, fmt: str = "png", dpi: int = 150, pages: str = "",
                  out_dir: str = "", quality: int = 90,
                  progress: ProgressCb | None = None) -> dict:
    """Render PDF pages to image files in a new ``<name>_images`` folder.

    Returns {"out_dir", "files", "pages"}.
    """
    fitz = _fitz()
    Image = _pil()
    if not os.path.isfile(path):
        raise ConvertError(f"File not found: {os.path.basename(path)}")
    key = (fmt or "png").lower().lstrip(".")
    if key == "jpeg":
        key = "jpg"
    if key not in PDF_IMAGE_FORMATS:
        raise ConvertError(f"Unsupported image format: {fmt}")
    pil_fmt, ext = IMG_FORMATS[key]
    dpi = int(dpi or 150)
    if not 36 <= dpi <= 600:
        raise ConvertError("DPI must be between 36 and 600")
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ConvertError(f"Cannot open PDF: {exc}") from exc
    try:
        if doc.needs_pass:
            raise ConvertError("This PDF is password-protected")
        wanted = parse_pages(pages, doc.page_count)
        stem = os.path.splitext(os.path.basename(path))[0]
        if not out_dir:
            out_dir = unique_dir(os.path.join(os.path.dirname(os.path.abspath(path)),
                                              stem + "_images"))
        os.makedirs(out_dir, exist_ok=True)
        width = max(2, len(str(doc.page_count)))
        files = []
        total = len(wanted)
        for i, pno in enumerate(wanted):
            if progress:
                progress(i, total, f"Page {pno + 1}")
            pix = doc[pno].get_pixmap(dpi=dpi, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            data = _encode(img, pil_fmt, quality if pil_fmt != "PNG" else 95, (dpi, dpi))
            name = f"{stem}_p{pno + 1:0{width}d}{ext}"
            files.append(_write_new(os.path.join(out_dir, name), data))
        if progress:
            progress(total, total, "Done")
        return {"out_dir": out_dir, "files": files, "pages": total}
    finally:
        doc.close()


# ── image format conversion ──────────────────────────────────────────────────

def convert_image(path: str, fmt: str, quality: int = 92, out_path: str = "") -> dict:
    """Convert an image to jpg/png/webp/bmp/tiff beside the original.

    Transparency is flattened onto white for formats without alpha.
    Returns {"out", "before", "after"}.
    """
    key = (fmt or "").lower().lstrip(".")
    if key not in IMG_FORMATS:
        raise ConvertError(f"Unsupported format: {fmt}")
    pil_fmt, ext = IMG_FORMATS[key]
    img, dpi = _load_oriented(path)
    if out_path:
        if os.path.abspath(out_path) == os.path.abspath(path):
            raise ConvertError("Refusing to overwrite the original image")
    else:
        out_path = os.path.splitext(path)[0] + ext
    q = max(10, min(95, int(quality or 92)))
    data = _encode(img, pil_fmt, q, dpi)
    final = _write_new(out_path, data)
    return {"out": final, "before": os.path.getsize(path), "after": len(data)}


# ── image compression ────────────────────────────────────────────────────────

def _fit_to_target(img, pil_fmt: str, target: int, dpi):
    """Find the best (largest) encoding of ``img`` that fits in ``target`` bytes.

    Binary-searches the quality (95 -> 10); if even quality 10 is too big the
    image is scaled down by 15% steps.  Returns (bytes, quality, size, met).
    """
    Image = _pil()
    cur = img
    smallest = None
    for _ in range(12):
        lo, hi, best = 10, 95, None
        while lo <= hi:
            q = (lo + hi) // 2
            data = _encode(cur, pil_fmt, q, dpi)
            if len(data) <= target:
                best = (data, q)
                lo = q + 1
            else:
                hi = q - 1
                if smallest is None or len(data) < len(smallest[0]):
                    smallest = (data, q, cur.size)
        if best:
            return best[0], best[1], cur.size, True
        if min(cur.size) <= 160:
            break
        w, h = cur.size
        cur = cur.resize((max(1, int(w * 0.85)), max(1, int(h * 0.85))), Image.LANCZOS)
    data, q, size = smallest
    return data, q, size, False


def _pick_output(path: str, fmt: str, target: bool):
    """Return (pil_fmt, ext, note) for compress_image."""
    src_ext = _ext(path)
    src_key = src_ext.lstrip(".")
    key = (fmt or "").lower().lstrip(".")
    note = ""
    if key:
        if key not in ("jpg", "jpeg", "webp", "png"):
            raise ConvertError(f"Unsupported output format: {fmt}")
        pil_fmt, ext = IMG_FORMATS[key]
        if src_key in ("jpg", "jpeg") and pil_fmt == "JPEG":
            ext = src_ext
    elif src_key in ("jpg", "jpeg", "png", "webp"):
        pil_fmt, ext = IMG_FORMATS[src_key][0], src_ext
    else:
        pil_fmt, ext = "JPEG", ".jpg"   # BMP/TIFF/GIF/HEIC compress best as JPEG
    if target and pil_fmt == "PNG":
        pil_fmt, ext = "JPEG", ".jpg"
        note = "Saved as JPG to reach the target (PNG has no quality setting)"
    return pil_fmt, ext, note


def compress_image(path: str, quality: int = 70, max_edge: int = 0, fmt: str = "",
                   out_path: str = "", target_bytes: int = 0,
                   save_smallest: bool = True, dry_run: bool = False) -> dict:
    """Re-encode an image smaller; the original is never touched.

    target_bytes > 0 searches for the best quality that fits under that size
    (shrinking the image if needed) and ignores ``quality``.
    When nothing changes but the encoding and the result is not smaller,
    no file is written and ``kept_original`` is True.
    With a target: an original already under the target is kept as it is,
    a result that is not smaller than the original is never written, and when
    the target cannot be reached the smallest version is written only if
    ``save_smallest`` (``can_save_smallest`` / ``smallest`` tell the caller).
    Returns {"out", "before", "after", "quality", "size", "kept_original",
             "target_met", "note", "target_bytes", "smallest",
             "can_save_smallest", "format_changed"}.
    ``dry_run`` does everything in memory and writes nothing ("out" is "",
    "after" is the size the copy would have).
    """
    Image = _pil()
    if not os.path.isfile(path):
        raise ConvertError(f"File not found: {os.path.basename(path)}")
    target = max(0, int(target_bytes or 0))
    quality = max(1, min(95, int(quality or 70)))
    max_edge = max(0, int(max_edge or 0))
    pil_fmt, ext, note = _pick_output(path, fmt, bool(target))
    before = os.path.getsize(path)

    if out_path and os.path.abspath(out_path) == os.path.abspath(path):
        raise ConvertError("Refusing to overwrite the original image")

    img, dpi = _load_oriented(path)
    orig_size = img.size
    if max_edge and max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)

    same_kind = ext.lower() == _ext(path) or (
        pil_fmt == "JPEG" and _ext(path) in (".jpg", ".jpeg"))
    resized = img.size != orig_size
    extra = {"target_bytes": target, "smallest": None, "can_save_smallest": False,
             "format_changed": not same_kind}

    def _kept(note_, met=None, after=before):
        return {"out": "", "before": before, "after": after, "quality": quality,
                "size": list(orig_size), "kept_original": True, "target_met": met,
                "note": note_, **extra}

    target_met = None
    if target:
        tlabel = fmt_short(target)
        if before <= target and not resized and not fmt:
            return _kept(f"Already under {tlabel} — the original was kept", True)
        data, quality, _, target_met = _fit_to_target(img, pil_fmt, target, dpi)
        extra["smallest"] = len(data)
        extra["can_save_smallest"] = len(data) < before
        if len(data) >= before and not resized and not fmt:
            # Never write a copy bigger than the original.
            if before <= target:
                return _kept(f"Already under {tlabel} — the original was kept", True)
            return _kept(f"Could not reach {tlabel} — the original ({fmt_short(before)}) "
                         "is already the smallest version", False)
        if not target_met:
            msg = (f"Could not reach {tlabel} — smallest possible is "
                   f"{fmt_short(len(data))}")
            if not save_smallest:
                return _kept(msg, False, len(data))
            msg += " — saved the smallest version"
            note = f"{note}. {msg}" if note else msg
    else:
        data = _encode(img, pil_fmt, quality, dpi)

    if not target and same_kind and not resized and len(data) >= before:
        return _kept("Already well compressed — the original was kept")

    with Image.open(io.BytesIO(data)) as chk:
        size = list(chk.size)
    if dry_run:
        return {"out": "", "before": before, "after": len(data), "quality": quality,
                "size": size, "kept_original": False, "target_met": target_met,
                "note": note, "dry_run": True, **extra}
    if not out_path:
        out_path = os.path.splitext(path)[0] + "_compressed" + ext
    final = _write_new(out_path, data)
    return {"out": final, "before": before, "after": len(data), "quality": quality,
            "size": size, "kept_original": False, "target_met": target_met,
            "note": note, **extra}


# ── PDF compression ──────────────────────────────────────────────────────────

def resolve_preset(preset: str) -> str:
    """Preset name / alias -> key.  "level-N" (1..len(PDF_TARGET_LADDER)-1)
    picks a step of the target-size ladder: the "Custom level" slider."""
    key = (preset or "ebook").lower().strip()
    key = _PRESET_ALIASES.get(key, key)
    m = re.fullmatch(r"level-(\d+)", key)
    if m and 1 <= int(m.group(1)) < len(PDF_TARGET_LADDER):
        return f"level-{int(m.group(1))}"
    if key not in PDF_PRESETS:
        raise ConvertError(f"Unknown compression preset: {preset}")
    return key


def preset_label(key: str) -> str:
    if key.startswith("level-"):
        return "Custom level (" + _step_label(PDF_TARGET_LADDER[int(key[6:])]) + ")"
    return PDF_PRESETS[key]["label"]


def _preset_bytes(src: bytes, key: str, images: list, cache: dict | None = None) -> bytes:
    """Compressed bytes of an in-memory PDF for a preset / level key."""
    if key.startswith("level-"):
        return _ladder_pass(src, PDF_TARGET_LADDER[int(key[6:])], images,
                            cache if cache is not None else {})
    cfg = PDF_PRESETS[key]
    return _pdf_pass(src, cfg["dpi_threshold"], cfg["dpi_target"], cfg["quality"])


def _rewrite_images_fallback(doc, threshold: int, target: int, quality: int) -> None:
    """Manual image downsampling for PyMuPDF versions without rewrite_images."""
    fitz = _fitz()
    Image = _pil()
    done = set()
    for page in doc:
        for info in page.get_images(full=True):
            xref, smask = info[0], info[1]
            if xref in done or smask:
                continue
            done.add(xref)
            try:
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                rw = max(r.width for r in rects) / 72.0
                if rw <= 0:
                    continue
                eff_dpi = pix.width / rw
                mode = "L" if pix.n - pix.alpha == 1 else "RGB"
                if pix.alpha:
                    pix = fitz.Pixmap(pix, 0)
                img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                if eff_dpi > threshold:
                    s = target / eff_dpi
                    img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                                     Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                page.replace_image(xref, stream=buf.getvalue())
            except Exception:
                continue


def fmt_short(n: int) -> str:
    """Compact size for messages: "340 KB", "1.2 MB", "2 MB"."""
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    mb = n / (1024 * 1024)
    s = f"{mb:.1f}".rstrip("0").rstrip(".")
    return f"{s} MB"


# Settings tried when compressing to a target size, best quality first.
# None = clean-up only (images untouched); (dpi, jpeg_quality) otherwise.
# The named presets sit on this ladder: printer (300, 80), ebook (150, 60),
# screen (72, 40).
PDF_TARGET_LADDER = [
    None,
    (300, 85), (300, 80), (250, 80), (200, 80), (200, 70), (170, 70),
    (150, 70), (150, 60), (135, 60), (120, 55), (110, 55), (96, 55),
    (96, 45), (85, 45), (72, 40), (72, 35), (60, 35), (60, 30), (50, 30),
]
# Decoded images are kept between attempts while they fit in this budget.
_DECODE_CACHE_PIXELS = 60_000_000

# Most recent "target not reachable" results, kept in memory so that
# "Save smallest anyway" does not have to run the search again.
_PDF_PENDING: dict = {}
_PDF_PENDING_MAX = 3


def _pending_key(path: str, target: int):
    st = os.stat(path)
    return (os.path.abspath(path).lower(), st.st_mtime_ns, st.st_size, int(target))


def _pdf_pass(src: bytes, threshold: int, target: int, quality: int) -> bytes:
    """One compression pass over an in-memory PDF; returns the new bytes.
    quality == 0 leaves images untouched (clean-up only)."""
    fitz = _fitz()
    doc = fitz.open(stream=src, filetype="pdf")
    try:
        if quality:
            if hasattr(doc, "rewrite_images"):
                doc.rewrite_images(dpi_threshold=threshold, dpi_target=target,
                                   quality=quality, bitonal=False)
            else:
                _rewrite_images_fallback(doc, threshold, target, quality)
        try:
            doc.subset_fonts()
        except Exception:
            pass
        save_kw = dict(garbage=4, deflate=True, deflate_images=True,
                       deflate_fonts=True, clean=True)
        try:
            return doc.tobytes(use_objstms=1, **save_kw)
        except TypeError:
            return doc.tobytes(**save_kw)
    finally:
        doc.close()


def _decode_image(doc, xref):
    """PDF image xref -> Pillow image (RGB or L), or None when not supported."""
    fitz = _fitz()
    Image = _pil()
    pix = fitz.Pixmap(doc, xref)
    if pix.n - pix.alpha >= 4:                 # CMYK etc.
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    if pix.n not in (1, 3):
        return None
    mode = "L" if pix.n == 1 else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _pdf_pass_exact(src: bytes, dpi: int, quality: int, images: list,
                    cache: dict) -> bytes:
    """One target-size attempt: every raster image shown above ``dpi`` is
    resampled to exactly ``dpi`` and re-encoded as JPEG ``quality``.  An image
    is only replaced when its new stream is smaller than the old one, and
    images with a transparency mask are left alone."""
    fitz = _fitz()
    Image = _pil()
    doc = fitz.open(stream=src, filetype="pdf")
    try:
        for im in images:
            xref = im["xref"]
            try:
                img = cache.get(xref)
                if img is None:
                    img = _decode_image(doc, xref)
                    if img is None:
                        continue
                    if cache.get("_px", 0) + img.width * img.height <= _DECODE_CACHE_PIXELS:
                        cache[xref] = img
                        cache["_px"] = cache.get("_px", 0) + img.width * img.height
                eff = img.width / im["width_in"]
                out = img
                if eff > dpi * 1.05:
                    s = dpi / eff
                    out = img.resize((max(1, round(img.width * s)),
                                      max(1, round(img.height * s))), Image.LANCZOS)
                buf = io.BytesIO()
                out.save(buf, format="JPEG", quality=int(quality), optimize=True)
                data = buf.getvalue()
                if len(data) < im["raw_len"]:
                    doc[im["pno"]].replace_image(xref, stream=data)
            except Exception:
                continue
        try:
            doc.subset_fonts()
        except Exception:
            pass
        save_kw = dict(garbage=4, deflate=True, deflate_images=True,
                       deflate_fonts=True, clean=True)
        try:
            return doc.tobytes(use_objstms=1, **save_kw)
        except TypeError:
            return doc.tobytes(**save_kw)
    finally:
        doc.close()


def _ladder_pass(src: bytes, step, images: list, cache: dict) -> bytes:
    if step is None or not images:
        return _pdf_pass(src, 0, 0, 0)
    return _pdf_pass_exact(src, step[0], step[1], images, cache)


def _step_label(step) -> str:
    return "Clean-up only" if step is None else f"{step[0]} dpi, quality {step[1]}"


def _scan_images(doc) -> list:
    """Raster images without a transparency mask, once each (see _read_pdf)."""
    images: dict = {}
    for page in doc:
        try:
            infos = page.get_images(full=True)
        except Exception:
            continue
        for info in infos:
            xref, smask = info[0], info[1]
            if smask:
                continue
            try:
                rects = page.get_image_rects(xref)
                w = max((r.width for r in rects), default=0) / 72.0
                if w <= 0:
                    continue
                cur = images.get(xref)
                if cur is None:
                    images[xref] = {"xref": xref, "pno": page.number, "width_in": w,
                                    "raw_len": len(doc.xref_stream_raw(xref) or b"")}
                elif w > cur["width_in"]:
                    cur["width_in"] = w
            except Exception:
                continue
    return list(images.values())


def _read_pdf(path: str):
    """Validate a PDF and return (bytes, images).

    ``images`` lists each raster image without a transparency mask once:
    {"xref", "pno" (a page showing it), "width_in" (widest display width in
    inches), "raw_len" (current stream size)}.  ``len(images)`` may be 0 for
    text/vector-only PDFs."""
    fitz = _fitz()
    if not os.path.isfile(path):
        raise ConvertError(f"File not found: {os.path.basename(path)}")
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ConvertError(f"Cannot open PDF: {exc}") from exc
    try:
        if doc.needs_pass:
            raise ConvertError("This PDF is password-protected")
        if not doc.is_pdf:
            raise ConvertError("Not a PDF file")
        images = _scan_images(doc)
    finally:
        doc.close()
    with open(path, "rb") as f:
        return f.read(), images


def _search_target(src: bytes, target: int, images: list,
                   progress: ProgressCb | None):
    """Find the best-quality ladder step whose output fits in ``target`` bytes.

    Tries clean-up only first, then the strongest step (if that does not fit
    nothing will), then binary-searches the ladder in between.
    Returns (data, step, met, attempts, smallest_data, smallest_step).
    """
    ladder = PDF_TARGET_LADDER if images else PDF_TARGET_LADDER[:1]
    cache: dict = {}
    n = len(ladder)
    est_total = 1 if n == 1 else 2 + max(1, (n - 2).bit_length())
    tried: dict[int, bytes] = {}

    def attempt(i: int) -> bytes:
        if i not in tried:
            if progress:
                progress(len(tried), est_total, f"Trying {_step_label(ladder[i])}…")
            tried[i] = _ladder_pass(src, ladder[i], images, cache)
            if progress:
                progress(len(tried), max(est_total, len(tried)),
                         f"{_step_label(ladder[i])} → {fmt_short(len(tried[i]))}")
        return tried[i]

    def fits(i: int) -> bool:
        return len(attempt(i)) <= target

    if not fits(0) and n > 1 and fits(n - 1):
        lo, hi = 1, n - 2
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(mid):
                hi = mid - 1
            else:
                lo = mid + 1
    good = sorted(i for i, d in tried.items() if len(d) <= target)
    small_i = min(tried, key=lambda i: len(tried[i]))
    if good:
        i = good[0]
        return tried[i], ladder[i], True, len(tried), tried[small_i], ladder[small_i]
    return (tried[small_i], ladder[small_i], False, len(tried),
            tried[small_i], ladder[small_i])


def compress_pdf(path: str, preset: str = "ebook", out_path: str = "",
                 target_kb: float | None = None, save_smallest: bool = False,
                 progress: ProgressCb | None = None) -> dict:
    """Shrink a PDF with PyMuPDF: downsample + recompress images, subset fonts,
    drop unused objects and deflate streams.  Text stays text.

    Without ``target_kb`` the named ``preset`` is used.  The result is written
    to ``<name>_compressed.pdf`` only when it is at least 1% smaller than the
    original; otherwise ``kept_original`` is True and no file is written.

    With ``target_kb`` the best-quality setting whose result is at or under
    the target is searched for (``preset`` is ignored).  When even the
    strongest setting cannot reach it, nothing is written unless
    ``save_smallest`` is True; ``target_met`` is False and ``smallest`` /
    ``note`` say what is possible.  A file bigger than the original is never
    written and the original is never overwritten.

    Returns {"out", "before", "after", "saved", "reduction", "kept_original",
    "preset", "note", "target_bytes", "target_met", "smallest",
    "can_save_smallest", "settings", "attempts"}.
    """
    target = int(float(target_kb or 0) * 1024)
    key = "target" if target > 0 else resolve_preset(preset)
    if out_path and os.path.abspath(out_path) == os.path.abspath(path):
        raise ConvertError("Refusing to overwrite the original PDF")
    src, images = _read_pdf(path)
    before = len(src)
    base = {"before": before, "preset": key, "target_bytes": target,
            "target_met": None, "smallest": None, "can_save_smallest": False,
            "settings": "", "attempts": 0}

    def kept(note, **kw):
        r = {**base, "out": "", "after": before, "saved": 0, "reduction": 0.0,
             "kept_original": True, "note": note}
        r.update(kw)
        return r

    def written(data, note="", **kw):
        nonlocal out_path
        if not out_path:
            out_path = os.path.splitext(path)[0] + "_compressed.pdf"
        final = _write_new(out_path, data)
        after = len(data)
        saved = before - after
        r = {**base, "out": final, "after": after, "saved": saved,
             "reduction": round(saved * 100.0 / before, 1) if before else 0.0,
             "kept_original": False, "note": note}
        r.update(kw)
        return r

    # ── named preset ──
    if not target:
        if progress:
            progress(0, 1, preset_label(key))
        data = _preset_bytes(src, key, images)
        if progress:
            progress(1, 1, "Done")
        after = len(data)
        if after >= before * 0.99:
            reason = ("The compressed copy came out larger"
                      if after >= before else "Less than 1% smaller")
            return kept(reason + " — the original was kept (no new file)")
        return written(data)

    # ── target size ──
    tlabel = fmt_short(target)
    if before <= target:
        return kept(f"Already under {tlabel} — the original was kept (no new file)",
                    target_met=True)

    pkey = _pending_key(path, target)
    cached = _PDF_PENDING.get(pkey) if save_smallest else None
    if cached:
        data, step, met, attempts = cached["data"], cached["step"], False, 0
        smallest, small_step = data, step
    else:
        data, step, met, attempts, smallest, small_step = _search_target(
            src, target, images, progress)
    base.update(attempts=attempts, smallest=len(smallest),
                can_save_smallest=len(smallest) < before)

    if met:
        base["settings"] = _step_label(step)
        _PDF_PENDING.pop(pkey, None)
        return written(data, f"Under {tlabel} ({_step_label(step).lower()})",
                       target_met=True)

    why = ("text and vector content can't be compressed further" if not images
           else "even with images at 50 dpi and the lowest quality")
    msg = f"Could not reach {tlabel} — smallest possible is {fmt_short(len(smallest))} ({why})"
    base["settings"] = _step_label(small_step)
    if len(smallest) >= before:
        _PDF_PENDING.pop(pkey, None)
        return kept(msg + ". No smaller copy is possible, so the original was kept.",
                    target_met=False)
    if not save_smallest:
        _PDF_PENDING[pkey] = {"data": smallest, "step": small_step}
        while len(_PDF_PENDING) > _PDF_PENDING_MAX:
            _PDF_PENDING.pop(next(iter(_PDF_PENDING)))
        r = kept(msg, target_met=False)
        r["after"] = len(smallest)          # what "Save smallest anyway" would give
        return r
    _PDF_PENDING.pop(pkey, None)
    return written(smallest, msg + " — saved the smallest version", target_met=False)


# ── size preview (in memory, nothing is written) ─────────────────────────────

# Bigger PDFs are measured on a few sample pages and extrapolated.
PREVIEW_SAMPLE_MIN_BYTES = 3 * 1024 * 1024
PREVIEW_SAMPLE_MIN_PAGES = 8
PREVIEW_SAMPLE_PAGES = 4
_PREVIEW_CACHE: dict = {}
_PREVIEW_CACHE_MAX = 400
_PREVIEW_SRC: dict = {}           # last few prepared sources (path key -> tuple)


def _file_key(path: str):
    st = os.stat(path)
    return (os.path.abspath(path).lower(), st.st_mtime_ns, st.st_size)


def _cache_put(key, value) -> None:
    _PREVIEW_CACHE[key] = value
    while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_MAX:
        _PREVIEW_CACHE.pop(next(iter(_PREVIEW_CACHE)))


def _preview_source(path: str):
    """(fkey, before, src_bytes, images, scale, estimate) for a PDF preview.

    Small PDFs use the whole file (exact).  Large ones use a handful of
    evenly spaced pages; ``scale`` turns a sample result into a whole-file
    estimate."""
    fkey = _file_key(path)
    hit = _PREVIEW_SRC.get(fkey)
    if hit:
        return hit
    src, images = _read_pdf(path)
    before = len(src)
    out = (fkey, before, src, images, 1.0, False)
    fitz = _fitz()
    if before >= PREVIEW_SAMPLE_MIN_BYTES:
        doc = fitz.open(stream=src, filetype="pdf")
        try:
            n = doc.page_count
            if n >= PREVIEW_SAMPLE_MIN_PAGES:
                k = PREVIEW_SAMPLE_PAGES
                pages = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
                sub = fitz.open()
                try:
                    for p in pages:
                        sub.insert_pdf(doc, from_page=p, to_page=p)
                    sample = sub.tobytes(garbage=3, deflate=True)
                finally:
                    sub.close()
                sdoc = fitz.open(stream=sample, filetype="pdf")
                try:
                    simgs = _scan_images(sdoc)
                finally:
                    sdoc.close()
                if sample:
                    out = (fkey, before, sample, simgs, before / len(sample), True)
        finally:
            doc.close()
    _preview_src_put(fkey, out)
    return out


def _preview_src_put(fkey, value) -> None:
    _PREVIEW_SRC[fkey] = value
    while len(_PREVIEW_SRC) > 3:
        _PREVIEW_SRC.pop(next(iter(_PREVIEW_SRC)))


def preview_pdf(path: str, keys: Iterable[str] = (), smallest: bool = False,
                cancelled: Callable[[], bool] | None = None) -> dict:
    """Predicted compressed size of a PDF for each preset / "level-N" key,
    computed in memory (nothing is written).

    ``smallest`` also measures the smallest size the target-size search can
    reach (to tell whether "Under 100 KB" is reachable).
    Returns {"before", "sizes": {key: bytes}, "smallest", "estimate"}.
    ``estimate`` is True when only sample pages were measured."""
    fkey, before, src, images, scale, estimate = _preview_source(path)
    cache: dict = {}
    sizes = {}
    for key in [resolve_preset(k) for k in (keys or [])]:
        if cancelled and cancelled():
            break
        ck = (fkey, "pdf", key)
        if ck not in _PREVIEW_CACHE:
            _cache_put(ck, int(len(_preset_bytes(src, key, images, cache)) * scale))
        sizes[key] = _PREVIEW_CACHE[ck]
    small = None
    if smallest and not (cancelled and cancelled()):
        ck = (fkey, "pdf", "smallest")
        if ck not in _PREVIEW_CACHE:
            vals = [len(_ladder_pass(src, None, images, cache))]
            if images:
                vals.append(len(_ladder_pass(src, PDF_TARGET_LADDER[-1], images, cache)))
            _cache_put(ck, int(min(vals) * scale))
        small = _PREVIEW_CACHE[ck]
    return {"before": before, "sizes": sizes, "smallest": small, "estimate": estimate}


def preview_image(path: str, quality: int = 70, max_edge: int = 0, fmt: str = "",
                  smallest: bool = False) -> dict:
    """Predicted size of compress_image(path, quality, max_edge, fmt) without
    writing anything, plus (``smallest``) roughly the smallest size a target
    search can reach.  Returns {"before", "after", "kept_original", "smallest",
    "estimate"} — "after" is exact, "smallest" approximate."""
    fkey = _file_key(path)
    q = max(1, min(95, int(quality or 70)))
    ck = (fkey, "img", q, int(max_edge or 0), (fmt or "").lower())
    if ck not in _PREVIEW_CACHE:
        r = compress_image(path, q, int(max_edge or 0), fmt or "", dry_run=True)
        _cache_put(ck, (r["after"], r["kept_original"]))
    after, kept = _PREVIEW_CACHE[ck]
    small = None
    if smallest:
        sk = (fkey, "img", "smallest")
        if sk not in _PREVIEW_CACHE:
            Image = _pil()
            img, dpi = _load_oriented(path)
            if min(img.size) > 160:
                s = 160 / min(img.size)
                img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                                 Image.LANCZOS)
            pil_fmt = _pick_output(path, "", True)[0]
            _cache_put(sk, min(len(_encode(img, pil_fmt, 10, dpi)), fkey[2]))
        small = _PREVIEW_CACHE[sk]
    return {"before": fkey[2], "after": after, "kept_original": kept,
            "smallest": small, "estimate": False}
