"""
pdf_edit.py — real text editing of text-based PDFs (like Acrobat's "Edit PDF").

Coordinates are *display* units: PDF points, origin top-left of the page as it
is shown (the page /Rotate applied) — the same space annotate.py / pdf-text.js
use, so the UI can overlay boxes on a rendered page image directly.

    extract_blocks(path, page) -> {page, width, height, rotation, blocks:[…], images:[…]}
        block = {id, bbox, text, lines:[{text, bbox, origin, font, size, color, bold, italic}],
                 font, family, css_family, size, color, bold, italic, align,
                 line_height, editable}
        image = {id, bbox, xref, width, height, background, replaceable}
    apply_edits(path, edits, out_path=None, replace=False) -> {out_path, backup, replaced,
                                                               substituted, warnings, count}

Edits (one dict each, ``page`` is 0-based):
    {type:'text',  page, block_id, new_text, font?, size?, color?, bold?, italic?, align?, bbox?}
    {type:'move',  page, block_id, bbox}                 # move / resize, text unchanged
    {type:'delete', page, block_id}
    {type:'add_text', page, rect, text, font?, size?, color?, bold?, italic?, align?}
    {type:'delete_image',  page, image_id}
    {type:'replace_image', page, image_id, data_url, fit?: 'contain'|'stretch'}

How a text change is written: the original glyphs are removed with redaction
annotations that only touch text (images and vector graphics stay), then the new
text is drawn at the original baseline with the original font when it is
embedded in full and has every glyph, else the closest Windows font. Form
fields and annotations are never touched; the new text stays selectable.

Never overwrites: the result goes to ``<name>_edited.pdf`` (free name), or —
with replace=True — over the original after a backup copy in ``_to_review/``.
"""
from __future__ import annotations

import base64
import datetime as _dt
import io
import math
import os
import re

import pdf_tools as _pt


class PdfEditError(Exception):
    """Readable error for the UI; ``code`` is a machine-readable reason."""

    def __init__(self, msg: str, code: str = ""):
        super().__init__(msg)
        self.code = code


SCANNED_MSG = "This page is a scanned image — text can't be edited directly."


def _fitz():
    import pymupdf
    return pymupdf


# ─────────────────────────────────────────────────────────────────────────────
# fonts
# ─────────────────────────────────────────────────────────────────────────────

FONT_DIR = os.path.join(os.environ.get("WINDIR") or r"C:\Windows", "Fonts")

# family -> (regular, bold, italic, bold italic) file names in C:\Windows\Fonts
FAMILIES = {
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
    "Malgun Gothic": ("malgun.ttf", "malgunbd.ttf", "malgun.ttf", "malgunbd.ttf"),
}
CSS_FAMILY = {
    "Arial": "Arial, Helvetica, sans-serif",
    "Times New Roman": "'Times New Roman', Times, serif",
    "Calibri": "Calibri, Carlito, sans-serif",
    "Courier New": "'Courier New', Courier, monospace",
    "Georgia": "Georgia, serif",
    "Verdana": "Verdana, sans-serif",
    "Segoe UI": "'Segoe UI', sans-serif",
    "Tahoma": "Tahoma, sans-serif",
    "Malgun Gothic": "'Malgun Gothic', sans-serif",
}
CJK_FAMILY = "Malgun Gothic"

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_CJK = re.compile(r"[\u1100-\u11ff\u2e80-\u9fff\ua960-\ua97f\uac00-\ud7ff\uf900-\ufaff\uff00-\uffef]")


def font_file(family: str, bold: bool = False, italic: bool = False) -> str:
    files = FAMILIES.get(family)
    if not files:
        return ""
    p = os.path.join(FONT_DIR, files[(1 if bold else 0) + (2 if italic else 0)])
    if os.path.isfile(p):
        return p
    p = os.path.join(FONT_DIR, files[0])
    return p if os.path.isfile(p) else ""


def available_fonts() -> list:
    """Font families the editor can write with → [{name, css, bold, italic}]."""
    out = []
    for fam, files in FAMILIES.items():
        if os.path.isfile(os.path.join(FONT_DIR, files[0])):
            out.append({"name": fam, "css": CSS_FAMILY.get(fam, "sans-serif"),
                        "bold": os.path.isfile(os.path.join(FONT_DIR, files[1])) and files[1] != files[0],
                        "italic": os.path.isfile(os.path.join(FONT_DIR, files[2])) and files[2] != files[0]})
    return out


def clean_font_name(name: str) -> str:
    return _SUBSET.sub("", str(name or "")).strip()


def style_from(name: str, flags: int) -> tuple:
    """(bold, italic) from the span flags and the font name."""
    n = clean_font_name(name).lower()
    bold = bool(flags & 16) or any(k in n for k in ("bold", "black", "heavy", "semibold", "demi")) \
        or bool(re.search(r"(,|-)bd$|bd$", n))
    italic = bool(flags & 2) or "italic" in n or "oblique" in n
    return bold, italic


