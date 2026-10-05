# -*- coding: utf-8 -*-
"""
qr_scan.py — robust QR code checking for images and PDFs (no Tk, no network).

Used by sfm_bridge (Scan QR from file / batch / screen). Built for checking
QR codes printed on documents (certificates, apostilles, IDs):

* images (EXIF orientation respected, multi-frame TIFF) and PDFs (every page,
  rendered at 200 dpi, re-rendered at 300 dpi when a page yields nothing);
* several QR codes per file / page, de-duplicated;
* a cheap first pass, then harder variants (auto-contrast, thresholds,
  sharpen, up/down-scale, rotations) only when the first pass finds nothing;
* every hit carries its position on the page (mapped back to the original
  image) and a small PNG crop thumbnail;
* ``classify_payload`` explains what the text is (URL, vCard, Wi-Fi, ...) and
  flags URLs worth a second look (plain http, IP hosts, look-alike / punycode
  domains, link shorteners, ``user@host`` tricks, unusual TLDs ...).

Decoding needs ``pyzbar`` (+ the zbar DLL). Everything else only needs Pillow
(and PyMuPDF for PDFs). Nothing here opens a browser — the UI does that only
after the user clicks.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import math
import os
import re
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

try:
    from PIL import Image, ImageFilter, ImageOps  # type: ignore
    _PIL_OK = True
except Exception:  # pragma: no cover
    Image = ImageFilter = ImageOps = None  # type: ignore
    _PIL_OK = False

_pyzbar = None
_PYZBAR_ERR = ""


def _load_pyzbar():
    global _pyzbar, _PYZBAR_ERR
    if _pyzbar is not None or _PYZBAR_ERR:
        return _pyzbar
    try:
        from pyzbar import pyzbar as _pz  # type: ignore
        _pyzbar = _pz
    except Exception as exc:  # ImportError, or the zbar DLL is missing
        _PYZBAR_ERR = str(exc) or exc.__class__.__name__
    return _pyzbar


def decoder_available() -> bool:
    return _PIL_OK and _load_pyzbar() is not None


def decoder_error() -> str:
    if not _PIL_OK:
        return "Pillow is not installed."
    if _load_pyzbar() is None:
        return ("QR decoder (pyzbar / zbar) is not available: " + _PYZBAR_ERR)
    return ""


IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
SCANNABLE_EXTS = IMAGE_EXTS | PDF_EXTS

PDF_DPI = 200
PDF_RETRY_DPI = 300
MAX_PAGES = 60
THUMB_MAX = 180


# =============================================================================
# Payload classification
# =============================================================================

_URL_SHORTENERS = {
    "bit.ly", "bitly.com", "tinyurl.com", "t.co", "goo.gl", "is.gd", "ow.ly",
    "buff.ly", "cutt.ly", "rebrand.ly", "shorturl.at", "tiny.cc", "rb.gy",
    "s.id", "qrco.de", "qr.co", "t.ly", "v.gd", "bl.ink", "lnkd.in", "short.io",
    "tinyurl.at", "u.to", "x.co", "shorte.st", "adf.ly", "me-qr.com", "qr.page",
}

_UNUSUAL_TLDS = {
    "zip", "mov", "xyz", "top", "click", "link", "loan", "work", "gq", "tk",
    "ml", "cf", "ga", "country", "kim", "men", "date", "racing", "review",
    "stream", "download", "win", "bid", "trade", "party", "science", "cam",
    "rest", "icu", "buzz", "monster", "cyou", "sbs", "lol", "quest",
}

# second-level labels that, before a 2-letter ccTLD, form the public suffix
_CC_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "go", "gob", "gouv",
                    "edu", "ac", "or", "ne", "mil", "nic", "govt", "gv", "re",
                    "ltd", "plc", "sch", "info", "biz", "nom", "pe", "in"}

_OFFICIAL_RE = re.compile(
    r"(^|\.)(gov|mil|edu|int)$|(^|\.)(gov|go|gob|gouv|govt|gv|edu|ac|mil)\.[a-z]{2}$",
    re.IGNORECASE)


def registrable_domain(host: str) -> str:
    """Best-effort 'example.co.uk' / 'example.com' from a hostname (no PSL)."""
    host = (host or "").strip(".").lower()
    if not host:
        return ""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in _CC_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _url_checks(url: str) -> dict:
    """Return {scheme, host, domain, warnings[], notes[], openable} for a URL."""
    warnings: list[str] = []
    notes: list[str] = []
    try:
        parts = urlsplit(url)
    except ValueError:
        return {"scheme": "", "host": "", "domain": "", "openable": False,
                "warnings": ["Malformed link"], "notes": []}
    scheme = (parts.scheme or "").lower()
    netloc = parts.netloc or ""
    host = (parts.hostname or "").lower()
    openable = scheme in ("http", "https") and bool(host)

    if scheme == "http":
        warnings.append("Not encrypted (http, not https)")
    if "@" in netloc:
        warnings.append("Link contains '@' — the real site is the part after the @")
    try:
        port = parts.port
    except ValueError:
        port = None
        warnings.append("Invalid port number in link")
    if port and port not in (80, 443):
        warnings.append(f"Unusual port :{port}")
    is_ip = False
    if host:
        try:
            ipaddress.ip_address(host.strip("[]"))
            is_ip = True
            warnings.append("Points to a raw IP address, not a domain name")
        except ValueError:
            pass
    if host and not is_ip:
        if "xn--" in host:
            warnings.append("Internationalised (punycode) domain — check for look-alike letters")
        try:
            host.encode("ascii")
        except UnicodeEncodeError:
            warnings.append("Domain uses non-Latin characters — check for look-alike letters")
        tld = host.rsplit(".", 1)[-1]
        if tld in _UNUSUAL_TLDS:
            warnings.append(f"Unusual top-level domain .{tld}")
        dom = registrable_domain(host)
        if dom in _URL_SHORTENERS or host in _URL_SHORTENERS:
            warnings.append("Shortened link — the final destination is hidden")
        if host.count(".") >= 4:
            warnings.append("Very long hostname with many sub-domains")
        if _OFFICIAL_RE.search(host):
            notes.append("Government / education domain")
    if len(url) > 300:
        warnings.append("Very long link")
    return {
        "scheme": scheme,
        "host": host,
        "domain": registrable_domain(host) if host else "",
        "openable": openable,
        "warnings": warnings,
        "notes": notes,
    }


_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(r"^(www\.)?[a-z0-9\-]+(\.[a-z0-9\-]+)+(/\S*)?$", re.IGNORECASE)


def classify_payload(text: str) -> dict:
    """Describe a decoded QR payload.

    Returns {type, label, url?, scheme?, host?, domain?, openable, warnings, notes}.
    ``type`` is one of url, email, phone, sms, wifi, vcard, contact, geo,
    event, text, other.
    """
    t = (text or "").strip()
    low = t.lower()
    base = {"type": "text", "label": "Text", "openable": False, "warnings": [], "notes": []}
    if not t:
        return base

    if low.startswith(("http://", "https://")):
        info = _url_checks(t)
        out = dict(base, type="url", label="Link (URL)", url=t)
        out.update(info)
        return out
    if low.startswith("www.") or (_BARE_DOMAIN_RE.match(t) and " " not in t and "." in t
                                 and not t.replace(".", "").isdigit()):
        url = "https://" + t
        info = _url_checks(url)
        out = dict(base, type="url", label="Link (no http/https prefix)", url=url)
        out.update(info)
        out["warnings"] = ["Link has no http(s):// prefix — https was assumed"] + info["warnings"]
        return out
    if low.startswith("mailto:") or low.startswith("matmsg:"):
        return dict(base, type="email", label="E-mail")
    if low.startswith("tel:"):
        return dict(base, type="phone", label="Phone number")
    if low.startswith(("smsto:", "sms:", "mmsto:")):
        return dict(base, type="sms", label="SMS")
    if low.startswith("wifi:"):
        return dict(base, type="wifi", label="Wi-Fi network")
    if low.startswith("begin:vcard"):
        return dict(base, type="vcard", label="Contact (vCard)")
    if low.startswith("mecard:"):
        return dict(base, type="contact", label="Contact (MeCard)")
    if low.startswith("geo:"):
        return dict(base, type="geo", label="Location")
    if low.startswith("begin:vevent") or low.startswith("begin:vcalendar"):
        return dict(base, type="event", label="Calendar event")
    m = _SCHEME_RE.match(t)
    if m and m.group(1).lower() in ("javascript", "data", "file", "vbscript", "intent"):
        return dict(base, type="other", label=f"{m.group(1).lower()}: link",
                    warnings=[f"'{m.group(1).lower()}:' links can run code — not opened"])
    if re.fullmatch(r"\+?[\d\s\-()]{7,}", t):
        return dict(base, type="phone", label="Number")
    return base


# =============================================================================
# Decoding
# =============================================================================

def _to_gray(img):
    if img.mode == "L":
        return img
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        return bg.convert("L")
    return img.convert("L")


def _otsu_threshold(gray) -> int:
    hist = gray.histogram()[:256]
    total = sum(hist)
    if not total:
        return 128
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_b = w_b = 0.0
    best_t, best_var = 128, -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def _binarize(gray, t: int):
    return gray.point(lambda v, _t=t: 255 if v > _t else 0)


def _rotated(gray, angle: float):
    """Rotate counter-clockwise by *angle* degrees (expand, white fill)."""
    if angle % 360 == 90:
        return gray.transpose(Image.Transpose.ROTATE_90)
    if angle % 360 == 180:
        return gray.transpose(Image.Transpose.ROTATE_180)
    if angle % 360 == 270:
        return gray.transpose(Image.Transpose.ROTATE_270)
    return gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)


def _rot_mapper(src_size, angle: float, rot_size) -> Callable[[float, float], tuple]:
    """Map a point in the rotated image back to the source image."""
    sw, sh = src_size
    rw, rh = rot_size
    th = math.radians(angle)
    c, s = math.cos(th), math.sin(th)

    def fn(x, y):
        dx, dy = x - rw / 2.0, y - rh / 2.0
        # inverse of a counter-clockwise (visual) rotation in y-down coords
        ox = dx * c - dy * s
        oy = dx * s + dy * c
        return ox + sw / 2.0, oy + sh / 2.0
    return fn


def _variants(gray, hard: bool) -> Iterable[tuple]:
    """Yield (label, image, mapper) — mapper converts variant coords → gray coords."""
    ident = lambda x, y: (x, y)  # noqa: E731
    w, h = gray.size
    edge = max(w, h)
    if not hard:
        yield "gray", gray, ident
        return

    ac = ImageOps.autocontrast(gray, cutoff=1)
    yield "autocontrast", ac, ident
    t = _otsu_threshold(gray)
    yield "otsu", _binarize(gray, t), ident
    yield "sharpen", ac.filter(ImageFilter.SHARPEN), ident
    yield "median", gray.filter(ImageFilter.MedianFilter(3)), ident   # halftone / print noise
    for thr in (100, 160):
        if abs(thr - t) > 20:
            yield f"thr{thr}", _binarize(gray, thr), ident
    if edge < 2400:
        f = 2 if edge >= 900 else 3
        up = ac.resize((w * f, h * f), Image.Resampling.LANCZOS)
        yield f"scale{f}x", up, (lambda x, y, _f=f: (x / _f, y / _f))
    if edge > 1400:
        f = 0.5
        dn = ac.resize((max(1, int(w * f)), max(1, int(h * f))), Image.Resampling.LANCZOS)
        yield "half", dn, (lambda x, y, _f=f: (x / _f, y / _f))
    # skewed / sideways prints
    base = ac if edge <= 2600 else ac.resize((int(w * 2600 / edge), int(h * 2600 / edge)),
                                             Image.Resampling.LANCZOS)
    bscale = base.size[0] / float(w)
    for ang in (90, 45, 315, 30, 330):
        r = _rotated(base, ang)
        m = _rot_mapper(base.size, ang, r.size)
        yield f"rot{ang}", r, (lambda x, y, _m=m, _s=bscale: tuple(v / _s for v in _m(x, y)))


def _decode_raw(img) -> list:
    pz = _load_pyzbar()
    if pz is None:
        return []
    try:
        return list(pz.decode(img))
    except Exception:
        return []


def _poly_of(sym) -> list:
    pts = [(p.x, p.y) for p in (getattr(sym, "polygon", None) or [])]
    if not pts:
        r = sym.rect
        pts = [(r.left, r.top), (r.left + r.width, r.top),
               (r.left + r.width, r.top + r.height), (r.left, r.top + r.height)]
    return pts


def _sym_text(sym) -> str:
    data = sym.data or b""
    for enc in ("utf-8", "shift_jis", "cp1252"):
        try:
            return data.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").strip()


def decode_image(img, hard: Optional[bool] = None, light: bool = False) -> list[dict]:
    """Decode every QR/barcode in a PIL image.

    Returns [{text, symbology, bbox:[x,y,w,h], strategy}] in *img* coordinates.
    With ``hard=None`` the harder variants run only if the cheap pass finds
    nothing; ``hard=False`` skips them; ``hard=True`` always runs them.
    ``light=True`` limits the fallback to the cheap auto-contrast / threshold
    variants (used for the high-dpi PDF re-render).
    """
    if _load_pyzbar() is None:
        raise RuntimeError(decoder_error())
    gray = _to_gray(img)
    W, H = gray.size
    found: dict[str, dict] = {}

    def run(hard_pass: bool, only=None) -> None:
        for label, var, mapper in _variants(gray, hard_pass):
            if only is not None:
                if label not in only:
                    if label not in ("autocontrast", "otsu"):
                        break   # the requested cheap variants come first
                    continue
            for sym in _decode_raw(var):
                text = _sym_text(sym)
                if not text:
                    continue
                pts = [mapper(x, y) for (x, y) in _poly_of(sym)]
                xs = [min(max(p[0], 0), W) for p in pts]
                ys = [min(max(p[1], 0), H) for p in pts]
                bbox = [int(min(xs)), int(min(ys)),
                        max(1, int(math.ceil(max(xs) - min(xs)))),
                        max(1, int(math.ceil(max(ys) - min(ys))))]
                key = text
                if key in found:
                    continue
                found[key] = {"text": text, "symbology": str(getattr(sym, "type", "QRCODE")),
                              "bbox": bbox, "strategy": label}
            if found and hard_pass and only is None:
                break   # good enough — stop burning time on further variants

    run(False)
    if light:
        if not found:
            run(True, only=("autocontrast", "otsu"))
    elif hard or (hard is None and not found):
        run(True)
    elif hard is None:
        # something found: two cheap extra passes catch a second, fainter code
        run(True, only=("autocontrast", "otsu"))
    return list(found.values())


def thumb_data_url(img, bbox, max_side: int = THUMB_MAX) -> str:
    """PNG data-URL of the bbox region (with a margin) of *img*."""
    try:
        x, y, w, h = bbox
        pad = int(max(w, h) * 0.12) + 4
        box = (max(0, x - pad), max(0, y - pad),
               min(img.width, x + w + pad), min(img.height, y + h + pad))
        crop = img.crop(box)
        if crop.mode not in ("RGB", "L"):
            crop = crop.convert("RGB")
        crop.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _result_entry(hit: dict, img, page: Optional[int], scale: float = 1.0,
                  thumbs: bool = True) -> dict:
    info = classify_payload(hit["text"])
    out = {
        "text": hit["text"],
        "symbology": hit["symbology"],
        "page": page,
        "bbox": hit["bbox"],
        "strategy": hit["strategy"],
        "thumb": thumb_data_url(img, hit["bbox"]) if thumbs else "",
    }
    if scale != 1.0:
        out["bbox_pt"] = [round(v / scale, 1) for v in hit["bbox"]]
    out.update(info)
    return out


def _open_oriented(path: str):
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img


def scan_file(path: str, *, max_pages: int = MAX_PAGES, thumbs: bool = True,
              cancelled: Optional[Callable[[], bool]] = None,
              progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Scan one image / PDF for QR codes.

    Returns {ok, path, name, kind, results:[...], page_count, pages_scanned,
    truncated, error?}. Never raises for per-file problems.
    """
    name = os.path.basename(path or "")
    out = {"ok": True, "path": path, "name": name, "kind": "", "results": [],
           "page_count": 0, "pages_scanned": 0, "truncated": False}
    try:
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        ext = os.path.splitext(path)[1].lower()
        if ext not in SCANNABLE_EXTS:
            raise ValueError(f"Unsupported file type for QR scan: {ext or '(none)'}")
        if not decoder_available():
            raise RuntimeError(decoder_error())

        if ext in PDF_EXTS:
            out["kind"] = "pdf"
            _scan_pdf(path, out, max_pages, thumbs, cancelled, progress)
        else:
            out["kind"] = "image"
            _scan_image(path, out, max_pages, thumbs, cancelled, progress)
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc) or exc.__class__.__name__
    return out


