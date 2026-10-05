"""
pdf_tools.py — PDF split / merge / arrange / extract engine for Office Axe.

Pure functions on top of PyMuPDF (+ Pillow for images). No UI, no Windows-only
imports, so it is unit-testable on any OS (see tests/test_pdf_tools.py).

Guarantees shared by every writer in this module:
  * Never overwrite an existing file. New outputs get a free name
    ('a.pdf' -> 'a (2).pdf') and are written with exclusive-create ('xb').
  * The only operation that replaces a file is build_pdf(..., replace=True),
    and it first copies the old version into '_to_review/' next to it.
  * Nothing is ever deleted.
  * Encrypted / corrupt inputs raise PdfToolsError with a readable message.

Page numbers in user-facing specs are 1-based; everything internal is 0-based.
"""
from __future__ import annotations

import base64
import datetime as _dt
import os
import re
import shutil
import threading
from collections import OrderedDict
from typing import Callable, Iterable, Optional

ProgressFn = Optional[Callable[[int, int, str], None]]

IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp",
})

REVIEW_DIR = "_to_review"


class PdfToolsError(Exception):
    """A user-facing error (message is shown in the UI as-is)."""


# ─────────────────────────────────────────────────────────────────────────────
# Engines
# ─────────────────────────────────────────────────────────────────────────────

def _fitz():
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            raise PdfToolsError("PyMuPDF is not installed (pip install pymupdf).")
    return fitz


def _pil():
    try:
        from PIL import Image, ImageOps
        return Image, ImageOps
    except ImportError:
        return None, None


def _name(path: str) -> str:
    return os.path.basename(str(path or ""))