def family_for(name: str, flags: int = 0, text: str = "") -> str:
    """Closest Windows family for a PDF font name."""
    n = clean_font_name(name).lower().replace(" ", "")
    if _CJK.search(text or "") or any(k in n for k in ("malgun", "gulim", "batang", "dotum", "gungsuh",
                                                      "nanum", "hygothic", "hymyeongjo")):
        return CJK_FAMILY
    if "calibri" in n or "carlito" in n:
        return "Calibri"
    if "times" in n or "tinos" in n or "liberationserif" in n or "nimbusrom" in n:
        return "Times New Roman"
    if "courier" in n or "cousine" in n or "mono" in n:
        return "Courier New"
    if "georgia" in n:
        return "Georgia"
    if "verdana" in n:
        return "Verdana"
    if "segoe" in n:
        return "Segoe UI"
    if "tahoma" in n:
        return "Tahoma"
    if "arial" in n or "helvetica" in n or "helv" in n or "arimo" in n or "liberationsans" in n:
        return "Arial"
    if flags & 8:
        return "Courier New"
    if flags & 4:
        return "Times New Roman"
    return "Arial"


def _int_hex(c) -> str:
    c = int(c or 0) & 0xFFFFFF
    return f"#{c:06x}"


def _hex_rgb(h, default=(0.0, 0.0, 0.0)) -> tuple:
    if h is None or h == "":
        return default
    if isinstance(h, (list, tuple)):
        return tuple(float(v) for v in h[:3])
    if isinstance(h, int):
        h = _int_hex(h)
    s = str(h).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# opening / geometry
# ─────────────────────────────────────────────────────────────────────────────

def _open(path: str):
    fitz = _fitz()
    path = os.path.abspath(str(path or ""))
    if not os.path.isfile(path):
        raise PdfEditError(f"File not found: {os.path.basename(path)}", "not_found")
    if not path.lower().endswith(".pdf"):
        raise PdfEditError("Only PDF files can be edited", "not_pdf")
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise PdfEditError(f"Could not open the PDF: {exc}", "open_failed")
    if doc.needs_pass:
        doc.close()
        raise PdfEditError("This PDF is password-protected. Remove the password first.", "encrypted")
    if not doc.is_pdf:
        doc.close()
        raise PdfEditError("Only PDF files can be edited", "not_pdf")
    return doc


def _r4(r) -> list:
    return [round(float(r[0]), 2), round(float(r[1]), 2), round(float(r[2]), 2), round(float(r[3]), 2)]


def _disp_rect(page, r):
    fitz = _fitz()
    return (fitz.Rect(r) * page.rotation_matrix).normalize()


def _vec(m, d) -> tuple:
    """Direction vector *d* through the linear part of matrix *m*."""
    fitz = _fitz()
    v = fitz.Point(d) * m - fitz.Point(0, 0) * m
    return (round(v.x, 3), round(v.y, 3))


# ─────────────────────────────────────────────────────────────────────────────
# extraction
# ─────────────────────────────────────────────────────────────────────────────

def _visible_text_chars(page) -> tuple:
    """(visible, invisible) glyph counts — OCR layers over scans are invisible (type 3)."""
    vis = inv = 0
    try:
        for tr in page.get_texttrace():
            n = len(tr.get("chars") or ())
            if tr.get("type") == 3 or float(tr.get("opacity", 1) or 0) == 0:
                inv += n
            else:
                vis += n
    except Exception:
        txt = page.get_text("text") or ""
        vis = len(txt.strip())
    return vis, inv


def _images(page) -> list:
    fitz = _fitz()
    out = []
    pr = page.rect
    parea = max(1.0, pr.width * pr.height)
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = []
    for i, im in enumerate(infos):
        r = _disp_rect(page, im["bbox"])
        r = r & pr
        if r.is_empty or r.width < 2 or r.height < 2:
            continue
        xref = int(im.get("xref") or 0)
        out.append({
            "id": f"i{i}", "bbox": _r4(r), "xref": xref,
            "width": int(im.get("width") or 0), "height": int(im.get("height") or 0),
            "background": (r.width * r.height) / parea > 0.85,
            "replaceable": xref > 0,
            "_transform": list(im.get("transform") or (1, 0, 0, 1, 0, 0)),
        })
    return out


def is_scanned(page, images=None) -> bool:
    vis, _inv = _visible_text_chars(page)
    if vis > 0:
        return False
    images = _images(page) if images is None else images
    pr = page.rect
    area = sum((im["bbox"][2] - im["bbox"][0]) * (im["bbox"][3] - im["bbox"][1]) for im in images)
    return area >= 0.5 * pr.width * pr.height


