"""
pdf_edit_bridge.py — JS-facing methods for the PDF text editor (ui/js/pdf-edit.js).

Mixed into SFMBridge, so every public method is callable from JS as
window.pywebview.api.<name>(...). All return {ok:true, ...} or
{ok:false, error, code}. Engine: pdf_edit.py.
"""
from __future__ import annotations

import logging

import pdf_edit as _pe

_log = logging.getLogger("sfm")


def _release_handles(path: str) -> None:
    try:
        import file_ops as _fo
        _fo.release_pdf_handles_for_paths((path,))
    except Exception:
        pass


class PdfEditBridgeMixin:
    """PDF text editor API."""

    def _pdfedit_run(self, label: str, fn, *args, **kwargs) -> dict:
        try:
            return {"ok": True, **fn(*args, **kwargs)}
        except _pe.PdfEditError as exc:
            _log.warning("%s: %s", label, exc)
            return {"ok": False, "error": str(exc), "code": exc.code}
        except PermissionError as exc:
            _log.error("%s: %s", label, exc)
            return {"ok": False, "code": "locked",
                    "error": f"Access denied — is the file open in another program? ({exc.filename or exc})"}
        except Exception as exc:
            _log.error("%s failed: %s", label, exc, exc_info=True)
            return {"ok": False, "error": f"{label} failed: {exc}", "code": "error"}

    def pdfedit_open(self, path: str) -> dict:
        """{pages:[{w,h,scanned}], name} — page sizes for the page strip."""
        _log.info("pdfedit_open: %s", path)
        return self._pdfedit_run("Open PDF", _pe.doc_info, path)

    def pdfedit_blocks(self, path: str, page: int = 0) -> dict:
        """Editable text blocks and images of one page (display coordinates).
        A scanned page answers {ok:false, code:'scanned_page'}."""
        return self._pdfedit_run("Read text blocks", _pe.extract_blocks, path, int(page or 0))

    def pdfedit_page_png(self, path: str, page: int = 0, scale: float = 1.5) -> dict:
        """Page image (form fields and annotations included) → {data_url, width, height}."""
        return self._pdfedit_run("Render page", _pe.render_page, path, int(page or 0), float(scale or 1.5))

    def pdfedit_apply(self, path: str, edits: list, opts: dict | None = None) -> dict:
        """opts: {mode:'new'|'over', out_path} → {out_path, backup, replaced,
        substituted:[{block_id, page, wanted, used}], warnings, count}.
        Never overwrites except mode 'over', which first copies the old file
        into _to_review/."""
        opts = opts or {}
        replace = str(opts.get("mode") or "new") == "over"
        _log.info("pdfedit_apply: %s (%d edits, replace=%s)", path, len(edits or []), replace)
        return self._pdfedit_run("Save edits", _pe.apply_edits, path, list(edits or []),
                                 out_path=str(opts.get("out_path") or "") or None,
                                 replace=replace, release_handles=_release_handles)

    def pdfedit_fonts(self) -> dict:
        """Font families the editor can write with → {fonts:[{name, css, bold, italic}]}."""
        return self._pdfedit_run("List fonts", lambda: {"fonts": _pe.available_fonts()})