def is_image(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in IMAGE_EXTS


def is_pdf(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() == ".pdf"


def _report(progress: ProgressFn, done: int, total: int, msg: str = "") -> None:
    if progress:
        try:
            progress(done, total, msg)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Paths — never overwrite
# ─────────────────────────────────────────────────────────────────────────────

def unique_path(path: str) -> str:
    """Return *path* if free, else 'stem (2).ext', 'stem (3).ext', …"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    m = re.match(r"^(.*) \((\d+)\)$", stem)
    if m:
        stem = m.group(1)
    i = 2
    while True:
        cand = f"{stem} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def _write_new(path: str, data: bytes) -> str:
    """Write *data* to a fresh file at *path* (or the next free name)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    for _ in range(1000):
        final = unique_path(path)
        try:
            with open(final, "xb") as f:      # exclusive create: never clobbers
                f.write(data)
            return final
        except FileExistsError:
            continue                          # lost a race — try the next name
    raise PdfToolsError(f"Could not find a free file name for {_name(path)}")


def _safe_stem(stem: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(stem)).strip().rstrip(".")
    return stem or "output"


def default_output(src: str, suffix: str, ext: str = ".pdf") -> str:
    """'C:/x/report.pdf' + '_merged' -> 'C:/x/report_merged.pdf' (free name)."""
    d = os.path.dirname(os.path.abspath(src))
    stem = os.path.splitext(_name(src))[0]
    return unique_path(os.path.join(d, _safe_stem(stem + suffix) + ext))


def backup_to_review(path: str) -> str:
    """Copy *path* into '<dir>/_to_review/<stem>.backup-<stamp><ext>'."""
    path = os.path.abspath(path)
    review = os.path.join(os.path.dirname(path), REVIEW_DIR)
    os.makedirs(review, exist_ok=True)
    stem, ext = os.path.splitext(_name(path))
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = unique_path(os.path.join(review, f"{stem}.backup-{stamp}{ext}"))
    shutil.copy2(path, dst)
    return dst


# ─────────────────────────────────────────────────────────────────────────────
# Page-range parsing
# ─────────────────────────────────────────────────────────────────────────────

_TOKEN_END = ("end", "last", "n", "$")


def _page_num(tok: str, page_count: int, part: str) -> int:
    t = tok.strip().lower()
    if t in _TOKEN_END:
        return page_count
    if not t.isdigit():
        raise PdfToolsError(f"'{part}' is not a page number or range.")
    n = int(t)
    if n < 1 or n > page_count:
        raise PdfToolsError(f"Page {n} is out of range — this PDF has {page_count} page(s).")
    return n


def parse_page_groups(spec: str, page_count: int) -> list[list[int]]:
    """Parse '1-3, 4-6, 7, 9-end' into 0-based groups: [[0,1,2],[3,4,5],[6],[8,…]].

    Supports N, A-B, A- (to the end), -B (from page 1), 'end'/'last' and
    descending ranges ('5-3' -> pages 5,4,3). Separators: ',' or ';'.
    """
    if page_count < 1:
        raise PdfToolsError("The PDF has no pages.")
    text = str(spec or "").strip()
    if not text:
        raise PdfToolsError("Enter page numbers or ranges, e.g. 1-3, 5, 8-end.")
    groups: list[list[int]] = []
    for raw in re.split(r"[,;]", text):
        part = raw.strip()
        if not part:
            continue
        part_n = re.sub(r"\s+", "", part)
        m = re.fullmatch(r"([^-–]*)[-–]([^-–]*)", part_n)
        if m:
            a_s, b_s = m.group(1), m.group(2)
            a = _page_num(a_s, page_count, part) if a_s else 1
            b = _page_num(b_s, page_count, part) if b_s else page_count
            step = 1 if b >= a else -1
            groups.append([p - 1 for p in range(a, b + step, step)])
        else:
            groups.append([_page_num(part_n, page_count, part) - 1])
    if not groups:
        raise PdfToolsError("Enter page numbers or ranges, e.g. 1-3, 5, 8-end.")
    return groups


def parse_page_list(spec: str, page_count: int) -> list[int]:
    """Flatten parse_page_groups, keeping the typed order and dropping repeats."""
    seen: set[int] = set()
    out: list[int] = []
    for g in parse_page_groups(spec, page_count):
        for p in g:
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def pages_label(pages: Iterable[int]) -> str:
    """0-based pages -> compact 1-based label: [0,1,2,4] -> '1-3_5'."""
    pages = list(pages)
    runs: list[str] = []
    i = 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        runs.append(str(pages[i] + 1) if i == j else f"{pages[i] + 1}-{pages[j] + 1}")
        i = j + 1
    return "_".join(runs)


# ─────────────────────────────────────────────────────────────────────────────
# Opening documents
# ─────────────────────────────────────────────────────────────────────────────

def open_pdf(path: str):
    """Open a PDF, raising PdfToolsError for missing / encrypted / corrupt files."""
    fitz = _fitz()
    path = os.path.abspath(str(path or ""))
    if not os.path.isfile(path):
        raise PdfToolsError(f"File not found: {_name(path)}")
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise PdfToolsError(f"Could not open {_name(path)} — the file is damaged or not a PDF ({exc}).")
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise PdfToolsError(
            f"{_name(path)} is password-protected. Open it in a PDF reader, remove the "
            f"password (save an unprotected copy), then try again.")
    if not doc.is_pdf:
        doc.close()
        raise PdfToolsError(f"{_name(path)} is not a PDF file.")
    if doc.page_count < 1:
        doc.close()
        raise PdfToolsError(f"{_name(path)} has no pages.")
    return doc


def image_to_pdf_doc(path: str):
    """Return a 1+ page in-memory PDF made from an image file (EXIF-rotated).

    Page size follows the image DPI (default 150 dpi), so a 1240×1754 px scan
    becomes an A4-sized page. Multi-frame TIFF/GIF becomes several pages.
    """
    fitz = _fitz()
    Image, ImageOps = _pil()
    path = os.path.abspath(str(path or ""))
    if not os.path.isfile(path):
        raise PdfToolsError(f"File not found: {_name(path)}")
    out = fitz.open()
    if Image is None:                         # PyMuPDF-only fallback
        try:
            img = fitz.open(path)
            out.insert_pdf(fitz.open("pdf", img.convert_to_pdf()))
            return out
        except Exception as exc:
            raise PdfToolsError(f"Could not read image {_name(path)}: {exc}")
    import io
    try:
        im = Image.open(path)
    except Exception as exc:
        raise PdfToolsError(f"Could not read image {_name(path)}: {exc}")
    n_frames = getattr(im, "n_frames", 1) if os.path.splitext(path)[1].lower() in (".tif", ".tiff") else 1
    for f in range(n_frames):
        try:
            im.seek(f)
        except EOFError:
            break
        fr = ImageOps.exif_transpose(im) if f == 0 else im.copy()
        dpi = im.info.get("dpi") or (150, 150)
        try:
            dx, dy = float(dpi[0]) or 150.0, float(dpi[1]) or 150.0
        except Exception:
            dx = dy = 150.0
        if dx < 30 or dy < 30:
            dx = dy = 150.0
        if fr.mode in ("RGBA", "LA", "P"):
            fr = fr.convert("RGBA")
            bg = Image.new("RGB", fr.size, (255, 255, 255))
            bg.paste(fr, mask=fr.split()[-1])
            fr = bg
        elif fr.mode not in ("RGB", "L"):
            fr = fr.convert("RGB")
        buf = io.BytesIO()
        if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg"):
            fr.save(buf, format="JPEG", quality=92)
        else:
            fr.save(buf, format="PNG", optimize=False)
        w_pt = fr.width * 72.0 / dx
        h_pt = fr.height * 72.0 / dy
        page = out.new_page(width=w_pt, height=h_pt)
        page.insert_image(page.rect, stream=buf.getvalue())
    if out.page_count < 1:
        raise PdfToolsError(f"Could not read image {_name(path)}")
    return out


def open_source(path: str):
    """Open a PDF or an image as a PDF document."""
    if is_image(path):
        return image_to_pdf_doc(path)
    return open_pdf(path)


def page_count(path: str) -> int:
    doc = open_source(path)
    try:
        return doc.page_count
    finally:
        doc.close()


def pdf_info(path: str) -> dict:
    doc = open_source(path)
    try:
        return {
            "path": os.path.abspath(path),
            "name": _name(path),
            "page_count": doc.page_count,
            "is_image": is_image(path),
            "encrypted": bool(getattr(doc, "is_encrypted", False)),
            "size": os.path.getsize(path),
        }
    finally:
        doc.close()


def _to_bytes(doc) -> bytes:
    return doc.tobytes(garbage=3, deflate=True)


# ─────────────────────────────────────────────────────────────────────────────
# Merge
# ─────────────────────────────────────────────────────────────────────────────

def merge(inputs: list, out_path: str = "", *, bookmarks: bool = True,
          progress: ProgressFn = None) -> dict:
    """Merge PDFs and images (each image becomes a page) in the given order.

    *inputs* items are paths, or dicts {path, pages?} where 'pages' is an
    optional 1-based spec ('1-3,7') to take only some pages of that file.
    With bookmarks=True every source gets a top-level bookmark (its file
    name) and its own outline is kept nested underneath.
    Returns {out_path, page_count, files}.
    """
    items = []
    for it in inputs or []:
        if isinstance(it, dict):
            items.append((str(it.get("path") or ""), str(it.get("pages") or "").strip()))
        else:
            items.append((str(it or ""), ""))
    items = [(p, s) for p, s in items if p]
    if not items:
        raise PdfToolsError("Choose at least one PDF or image to merge.")
    for p, _s in items:
        if not (is_pdf(p) or is_image(p)):
            raise PdfToolsError(f"{_name(p)} is not a PDF or image file.")
    if not out_path:
        out_path = default_output(items[0][0], "_merged")
    out_abs = os.path.abspath(out_path)
    if not out_abs.lower().endswith(".pdf"):
        out_abs += ".pdf"

    fitz = _fitz()
    out = fitz.open()
    toc: list[list] = []
    total = len(items)
    try:
        for i, (p, spec) in enumerate(items):
            _report(progress, i, total, f"Adding {_name(p)}")
            src = open_source(p)
            try:
                start = out.page_count
                if spec:
                    sel = parse_page_list(spec, src.page_count)
                    for pg in sel:
                        out.insert_pdf(src, from_page=pg, to_page=pg)
                else:
                    sel = None
                    out.insert_pdf(src)
                if bookmarks and out.page_count > start:
                    toc.append([1, os.path.splitext(_name(p))[0], start + 1])
                    if sel is None:
                        try:
                            for lvl, title, pno, *_rest in src.get_toc(simple=True):
                                if pno >= 1:
                                    toc.append([lvl + 1, title, start + pno])
                        except Exception:
                            pass
            finally:
                src.close()
        if out.page_count < 1:
            raise PdfToolsError("Nothing to merge — no pages were found.")
        if bookmarks and toc:
            try:
                out.set_toc(_fix_toc_levels(toc))
            except Exception:
                out.set_toc([e for e in toc if e[0] == 1])
        _report(progress, total, total, "Saving…")
        n = out.page_count
        final = _write_new(out_abs, _to_bytes(out))
    finally:
        out.close()
    return {"out_path": final, "page_count": n, "files": [final]}


def _fix_toc_levels(toc: list[list]) -> list[list]:
    """PyMuPDF requires each level to be at most previous+1."""
    fixed, prev = [], 0
    for lvl, title, pno in toc:
        lvl = max(1, min(int(lvl), prev + 1))
        fixed.append([lvl, str(title), int(pno)])
        prev = lvl
    return fixed


# ─────────────────────────────────────────────────────────────────────────────
# Split
# ─────────────────────────────────────────────────────────────────────────────

SPLIT_MODES = ("each", "every", "ranges", "extract")


def split_groups(page_count_: int, mode: str, every: int = 1, ranges: str = "") -> list[list[int]]:
    """Return the 0-based page groups a split would write (one file per group)."""
    mode = (mode or "each").lower()
    if mode == "each":
        return [[i] for i in range(page_count_)]
    if mode == "every":
        try:
            n = int(every)
        except (TypeError, ValueError):
            raise PdfToolsError("'Every N pages' needs a whole number.")
        if n < 1:
            raise PdfToolsError("'Every N pages' must be 1 or more.")
        return [list(range(i, min(i + n, page_count_))) for i in range(0, page_count_, n)]
    if mode == "ranges":
        return parse_page_groups(ranges, page_count_)
    if mode == "extract":
        return [parse_page_list(ranges, page_count_)]
    raise PdfToolsError(f"Unknown split mode: {mode}")


def split(path: str, mode: str = "each", *, every: int = 1, ranges: str = "",
          out_dir: str = "", progress: ProgressFn = None) -> dict:
    """Split *path* into several PDFs inside '<dir>/<stem>_split/'.

    mode: 'each' (one file per page), 'every' (chunks of *every* pages),
    'ranges' (one file per comma-separated range in *ranges*), or 'extract'
    (one file with the pages in *ranges*). Files are named '<stem>_p1-3.pdf'.
    Returns {out_dir, files, count}.
    """
    src = open_pdf(path)
    try:
        groups = split_groups(src.page_count, mode, every, ranges)
        stem = _safe_stem(os.path.splitext(_name(path))[0])
        if not out_dir:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(path)), stem + "_split")
        out_dir = os.path.abspath(out_dir)
        if os.path.exists(out_dir) and not os.path.isdir(out_dir):
            raise PdfToolsError(f"{_name(out_dir)} exists and is not a folder.")
        os.makedirs(out_dir, exist_ok=True)
        fitz = _fitz()
        files: list[str] = []
        total = len(groups)
        for k, g in enumerate(groups):
            _report(progress, k, total, f"Writing part {k + 1} of {total}")
            part = fitz.open()
            try:
                for pg in g:
                    part.insert_pdf(src, from_page=pg, to_page=pg)
                label = pages_label(g)
                if len(label) > 60:
                    label = f"{g[0] + 1}-{g[-1] + 1}_part{k + 1}"
                name = f"{stem}_p{label}.pdf"
                files.append(_write_new(os.path.join(out_dir, name), _to_bytes(part)))
            finally:
                part.close()
        _report(progress, total, total, "Done")
    finally:
        src.close()
    return {"out_dir": out_dir, "files": files, "count": len(files)}