def _line_info(line, md, mu) -> dict | None:
    """One text line; *md* maps the dict's coordinates to display space, *mu* to
    unrotated page space (where redaction annotations live)."""
    fitz = _fitz()
    spans = []
    for s in line.get("spans") or []:
        t = s.get("text") or ""
        if not t:
            continue
        spans.append(s)
    if not spans:
        return None
    text = "".join(s["text"] for s in spans).translate(_NORM)
    if not text.strip():
        return None
    dom = max(spans, key=lambda s: len((s.get("text") or "").strip()))
    bold, italic = style_from(dom.get("font", ""), int(dom.get("flags") or 0))
    bbox = fitz.Rect()
    for s in spans:
        if (s.get("text") or "").strip():
            bbox |= fitz.Rect(s["bbox"])
    if bbox.is_empty:
        bbox = fitz.Rect(line["bbox"])
    ldir = line.get("dir") or (1, 0)
    d = _vec(md, ldir)
    origin = fitz.Point(dom.get("origin") or spans[0].get("origin")) * md
    first_origin = fitz.Point(spans[0].get("origin")) * md
    return {
        "text": text.rstrip("\n"),
        "bbox": _r4((bbox * md).normalize()),
        "origin": [round(first_origin.x, 2), round(origin.y, 2)],
        "font": clean_font_name(dom.get("font", "")),
        "raw_font": dom.get("font", ""),
        "size": round(float(dom.get("size") or 0), 2),
        "color": _int_hex(dom.get("color")),
        "bold": bold, "italic": italic,
        "flags": int(dom.get("flags") or 0),
        "dir": d,
        "_spans": [((fitz.Rect(s["bbox"]) * mu).normalize(), _vec(mu, ldir), float(s.get("size") or 0))
                   for s in spans],
    }


def _detect_align(page, lines: list) -> str:
    pr = page.rect
    if len(lines) >= 2:
        x0 = [ln["bbox"][0] for ln in lines]
        x1 = [ln["bbox"][2] for ln in lines]
        cx = [(a + b) / 2 for a, b in zip(x0, x1)]
        sp = lambda v: max(v) - min(v)
        if sp(x0) <= 1.5:
            return "left"
        if sp(cx) <= 1.5:
            return "center"
        if sp(x1) <= 1.5:
            return "right"
        return "left"
    b = lines[0]["bbox"]
    cx = (b[0] + b[2]) / 2
    if abs(cx - pr.width / 2) <= 0.03 * pr.width and b[0] > 0.12 * pr.width:
        return "center"
    if b[2] > 0.75 * pr.width and b[0] > 0.5 * pr.width and pr.width - b[2] < 0.15 * pr.width:
        return "right"
    return "left"


def _textdict(page) -> dict:
    """get_text('dict') of the page *contents only* — text drawn by form fields
    and annotations is not part of the page and can't be edited here.
    → (dict, matrix to display space, matrix to unrotated page space)."""
    fitz = _fitz()
    flags = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_MEDIABOX_CLIP
    try:
        dl = page.get_displaylist(annots=False)
        tp = dl.get_textpage(flags)
        if not isinstance(tp, fitz.TextPage):
            tp = fitz.TextPage(tp)
        tp.parent = page
        # a display list is built in display (rotated) space
        return tp.extractDICT(), fitz.Identity, page.derotation_matrix
    except Exception:
        return page.get_text("dict", flags=flags), page.rotation_matrix, fitz.Identity


_NORM = str.maketrans({0x00A0: " ", 0x00AD: "-"})   # no-break space, soft hyphen


def _units(page) -> list:
    """Editable blocks of *page* (internal: keeps the raw span rects)."""
    fitz = _fitz()
    d, md, mu = _textdict(page)
    units = []
    for blk in d.get("blocks") or []:
        if blk.get("type") != 0:
            continue
        cur = []
        prev = None
        for line in blk.get("lines") or []:
            li = _line_info(line, md, mu)
            if not li:
                continue
            if prev is not None:
                same_style = (li["font"] == prev["font"] and abs(li["size"] - prev["size"]) < 0.6
                              and li["dir"] == prev["dir"])
                gap = li["origin"][1] - prev["origin"][1]
                ok_gap = 0 < gap <= max(li["size"], prev["size"]) * 2.2
                if not (same_style and ok_gap):
                    units.append(cur)
                    cur = []
            cur.append(li)
            prev = li
        if cur:
            units.append(cur)

    out = []
    for k, lines in enumerate(units):
        bbox = fitz.Rect()
        for ln in lines:
            bbox |= fitz.Rect(ln["bbox"])
        chars = {}
        for ln in lines:
            key = (ln["raw_font"], ln["size"], ln["color"], ln["bold"], ln["italic"], ln["flags"])
            chars[key] = chars.get(key, 0) + len(ln["text"].strip())
        raw_font, size, color, bold, italic, flags = max(chars, key=chars.get)
        text = "\n".join(ln["text"] for ln in lines)
        fam = family_for(raw_font, flags, text)
        if len(lines) >= 2:
            gaps = [lines[i + 1]["origin"][1] - lines[i]["origin"][1] for i in range(len(lines) - 1)]
            lh = sum(gaps) / len(gaps)
        else:
            lh = 0.0
        horizontal = all(abs(ln["dir"][0] - 1) < 0.02 and abs(ln["dir"][1]) < 0.02 for ln in lines)
        out.append({
            "id": f"b{k}",
            "bbox": _r4(bbox),
            "text": text,
            "lines": lines,
            "font": clean_font_name(raw_font),
            "raw_font": raw_font,
            "family": fam,
            "css_family": CSS_FAMILY.get(fam, "sans-serif"),
            "size": size,
            "color": color,
            "bold": bold,
            "italic": italic,
            "flags": flags,
            "align": _detect_align(page, lines),
            "line_height": round(lh, 2),
            "editable": horizontal,
        })
    return out


