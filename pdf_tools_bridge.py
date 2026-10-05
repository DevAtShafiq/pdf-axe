"""
pdf_tools_bridge.py — JS-facing methods for split / merge / arrange / extract.

Mixed into SFMBridge (class SFMBridge(PdfToolsBridgeMixin)), so every public
method here is callable from JS as window.pywebview.api.<name>(...). All
methods return {ok:true, ...} or {ok:false, error:"readable message"}.

Long operations accept an optional job_id; while they run, progress events
are pushed to JS as  pdf_tools_progress {job, done, total, msg}.
"""
from __future__ import annotations

import logging
import os
import time

import pdf_tools as _pt

_log = logging.getLogger("sfm")


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str, **kw) -> dict:
    return {"ok": False, "error": str(msg), **kw}


def _release_handles(path: str) -> None:
    """Close the preview's cached fitz handle so Windows lets us replace the file."""
    try:
        import file_ops as _fo
        _fo.release_pdf_handles_for_paths((path,))
    except Exception:
        pass


class PdfToolsBridgeMixin:
    """Split / merge / arrange / extract API (see pdf_tools.py for the engine)."""

    # SFMBridge provides _emit(); keep a harmless default for direct use/tests.
    def _emit(self, event, payload=None):  # pragma: no cover - overridden
        return None

    def _pdf_progress(self, job_id: str):
        if not job_id:
            return None
        last = [0.0]

        def cb(done: int, total: int, msg: str = "") -> None:
            now = time.monotonic()
            if done < total and now - last[0] < 0.15:   # throttle UI events
                return
            last[0] = now
            self._emit("pdf_tools_progress",
                       {"job": job_id, "done": int(done), "total": int(total), "msg": str(msg)})
        return cb

    def _pdf_run(self, label: str, fn, *args, **kwargs) -> dict:
        try:
            return _ok(**fn(*args, **kwargs))
        except _pt.PdfToolsError as exc:
            _log.warning("%s: %s", label, exc)
            return _err(str(exc))
        except PermissionError as exc:
            _log.error("%s: %s", label, exc)
            return _err(f"Access denied — is the file open in another program? ({exc.filename or exc})")
        except Exception as exc:
            _log.error("%s failed: %s", label, exc, exc_info=True)
            return _err(f"{label} failed: {exc}")

    # ── info / thumbnails ────────────────────────────────────────────────────
    def pdf_info(self, path: str) -> dict:
        """{page_count, is_image, encrypted, size} for a PDF or image."""
        return self._pdf_run("PDF info", _pt.pdf_info, path)

    def pdf_infos(self, paths: list) -> dict:
        """pdf_info for several files; per-file errors are reported, not raised."""
        out = []
        for p in paths or []:
            r = self.pdf_info(p)
            r["path"] = os.path.abspath(str(p))
            r.setdefault("name", os.path.basename(str(p)))
            out.append(r)
        return _ok(items=out)

    def pdf_thumbnails(self, path: str, pages: list, width: int = 160) -> dict:
        """Batch-render page thumbnails → {thumbs: {"0": data_url, ...}}."""
        try:
            t = _pt.thumbnails(path, pages, width)
            return _ok(thumbs={str(k): v for k, v in t.items()})
        except _pt.PdfToolsError as exc:
            return _err(str(exc))
        except Exception as exc:
            _log.error("pdf_thumbnails failed: %s", exc)
            return _err(str(exc))

    def pdf_parse_ranges(self, spec: str, page_count: int, mode: str = "ranges",
                         every: int = 1) -> dict:
        """Validate a split/extract spec → {groups: [[1,2,3],[4]], count}. 1-based."""
        try:
            groups = _pt.split_groups(int(page_count), mode, every, spec)
            return _ok(groups=[[p + 1 for p in g] for g in groups], count=len(groups))
        except _pt.PdfToolsError as exc:
            return _err(str(exc))
        except Exception as exc:
            return _err(str(exc))

    def browse_for_pdfs_or_images(self) -> dict:
        """Native multi-select picker for PDFs and images (Merge 'Add files…')."""
        try:
            import webview
            win = getattr(self, "_window", None)
            if win is None:
                return _err("Window not ready")
            result = win.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=(
                    "PDFs and images (*.pdf;*.jpg;*.jpeg;*.png;*.bmp;*.gif;*.tif;*.tiff;*.webp)",
                    "PDF files (*.pdf)",
                    "All files (*.*)",
                ),
            )
            if result:
                return _ok(paths=[os.path.normpath(p) for p in result])
            return _ok(paths=[], cancelled=True)
        except Exception as exc:
            return _err(str(exc))

    # ── operations ───────────────────────────────────────────────────────────
    def pdf_merge(self, inputs: list, out_path: str = "", bookmarks: bool = True,
                  job_id: str = "") -> dict:
        """Merge PDFs/images in order → new file (never overwrites)."""
        _log.info("pdf_merge: %d input(s) -> %s", len(inputs or []), out_path)
        return self._pdf_run("Merge", _pt.merge, inputs, out_path,
                             bookmarks=bool(bookmarks), progress=self._pdf_progress(job_id))

    def pdf_split(self, path: str, mode: str = "each", every: int = 1, ranges: str = "",
                  out_dir: str = "", job_id: str = "") -> dict:
        """Split into '<name>_split/<name>_p1-3.pdf' files."""
        _log.info("pdf_split: %s mode=%s every=%s ranges=%r", path, mode, every, ranges)
        return self._pdf_run("Split", _pt.split, path, mode, every=every, ranges=ranges,
                             out_dir=out_dir, progress=self._pdf_progress(job_id))

    def pdf_extract(self, path: str, spec: str, out_path: str = "", job_id: str = "") -> dict:
        """Pages in *spec* (e.g. '1-3,7') → new PDF."""
        _log.info("pdf_extract: %s spec=%r -> %s", path, spec, out_path)
        return self._pdf_run("Extract", _pt.extract, path, spec, out_path,
                             progress=self._pdf_progress(job_id))

    def pdf_build(self, pages: list, out_path: str, replace: bool = False,
                  job_id: str = "") -> dict:
        """Arrange save: ordered [{path, page, rotate}] → new file, or replace
        *out_path* after backing the old version up into _to_review/."""
        _log.info("pdf_build: %d page(s) -> %s (replace=%s)", len(pages or []), out_path, replace)
        return self._pdf_run("Save", _pt.build_pdf, pages, out_path, replace=bool(replace),
                             progress=self._pdf_progress(job_id),
                             release_handles=_release_handles)