# ─────────────────────────────────────────────────────────────────────────────
# Extract
# ─────────────────────────────────────────────────────────────────────────────

def extract(path: str, spec: str, out_path: str = "", *, progress: ProgressFn = None) -> dict:
    """Write the pages in *spec* (typed order, e.g. '3,1-2') to a new PDF.

    Default output: '<stem>_p<label>.pdf' next to the source. Never overwrites.
    Returns {out_path, pages (1-based), page_count}.
    """
    src = open_pdf(path)
    try:
        pages = parse_page_list(spec, src.page_count)
        if not out_path:
            label = pages_label(pages)
            if len(label) > 60:
                label = "extract"
            out_path = default_output(path, f"_p{label}")
        fitz = _fitz()
        out = fitz.open()
        try:
            for i, pg in enumerate(pages):
                if i % 25 == 0:
                    _report(progress, i, len(pages), "Copying pages…")
                out.insert_pdf(src, from_page=pg, to_page=pg)
            final = _write_new(out_path, _to_bytes(out))
        finally:
            out.close()
    finally:
        src.close()
    return {"out_path": final, "pages": [p + 1 for p in pages], "page_count": len(pages)}


# ─────────────────────────────────────────────────────────────────────────────
# Build (arrange): an ordered list of page slots from one or more sources
# ─────────────────────────────────────────────────────────────────────────────

