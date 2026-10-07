"""
annotate_bridge.py — JS-facing methods for the annotation editor (ui/js/annotate.js).

Mixed into SFMBridge, so every public method is callable from JS as
window.pywebview.api.<name>(...). All return {ok:true, ...} or {ok:false, error}.
Engine: annotate.py.
"""
from __future__ import annotations

import getpass
import logging
import os

import annotate as _an

_log = logging.getLogger("sfm")

_MAX_SIGS = 4


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str, **kw) -> dict:
    return {"ok": False, "error": str(msg), **kw}


def _release_handles(path: str) -> None:
    try:
        import file_ops as _fo
        _fo.release_pdf_handles_for_paths((path,))
    except Exception:
        pass


class AnnotateBridgeMixin:
    """Annotation editor API."""

    def _annot_run(self, label: str, fn, *args, **kwargs) -> dict:
        try:
            return _ok(**fn(*args, **kwargs))
        except _an.AnnotateError as exc:
            _log.warning("%s: %s", label, exc)
            return _err(str(exc))
        except PermissionError as exc:
            _log.error("%s: %s", label, exc)
            return _err(f"Access denied — is the file open in another program? ({exc.filename or exc})")
        except Exception as exc:
            _log.error("%s failed: %s", label, exc, exc_info=True)
            return _err(f"{label} failed: {exc}")

    # ── who is annotating ────────────────────────────────────────────────────
    def annot_author(self) -> dict:
        """Signed-in account email, else the Windows user name."""
        name = ""
        try:
            u = getattr(self, "_cloud_user_cache", None)
            if isinstance(u, dict):
                name = str(u.get("email") or "")
        except Exception:
            name = ""
        if not name:
            try:
                name = os.environ.get("USERNAME") or getpass.getuser()
            except Exception:
                name = ""
        return _ok(author=name or "User")

    # ── load / render ────────────────────────────────────────────────────────
    def annot_load(self, path: str) -> dict:
        """{kind:'pdf'|'image', source, pages:[{w,h}], annotations:[…], author}."""
        _log.info("annot_load: %s", path)
        r = self._annot_run("Open for annotation", _an.load, path)
        if r.get("ok"):
            r["author"] = self.annot_author().get("author", "")
        return r

    def annot_page_png(self, path: str, page: int = 0, scale: float = 1.5) -> dict:
        """Page (without its annotations) as a data URL → {data_url, width, height}."""
        return self._annot_run("Render page", _an.render_page, path, int(page or 0), float(scale or 1.5))

    def annot_words(self, path: str, page: int = 0) -> dict:
        """Word boxes for text selection → {words: [[x0,y0,x1,y1,text,block,line,word], …]}."""
        return self._annot_run("Read words", lambda: {"words": _an.words(path, int(page or 0))})

    # ── save ─────────────────────────────────────────────────────────────────
    def annot_save(self, path: str, annotations: list, opts: dict | None = None) -> dict:
        """opts: {mode:'new'|'over', out_path, flatten, apply_redactions}
        → {out_path, backup, replaced, count, sidecar}. Never overwrites except
        mode 'over', which first copies the old file into _to_review/."""
        opts = opts or {}
        replace = str(opts.get("mode") or "new") == "over"
        _log.info("annot_save: %s (%d annots, replace=%s, flatten=%s)", path,
                  len(annotations or []), replace, bool(opts.get("flatten")))
        author = self.annot_author().get("author", "")
        return self._annot_run(
            "Save annotations", _an.save, path, annotations or [],
            out_path=str(opts.get("out_path") or ""), replace=replace,
            flatten=bool(opts.get("flatten")), apply_redactions=bool(opts.get("apply_redactions")),
            author=author, release_handles=_release_handles)

    # ── remembered signatures (settings) ─────────────────────────────────────
    def annot_signatures(self) -> dict:
        try:
            sigs = self._load_settings().get("annot_signatures") or []
            return _ok(signatures=[s for s in sigs if isinstance(s, str) and s.startswith("data:image/")])
        except Exception as exc:
            return _err(str(exc))

    def annot_signature_save(self, data_url: str) -> dict:
        """Remember a signature (newest first, at most 4)."""
        try:
            if not (isinstance(data_url, str) and data_url.startswith("data:image/png;base64,")):
                return _err("Not a PNG signature")
            if len(data_url) > 1_500_000:
                return _err("Signature image is too large")
            sigs = [s for s in (self._load_settings().get("annot_signatures") or []) if s != data_url]
            sigs = [data_url] + sigs
            self._save_settings({"annot_signatures": sigs[:_MAX_SIGS]})
            return _ok(signatures=sigs[:_MAX_SIGS])
        except Exception as exc:
            return _err(str(exc))

    def annot_signature_forget(self, index: int) -> dict:
        """Drop a remembered signature from the settings list (no file is touched)."""
        try:
            sigs = list(self._load_settings().get("annot_signatures") or [])
            i = int(index)
            if 0 <= i < len(sigs):
                sigs.pop(i)
                self._save_settings({"annot_signatures": sigs})
            return _ok(signatures=sigs)
        except Exception as exc:
            return _err(str(exc))
