"""
annotate.py — annotation engine for PDFs (PyMuPDF) and images (Pillow).

One JSON shape for both (all coordinates in *page units*, origin top-left of
the page as displayed — PDF points for PDFs, pixels for images):

    {
      "id": "a12",            "type": "highlight",       "page": 0,
      "rect": [x0, y0, x1, y1],                           # bounding box
      "color": "#ffd400",     "fill": null | "#rrggbb",   "opacity": 1.0,
      "width": 2,             "font_size": 12,
      "text": "...",          # free text / stamp label / note + markup comment
      "author": "...",        "created": "ISO",  "modified": "ISO",
      "quads":  [[x0,y0,x1,y1], ...],        # text markup: one box per line
      "points": [[[x,y], ...], ...],         # ink strokes
      "line":   [x1, y1, x2, y2],            # line / arrow
      "image":  "data:image/png;base64,...", # signature
      "xref":   123,                         # existing PDF annotation (load only)
    }

Types: highlight underline strikeout squiggly ink rect ellipse line arrow text
note stamp signature cover redact, plus "other" (an existing annotation of a
kind this editor does not edit — kept as is unless the user erases it).

PDF output — real annotations, editable in Acrobat / Edge:
    highlight/underline/strikeout/squiggly → Highlight/Underline/StrikeOut/Squiggly
    ink → Ink · rect/cover → Square · ellipse → Circle · line/arrow → Line
    text/stamp → FreeText · note → Text (sticky note) · signature → image Stamp
    redact → Redact, applied (content removed) only when apply_redactions=True
  Style details the PDF has no field for (font size, stamp label, …) are kept in
  a private /OAData key on the annotation; geometry, colours and text are always
  read back from the PDF itself so edits made in other viewers win.

Image output — the annotations are drawn onto a copy (``<name>_annotated.<ext>``)
and the editable layer is stored in a **sidecar JSON** next to the output,
``<output stem>.annot.json`` = {"version":1, "source": <original image>, "annotations":[…]}.
Opening the output again finds the sidecar, loads the original pixels from
"source" and the annotations as editable objects.  (Sidecar instead of a PNG
text chunk because it works for every format — JPG/WEBP/BMP too.)

Never overwrites: new files get a free name; "save over original" first copies
the old version into ``_to_review/`` next to it.
"""
from __future__ import annotations

import base64
import datetime as _dt
import io
import json
import math
import os
import re

import pdf_tools as _pt

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
MARKUP = ("highlight", "underline", "strikeout", "squiggly")
STAMP_SUBJECT = "OA:stamp"
SIDE_EXT = ".annot.json"


class AnnotateError(Exception):
    """Readable error for the UI."""


# ─────────────────────────────────────────────────────────────────────────────
# small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fitz():
    import pymupdf
    return pymupdf


def is_image(path: str) -> bool:
    return os.path.splitext(str(path))[1].lower() in IMAGE_EXTS


def is_pdf(path: str) -> bool:
    return str(path).lower().endswith(".pdf")


def hex_rgb(h, default=(0, 0, 0)):
    """'#ffd400' → (1.0, 0.83, 0.0) floats; None → None."""
    if h is None or h == "":
        return None
    if isinstance(h, (list, tuple)):
        return tuple(float(c) for c in h[:3])
    s = str(h).lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return default


def rgb_hex(c) -> str | None:
    if not c:
        return None
    c = list(c)
    if len(c) == 1:
        c = c * 3
    if len(c) == 4:   # CMYK → RGB (rough)
        k = c[3]
        c = [(1 - c[0]) * (1 - k), (1 - c[1]) * (1 - k), (1 - c[2]) * (1 - k)]
    return "#" + "".join(f"{max(0, min(255, round(float(v) * 255))):02x}" for v in c[:3])


def _rgb255(h, default=(0, 0, 0)):
    c = hex_rgb(h, None)
    if c is None:
        return default
    return tuple(int(round(v * 255)) for v in c)


def pdf_date(iso: str | None) -> str:
    """ISO string (or None = now) → PDF date 'D:YYYYMMDDHHmmSS'."""
    t = None
    if iso:
        try:
            t = _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except ValueError:
            t = None
    t = t or _dt.datetime.now()
    return "D:" + t.strftime("%Y%m%d%H%M%S")