def build_pdf(pages: list, out_path: str, *, replace: bool = False,
              progress: ProgressFn = None,
              release_handles: Optional[Callable[[str], None]] = None) -> dict:
    """Write a PDF from ordered slots [{path, page (0-based), rotate}].

    Slots may repeat (duplicate page) and come from several files (inserted
    pages, images). 'rotate' is added to the page's current rotation.

    replace=False: written as a NEW file (free name based on *out_path*).
    replace=True : *out_path* must exist; the old version is first copied to
                   '<dir>/_to_review/<stem>.backup-<stamp>.pdf', then replaced.
    Returns {out_path, page_count, backup, replaced}.
    """
    if not pages:
        raise PdfToolsError("There are no pages to save.")
    fitz = _fitz()
    if not str(out_path or "").strip():
        raise PdfToolsError("No output file given.")
    out_abs = os.path.abspath(str(out_path))
    if not out_abs.lower().endswith(".pdf"):
        out_abs += ".pdf"
    if replace and not os.path.isfile(out_abs):
        raise PdfToolsError(f"Cannot overwrite {_name(out_abs)} — it no longer exists.")

    docs: dict[str, object] = {}
    out = fitz.open()
    total = len(pages)
    try:
        for i, it in enumerate(pages):
            if i % 10 == 0:
                _report(progress, i, total, f"Page {i + 1} of {total}")
            sp = os.path.abspath(str(it.get("path") or ""))
            key = sp.lower()
            src = docs.get(key)
            if src is None:
                src = docs[key] = open_source(sp)
            pi = int(it.get("page", 0))
            if pi < 0 or pi >= src.page_count:
                raise PdfToolsError(f"Page {pi + 1} does not exist in {_name(sp)}.")
            out.insert_pdf(src, from_page=pi, to_page=pi)
            rot = int(it.get("rotate", 0) or 0) % 360
            if rot:
                pg = out[-1]
                pg.set_rotation((pg.rotation + rot) % 360)
        _report(progress, total, total, "Saving…")
        data = _to_bytes(out)
    finally:
        out.close()
        for d in docs.values():
            try:
                d.close()
            except Exception:
                pass

    if not replace:
        final = _write_new(out_abs, data)
        return {"out_path": final, "page_count": total, "backup": "", "replaced": False}

    backup = backup_to_review(out_abs)
    tmp = _write_new(out_abs + ".saving.pdf", data)
    if release_handles:
        try:
            release_handles(out_abs)
        except Exception:
            pass
    try:
        os.replace(tmp, out_abs)
    except OSError as exc:
        # Original is locked (open in another program). Keep the new version
        # under a normal name instead of leaving a stray temp file.
        alt = default_output(out_abs, "_arranged")
        os.replace(tmp, alt)
        raise PdfToolsError(
            f"Could not overwrite {_name(out_abs)} ({exc.strerror or exc}). "
            f"Your changes were saved as {_name(alt)} instead.")
    return {"out_path": out_abs, "page_count": total, "backup": backup, "replaced": True}


