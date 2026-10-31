"""
pdf_objects.py — page objects (images, vector graphics, form XObjects) for the
PDF editor (pdf_edit.py), like the objects Acrobat's "Edit PDF" lets you pick.

The page content stream is tokenised and interpreted just enough to know, for
every painted path, image and form XObject, *which operators draw it*, under
which transformation (CTM) and clipping, and where it lands on the page. That
gives two things:

    extract(page)                 → [{id, kind, bbox, …}] in display coordinates
    rewrite(doc, page, edits)     → change just those operators in place

An edit moves / resizes / rotates / crops (an extra ``cm`` around the object's
own operators), deletes (the operators are dropped), recolours a vector object
(colour / line-width operators inside a ``q … Q`` around it) or swaps an image
(the ``Do`` names a new image XObject). Every other operator — text above all —
is copied byte for byte, so text stays selectable, nothing is flattened, and
annotations / form fields (which live outside the content stream) are never
touched.

When an object has to leave its clipping area (a moved picture that Word
clipped to its frame, a cropped image, …) the graphics state is unwound with
``Q`` at the object's place in the stream, the object is drawn with the saved
state replayed under the extra transform (so its own clip moves with it), and
the state is then rebuilt — the stacking order never changes.

Coordinates: *display* units — PDF points, page /Rotate applied, origin top
left — like the rest of the editor. Object ids ("o0", "o1", …) are the order
in the stream after grouping, so the same file gives the same ids.
"""
from __future__ import annotations

import math
import re

# ─────────────────────────────────────────────────────────────────────────────
# tokenizer
# ─────────────────────────────────────────────────────────────────────────────

_WS = b" \t\r\n\x0c\x00"
_TOK = re.compile(rb"""
    (?P<ws>[ \t\r\n\x0c\x00]+|%[^\r\n]*)
  | (?P<num>[+-]?(?:\d+\.?\d*|\.\d+)(?![^ \t\r\n\x0c\x00()<>\[\]{}/%]))
  | (?P<name>/[^ \t\r\n\x0c\x00()<>\[\]{}/%]*)
  | (?P<dopen><<) | (?P<dclose>>>)
  | (?P<hex><[^>]*>)
  | (?P<lp>\()
  | (?P<arr>[\[\]])
  | (?P<brace>[{}])
  | (?P<kw>[^ \t\r\n\x0c\x00()<>\[\]{}/%]+)
  | (?P<bad>.)
""", re.X | re.S)

_MARK_ARR = object()
_MARK_DICT = object()


class Op:
    __slots__ = ("op", "args", "start", "end")

    def __init__(self, op, args, start, end):
        self.op = op
        self.args = args
        self.start = start
        self.end = end

    def __repr__(self):            # pragma: no cover - debugging aid
        return f"Op({self.op!r}, {self.args!r}, {self.start}, {self.end})"


def _name(raw: bytes) -> str:
    s = raw[1:]
    if b"#" in s:
        s = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), s)
    return s.decode("latin-1")


def _lit_end(data: bytes, i: int) -> int:
    """*i* points after '('; → index after the matching ')'."""
    depth = 1
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:          # backslash
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _inline_image_end(data: bytes, i: int) -> int:
    """*i* points just after 'ID'; → index after the closing 'EI'."""
    n = len(data)
    if i < n and data[i] in _WS:
        i += 1
    j = i
    while True:
        k = data.find(b"EI", j)
        if k < 0:
            return n
        before = data[k - 1] if k > 0 else 0x20
        after = data[k + 2] if k + 2 < n else 0x20
        if before in _WS and after in _WS:
            return k + 2
        j = k + 2


def tokenize(data: bytes) -> list:
    """Content stream → [Op]. Unknown / broken bytes are skipped."""
    ops = []
    stack = []
    start = None
    i, n = 0, len(data)
    m = _TOK.match
    while i < n:
        t = m(data, i)
        if t is None:
            i += 1
            continue
        kind = t.lastgroup
        tok_start, i = t.start(), t.end()
        if kind == "ws" or kind == "bad" or kind == "brace":
            continue
        if start is None:
            start = tok_start
        if kind == "num":
            stack.append(float(t.group()))
        elif kind == "name":
            stack.append(("/", _name(t.group())))
        elif kind == "hex":
            stack.append(("x", t.group()))
        elif kind == "lp":
            i = _lit_end(data, i)
            stack.append(("s", data[tok_start:i]))
        elif kind == "arr":
            if t.group() == b"[":
                stack.append(_MARK_ARR)
            else:
                k = len(stack) - 1
                while k >= 0 and stack[k] is not _MARK_ARR:
                    k -= 1
                if k >= 0:
                    stack[k:] = [list(stack[k + 1:])]
        elif kind == "dopen":
            stack.append(_MARK_DICT)
        elif kind == "dclose":
            k = len(stack) - 1
            while k >= 0 and stack[k] is not _MARK_DICT:
                k -= 1
            if k >= 0:
                stack[k:] = [("d", list(stack[k + 1:]))]
        else:  # keyword
            w = t.group().decode("latin-1")
            if w in ("true", "false", "null"):
                stack.append(w)
                continue
            if w == "BI":
                # inline image: key/value pairs up to ID, binary data, EI
                j = data.find(b"ID", i)
                while j >= 0 and not ((j + 2 >= n or data[j + 2] in _WS) and data[j - 1] in _WS):
                    j = data.find(b"ID", j + 2)
                if j < 0:
                    i = n
                    break
                end = _inline_image_end(data, j + 2)
                ops.append(Op("BI", [], start, end))
                i = end
                stack = []
                start = None
                continue
            ops.append(Op(w, stack, start, i))
            stack = []
            start = None
    return ops


