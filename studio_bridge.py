"""
studio_bridge.py — JS-facing methods for PDF Studio (ui/js/studio.js).

Mixed into SFMBridge, so every public method is callable from JS as
window.pywebview.api.<name>(...). All return {ok:true, ...} or
{ok:false, error, code}. Engine: pdf_studio.py.

The Studio's tools write their steps with the existing engines (annot_save,
pdfedit_apply, pdf_build) into the paths studio_next_step hands out.
"""
from __future__ import annotations

import logging

import pdf_studio as _ps

_log = logging.getLogger("sfm")


def _release_handles(path: str) -> None:
    try:
        import file_ops as _fo
        _fo.release_pdf_handles_for_paths((path,))
    except Exception:
        pass


class StudioBridgeMixin:
    """PDF Studio API."""

    def _studio_run(self, label: str, fn, *args, **kwargs) -> dict:
        try:
            return {"ok": True, **fn(*args, **kwargs)}
        except _ps.StudioError as exc:
            _log.warning("%s: %s", label, exc)
            return {"ok": False, "error": str(exc), "code": exc.code}
        except PermissionError as exc:
            _log.error("%s: %s", label, exc)
            return {"ok": False, "code": "locked",
                    "error": f"Access denied — is the file open in another program? ({exc.filename or exc})"}
        except Exception as exc:
            code = getattr(exc, "code", "error")
            _log.error("%s failed: %s", label, exc, exc_info=True)
            return {"ok": False, "error": f"{label} failed: {exc}", "code": code if isinstance(code, str) else "error"}

    def studio_begin(self, path: str, password: str = "") -> dict:
        """Start a session: {session, work, source, name, page_count, has_text, has_form, protected}."""
        _log.info("studio_begin: %s", path)
        return self._studio_run("Open in PDF Studio", _ps.begin, path, password or "")

    def studio_next_step(self, session: str) -> dict:
        """{path, index}: where the next tool should write its result (a new file)."""
        return self._studio_run("Prepare step", _ps.next_step, session)

    def studio_step_info(self, work: str) -> dict:
        """{work, size, page_count, has_text, has_form} for a step file."""
        return self._studio_run("Read step", _ps.step_info, work)

    def studio_finish(self, work: str, original: str, opts: dict | None = None) -> dict:
        """opts: {mode:'new'|'over', out_path, target_kb, password, make_dir}
        → {out_path, backup, replaced, size, compress, protected}. Never
        overwrites except mode 'over', which first copies the original into
        _to_review/."""
        opts = opts or {}
        replace = str(opts.get("mode") or "new") == "over"
        _log.info("studio_finish: %s -> %s (replace=%s, target_kb=%s, password=%s)", work,
                  opts.get("out_path") or original, replace, opts.get("target_kb"), bool(opts.get("password")))
        return self._studio_run("Save", _ps.finish, work, original,
                                out_path=str(opts.get("out_path") or ""), replace=replace,
                                target_kb=float(opts.get("target_kb") or 0),
                                password=str(opts.get("password") or ""),
                                make_dir=bool(opts.get("make_dir")),
                                release_handles=_release_handles)