# ─────────────────────────────────────────────────────────────────────────────
# Thumbnails (small LRU cache keyed by file identity)
# ─────────────────────────────────────────────────────────────────────────────

_THUMB_CACHE: "OrderedDict[tuple, str]" = OrderedDict()
_THUMB_CACHE_MAX_BYTES = 48 * 1024 * 1024
_thumb_bytes = 0
_thumb_lock = threading.Lock()


def _file_key(path: str) -> tuple:
    st = os.stat(path)
    return (os.path.abspath(path).lower(), st.st_mtime_ns, st.st_size)


def _cache_get(key):
    with _thumb_lock:
        v = _THUMB_CACHE.get(key)
        if v is not None:
            _THUMB_CACHE.move_to_end(key)
        return v


def _cache_put(key, value: str) -> None:
    global _thumb_bytes
    with _thumb_lock:
        if key in _THUMB_CACHE:
            return
        _THUMB_CACHE[key] = value
        _thumb_bytes += len(value)
        while _thumb_bytes > _THUMB_CACHE_MAX_BYTES and _THUMB_CACHE:
            _k, v = _THUMB_CACHE.popitem(last=False)
            _thumb_bytes -= len(v)


def thumbnails(path: str, pages: list, width: int = 160) -> dict:
    """Render JPEG data-URLs for the 0-based *pages* of a PDF/image at *width* px.

    Opens the document once per call; results are cached in memory (≈48 MB
    LRU) keyed by (file, mtime, size, page, width) so re-opening the
    organiser or scrolling back is instant. Returns {page: data_url}.
    """
    width = max(40, min(int(width or 160), 1200))
    path = os.path.abspath(str(path or ""))
    fkey = _file_key(path)
    result: dict[int, str] = {}
    todo: list[int] = []
    for p in pages or []:
        p = int(p)
        hit = _cache_get(fkey + (p, width))
        if hit is not None:
            result[p] = hit
        else:
            todo.append(p)
    if not todo:
        return result
    fitz = _fitz()
    doc = open_source(path)
    try:
        for p in todo:
            if p < 0 or p >= doc.page_count:
                continue
            page = doc.load_page(p)
            r = page.rect                      # already accounts for /Rotate
            pw = max(float(r.width), 1.0)
            zoom = min(width / pw, 4.0)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            try:
                data = pix.tobytes("jpg", jpg_quality=72)
                mime = "image/jpeg"
            except Exception:
                data = pix.tobytes("png")
                mime = "image/png"
            du = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            _cache_put(fkey + (p, width), du)
            result[p] = du
    finally:
        doc.close()
    return result