def _public_block(b: dict) -> dict:
    o = {k: v for k, v in b.items() if k not in ("lines", "raw_font", "flags")}
    o["lines"] = [{k: v for k, v in ln.items() if not k.startswith("_") and k not in ("raw_font", "flags")}
                  for ln in b["lines"]]
    return o


def page_count(path: str) -> int:
    doc = _open(path)
    try:
        return doc.page_count
    finally:
        doc.close()


def extract_blocks(path: str, page: int = 0) -> dict:
    """Editable text blocks + images of one page (display coords)."""
    doc = _open(path)
    try:
        pno = int(page or 0)
        if pno < 0 or pno >= doc.page_count:
            raise PdfEditError("Page out of range", "bad_page")
        pg = doc.load_page(pno)
        imgs = _images(pg)
        if is_scanned(pg, imgs):
            raise PdfEditError(SCANNED_MSG, "scanned_page")
        blocks = [_public_block(b) for b in _units(pg)]
        return {"page": pno, "page_count": doc.page_count,
                "width": round(pg.rect.width, 2), "height": round(pg.rect.height, 2),
                "rotation": pg.rotation, "blocks": blocks,
                "images": [{k: v for k, v in im.items() if not k.startswith("_")} for im in imgs]}
    finally:
        doc.close()


def render_page(path: str, page: int = 0, scale: float = 1.5) -> dict:
    """Page image (with form fields / annotations) → {data_url, width, height}."""
    fitz = _fitz()
    doc = _open(path)
    try:
        pno = int(page or 0)
        if pno < 0 or pno >= doc.page_count:
            raise PdfEditError("Page out of range", "bad_page")
        pg = doc.load_page(pno)
        r = pg.rect
        s = max(0.05, min(float(scale or 1.5), 6.0))
        s = min(s, math.sqrt(24e6 / max(1.0, r.width * r.height)))
        pix = pg.get_pixmap(matrix=fitz.Matrix(s, s), annots=True)
        data = pix.tobytes("png")
        return {"data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
                "width": pix.width, "height": pix.height}
    finally:
        doc.close()


def doc_info(path: str) -> dict:
    """{pages:[{w,h,scanned}]} for the page strip."""
    doc = _open(path)
    try:
        pages = []
        for pno in range(doc.page_count):
            pg = doc.load_page(pno)
            try:
                sc = is_scanned(pg)
            except Exception:
                sc = False
            pages.append({"w": round(pg.rect.width, 2), "h": round(pg.rect.height, 2), "scanned": sc})
        return {"pages": pages, "name": os.path.basename(path)}
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# writing
# ─────────────────────────────────────────────────────────────────────────────

class _FontPick:
    def __init__(self, alias, font, used, original, fontfile="", buffer=None):
        self.alias = alias
        self.font = font            # fitz.Font for measuring / glyph checks
        self.used = used            # human name
        self.original = original    # True = the PDF's own embedded font
        self.fontfile = fontfile
        self.buffer = buffer


def _has_all(font, text: str) -> bool:
    for ch in text:
        if ch.isspace():
            continue
        try:
            if not font.has_glyph(ord(ch)):
                return False
        except Exception:
            return False
    return True