# ─────────────────────────────────────────────────────────────────────────────
# small geometry helpers (fitz.Matrix: row vectors, p * A * B applies A first)
# ─────────────────────────────────────────────────────────────────────────────

def _fitz():
    import pymupdf
    return pymupdf


def _num(v, d=0.0) -> float:
    return float(v) if isinstance(v, float) else d


def fmt_num(v: float) -> str:
    if abs(v) < 5e-7:
        return "0"
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def fmt_matrix(m) -> bytes:
    return (" ".join(fmt_num(x) for x in (m.a, m.b, m.c, m.d, m.e, m.f)) + " cm").encode()


def _rect_of(points) -> "fitz.Rect":
    fitz = _fitz()
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys))


def _bez(p0, p1, p2, p3, steps=8):
    fitz = _fitz()
    out = []
    for k in range(1, steps + 1):
        t = k / steps
        u = 1 - t
        out.append(fitz.Point(u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x,
                              u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y))
    return out


def _scale(m) -> float:
    return math.sqrt(abs(m.a * m.d - m.b * m.c)) or 1.0


def display_matrix(page):
    """PDF default user space → display space of *page* (CropBox and /Rotate
    applied). PyMuPDF's page.transformation_matrix ignores the CropBox offset
    on rotated pages, so ask MuPDF itself."""
    fitz = _fitz()
    try:
        ctm = fitz.mupdf.FzMatrix()
        fitz.mupdf.pdf_page_transform(page._pdf_page(), fitz.mupdf.FzRect(), ctm)
        m = fitz.Matrix(ctm)
        if abs(m.a * m.d - m.b * m.c) > 1e-9:
            return m
    except Exception:
        pass
    return page.transformation_matrix * page.rotation_matrix


def rect_transform(src, deg: int, dst):
    """Display-space matrix: rotate *src* by *deg* (multiple of 90, clockwise)
    about its centre and scale / move it onto *dst*."""
    fitz = _fitz()
    src, dst = fitz.Rect(src), fitz.Rect(dst)
    deg = int(round((deg or 0) / 90.0)) * 90 % 360
    sw, sh = max(src.width, 1e-6), max(src.height, 1e-6)
    if deg in (90, 270):
        sw, sh = sh, sw
    m = fitz.Matrix(1, 0, 0, 1, -(src.x0 + src.x1) / 2, -(src.y0 + src.y1) / 2)
    if deg:
        m = m * fitz.Matrix(deg)
    m = m * fitz.Matrix(dst.width / sw, 0, 0, dst.height / sh, (dst.x0 + dst.x1) / 2, (dst.y0 + dst.y1) / 2)
    return m


