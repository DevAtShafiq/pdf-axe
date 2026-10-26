"""
doc_convert.py — Word and Hangul (HWP / HWPX) documents → PDF.

    to_pdf(path, out_path="") → {out_path, pages, app}

The PDF is written next to the document as "<name>.pdf" (" (2)", " (3)" … when
taken); the original is opened read-only and never changed or moved.

  * Word (.docx .doc .rtf .odt …): Microsoft Word, through the same converter
    the Word preview uses (doc_preview.office_to_pdf), then copied out.
  * Hangul (.hwp .hwpx): Hancom Office 한글 (HWPFrame.HwpObject automation).

Both need the program installed on the PC (Windows); without it the error says
which one is missing.
"""
from __future__ import annotations

import os
import shutil
import threading
import time

WORD_EXT = {".docx", ".doc", ".docm", ".dotx", ".dot", ".dotm", ".rtf", ".odt"}
HWP_EXT = {".hwp", ".hwpx"}
DOC_EXT = WORD_EXT | HWP_EXT
TIMEOUT = 180


class ConvertError(Exception):
    def __init__(self, msg: str, code: str = "error"):
        super().__init__(msg)
        self.code = code


def free_pdf_path(path: str) -> str:
    """'<folder>\\<stem>.pdf', or '<stem> (2).pdf' … when that name is taken."""
    folder = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(folder, stem + ".pdf")
    i = 2
    while os.path.exists(out):
        out = os.path.join(folder, f"{stem} ({i}).pdf")
        i += 1
    return out


def _pages(p: str) -> int:
    try:
        if not os.path.isfile(p) or os.path.getsize(p) < 64:
            return 0
        import pymupdf as fitz
        with fitz.open(p) as d:
            return len(d)
    except Exception:
        return 0


def hwp_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "HWPFrame.HwpObject\\CLSID"):
            pass
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


def _convert_hwp(src: str, dst: str) -> None:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    hwp = None
    try:
        hwp = win32com.client.DispatchEx("HWPFrame.HwpObject")
        try:
            # without the file-path checker module 한글 asks "allow access?" for every file
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        except Exception:
            pass
        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:
            pass
        fmt = "HWPX" if src.lower().endswith(".hwpx") else "HWP"
        if not hwp.Open(src, fmt, "forceopen:true;suspendpassword:true;versionwarning:false"):
            raise ConvertError("한글 could not open the file (is it password-protected?)", "open_failed")
        hwp.SaveAs(dst, "PDF", "")
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)              # close the document without saving it
            except Exception:
                pass
            try:
                hwp.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _run(fn, timeout: float, what: str) -> None:
    res: dict = {}

    def work():
        try:
            fn()
        except Exception as exc:     # noqa: BLE001
            res["error"] = exc

    t = threading.Thread(target=work, daemon=True, name="doc-to-pdf")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise ConvertError(f"{what} took too long (over {int(timeout)} s)", "timeout")
    if "error" in res:
        exc = res["error"]
        if isinstance(exc, ConvertError):
            raise exc
        msg = str(exc)
        if "password" in msg.lower():
            raise ConvertError("The document is password-protected", "password")
        raise ConvertError(f"{what} could not convert the file: {msg[:300]}", "convert_failed")


def to_pdf(path: str, out_path: str = "", timeout: float = TIMEOUT, *, _word=None, _hwp=None) -> dict:
    """Convert *path*; _word / _hwp replace the Office / 한글 step (tests)."""
    if not path or not os.path.isfile(path):
        raise ConvertError(f"File not found: {os.path.basename(str(path or ''))}", "not_found")
    ext = os.path.splitext(path)[1].lower()
    if ext not in DOC_EXT:
        raise ConvertError(f"Only Word and 한글 (HWP/HWPX) files can be converted here (got '{ext or 'no extension'}')",
                           "unsupported")
    out = os.path.abspath(out_path) if out_path else free_pdf_path(path)
    if os.path.exists(out):
        raise ConvertError(f"{os.path.basename(out)} already exists", "exists")
    t0 = time.time()
    if ext in WORD_EXT:
        if _word is not None:
            _word(os.path.abspath(path), out)
            app = "Word"
        else:
            import doc_preview as _dp
            try:
                r = _dp.office_to_pdf(path, timeout, app="Word")
            except _dp.PreviewError as exc:
                raise ConvertError(str(exc), exc.code) from exc
            shutil.copyfile(r["pdf_path"], out)
            app = "Word"
    else:
        if _hwp is None and not hwp_available():
            raise ConvertError("Hancom Office 한글 is not installed on this PC, so HWP / HWPX files can't be converted",
                               "no_hwp")
        conv = _hwp or _convert_hwp
        # write into the app's own cache first, so a failed run leaves nothing
        # half-made next to the document
        import doc_preview as _dp
        tmp = os.path.join(_dp.preview_cache_dir(), f"hwp_{os.getpid()}_{int(t0 * 1000)}.pdf")
        _run(lambda: conv(os.path.abspath(path), tmp), timeout, "한글")
        if not _pages(tmp):
            raise ConvertError("한글 did not produce a PDF", "convert_failed")
        shutil.copyfile(tmp, out)
        app = "Hangul"
    n = _pages(out)
    if not n:
        raise ConvertError("The converted PDF could not be read", "convert_failed")
    return {"out_path": out, "pages": n, "app": app, "seconds": round(time.time() - t0, 1)}