class _Fonts:
    """Per-document font cache: original embedded fonts and Windows fonts."""

    def __init__(self, doc):
        self.doc = doc
        self._file_fonts = {}
        self._orig = {}
        self._n = 0

    def _alias(self) -> str:
        self._n += 1
        return f"OAE{self._n}"

    def original(self, page, raw_name: str):
        """_FontPick for the page's embedded font *raw_name*, or None if subset / missing."""
        fitz = _fitz()
        key = (page.number, raw_name)
        if key in self._orig:
            return self._orig[key]
        pick = None
        want = clean_font_name(raw_name)
        def squash(s):      # 'Arial-BoldMT' == 'Arial Bold', 'ArialMT' == 'Arial Regular'
            s = re.sub(r"[^a-z0-9]", "", clean_font_name(s).lower())
            return re.sub(r"(psmt|mt|ps|regular|roman|std)$", "", s)
        try:
            for f in page.get_fonts(full=False):
                xref, ext, basefont = f[0], f[1], f[3]
                if ext in ("n/a", "", None):
                    continue
                try:
                    _bn, fext, _ft, content = self.doc.extract_font(xref)
                except Exception:
                    continue
                if not content or fext not in ("ttf", "otf", "cff"):
                    continue
                try:
                    font = fitz.Font(fontbuffer=content)
                except Exception:
                    continue
                if squash(basefont) != squash(want) and squash(font.name) != squash(want):
                    continue
                if _SUBSET.match(basefont or ""):
                    break                    # subset: glyphs for new text are missing
                pick = _FontPick(self._alias(), font, want, True, buffer=content)
                break
        except Exception:
            pick = None
        self._orig[key] = pick
        return pick

    def windows(self, family: str, bold: bool, italic: bool):
        fitz = _fitz()
        path = font_file(family, bold, italic)
        if not path:
            return None
        if path not in self._file_fonts:
            try:
                font = fitz.Font(fontfile=path)
            except Exception:
                self._file_fonts[path] = None
                return None
            files = [f.lower() for f in FAMILIES[family]]
            base = os.path.basename(path).lower()
            idx = files.index(base) if base in files else 0
            label = family + (" Bold" if idx in (1, 3) else "") + (" Italic" if idx in (2, 3) else "")
            self._file_fonts[path] = _FontPick(self._alias(), font, label, False, fontfile=path)
        return self._file_fonts[path]

    def helv(self):
        fitz = _fitz()
        if "helv" not in self._file_fonts:
            self._file_fonts["helv"] = _FontPick("helv", fitz.Font("helv"), "Helvetica", False)
        return self._file_fonts["helv"]


def _choose_font(fonts: _Fonts, page, b: dict | None, e: dict, text: str) -> tuple:
    """→ (_FontPick, wanted_name). Original embedded font first, then Windows fonts."""
    want_family = str(e.get("font") or "").strip()
    bold = bool(e["bold"]) if e.get("bold") is not None else bool(b and b["bold"])
    italic = bool(e["italic"]) if e.get("italic") is not None else bool(b and b["italic"])
    wanted = (b["font"] if b else (want_family or "Arial")) or "Arial"
    style_changed = b is not None and (bold != b["bold"] or italic != b["italic"])
    if b is not None and (not want_family or want_family.lower() == "original") and not style_changed:
        pick = fonts.original(page, b["raw_font"])
        if pick and _has_all(pick.font, text):
            return pick, wanted
    if want_family and want_family.lower() != "original" and want_family in FAMILIES:
        fam = want_family
        wanted = want_family + (" Bold" if bold else "") + (" Italic" if italic else "")
    else:
        fam = family_for(b["raw_font"] if b else "", b["flags"] if b else 0, text) if b else "Arial"
        if b is not None and _CJK.search(text):
            fam = CJK_FAMILY
    cands = [fam]
    if _CJK.search(text) and CJK_FAMILY not in cands:
        cands.append(CJK_FAMILY)
    cands += ["Arial", CJK_FAMILY]
    for f in cands:
        pick = fonts.windows(f, bold, italic)
        if pick and _has_all(pick.font, text):
            return pick, wanted
    return fonts.helv(), wanted


def _wrap(font, size: float, text: str, width: float) -> list:
    lines = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = (cur + " " + w) if cur else w
            if not cur or font.text_length(cand, fontsize=size) <= width + 0.01:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _layout(page, font, b: dict | None, e: dict, text: str) -> tuple:
    """→ (size, [(x, baseline_y, line_text)], align) in display coords."""
    pr = page.rect
    margin = 18.0
    req_size = float(e.get("size") or (b["size"] if b else 12) or 12)
    req_size = max(2.0, min(req_size, 400.0))
    size = req_size
    if b is not None:
        ob = b["bbox"]
        nb = [float(v) for v in (e.get("bbox") or ob)]
        dx, dy = nb[0] - ob[0], nb[1] - ob[1]
        resized = e.get("bbox") is not None and (abs((nb[2] - nb[0]) - (ob[2] - ob[0])) > 0.5)
        align = str(e.get("align") or b["align"] or "left")
        first = b["lines"][0]
        asc = first["origin"][1] - first["bbox"][1]
        base0 = nb[1] + asc * (req_size / (b["size"] or req_size))
        lh = (b["line_height"] * req_size / b["size"]) if (b["line_height"] and b["size"]) else req_size * 1.2
        single = len(b["lines"]) == 1 and "\n" not in text and not resized
        if single:
            x0, x1 = first["bbox"][0] + dx, first["bbox"][2] + dx
            cx = (x0 + x1) / 2
            if align == "center":
                avail = 2 * max(10.0, min(cx - margin, pr.width - margin - cx))
            elif align == "right":
                avail = max(10.0, x1 - margin)
            else:
                avail = max(10.0, pr.width - margin - x0)
            w = font.text_length(text, fontsize=size)
            if w > avail:
                size = max(2.0, size * avail / w)
            w = font.text_length(text, fontsize=size)
            x = cx - w / 2 if align == "center" else (x1 - w if align == "right" else first["origin"][0] + dx)
            return size, [(x, first["origin"][1] + dy, text)], align
        left, right = nb[0], nb[2]
    else:
        nb = [float(v) for v in (e.get("rect") or [50, 50, 250, 80])]
        align = str(e.get("align") or "left")
        left, right = nb[0], nb[2]
        lh = req_size * 1.2
        try:
            asc = float(font.ascender) * req_size
        except Exception:
            asc = req_size * 0.8
        base0 = nb[1] + asc
    width = max(4.0, right - left)
    # shrink if a single word cannot fit the box width
    widest = max((font.text_length(w, fontsize=size) for w in re.split(r"[ \n]", text) if w), default=0)
    if widest > width:
        size = max(2.0, size * width / widest)
        lh = lh * size / req_size
    lines = _wrap(font, size, text, width)
    out = []
    for i, ln in enumerate(lines):
        w = font.text_length(ln, fontsize=size)
        if align == "center":
            x = left + (width - w) / 2
        elif align == "right":
            x = right - w
        else:
            x = left
        out.append((x, base0 + i * lh, ln))
    return size, out, align