def iso_date(pdfd: str | None) -> str:
    """PDF date → ISO (local, seconds) or ''."""
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", str(pdfd or ""))
    if not m:
        return ""
    parts = [int(p) if p else d for p, d in zip(m.groups(), (0, 1, 1, 0, 0, 0))]
    try:
        return _dt.datetime(*parts).isoformat(timespec="seconds")
    except ValueError:
        return ""


def _rect4(r) -> list:
    return [round(float(r[0]), 2), round(float(r[1]), 2), round(float(r[2]), 2), round(float(r[3]), 2)]


def _norm(r) -> list:
    x0, y0, x1, y1 = (float(v) for v in r[:4])
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _decode_data_url(du: str) -> bytes:
    if not du:
        raise AnnotateError("Signature image is missing")
    m = re.match(r"data:[^;,]*(;base64)?,(.*)$", du, re.S)
    if not m:
        raise AnnotateError("Bad image data")
    return base64.b64decode(m.group(2)) if m.group(1) else m.group(2).encode()


def _data_url(png: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(png).decode("ascii")


def default_output(path: str) -> str:
    """'<dir>/report.pdf' → '<dir>/report_annotated.pdf' (free name; no '_annotated_annotated')."""
    ext = os.path.splitext(path)[1].lower() or ".pdf"
    stem = os.path.splitext(os.path.basename(path))[0]
    if re.search(r"_annotated( \(\d+\))?$", stem):
        base = re.sub(r" \(\d+\)$", "", stem)
        return _pt.unique_path(os.path.join(os.path.dirname(os.path.abspath(path)), base + ext))
    return _pt.default_output(path, "_annotated", ext)


# ─────────────────────────────────────────────────────────────────────────────
# sidecar (images)
# ─────────────────────────────────────────────────────────────────────────────

def sidecar_for(path: str) -> str:
    p = os.path.abspath(path)
    return os.path.join(os.path.dirname(p), os.path.splitext(os.path.basename(p))[0] + SIDE_EXT)


def read_sidecar(path: str) -> dict | None:
    sc = sidecar_for(path)
    if not os.path.isfile(sc):
        return None
    try:
        with open(sc, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    src = data.get("source") or ""
    if src and not os.path.isabs(src):
        src = os.path.join(os.path.dirname(sc), src)
    if not src or not os.path.isfile(src):
        return None
    data["source"] = os.path.abspath(src)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# load
# ─────────────────────────────────────────────────────────────────────────────

def load(path: str) -> dict:
    """{kind, source, pages:[{w,h}], annotations:[…]} for a PDF or image."""
    path = os.path.abspath(str(path or ""))
    if not os.path.isfile(path):
        raise AnnotateError(f"File not found: {os.path.basename(path)}")
    if is_image(path):
        return _load_image(path)
    if is_pdf(path):
        return _load_pdf(path)
    raise AnnotateError("Only PDFs and images (JPG, PNG, WEBP, BMP) can be annotated")


def _open_upright(path: str):
    """Pillow image with EXIF orientation applied (what every viewer shows)."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im.load()
        try:
            out = ImageOps.exif_transpose(im)
        except Exception:
            out = im.copy()
    return out if out is not None else im


def _load_image(path: str) -> dict:
    side = read_sidecar(path)
    source = side["source"] if side else path
    w, h = _open_upright(source).size
    anns = []
    if side:
        for i, a in enumerate(side.get("annotations") or []):
            if isinstance(a, dict) and a.get("type"):
                a = dict(a)
                a["page"] = 0
                a.setdefault("id", f"s{i + 1}")
                anns.append(a)
    return {"kind": "image", "source": source, "path": path, "has_layer": bool(side),
            "pages": [{"w": w, "h": h}], "annotations": anns}


def _open_pdf(path: str):
    fitz = _fitz()
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise AnnotateError(f"Could not open the PDF: {exc}")
    if doc.needs_pass:
        doc.close()
        raise AnnotateError("This PDF is password-protected. Remove the password first.")
    return doc


def _oadata(doc, xref: int) -> dict:
    try:
        t, v = doc.xref_get_key(xref, "OAData")
        if t == "string" and v:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


_DA_SIZE = re.compile(r"([\d.]+)\s+Tf")
_DA_RGB = re.compile(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg")


def _quads_from_vertices(verts, mat) -> list:
    """Text-markup vertices (4 points per quad) → display rects."""
    fitz = _fitz()
    out = []
    for i in range(0, len(verts or []) - 3, 4):
        pts = [fitz.Point(p) * mat for p in verts[i:i + 4]]
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        out.append(_rect4((min(xs), min(ys), max(xs), max(ys))))
    return out


def _load_pdf(path: str) -> dict:
    fitz = _fitz()
    doc = _open_pdf(path)
    try:
        pages, anns = [], []
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            pages.append({"w": round(page.rect.width, 2), "h": round(page.rect.height, 2)})
            mat = page.rotation_matrix
            for annot in page.annots() or []:
                try:
                    a = _read_annot(doc, page, annot, mat)
                except Exception:
                    a = None
                if a:
                    a["page"] = pno
                    a["id"] = f"x{annot.xref}"
                    anns.append(a)
        return {"kind": "pdf", "source": path, "path": path, "has_layer": bool(anns),
                "pages": pages, "annotations": anns}
    finally:
        doc.close()


def _read_annot(doc, page, annot, mat) -> dict | None:
    fitz = _fitz()
    code, tname = annot.type[0], annot.type[1]
    if code == fitz.PDF_ANNOT_POPUP:
        return None
    info = annot.info or {}
    extra = _oadata(doc, annot.xref)
    colors = annot.colors or {}
    stroke = rgb_hex(colors.get("stroke"))
    fill = rgb_hex(colors.get("fill"))
    op = annot.opacity
    rect = fitz.Rect(annot.rect) * mat
    a = {
        "xref": annot.xref,
        "rect": _rect4(rect),
        "color": stroke or extra.get("color") or "#000000",
        "fill": fill,
        "opacity": round(op, 3) if op is not None and op >= 0 else 1.0,
        "width": (annot.border or {}).get("width") or 0,
        "text": info.get("content", "") or "",
        "author": info.get("title", "") or "",
        "created": iso_date(info.get("creationDate")),
        "modified": iso_date(info.get("modDate")),
    }
    if a["width"] is None or a["width"] < 0:
        a["width"] = 0
    kind = extra.get("type")
    if extra.get("r") and extra.get("ar"):
        cur, ar, r0 = a["rect"], extra["ar"], extra["r"]
        if (abs((cur[2] - cur[0]) - (ar[2] - ar[0])) < 0.6 and
                abs((cur[3] - cur[1]) - (ar[3] - ar[1])) < 0.6):
            dx, dy = cur[0] - ar[0], cur[1] - ar[1]      # moved in another viewer
            a["rect"] = _rect4((r0[0] + dx, r0[1] + dy, r0[2] + dx, r0[3] + dy))

    if code in (fitz.PDF_ANNOT_HIGHLIGHT, fitz.PDF_ANNOT_UNDERLINE,
                fitz.PDF_ANNOT_STRIKE_OUT, fitz.PDF_ANNOT_SQUIGGLY):
        a["type"] = {fitz.PDF_ANNOT_HIGHLIGHT: "highlight", fitz.PDF_ANNOT_UNDERLINE: "underline",
                     fitz.PDF_ANNOT_STRIKE_OUT: "strikeout", fitz.PDF_ANNOT_SQUIGGLY: "squiggly"}[code]
        a["quads"] = _quads_from_vertices(annot.vertices, mat) or [a["rect"]]
        a["fill"] = None
    elif code == fitz.PDF_ANNOT_INK:
        a["type"] = "ink"
        a["points"] = [[[round(p.x, 2), round(p.y, 2)] for p in (fitz.Point(q) * mat for q in stroke_pts)]
                       for stroke_pts in (annot.vertices or [])]
        a["width"] = a["width"] or 1
    elif code == fitz.PDF_ANNOT_LINE:
        v = annot.vertices or []
        if len(v) < 2:
            return None
        p1, p2 = fitz.Point(v[0]) * mat, fitz.Point(v[-1]) * mat
        ends = annot.line_ends or (0, 0)
        a["type"] = "arrow" if (ends[1] or ends[0]) else "line"
        a["line"] = [round(p1.x, 2), round(p1.y, 2), round(p2.x, 2), round(p2.y, 2)]
        a["width"] = a["width"] or 1
        a["fill"] = None
    elif code == fitz.PDF_ANNOT_SQUARE:
        a["type"] = "cover" if kind == "cover" else "rect"
        if a["type"] == "cover":
            a["color"] = fill or stroke or "#000000"
    elif code == fitz.PDF_ANNOT_CIRCLE:
        a["type"] = "ellipse"
    elif code == fitz.PDF_ANNOT_FREE_TEXT:
        a["type"] = "stamp" if (kind == "stamp" or info.get("subject") == STAMP_SUBJECT) else "text"
        try:
            da = doc.xref_get_key(annot.xref, "DA")[1] or ""
        except Exception:
            da = ""
        m = _DA_SIZE.search(da)
        a["font_size"] = extra.get("font_size") or (float(m.group(1)) if m else 12)
        m = _DA_RGB.search(da)
        tcol = rgb_hex([float(x) for x in m.groups()]) if m else None
        a["color"] = extra.get("color") or tcol or stroke or "#000000"
        # FreeText /C is the background (fill) colour.
        a["fill"] = extra.get("fill", fill if fill else None)
        if kind != "stamp" and extra.get("fill") is None and stroke and not fill:
            a["fill"] = stroke if stroke != a["color"] else None
        if a["type"] == "stamp":
            a["fill"] = None
            a["width"] = a["width"] or 2
    elif code == fitz.PDF_ANNOT_TEXT:
        a["type"] = "note"
        a["fill"] = None
    elif code == fitz.PDF_ANNOT_STAMP and kind == "signature":
        a["type"] = "signature"
        a["image"] = _annot_png(annot)
    elif code == fitz.PDF_ANNOT_REDACT:
        a["type"] = "redact"
        a["color"] = fill or "#000000"
    else:
        a["type"] = "other"
        a["label"] = tname
        a["image"] = _annot_png(annot)
    return a


def _annot_png(annot) -> str:
    fitz = _fitz()
    try:
        r = annot.rect
        z = max(1.0, min(4.0, 600.0 / max(r.width, r.height, 1)))
        pix = annot.get_pixmap(matrix=fitz.Matrix(z, z), alpha=True)
        return _data_url(pix.tobytes("png"))
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# render / words
# ─────────────────────────────────────────────────────────────────────────────

def render_page(path: str, page: int = 0, scale: float = 1.5) -> dict:
    """Page image WITHOUT annotations (the editor draws them) → {data_url, width, height}."""
    path = os.path.abspath(str(path or ""))
    scale = max(0.05, min(float(scale or 1.0), 6.0))
    if is_image(path):
        from PIL import Image
        side = read_sidecar(path)
        src = side["source"] if side else path
        im = _open_upright(src)
        w, h = im.size
        tw, th = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        if (tw, th) != (w, h):
            im = im.resize((tw, th), Image.LANCZOS)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.mode else "RGB")
        buf = io.BytesIO()
        if im.mode == "RGBA":
            im.save(buf, "PNG")
            mime = "image/png"
        else:
            im.save(buf, "JPEG", quality=90)
            mime = "image/jpeg"
        return {"data_url": _data_url(buf.getvalue(), mime), "width": tw, "height": th}
    fitz = _fitz()
    doc = _open_pdf(path)
    try:
        if page < 0 or page >= doc.page_count:
            raise AnnotateError("Page out of range")
        pg = doc.load_page(int(page))
        # keep very large pages bounded (~ 24 MP)
        r = pg.rect
        s = min(scale, math.sqrt(24e6 / max(1.0, r.width * r.height)))
        pix = pg.get_pixmap(matrix=fitz.Matrix(s, s), annots=False)
        return {"data_url": _data_url(pix.tobytes("jpeg", jpg_quality=88), "image/jpeg"),
                "width": pix.width, "height": pix.height}
    finally:
        doc.close()


def words(path: str, page: int = 0) -> list:
    """Word boxes on a PDF page in display coords: [[x0,y0,x1,y1,text,block,line,word], …]."""
    if not is_pdf(path):
        return []
    fitz = _fitz()
    doc = _open_pdf(os.path.abspath(path))
    try:
        if page < 0 or page >= doc.page_count:
            return []
        pg = doc.load_page(int(page))
        mat = pg.rotation_matrix
        out = []
        for w in pg.get_text("words"):
            r = fitz.Rect(w[:4]) * mat
            if r.is_empty:
                continue
            out.append([round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2),
                        w[4], int(w[5]), int(w[6]), int(w[7])])
        return out
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# save
# ─────────────────────────────────────────────────────────────────────────────

def save(path: str, annotations: list, *, out_path: str = "", replace: bool = False,
         flatten: bool = False, apply_redactions: bool = False, author: str = "",
         release_handles=None) -> dict:
    """Write *annotations* (the full set) → new file, or over *path* after a backup.

    Returns {out_path, backup, replaced, count, sidecar}.
    """
    path = os.path.abspath(str(path or ""))
    if not os.path.isfile(path):
        raise AnnotateError(f"File not found: {os.path.basename(path)}")
    anns = [a for a in (annotations or []) if isinstance(a, dict) and a.get("type")]
    if is_image(path):
        return _save_image(path, anns, out_path=out_path, replace=replace, author=author,
                           release_handles=release_handles)
    if not is_pdf(path):
        raise AnnotateError("Only PDFs and images can be annotated")
    data, count = _pdf_bytes(path, anns, flatten=flatten, apply_redactions=apply_redactions,
                             author=author)
    if not replace:
        target = out_path or default_output(path)
        if not target.lower().endswith(".pdf"):
            target += ".pdf"
        final = _pt._write_new(os.path.abspath(target), data)
        return {"out_path": final, "backup": "", "replaced": False, "count": count, "sidecar": ""}
    return _replace(path, data, release_handles) | {"count": count, "sidecar": ""}


def _replace(path: str, data: bytes, release_handles=None) -> dict:
    backup = _pt.backup_to_review(path)
    stem, ext = os.path.splitext(path)
    tmp = _pt._write_new(stem + ".saving" + ext, data)
    if release_handles:
        try:
            release_handles(path)
        except Exception:
            pass
    try:
        os.replace(tmp, path)
    except OSError as exc:
        alt = default_output(path)
        os.replace(tmp, alt)
        raise AnnotateError(
            f"Could not overwrite {os.path.basename(path)} ({exc.strerror or exc}). "
            f"Your annotations were saved as {os.path.basename(alt)} instead.")
    return {"out_path": path, "backup": backup, "replaced": True}


def _pdf_bytes(path: str, anns: list, *, flatten=False, apply_redactions=False, author="") -> tuple:
    fitz = _fitz()
    doc = _open_pdf(path)
    try:
        keep = {int(a["xref"]) for a in anns if a.get("type") == "other" and a.get("xref")}
        # 1) drop every existing annotation we manage (they are re-created from
        #    the editor's list); unknown kinds stay unless the user erased them.
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            xrefs = []
            for an in page.annots() or []:
                if an.type[0] == fitz.PDF_ANNOT_POPUP:
                    continue
                if an.xref in keep:
                    continue
                xrefs.append(an.xref)
            for x in xrefs:
                try:
                    an = page.load_annot(x)
                    if an:
                        page.delete_annot(an)
                except Exception:
                    pass
        # 2) write the editor's annotations
        count = 0
        redact_pages = set()
        for a in anns:
            if a.get("type") == "other":
                continue
            pno = int(a.get("page", 0) or 0)
            if pno < 0 or pno >= doc.page_count:
                continue
            page = doc.load_page(pno)
            if _write_annot(doc, page, a, author):
                count += 1
                if a.get("type") == "redact":
                    redact_pages.add(pno)
        if apply_redactions:
            for pno in sorted(redact_pages):
                doc.load_page(pno).apply_redactions()
        if flatten:
            doc.bake(annots=True, widgets=False)
        buf = io.BytesIO()
        doc.save(buf, garbage=3, deflate=True)
        return buf.getvalue(), count
    finally:
        doc.close()


def _derot(page, r):
    fitz = _fitz()
    return (fitz.Rect(_norm(r)) * page.derotation_matrix).normalize()


def _derot_pt(page, x, y):
    fitz = _fitz()
    return fitz.Point(float(x), float(y)) * page.derotation_matrix


def _write_annot(doc, page, a: dict, default_author: str):
    fitz = _fitz()
    t = a.get("type")
    color = hex_rgb(a.get("color"), (0, 0, 0)) or (0, 0, 0)
    fill = hex_rgb(a.get("fill"), None)
    opacity = a.get("opacity")
    opacity = 1.0 if opacity is None else max(0.05, min(1.0, float(opacity)))
    width = max(0.0, float(a.get("width") or 0))
    text = str(a.get("text") or "")
    rect = a.get("rect") or [0, 0, 0, 0]
    extra = {"type": t}
    annot = None

    if t in MARKUP:
        quads = a.get("quads") or [rect]
        rects = [_derot(page, q) for q in quads if q and len(q) >= 4]
        rects = [r for r in rects if not r.is_empty]
        if not rects:
            return None
        fn = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
              "strikeout": page.add_strikeout_annot, "squiggly": page.add_squiggly_annot}[t]
        annot = fn(quads=rects)
        annot.set_colors(stroke=color)
    elif t == "ink":
        strokes = []
        for s in a.get("points") or []:
            pts = [tuple(_derot_pt(page, p[0], p[1])) for p in s if p and len(p) >= 2]
            if len(pts) == 1:
                pts.append((pts[0][0] + 0.1, pts[0][1] + 0.1))
            if pts:
                strokes.append(pts)
        if not strokes:
            return None
        annot = page.add_ink_annot(strokes)
        annot.set_colors(stroke=color)
        annot.set_border(width=width or 2)
    elif t in ("rect", "ellipse", "cover"):
        r = _derot(page, rect)
        if r.is_empty:
            return None
        annot = (page.add_circle_annot if t == "ellipse" else page.add_rect_annot)(r)
        if t == "cover":
            c = color
            annot.set_colors(stroke=c, fill=c)
            annot.set_border(width=0)
        else:
            annot.set_colors(stroke=color, fill=fill)
            annot.set_border(width=width if width else 2)
    elif t in ("line", "arrow"):
        ln = a.get("line") or [rect[0], rect[1], rect[2], rect[3]]
        p1, p2 = _derot_pt(page, ln[0], ln[1]), _derot_pt(page, ln[2], ln[3])
        if abs(p1.x - p2.x) + abs(p1.y - p2.y) < 0.5:
            return None
        annot = page.add_line_annot(p1, p2)
        annot.set_border(width=width or 2)
        if t == "arrow":
            annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
            annot.set_colors(stroke=color, fill=color)
        else:
            annot.set_colors(stroke=color)
    elif t in ("text", "stamp"):
        r = _derot(page, rect)
        if r.is_empty:
            return None
        fs = max(4.0, min(144.0, float(a.get("font_size") or (16 if t == "stamp" else 12))))
        stamp = t == "stamp"
        annot = page.add_freetext_annot(
            r, text or " ", fontsize=fs, fontname="Helv", text_color=color,
            fill_color=None if stamp else fill,
            border_width=(width or 2) if stamp else 0,
            align=fitz.TEXT_ALIGN_CENTER if stamp else fitz.TEXT_ALIGN_LEFT,
            rotate=page.rotation, opacity=opacity)
        extra.update(font_size=fs, color=a.get("color"), fill=None if stamp else a.get("fill"))
    elif t == "note":
        pt = _derot_pt(page, rect[0], rect[1])
        annot = page.add_text_annot(pt, text, icon="Comment")
        annot.set_colors(stroke=color)
    elif t == "signature":
        r = _derot(page, rect)
        if r.is_empty:
            return None
        img = _decode_data_url(a.get("image") or "")
        if page.rotation:
            from PIL import Image
            with Image.open(io.BytesIO(img)) as im:
                im = im.rotate(page.rotation, expand=True)   # PIL rotates CCW = PDF /Rotate inverse
                b = io.BytesIO()
                im.save(b, "PNG")
                img = b.getvalue()
        annot = page.add_stamp_annot(r, stamp=img)
    elif t == "redact":
        r = _derot(page, rect)
        if r.is_empty:
            return None
        annot = page.add_redact_annot(r, fill=hex_rgb(a.get("color"), (0, 0, 0)) or (0, 0, 0))
    else:
        return None

    info = {"title": str(a.get("author") or default_author or ""),
            "content": text,
            "creationDate": pdf_date(a.get("created")),
            "modDate": pdf_date(a.get("modified"))}
    if t == "stamp":
        info["subject"] = STAMP_SUBJECT
    elif t == "signature":
        info["subject"] = "Signature"
        info["content"] = text or "Signature"
    try:
        annot.set_info(**info)
    except Exception:
        pass
    if t not in ("text", "stamp", "redact"):
        try:
            annot.set_opacity(opacity)
        except Exception:
            pass
    try:
        annot.update()
    except Exception:
        pass
    if t in ("text", "stamp"):
        # update() may rebuild the appearance with defaults — re-apply style.
        try:
            annot.update(fontsize=extra["font_size"], text_color=color,
                         fill_color=None if t == "stamp" else fill, opacity=opacity)
        except Exception:
            pass
    if t in ("rect", "ellipse", "cover", "text", "stamp", "signature", "redact"):
        # MuPDF grows /Rect by the border width; remember both so a reload
        # gives back exactly what the user drew (and follows moves elsewhere).
        try:
            extra["r"] = _rect4(_norm(rect))
            extra["ar"] = _rect4(_fitz().Rect(annot.rect) * page.rotation_matrix)
        except Exception:
            pass
    try:
        doc.xref_set_key(annot.xref, "OAData", _fitz().get_pdf_str(json.dumps(extra)))
    except Exception:
        pass
    return annot


# ─────────────────────────────────────────────────────────────────────────────
# images (Pillow)
# ─────────────────────────────────────────────────────────────────────────────

def _font(size: float, bold: bool = False):
    from PIL import ImageFont
    size = max(6, int(round(size)))
    for name in (("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf") if bold
                 else ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:   # Pillow < 10.1
        return ImageFont.load_default()


def _wrap(draw, text: str, font, width: float) -> list:
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = (cur + " " + word) if cur else word
            if draw.textlength(cand, font=font) <= width or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def render_image(source: str, anns: list):
    """Pillow image of *source* with *anns* drawn on (RGBA)."""
    from PIL import Image, ImageChops, ImageDraw
    base = _open_upright(source).convert("RGBA")
    W, H = base.size
    for a in anns:
        t = a.get("type")
        if t in ("other",):
            continue
        col = _rgb255(a.get("color"), (0, 0, 0))
        op = a.get("opacity")
        op = 1.0 if op is None else max(0.05, min(1.0, float(op)))
        width = max(1, int(round(float(a.get("width") or 2))))
        r = _norm(a.get("rect") or [0, 0, 0, 0])
        if t == "highlight":
            layer = Image.new("RGB", (W, H), (255, 255, 255))
            d = ImageDraw.Draw(layer)
            for q in a.get("quads") or [r]:
                d.rectangle(_norm(q), fill=col)
            mult = ImageChops.multiply(base.convert("RGB"), layer).convert("RGBA")
            mult.putalpha(base.getchannel("A"))
            base = Image.blend(base, mult, op)
            continue
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        rgba = col + (255,)
        if t in ("underline", "strikeout", "squiggly"):
            for q in a.get("quads") or [r]:
                x0, y0, x1, y1 = _norm(q)
                lw = max(1, int(round((y1 - y0) * 0.08)))
                if t == "underline":
                    y = y1 - lw / 2
                    d.line([(x0, y), (x1, y)], fill=rgba, width=lw)
                elif t == "strikeout":
                    y = (y0 + y1) / 2
                    d.line([(x0, y), (x1, y)], fill=rgba, width=lw)
                else:
                    amp, step = max(1.5, (y1 - y0) * 0.08), max(3.0, (y1 - y0) * 0.25)
                    pts, x, k = [], x0, 0
                    while x <= x1:
                        pts.append((x, y1 - amp - (amp if k % 2 else 0)))
                        x += step
                        k += 1
                    if len(pts) > 1:
                        d.line(pts, fill=rgba, width=lw)
        elif t == "ink":
            for s in a.get("points") or []:
                pts = [(float(p[0]), float(p[1])) for p in s]
                if len(pts) > 1:
                    d.line(pts, fill=rgba, width=width, joint="curve")
                rr = width / 2
                for (x, y) in (pts[:1] + pts[-1:]):
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=rgba)
        elif t in ("rect", "ellipse", "cover", "redact"):
            fill = _rgb255(a.get("fill"), None) if a.get("fill") else None
            if t in ("cover", "redact"):
                d.rectangle(r, fill=rgba)
            elif t == "rect":
                d.rectangle(r, outline=rgba, width=width, fill=(fill + (255,)) if fill else None)
            else:
                d.ellipse(r, outline=rgba, width=width, fill=(fill + (255,)) if fill else None)
        elif t in ("line", "arrow"):
            x1, y1, x2, y2 = (float(v) for v in (a.get("line") or r))
            d.line([(x1, y1), (x2, y2)], fill=rgba, width=width)
            if t == "arrow":
                ang = math.atan2(y2 - y1, x2 - x1)
                L = max(8.0, width * 4.0)
                pts = [(x2, y2),
                       (x2 - L * math.cos(ang - 0.45), y2 - L * math.sin(ang - 0.45)),
                       (x2 - L * math.cos(ang + 0.45), y2 - L * math.sin(ang + 0.45))]
                d.polygon(pts, fill=rgba)
        elif t in ("text", "stamp"):
            fs = float(a.get("font_size") or (16 if t == "stamp" else 12))
            font = _font(fs, bold=(t == "stamp"))
            if t == "stamp":
                d.rounded_rectangle(r, radius=max(2, int(fs * 0.3)), outline=rgba, width=width)
            elif a.get("fill"):
                d.rectangle(r, fill=_rgb255(a.get("fill")) + (255,))
            pad = max(2.0, fs * 0.25)
            lines = _wrap(d, a.get("text") or "", font, max(1.0, r[2] - r[0] - 2 * pad))
            lh = fs * 1.2
            y = r[1] + pad if t == "text" else (r[1] + r[3]) / 2 - lh * len(lines) / 2
            for ln in lines:
                if t == "stamp":
                    x = (r[0] + r[2]) / 2 - d.textlength(ln, font=font) / 2
                else:
                    x = r[0] + pad
                d.text((x, y), ln, font=font, fill=rgba)
                y += lh
        elif t == "note":
            s = max(14.0, min(W, H) * 0.03)
            x, y = r[0], r[1]
            d.rounded_rectangle([x, y, x + s, y + s * 0.8], radius=s * 0.15, fill=rgba)
            d.polygon([(x + s * 0.2, y + s * 0.8), (x + s * 0.45, y + s * 0.8), (x + s * 0.2, y + s)], fill=rgba)
            for k in (0.25, 0.45):
                d.line([(x + s * 0.2, y + s * k * 1.2), (x + s * 0.8, y + s * k * 1.2)],
                       fill=(255, 255, 255, 255), width=max(1, int(s * 0.07)))
        elif t == "signature":
            try:
                sig = Image.open(io.BytesIO(_decode_data_url(a.get("image") or ""))).convert("RGBA")
            except Exception:
                continue
            bw, bh = max(1, int(r[2] - r[0])), max(1, int(r[3] - r[1]))
            sc = min(bw / sig.width, bh / sig.height)
            sig = sig.resize((max(1, int(sig.width * sc)), max(1, int(sig.height * sc))), Image.LANCZOS)
            ox = int(r[0] + (bw - sig.width) / 2)
            oy = int(r[1] + (bh - sig.height) / 2)
            layer.alpha_composite(sig, (max(0, ox), max(0, oy)))
        if op < 1.0:
            alpha = layer.getchannel("A").point(lambda v: int(v * op))
            layer.putalpha(alpha)
        base.alpha_composite(layer)
    return base


def _encode_image(img, ext: str) -> bytes:
    ext = ext.lower()
    buf = io.BytesIO()
    if ext in (".jpg", ".jpeg"):
        img.convert("RGB").save(buf, "JPEG", quality=95)
    elif ext == ".webp":
        img.save(buf, "WEBP", quality=95)
    elif ext == ".bmp":
        img.convert("RGB").save(buf, "BMP")
    else:
        img.save(buf, "PNG")
    return buf.getvalue()


def _write_sidecar(out_path: str, source: str, anns: list) -> str:
    sc = sidecar_for(out_path)
    try:
        rel = os.path.relpath(source, os.path.dirname(sc))
    except ValueError:      # different drive
        rel = source
    payload = {"version": 1, "source": rel, "source_abs": source,
               "saved": _dt.datetime.now().isoformat(timespec="seconds"),
               "annotations": anns}
    data = json.dumps(payload, ensure_ascii=False, indent=1)
    # An older sidecar for this name is kept as a backup copy in _to_review/.
    if os.path.exists(sc):
        try:
            _pt.backup_to_review(sc)
        except OSError:
            pass
    tmp = sc + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, sc)
    return sc


def _save_image(path: str, anns: list, *, out_path="", replace=False, author="",
                release_handles=None) -> dict:
    side = read_sidecar(path)
    source = side["source"] if side else path
    ext = os.path.splitext(path)[1].lower()
    for a in anns:
        a.setdefault("author", author)
    clean = [{k: v for k, v in a.items() if k != "xref"} for a in anns]
    img = render_image(source, clean)
    data = _encode_image(img, ext)
    if not replace:
        target = out_path or default_output(os.path.splitext(source)[0] + ext)
        if not os.path.splitext(target)[1]:
            target += ext
        final = _pt._write_new(os.path.abspath(target), data)
        sc = _write_sidecar(final, source, clean)
        return {"out_path": final, "backup": "", "replaced": False, "count": len(clean), "sidecar": sc}
    res = _replace(path, data, release_handles)
    # The untouched pixels now live in the backup (unless a sidecar already
    # points at the real original) — keep the layer editable from there.
    src_for_layer = source if side else res["backup"]
    sc = _write_sidecar(path, src_for_layer, clean)
    return res | {"count": len(clean), "sidecar": sc}