def _hex(rgb) -> str | None:
    if rgb is None:
        return None
    r, g, b = (max(0, min(255, int(round(c * 255)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgb_of(space: str, comps) -> tuple | None:
    comps = [c for c in comps if isinstance(c, float)]
    if space == "pattern":
        return None
    if len(comps) == 1:
        return (comps[0],) * 3
    if len(comps) == 3:
        return tuple(comps)
    if len(comps) == 4:
        c, m, y, k = comps
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def parse_hex(h) -> tuple | None:
    s = str(h or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# interpretation
# ─────────────────────────────────────────────────────────────────────────────

_CONSTRUCT = {"m", "l", "c", "v", "y", "h", "re"}
_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
_TEXT_SHOW = {"Tj", "TJ", "'", '"'}
_BREAK = {"q", "Q", "cm", "BT", "ET", "Do", "BI", "sh", "BDC", "BMC", "EMC", "MP", "DP", "d0", "d1"} | _TEXT_SHOW
# state operators replayed when an object leaves its clip (last one per kind wins)
_STATE_KIND = {"w": "w", "J": "J", "j": "j", "M": "M", "d": "d", "ri": "ri", "i": "i",
               "Tc": "Tc", "Tw": "Tw", "Tz": "Tz", "TL": "TL", "Tf": "Tf", "Tr": "Tr", "Ts": "Ts"}


class _Level:
    __slots__ = ("ops", "last")

    def __init__(self):
        self.ops = []        # [[kind, bytes]] (None-ed when superseded)
        self.last = {}       # kind -> index in ops

    def add(self, kind, raw, kills=()):
        for k in (kind,) + tuple(kills):
            if k in self.last and k not in ("cm", "clip", "gs"):
                self.ops[self.last.pop(k)][1] = None
        self.last[kind] = len(self.ops)
        self.ops.append([kind, raw])

    def raw(self) -> list:
        return [b for _k, b in self.ops if b is not None]


class _GS:
    __slots__ = ("ctm", "clip", "scs", "fcs", "sc", "fc", "lw")

    def __init__(self, ctm, clip):
        self.ctm = ctm
        self.clip = clip      # fitz.Rect in default space, or None (= no clip)
        self.scs = "gray"     # stroke colour space family
        self.fcs = "gray"
        self.sc = (0.0, 0.0, 0.0)
        self.fc = (0.0, 0.0, 0.0)
        self.lw = 1.0

    def copy(self):
        g = _GS(self.ctm, self.clip)
        g.scs, g.fcs, g.sc, g.fc, g.lw = self.scs, self.fcs, self.sc, self.fc, self.lw
        return g


def _cs_family(name: str, page_cs: dict) -> str:
    n = name.lstrip("/")
    if n in ("DeviceGray", "G", "CalGray"):
        return "gray"
    if n in ("DeviceRGB", "RGB", "CalRGB", "Lab"):
        return "rgb"
    if n in ("DeviceCMYK", "CMYK"):
        return "cmyk"
    if n == "Pattern":
        return "pattern"
    return page_cs.get(n, "other")


class Unit:
    """One drawing operation inside an object: a painted path, a Do, an inline
    image or a shading."""
    __slots__ = ("kind", "i0", "i1", "ctm", "depth", "clip", "bbox", "paint", "clipop",
                 "stroke", "fill", "lw", "snap", "name")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


class PageObjects:
    """Interpretation of one page's content stream."""

    def __init__(self, doc, page, keep_state: bool = False):
        fitz = _fitz()
        self.doc = doc
        self.page = page
        self.raw = content_bytes(doc, page)
        self.ops = tokenize(self.raw)
        self.D = display_matrix(page)
        self.Dinv = ~self.D
        self.prect = page.rect                                   # display
        self.pdf_page = (fitz.Rect(page.rect) * self.Dinv).normalize()   # default space
        self.keep_state = keep_state
        self.final_depth = 0
        self._xobjects()
        self._cspaces()
        self.units = []           # Units and barrier markers (None) in stream order
        self._interpret()
        self.objects = self._group()

    # resources ------------------------------------------------------------
    def _xobjects(self):
        fitz = _fitz()
        self.images = {}          # name -> xref
        self.forms = {}           # name -> (xref, bbox Rect (form space), Matrix)
        try:
            for it in self.page.get_images(full=True):
                if len(it) > 9 and it[9]:          # referenced from a Form XObject
                    continue
                self.images[str(it[7])] = int(it[0])
        except Exception:
            pass
        try:
            for xref, name, invoker, bbox in self.page.get_xobjects():
                if invoker:
                    continue
                mat = fitz.Identity
                try:
                    t, v = self.doc.xref_get_key(xref, "Matrix")
                    if t == "array":
                        nums = [float(x) for x in v.strip("[]").split()]
                        if len(nums) == 6:
                            mat = fitz.Matrix(*nums)
                except Exception:
                    pass
                self.forms[str(name)] = (int(xref), fitz.Rect(bbox), mat)
        except Exception:
            pass

    def _cspaces(self):
        """Named colour spaces of the page → component family."""
        self.cspaces = {}
        try:
            t, v = self.doc.xref_get_key(self.page.xref, "Resources/ColorSpace")
        except Exception:
            return
        if t not in ("dict", "xref"):
            return
        try:
            if t == "xref":
                v = self.doc.xref_object(int(v.split()[0]), compressed=True)
            for name, val in re.findall(r"/([^\s/\[\]<>()]+)\s*(\[[^\]]*\]|/[^\s/\[\]<>]+|\d+ 0 R)", v):
                fam = "other"
                txt = val
                m = re.match(r"(\d+) 0 R", val)
                if m:
                    txt = self.doc.xref_object(int(m.group(1)), compressed=True)
                if "ICCBased" in txt:
                    nm = re.search(r"(\d+) 0 R", txt[txt.find("ICCBased"):])
                    ncomp = 3
                    if nm:
                        try:
                            ncomp = int(self.doc.xref_get_key(int(nm.group(1)), "N")[1])
                        except Exception:
                            pass
                    fam = {1: "gray", 3: "rgb", 4: "cmyk"}.get(ncomp, "other")
                elif "DeviceRGB" in txt or "CalRGB" in txt:
                    fam = "rgb"
                elif "DeviceGray" in txt or "CalGray" in txt:
                    fam = "gray"
                elif "DeviceCMYK" in txt:
                    fam = "cmyk"
                elif "Pattern" in txt:
                    fam = "pattern"
                self.cspaces[name] = fam
        except Exception:
            pass

    # interpretation -------------------------------------------------------
    def _snapshot(self, levels):
        return [lv.raw() for lv in levels] if self.keep_state else None

    def _interpret(self):
        fitz = _fitz()
        ops = self.ops
        gs = _GS(fitz.Identity, None)
        gstack = []
        levels = [_Level()]
        path_ops = []          # indices of construction ops of the current path
        pts = []
        cur = None             # current point (user space)
        start_pt = None
        clip_op = None
        in_text = False
        for idx, o in enumerate(ops):
            op, a = o.op, o.args
            if op in _CONSTRUCT:
                if not path_ops:
                    pts = []
                path_ops.append(idx)
                try:
                    if op == "m":
                        cur = start_pt = fitz.Point(a[-2], a[-1])
                        pts.append(cur * gs.ctm)
                    elif op == "l":
                        cur = fitz.Point(a[-2], a[-1])
                        pts.append(cur * gs.ctm)
                    elif op == "re":
                        x, y, w, h = a[-4:]
                        for p in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
                            pts.append(fitz.Point(p) * gs.ctm)
                        cur = start_pt = fitz.Point(x, y)
                    elif op in ("c", "v", "y"):
                        if op == "c":
                            p1, p2, p3 = fitz.Point(a[-6], a[-5]), fitz.Point(a[-4], a[-3]), fitz.Point(a[-2], a[-1])
                        elif op == "v":
                            p1, p2, p3 = cur or fitz.Point(a[-4], a[-3]), fitz.Point(a[-4], a[-3]), fitz.Point(a[-2], a[-1])
                        else:
                            p1, p2, p3 = fitz.Point(a[-4], a[-3]), fitz.Point(a[-2], a[-1]), fitz.Point(a[-2], a[-1])
                        p0 = cur or p1
                        for p in _bez(p0, p1, p2, p3):
                            pts.append(p * gs.ctm)
                        cur = p3
                    elif op == "h":
                        cur = start_pt
                except (TypeError, ValueError, IndexError):
                    pass
                continue
            if op in ("W", "W*"):
                clip_op = op
                continue
            if op in _PAINT:
                if path_ops:
                    bbox = _rect_of(pts) if pts else None
                    snap = None
                    if op != "n" and bbox is not None and not in_text:
                        stroke = op in ("S", "s", "B", "B*", "b", "b*")
                        fill = op not in ("S", "s", "n")
                        sb = fitz.Rect(bbox)
                        if stroke:
                            half = max(gs.lw * _scale(gs.ctm), 0.5) / 2
                            sb = fitz.Rect(sb.x0 - half, sb.y0 - half, sb.x1 + half, sb.y1 + half)
                        snap = self._snapshot(levels)
                        self.units.append(Unit(
                            kind="path", i0=path_ops[0], i1=idx, ctm=gs.ctm, depth=len(levels) - 1,
                            clip=gs.clip, bbox=sb, paint=op, clipop=clip_op,
                            stroke=_rgb_of(gs.scs, gs.sc) if stroke else None,
                            fill=_rgb_of(gs.fcs, gs.fc) if fill else None,
                            lw=gs.lw * _scale(gs.ctm) if stroke else 0.0, snap=snap))
                    if clip_op and bbox is not None:
                        gs.clip = bbox if gs.clip is None else (gs.clip & bbox)
                        if self.keep_state:
                            body = b" ".join(self.raw[ops[k].start:ops[k].end] for k in path_ops)
                            levels[-1].add("clip", body + b" " + clip_op.encode() + b" n")
                path_ops = []
                pts = []
                clip_op = None
                continue
            # State operators inside a path (PyMuPDF's own Shape output puts
            # 'w', 'RG', 'rg' between 're' and 'B') keep the path; anything
            # else ends a dangling path (malformed content).
            if path_ops and op in _BREAK:
                path_ops, pts, clip_op = [], [], None
            if op == "q":
                gstack.append(gs.copy())
                levels.append(_Level())
            elif op == "Q":
                if gstack:
                    gs = gstack.pop()
                    levels.pop()
            elif op == "cm":
                try:
                    gs.ctm = fitz.Matrix(*a[-6:]) * gs.ctm
                except Exception:
                    pass
                if self.keep_state:
                    levels[-1].add("cm", self.raw[o.start:o.end])
            elif op == "BT":
                in_text = True
                self.units.append(None)
            elif op == "ET":
                in_text = False
            elif op in _TEXT_SHOW:
                self.units.append(None)
            elif op == "Do":
                name = a[-1][1] if a and isinstance(a[-1], tuple) and a[-1][0] == "/" else ""
                bbox = None
                xref = 0
                if name in self.images:
                    xref = self.images[name]
                    bbox = _rect_of([fitz.Point(p) * gs.ctm for p in ((0, 0), (1, 0), (1, 1), (0, 1))])
                    kind = "image"
                elif name in self.forms:
                    xref, fb, fm = self.forms[name]
                    bbox = _rect_of([p * fm * gs.ctm for p in (fb.tl, fb.tr, fb.br, fb.bl)])
                    kind = "form"
                if bbox is not None and not in_text:
                    self.units.append(Unit(kind=kind, i0=idx, i1=idx, ctm=gs.ctm, depth=len(levels) - 1,
                                           clip=gs.clip, bbox=bbox, name=(name, xref),
                                           snap=self._snapshot(levels)))
                else:
                    self.units.append(None)
            elif op == "BI":
                bbox = _rect_of([fitz.Point(p) * gs.ctm for p in ((0, 0), (1, 0), (1, 1), (0, 1))])
                self.units.append(Unit(kind="inline", i0=idx, i1=idx, ctm=gs.ctm, depth=len(levels) - 1,
                                       clip=gs.clip, bbox=bbox, name=("", 0), snap=self._snapshot(levels)))
            elif op == "sh":
                if gs.clip is not None and not in_text:
                    self.units.append(Unit(kind="shade", i0=idx, i1=idx, ctm=gs.ctm, depth=len(levels) - 1,
                                           clip=gs.clip, bbox=fitz.Rect(gs.clip), name=("", 0),
                                           snap=self._snapshot(levels)))
                else:
                    self.units.append(None)
            elif op == "w":
                gs.lw = _num(a[-1] if a else 1.0, 1.0)
                if self.keep_state:
                    levels[-1].add("w", self.raw[o.start:o.end])
            elif op in ("G", "RG", "K", "g", "rg", "k"):
                stroke = op.isupper()
                fam = {"g": "gray", "rg": "rgb", "k": "cmyk"}[op.lower()]
                comps = tuple(x for x in a if isinstance(x, float))
                if stroke:
                    gs.scs, gs.sc = fam, comps
                else:
                    gs.fcs, gs.fc = fam, comps
                if self.keep_state:
                    k = "SC" if stroke else "sc"
                    levels[-1].add(k, self.raw[o.start:o.end], kills=("CS",) if stroke else ("cs",))
            elif op in ("CS", "cs"):
                name = a[-1][1] if a and isinstance(a[-1], tuple) else ""
                fam = _cs_family(name, self.cspaces)
                default = {"gray": (0.0,), "rgb": (0.0, 0.0, 0.0), "cmyk": (0.0, 0.0, 0.0, 1.0)}.get(fam, ())
                if op == "CS":
                    gs.scs, gs.sc = fam, default
                else:
                    gs.fcs, gs.fc = fam, default
                if self.keep_state:
                    levels[-1].add(op, self.raw[o.start:o.end], kills=("SC",) if op == "CS" else ("sc",))
            elif op in ("SC", "SCN", "sc", "scn"):
                comps = tuple(x for x in a if isinstance(x, float))
                if op.isupper():
                    gs.sc = comps if gs.scs != "pattern" else ()
                else:
                    gs.fc = comps if gs.fcs != "pattern" else ()
                if self.keep_state:
                    levels[-1].add("SC" if op.isupper() else "sc", self.raw[o.start:o.end])
            elif op == "gs":
                if self.keep_state:
                    levels[-1].add("gs", self.raw[o.start:o.end])
            elif op in _STATE_KIND:
                if self.keep_state:
                    levels[-1].add(_STATE_KIND[op], self.raw[o.start:o.end])
        self.final_depth = len(gstack)

    # grouping -------------------------------------------------------------
    def _group(self) -> list:
        """Paths drawn one after the other (no text / picture in between) that
        touch each other form one object — a logo, a table grid, a framed box."""
        fitz = _fitz()
        parea = max(1.0, self.pdf_page.width * self.pdf_page.height)
        big = lambda r: r.width * r.height > 0.25 * parea
        groups = []
        run = []

        def flush():
            if not run:
                return
            n = len(run)
            parent = list(range(n))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            tol = 1.5
            if n <= 4000:
                order = sorted(range(n), key=lambda k: run[k][1].x0)
                active = []
                for k in order:
                    r = run[k][1]
                    if big(r):
                        continue
                    active = [j for j in active if run[j][1].x1 + tol >= r.x0]
                    for j in active:
                        q = run[j][1]
                        if not (r.y0 > q.y1 + tol or r.y1 < q.y0 - tol):
                            a, b = find(j), find(k)
                            if a != b:
                                parent[max(a, b)] = min(a, b)
                    active.append(k)
            seen = {}
            for k in range(n):
                root = find(k)
                if root not in seen:
                    seen[root] = {"kind": "path", "units": [], "uvis": [], "vis": fitz.Rect(run[k][1])}
                    groups.append(seen[root])
                g = seen[root]
                g["units"].append(run[k][0])
                g["uvis"].append(run[k][1])
                g["vis"] = g["vis"] | run[k][1]
            run.clear()

        for u in self.units:
            if u is None:
                flush()
                continue
            vis = fitz.Rect(u.bbox)
            if u.clip is not None:
                vis &= u.clip
            if vis.is_empty:
                continue
            if u.kind == "path":
                run.append((u, vis))
            else:
                flush()
                groups.append({"kind": u.kind, "units": [u], "uvis": [vis], "vis": vis})
        flush()
        out = []
        prect = fitz.Rect(self.prect)
        parea_d = max(1.0, prect.width * prect.height)
        for g in groups:
            vis_d = (g["vis"] * self.D).normalize()
            shown = vis_d & prect
            if shown.is_empty or shown.width < 0.5 and shown.height < 0.5:
                continue
            us = g["units"]
            u0 = us[0]
            stroke = next((u.stroke for u in us if u.stroke is not None), None)
            fill = next((u.fill for u in us if u.fill is not None), None)
            o = {
                "id": f"o{len(out)}",
                "kind": g["kind"],
                "bbox": [round(v, 2) for v in (shown.x0, shown.y0, shown.x1, shown.y1)],
                "full": [round(v, 2) for v in (vis_d.x0, vis_d.y0, vis_d.x1, vis_d.y1)],
                "background": (shown.width * shown.height) / parea_d > 0.85,
                "paths": len(us) if g["kind"] == "path" else 0,
                "stroke": _hex(stroke), "fill": _hex(fill),
                "has_stroke": any(u.paint in ("S", "s", "B", "B*", "b", "b*") for u in us) if g["kind"] == "path" else False,
                "has_fill": any(u.paint not in ("S", "s", "n") for u in us) if g["kind"] == "path" else False,
                "width": round(max((u.lw or 0) for u in us), 2) if g["kind"] == "path" else 0,
                "xref": u0.name[1] if u0.name else 0,
                "_units": us,
                "_vis": g["vis"],
            }
            if o["kind"] in ("image", "inline"):
                o["aspect"] = _placement_aspect(u0.ctm * self.D)
            # Hit areas: only where something is drawn, so the empty cells of
            # a table grid or the inside of a frame don't catch clicks.
            if g["kind"] == "path":
                parts = []
                for u, uv in zip(us, g["uvis"]):
                    r = (uv * self.D).normalize() & prect
                    if r.is_empty and not (r.width or r.height):
                        continue
                    if u.fill is None and u.paint in ("S", "s") and r.width > 12 and r.height > 12:
                        e = max(2.0, min(6.0, (u.lw or 1) + 2))
                        parts += [(r.x0, r.y0, r.x1, r.y0 + e), (r.x0, r.y1 - e, r.x1, r.y1),
                                  (r.x0, r.y0, r.x0 + e, r.y1), (r.x1 - e, r.y0, r.x1, r.y1)]
                    else:
                        parts.append((r.x0, r.y0, r.x1, r.y1))
                outline_only = all(u.fill is None for u in us)
                if outline_only:
                    o["background"] = False          # a page frame stays clickable on its border
                if len(parts) <= 400 and (len(parts) > 1 or outline_only):
                    o["parts"] = [[round(v, 2) for v in p] for p in parts]
            out.append(o)
        return out


def _placement_aspect(m) -> float:
    """Shown width / height of an image drawn with *m*, in the image's own
    axes (a picture placed rotated by 90° still reports its own aspect)."""
    w = math.hypot(m.a, m.b)
    h = math.hypot(m.c, m.d)
    return (w / h) if h > 1e-9 else 1.0


def content_bytes(doc, page) -> bytes:
    parts = []
    for x in page.get_contents():
        try:
            parts.append(doc.xref_stream(x) or b"")
        except Exception:
            pass
    return b"\n".join(parts)


def public(o: dict) -> dict:
    return {k: v for k, v in o.items() if not k.startswith("_")}


def extract(doc, page) -> list:
    """Selectable objects of *page* (display coordinates, stacking order)."""
    try:
        po = PageObjects(doc, page)
    except Exception:
        return []
    return [public(o) for o in po.objects]


# ─────────────────────────────────────────────────────────────────────────────
# rewriting
# ─────────────────────────────────────────────────────────────────────────────

class ObjectEditError(Exception):
    def __init__(self, msg, code="bad_edit"):
        super().__init__(msg)
        self.code = code


def _paint_for(paint: str, stroke: bool, fill: bool) -> str:
    closing = paint in ("s", "b", "b*")
    eo = paint.endswith("*")
    if stroke and fill:
        return ("b" if closing else "B") + ("*" if eo else "")
    if stroke:
        return "s" if closing else "S"
    if fill:
        return "f*" if eo else "f"
    return "n"


def _rect_ops(r) -> bytes:
    return (" ".join(fmt_num(v) for v in (r.x0, r.y0, r.width, r.height)) + " re W n").encode()


class _Writer:
    def __init__(self, po: PageObjects):
        self.po = po
        self.repl = []           # (start, end, bytes)

    def unit_range(self, u: Unit) -> tuple:
        ops = self.po.ops
        return ops[u.i0].start, ops[u.i1].end

    def path_parts(self, u: Unit) -> tuple:
        """(construction bytes, stray non-construction op bytes)"""
        raw, ops = self.po.raw, self.po.ops
        cons, other = [], []
        for k in range(u.i0, u.i1):
            o = ops[k]
            (cons if o.op in _CONSTRUCT else other).append(raw[o.start:o.end])
        return b" ".join(cons), b" ".join(other)

    def body(self, u: Unit, style: dict | None, ctm_at_paint, newname: str | None) -> bytes:
        """The unit's own drawing operators (+ style) — no clip side effects."""
        raw, ops = self.po.raw, self.po.ops
        if u.kind == "path":
            cons, _ = self.path_parts(u)
            paint = u.paint
            pre = []
            if style:
                stroke = paint in ("S", "s", "B", "B*", "b", "b*")
                fill = paint not in ("S", "s", "n")
                if "stroke" in style:
                    if style["stroke"] is None:
                        stroke = False
                    else:
                        if stroke or style.get("add_stroke"):
                            r, g, b = style["stroke"]
                            pre.append(f"{fmt_num(r)} {fmt_num(g)} {fmt_num(b)} RG".encode())
                            if not stroke and style.get("width") is None:
                                pre.append(f"{fmt_num(1.0 / _scale(ctm_at_paint))} w".encode())
                            stroke = True
                if "fill" in style:
                    if style["fill"] is None:
                        fill = False
                    else:
                        if fill or style.get("add_fill"):
                            r, g, b = style["fill"]
                            pre.append(f"{fmt_num(r)} {fmt_num(g)} {fmt_num(b)} rg".encode())
                            fill = True
                if style.get("width") is not None and stroke:
                    pre.append(f"{fmt_num(max(0.0, float(style['width'])) / _scale(ctm_at_paint))} w".encode())
                paint = _paint_for(paint, stroke, fill)
            return b" ".join(pre + [cons, paint.encode()])
        if newname:
            return f"/{newname} Do".encode()
        s, e = self.unit_range(u)
        return raw[s:e]

    def clip_part(self, u: Unit) -> bytes:
        if u.kind == "path" and u.clipop:
            cons, _ = self.path_parts(u)
            return cons + b" " + u.clipop.encode() + b" n"
        return b""

    def edit_unit(self, u: Unit, T_pdf, crop_pdf, style, newname, delete: bool):
        po = self.po
        s, e = self.unit_range(u)
        _cons, stray = self.path_parts(u) if u.kind == "path" else (b"", b"")
        head = (stray + b"\n") if stray else b""
        clip = self.clip_part(u)
        if delete:
            self.repl.append((s, e, head + clip))
            return
        if T_pdf is None and crop_pdf is None:
            body = self.body(u, style, u.ctm, newname)
            self.repl.append((s, e, head + (clip + b"\n" if clip else b"") + b"q " + body + b" Q"))
            return
        new_ctm = u.ctm * T_pdf if T_pdf is not None else u.ctm
        moved = (u.bbox * T_pdf).normalize() if T_pdf is not None else fitz_rect(u.bbox)
        inside = (crop_pdf is None and _contains(u.clip, u.bbox) and _contains(u.clip, moved))
        if inside:
            M = u.ctm * T_pdf * ~u.ctm
            body = self.body(u, style, new_ctm, newname)
            self.repl.append((s, e, head + (clip + b"\n" if clip else b"") +
                              b"q " + fmt_matrix(M) + b"\n" + body + b"\nQ"))
            return
        # leave the clip: unwind, draw with the replayed state under T, rebuild
        snap = u.snap or []
        out = [head, b"\n", b"Q\n" * (u.depth + 1), b"q\n"]
        if crop_pdf is not None:
            out += [_rect_ops(crop_pdf), b"\n"]
        if T_pdf is not None:
            out += [fmt_matrix(T_pdf), b"\n"]
        for lv in snap:
            for r in lv:
                out += [r, b"\n"]
        out += [self.body(u, style, new_ctm, newname), b"\nQ\n"]
        for lv in snap:
            out.append(b"q\n")
            for r in lv:
                out += [r, b"\n"]
        if clip:
            out += [clip, b"\n"]
        self.repl.append((s, e, b"".join(out)))

    def isolate_text(self):
        """Remove every text-showing operator (sprites of single objects)."""
        for o in self.po.ops:
            if o.op in _TEXT_SHOW:
                self.repl.append((o.start, o.end, b""))

    def build(self, tail: bytes = b"") -> bytes:
        raw = self.po.raw
        out = [b"q\n"]
        pos = 0
        for s, e, b in sorted(self.repl, key=lambda r: r[0]):
            if s < pos:
                continue              # overlapping (shouldn't happen)
            out += [raw[pos:s], b, b" "] if b else [raw[pos:s], b" "]
            pos = e
        out.append(raw[pos:])
        out.append(b"\n" + b"Q\n" * self.po.final_depth + b"Q\n")
        out.append(tail)
        return b"".join(out)


def fitz_rect(r):
    return _fitz().Rect(r)


def _contains(clip, r, tol=0.75) -> bool:
    if clip is None:
        return True
    return (r.x0 >= clip.x0 - tol and r.y0 >= clip.y0 - tol and r.x1 <= clip.x1 + tol and r.y1 <= clip.y1 + tol)


def set_contents(doc, page, data: bytes):
    """Make *data* the page's only content stream (a new object: streams can be
    shared by several pages)."""
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, data)
    doc.xref_set_key(page.xref, "Contents", f"{x} 0 R")
    return doc.reload_page(page)


def _image_name(page, xref: int) -> str:
    for it in page.get_images(full=True):
        if int(it[0]) == xref and not (len(it) > 9 and it[9]):
            return str(it[7])
    raise ObjectEditError("Could not add the image to the page", "image_failed")


def _insert_image_xobject(page, data: bytes) -> str:
    """Put image *data* into the page resources; → resource name. The drawing
    PyMuPDF appends is discarded later (the page contents are replaced)."""
    fitz = _fitz()
    r = page.rect
    xref = page.insert_image(fitz.Rect(r.x0, r.y0, r.x0 + 10, r.y0 + 10), stream=data, keep_proportion=False)
    return _image_name(page, xref)


def apply(doc, page, edits: list, *, image_bytes=None, isolate: str | None = None):
    """Apply object edits to *page* (in *doc*, in memory) → reloaded page.

    edit: {type:'object', object_id, orig_bbox?, bbox?, rotate?, crop?, delete?,
           stroke?, fill?, width?, data_url?/image?}
          {type:'add_image', rect, rotate?, image: bytes}
    image_bytes(edit, obj) → PNG/JPEG bytes for a replaced image (padding etc.).
    isolate=object id: keep only that object (and no text) — for sprites.
    """
    fitz = _fitz()
    po = PageObjects(doc, page, keep_state=True)
    by_id = {o["id"]: o for o in po.objects}
    w = _Writer(po)
    D, Dinv = po.D, po.Dinv
    tail = []

    if isolate is not None:
        for o in po.objects:
            if o["id"] != isolate:
                for u in o["_units"]:
                    w.edit_unit(u, None, None, None, None, True)
        w.isolate_text()

    # resource names for new images first (insert_image changes /Contents)
    pending = []
    for e in edits or []:
        t = e.get("type")
        if t == "object":
            o = by_id.get(str(e.get("object_id") or ""))
            if o is None:
                raise ObjectEditError("An object was not found on the page — the file may have changed. Reload it.",
                                      "stale")
            ob = e.get("orig_bbox")
            if ob is not None and any(abs(float(a) - float(b)) > 1.0 for a, b in zip(ob, o["bbox"])):
                raise ObjectEditError("The page changed since it was opened. Reload the file.", "stale")
            newname = None
            if e.get("image") is not None or e.get("data_url"):
                if o["kind"] not in ("image", "inline"):
                    raise ObjectEditError("Only pictures can be replaced", "bad_edit")
                data = image_bytes(e, o) if image_bytes else e.get("image")
                newname = _insert_image_xobject(page, data)
            pending.append((e, o, newname))
        elif t == "add_image":
            data = e.get("image")
            if data is None and image_bytes:
                data = image_bytes(e, None)
            if not data or not e.get("rect"):
                raise ObjectEditError("A new picture has no image or position", "bad_edit")
            pending.append((e, None, _insert_image_xobject(page, data)))

    for e, o, newname in pending:
        if o is None:                                    # add_image
            rect = fitz.Rect(e["rect"])
            deg = int(round(float(e.get("rotate") or 0) / 90.0)) * 90 % 360
            src = fitz.Rect(rect)
            if deg in (90, 270):
                cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
                src = fitz.Rect(cx - rect.height / 2, cy - rect.width / 2, cx + rect.height / 2, cy + rect.width / 2)
            U = fitz.Matrix(src.width, 0, 0, -src.height, src.x0, src.y1)      # unit square → display, upright
            m = U * rect_transform(src, deg, rect) * Dinv
            tail.append(b"q\n" + fmt_matrix(m) + f"\n/{newname} Do\nQ\n".encode())
            continue
        delete = bool(e.get("delete"))
        vis_d = fitz.Rect(o["full"])
        crop = fitz.Rect(e["crop"]) if e.get("crop") else None
        src = (crop & vis_d) if crop is not None else vis_d
        deg = int(round(float(e.get("rotate") or 0) / 90.0)) * 90 % 360
        dst = fitz.Rect(e["bbox"]) if e.get("bbox") else None
        T_pdf = None
        if dst is not None or deg or crop is not None:
            if dst is None:
                dst = fitz.Rect(src)
            if deg in (90, 270) and not e.get("bbox"):
                cx, cy = (src.x0 + src.x1) / 2, (src.y0 + src.y1) / 2
                dst = fitz.Rect(cx - src.height / 2, cy - src.width / 2, cx + src.height / 2, cy + src.width / 2)
            T = rect_transform(src, deg, dst)
            ident = all(abs(x - y) < 1e-4 for x, y in zip((T.a, T.b, T.c, T.d, T.e, T.f), (1, 0, 0, 1, 0, 0)))
            T_pdf = None if ident else (D * T * Dinv)
        crop_pdf = None
        if crop is not None and not (crop.contains(vis_d) or (crop & vis_d) == vis_d):
            cd = (src * rect_transform(src, deg, dst)).normalize() if dst is not None else src
            crop_pdf = (cd * Dinv).normalize()
        style = None
        if o["kind"] == "path" and any(k in e for k in ("stroke", "fill", "width")):
            style = {}
            if "stroke" in e:
                style["stroke"] = None if e["stroke"] in (None, "none", "") else parse_hex(e["stroke"])
                style["add_stroke"] = not o["has_stroke"]
            if "fill" in e:
                style["fill"] = None if e["fill"] in (None, "none", "") else parse_hex(e["fill"])
                style["add_fill"] = not o["has_fill"]
            if e.get("width") is not None:
                style["width"] = float(e["width"])
        for u in o["_units"]:
            w.edit_unit(u, T_pdf, crop_pdf, style, newname, delete)

    data = w.build(b"".join(tail))
    return set_contents(doc, page, data)