def _write_lines(page, pick: _FontPick, size: float, color, lines: list, used: dict):
    """Draw *lines* [(x, baseline_y, text)] (display coords) upright on *page*.
    *used* collects {font xref: (fitz.Font, set(chars))} for the ToUnicode fix."""
    fitz = _fitz()
    xref = 0
    if pick.alias != "helv":
        if pick.buffer is not None:
            xref = page.insert_font(fontname=pick.alias, fontbuffer=pick.buffer)
        else:
            xref = page.insert_font(fontname=pick.alias, fontfile=pick.fontfile)
    dm = page.derotation_matrix
    for x, y, t in lines:
        if not t.strip():
            continue
        pt = fitz.Point(x, y) * dm
        page.insert_text(pt, t, fontsize=size, fontname=pick.alias, color=color,
                         rotate=page.rotation)
        if xref:
            used.setdefault(xref, (pick.font, set()))[1].update(t)


def _fix_tounicode(doc, used: dict):
    """MuPDF builds the ToUnicode map of an inserted TrueType font from the font's
    cmap, so glyphs shared by several code points (space / no-break space,
    hyphen / soft hyphen) copy out as the wrong character. Rewrite the map for
    exactly the characters we wrote (plain character wins for a shared glyph)."""
    for xref, (font, chars) in used.items():
        try:
            t, v = doc.xref_get_key(xref, "ToUnicode")
            m = re.match(r"(\d+) 0 R", v or "")
            if t != "xref" or not m:
                continue
            pairs = {}
            for ch in chars:
                gid = font.has_glyph(ord(ch))
                if gid and (gid not in pairs or ord(ch) < ord(pairs[gid])):
                    pairs[gid] = ch
            if not pairs:
                continue
            items = sorted(pairs.items())
            body = []
            for i in range(0, len(items), 100):
                chunk = items[i:i + 100]
                body.append(f"{len(chunk)} beginbfchar")
                body += [f"<{gid:04x}> <{ch.encode('utf-16-be').hex()}>" for gid, ch in chunk]
                body.append("endbfchar")
            cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                    "/CIDSystemInfo <</Registry(Adobe)/Ordering(UCS)/Supplement 0>> def\n"
                    "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
                    "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
                    + "\n".join(body)
                    + "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
            doc.update_stream(int(m.group(1)), cmap.encode("ascii"))
        except Exception:
            continue


def _redact_rect(r, d, size):
    """Shrink a span rect across the line direction so neighbours are never caught."""
    fitz = _fitz()
    r = fitz.Rect(r)
    if abs(d[0]) >= abs(d[1]):
        inset = r.height * 0.28
        return fitz.Rect(r.x0 + 0.2, r.y0 + inset, r.x1 - 0.2, r.y1 - inset)
    inset = r.width * 0.28
    return fitz.Rect(r.x0 + inset, r.y0 + 0.2, r.x1 - inset, r.y1 - 0.2)


