"""
doc_preview_bridge.py — preview-pane bridge methods (mixed into SFMBridge).

  pdf_text_layer(path, page)      → positioned words for the selectable text layer
  pdf_page_text(path, page)       → plain text of one page
  pdf_all_text(path)              → plain text of the whole PDF
  docx_preview_html(path)         → Word document as HTML
  sheet_preview_html(path, sheet) → Excel sheet / CSV as an HTML grid
  office_preview_info(path)       → which Office app could convert it, installed?
  office_preview_pdf(path)        → convert via Microsoft Office into the preview cache

All read-only: the source file is never modified.
"""
from __future__ import annotations

import logging
import os

import doc_preview as _dp

_log = logging.getLogger("sfm.doc_preview")


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str, code: str = "") -> dict:
    d = {"ok": False, "error": str(msg)}
    if code:
        d["code"] = code
    return d


def _guard(label, fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except _dp.PreviewError as exc:
        return _err(str(exc), exc.code)
    except Exception as exc:  # noqa: BLE001
        _log.error("%s failed: %s", label, exc, exc_info=True)
        return _err(str(exc))


class DocPreviewBridgeMixin:

    # ── PDF text ─────────────────────────────────────────────────────────────
    def pdf_text_layer(self, path: str, page: int = 0) -> dict:
        return _guard("pdf_text_layer", lambda: _ok(**_dp.pdf_text_layer(path, int(page))))

    def pdf_page_text(self, path: str, page: int = 0) -> dict:
        def run():
            t = _dp.pdf_page_text(path, int(page))
            return _ok(text=t, chars=len(t))
        return _guard("pdf_page_text", run)

    def pdf_all_text(self, path: str) -> dict:
        def run():
            t, n = _dp.pdf_all_text(path)
            return _ok(text=t, chars=len(t), pages=n)
        return _guard("pdf_all_text", run)

    # ── Word / Excel / CSV ───────────────────────────────────────────────────
    def docx_preview_html(self, path: str) -> dict:
        def run():
            r = _dp.docx_to_html(path)
            app = _dp.office_app_for(os.path.splitext(path)[1], include_native=True)
            return _ok(office=bool(app and _dp.office_available(app)), app=app or "", **r)
        return _guard("docx_preview_html", run)

    def sheet_preview_html(self, path: str, sheet: str = "") -> dict:
        def run():
            ext = os.path.splitext(path)[1].lower()
            r = _dp.csv_to_html(path) if ext in _dp.CSV_EXT else _dp.xlsx_to_html(path, sheet or "")
            app = "Excel" if ext not in _dp.CSV_EXT else ""
            return _ok(office=bool(app and _dp.office_available(app)), app=app, **r)
        return _guard("sheet_preview_html", run)

    # ── Office → PDF ─────────────────────────────────────────────────────────
    def office_preview_info(self, path: str) -> dict:
        def run():
            ext = os.path.splitext(path)[1].lower()
            app = _dp.office_app_for(ext, include_native=True)
            avail = bool(app and _dp.office_available(app))
            cached = ""
            if avail and os.path.isfile(path):
                p = _dp.cached_pdf_path(path)
                if _dp._valid_pdf(p):
                    cached = p
            return _ok(app=app or "", available=avail, cached_pdf=cached)
        return _guard("office_preview_info", run)

    def office_preview_pdf(self, path: str) -> dict:
        def run():
            _log.info("office_preview_pdf: %s", path)
            r = _dp.office_to_pdf(path)
            _log.info("office_preview_pdf: → %s (%s pages, cached=%s)", r["pdf_path"], r["pages"], r["cached"])
            return _ok(**r)
        return _guard("office_preview_pdf", run)