def _scan_image(path, out, max_pages, thumbs, cancelled, progress):
    img = _open_oriented(path)
    n_frames = int(getattr(img, "n_frames", 1) or 1)
    out["page_count"] = n_frames
    frames = min(n_frames, max_pages)
    out["truncated"] = n_frames > frames
    for i in range(frames):
        if cancelled and cancelled():
            break
        if n_frames > 1:
            img.seek(i)
            frame = img.copy()
        else:
            frame = img
        frame.load()
        page = (i + 1) if n_frames > 1 else None
        for hit in decode_image(frame):
            out["results"].append(_result_entry(hit, frame, page, thumbs=thumbs))
        out["pages_scanned"] = i + 1
        if progress:
            progress(i + 1, frames)


def _fitz():
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        import fitz  # type: ignore
    return fitz


def _render_page(page, dpi: int):
    fitz = _fitz()
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _scan_pdf(path, out, max_pages, thumbs, cancelled, progress):
    try:
        fitz = _fitz()
    except ImportError:
        raise RuntimeError("PyMuPDF is not installed — cannot scan PDFs")
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            raise RuntimeError("PDF is password-protected")
        n = doc.page_count
        out["page_count"] = n
        pages = min(n, max_pages)
        out["truncated"] = n > pages
        for i in range(pages):
            if cancelled and cancelled():
                break
            page = doc.load_page(i)
            img = _render_page(page, PDF_DPI)
            hits = decode_image(img)
            scale = PDF_DPI / 72.0
            if not hits:
                # small / dense codes: try a sharper render
                img = _render_page(page, PDF_RETRY_DPI)
                hits = decode_image(img, light=True)
                scale = PDF_RETRY_DPI / 72.0
            for hit in hits:
                out["results"].append(_result_entry(hit, img, i + 1, scale, thumbs))
            out["pages_scanned"] = i + 1
            if progress:
                progress(i + 1, pages)
    finally:
        doc.close()


def scan_pil(img, *, region_offset=(0, 0), thumbs: bool = True) -> list[dict]:
    """Decode a PIL image (e.g. a screenshot region). bboxes are offset by
    *region_offset* so they refer to the full screenshot."""
    hits = decode_image(img)
    res = []
    ox, oy = region_offset
    for hit in hits:
        e = _result_entry(hit, img, None, thumbs=thumbs)
        e["bbox"] = [hit["bbox"][0] + ox, hit["bbox"][1] + oy, hit["bbox"][2], hit["bbox"][3]]
        res.append(e)
    return res


def is_safe_to_open(url: str) -> bool:
    """Only plain http(s) links with a host are ever handed to the browser."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return False
    return p.scheme.lower() in ("http", "https") and bool(p.hostname) \
        and not any(ch in url for ch in ("\n", "\r", "\t", " "))