def _apply_text_redactions(doc, page, rects: list):
    """Remove only the text glyphs under *rects* (images, line art, widgets stay)."""
    fitz = _fitz()
    # Existing (user) Redact annotations must not be applied by us: hide them.
    held = []
    for an in page.annots(types=[fitz.PDF_ANNOT_REDACT]) or []:
        held.append(an.xref)
    for x in held:
        doc.xref_set_key(x, "Subtype", "/OAHeldRedact")
    if held:
        page = doc.reload_page(page)
    for r in rects:
        if r.is_empty or r.width <= 0 or r.height <= 0:
            continue
        page.add_redact_annot(r, fill=False, cross_out=False)
    kw = dict(images=fitz.PDF_REDACT_IMAGE_NONE)
    if hasattr(fitz, "PDF_REDACT_LINE_ART_NONE"):
        kw["graphics"] = fitz.PDF_REDACT_LINE_ART_NONE
    page.apply_redactions(**kw)
    for x in held:
        doc.xref_set_key(x, "Subtype", "/Redact")
    if held:
        page = doc.reload_page(page)
    return page


def _decode_data_url(du: str) -> bytes:
    m = re.match(r"data:[^;,]*(;base64)?,(.*)$", str(du or ""), re.S)
    if not m:
        raise PdfEditError("Bad image data", "bad_image")
    return base64.b64decode(m.group(2)) if m.group(1) else m.group(2).encode()


def _image_bytes_for(im: dict, page, data: bytes, fit: str) -> bytes:
    """New image bytes, padded (transparent) to the placement's aspect for 'contain'."""
    from PIL import Image
    try:
        src = Image.open(io.BytesIO(data))
        src.load()
    except Exception as exc:
        raise PdfEditError(f"Couldn't read the new image: {exc}", "bad_image")
    if src.mode not in ("RGB", "RGBA"):
        src = src.convert("RGBA")
    if fit != "stretch":
        fitz = _fitz()
        t = fitz.Matrix(im["_transform"]) * page.rotation_matrix
        bw, bh = im["bbox"][2] - im["bbox"][0], im["bbox"][3] - im["bbox"][1]
        swapped = abs(t.a) + abs(t.d) < abs(t.b) + abs(t.c)
        target = (bh / bw) if swapped else (bw / bh)
        sw, sh = src.size
        if abs(sw / sh - target) > 0.01:
            if sw / sh > target:
                W, H = sw, max(1, int(round(sw / target)))
            else:
                W, H = max(1, int(round(sh * target))), sh
            canvas = Image.new("RGBA", (W, H), (255, 255, 255, 0))
            canvas.paste(src.convert("RGBA"), ((W - sw) // 2, (H - sh) // 2))
            src = canvas
    buf = io.BytesIO()
    src.save(buf, "PNG")
    return buf.getvalue()


def _image_uses(doc, xref: int) -> int:
    n = 0
    for pno in range(doc.page_count):
        try:
            n += sum(1 for im in doc.load_page(pno).get_image_info(xrefs=True) if im.get("xref") == xref)
        except Exception:
            pass
    return n


def default_output(path: str) -> str:
    """'<dir>/x.pdf' → '<dir>/x_edited.pdf' (free name; no '_edited_edited')."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if re.search(r"_edited( \(\d+\))?$", stem):
        base = re.sub(r" \(\d+\)$", "", stem)
        return _pt.unique_path(os.path.join(os.path.dirname(os.path.abspath(path)), base + ".pdf"))
    return _pt.default_output(path, "_edited", ".pdf")


def _set_metadata(doc):
    fitz = _fitz()
    now = "D:" + _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    md = dict(doc.metadata or {})
    keep = {k: (md.get(k) or "") for k in ("title", "author", "subject", "keywords", "creator", "creationDate")}
    keep["producer"] = "Office Axe"
    keep["modDate"] = now
    try:
        doc.set_metadata(keep)
    except Exception:
        pass
    try:
        t, v = doc.xref_get_key(-1, "Info")
        m = re.match(r"(\d+) 0 R", v or "")
        if t == "xref" and m:
            note = "Text edited with Office Axe on " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            doc.xref_set_key(int(m.group(1)), "OfficeAxeEdit", fitz.get_pdf_str(note))
    except Exception:
        pass


def _edit_bytes(path: str, edits: list) -> tuple:
    fitz = _fitz()
    doc = _open(path)
    try:
        fonts = _Fonts(doc)
        used = {}
        substituted, warnings = [], []
        count = 0
        by_page = {}
        for e in edits or []:
            if not isinstance(e, dict) or not e.get("type"):
                continue
            pno = int(e.get("page", 0) or 0)
            if pno < 0 or pno >= doc.page_count:
                raise PdfEditError(f"Page {pno + 1} does not exist", "bad_page")
            by_page.setdefault(pno, []).append(e)

        for pno in sorted(by_page):
            page = doc.load_page(pno)
            pedits = by_page[pno]
            block_edits = [e for e in pedits if e["type"] in ("text", "move", "delete")]
            imgs = _images(page)
            if block_edits and is_scanned(page, imgs):
                raise PdfEditError(SCANNED_MSG, "scanned_page")
            blocks = {b["id"]: b for b in _units(page)} if block_edits else {}
            img_by_id = {im["id"]: im for im in imgs}

            # 1) images (placement-level edits, before the text layer changes)
            for e in pedits:
                if e["type"] not in ("delete_image", "replace_image"):
                    continue
                im = img_by_id.get(str(e.get("image_id") or ""))
                if im is None:
                    raise PdfEditError("That image was not found on the page — reload the file.", "stale")
                if not im["replaceable"]:
                    raise PdfEditError("This image is stored inline and can't be changed.", "inline_image")
                if _image_uses(doc, im["xref"]) > 1:
                    warnings.append(f"The image on page {pno + 1} is used more than once in this PDF; "
                                    "every copy was changed.")
                if e["type"] == "delete_image":
                    page.delete_image(im["xref"])
                else:
                    data = _image_bytes_for(im, page, _decode_data_url(e.get("data_url")),
                                            str(e.get("fit") or "contain"))
                    page.replace_image(im["xref"], stream=data)
                count += 1

            # 2) remove old glyphs of every changed / moved / deleted block
            writes = []
            rects = []
            seen = set()
            for e in block_edits:
                bid = str(e.get("block_id") or "")
                b = blocks.get(bid)
                if b is None:
                    raise PdfEditError("A text block was not found — the file may have changed. Reload it.",
                                       "stale")
                if e.get("orig_text") is not None and str(e["orig_text"]) != b["text"]:
                    raise PdfEditError("The text on the page changed since it was opened. Reload the file.",
                                       "stale")
                if bid in seen:
                    continue
                seen.add(bid)
                if e["type"] == "text":
                    nt = str(e.get("new_text") if e.get("new_text") is not None else b["text"])
                    nt = nt.replace("\r\n", "\n").replace("\r", "\n")
                    same = (nt == b["text"] and not e.get("bbox")
                            and all(e.get(k) in (None, "", b.get(k)) for k in ("size", "color", "bold", "italic",
                                                                              "align"))
                            and str(e.get("font") or "").lower() in ("", "original", b["family"].lower()))
                    if same:
                        continue
                elif e["type"] == "move":
                    nt = b["text"]
                else:
                    nt = None
                for ln in b["lines"]:
                    for (r, d, sz) in ln["_spans"]:
                        rects.append(_redact_rect(r, d, sz))
                count += 1
                if nt is not None and nt.strip():
                    writes.append((b, e, nt))
            if rects:
                page = _apply_text_redactions(doc, page, rects)

            # 3) write the new text
            for b, e, nt in writes:
                pick, wanted = _choose_font(fonts, page, b, e, nt)
                size, lines, _align = _layout(page, pick.font, b, e, nt)
                color = _hex_rgb(e.get("color") or b["color"])
                _write_lines(page, pick, size, color, lines, used)
                if not pick.original:
                    substituted.append({"block_id": b["id"], "page": pno, "wanted": wanted, "used": pick.used})
            for e in pedits:
                if e["type"] != "add_text":
                    continue
                t = str(e.get("text") or "").replace("\r\n", "\n")
                if not t.strip():
                    continue
                if not e.get("rect"):
                    raise PdfEditError("A new text box has no position", "bad_edit")
                pick, wanted = _choose_font(fonts, page, None, e, t)
                size, lines, _align = _layout(page, pick.font, None, e, t)
                _write_lines(page, pick, size, _hex_rgb(e.get("color") or "#000000"), lines, used)
                if str(e.get("font") or "Arial") not in pick.used:
                    substituted.append({"block_id": "", "page": pno, "wanted": wanted, "used": pick.used})
                count += 1
            for e in pedits:
                if e["type"] not in ("text", "move", "delete", "add_text", "delete_image", "replace_image"):
                    raise PdfEditError(f"Unknown edit: {e['type']}", "bad_edit")

        _set_metadata(doc)
        if used:
            try:
                doc.subset_fonts()
            except Exception:
                pass
            _fix_tounicode(doc, used)
        buf = io.BytesIO()
        doc.save(buf, garbage=3, deflate=True)
        return buf.getvalue(), substituted, warnings, count
    finally:
        doc.close()


def apply_edits(path: str, edits: list, out_path: str | None = None, replace: bool = False,
                release_handles=None) -> dict:
    """Apply *edits* → new file (default) or over *path* after a `_to_review/` backup."""
    path = os.path.abspath(str(path or ""))
    data, substituted, warnings, count = _edit_bytes(path, edits)
    if not replace:
        target = out_path or default_output(path)
        if not target.lower().endswith(".pdf"):
            target += ".pdf"
        final = _pt._write_new(os.path.abspath(target), data)
        return {"out_path": final, "backup": "", "replaced": False, "substituted": substituted,
                "warnings": warnings, "count": count}
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
        raise PdfEditError(
            f"Could not overwrite {os.path.basename(path)} ({exc.strerror or exc}). "
            f"Your edits were saved as {os.path.basename(alt)} instead.", "locked")
    return {"out_path": path, "backup": backup, "replaced": True, "substituted": substituted,
            "warnings": warnings, "count": count}
