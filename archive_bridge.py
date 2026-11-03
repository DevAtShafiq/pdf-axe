"""
archive_bridge.py — JS-facing methods for ZIP / unzip and ZIP / PDF passwords.

Mixed into SFMBridge (class SFMBridge(..., ArchiveBridgeMixin)), so every public
method here is callable from JS as window.pywebview.api.<name>(...). All
methods return {ok:true, ...} or {ok:false, error:"readable message", code}.
`code` comes from archive_tools.ArchiveError (need_password, wrong_password,
need_pyzipper, unsafe_path, not_zip, ...) so the UI can e.g. ask for a password.

Long jobs (zip_paths, zip_each, zip_extract) return at once with {ok, job_id}
and run in a thread, pushing:
    archive_progress {job_id, done, total, file}
    archive_done     {job_id, ok, out_path, error, code, ...result}
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid

import archive_tools as _at

_log = logging.getLogger("sfm")


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str, code: str = "error", **kw) -> dict:
    return {"ok": False, "error": str(msg), "code": code, **kw}


def _run(label: str, fn, *args, **kwargs) -> dict:
    try:
        return _ok(**fn(*args, **kwargs))
    except _at.ArchiveError as exc:
        _log.warning("%s: %s (%s)", label, exc, exc.code)
        return _err(str(exc), exc.code)
    except PermissionError as exc:
        _log.error("%s: %s", label, exc)
        return _err(f"Access denied — is the file open in another program? ({exc.filename or exc})",
                    "access_denied")
    except Exception as exc:
        _log.error("%s failed: %s", label, exc, exc_info=True)
        return _err(f"{label} failed: {exc}")


class ArchiveBridgeMixin:
    """ZIP / unzip / password API (see archive_tools.py for the engine)."""

    # SFMBridge provides _emit(); keep a harmless default for direct use/tests.
    def _emit(self, event, payload=None):  # pragma: no cover - overridden
        return None

    # ── job plumbing ─────────────────────────────────────────────────────────
    def _archive_jobs(self) -> dict:
        jobs = getattr(self, "_archive_job_flags", None)
        if jobs is None:
            jobs = self._archive_job_flags = {}
        return jobs

    def _archive_start(self, job_id: str, label: str, fn, *args, **kwargs) -> dict:
        job_id = str(job_id or "") or "zip_" + uuid.uuid4().hex[:10]
        cancel = threading.Event()
        self._archive_jobs()[job_id] = cancel
        last = [0.0]

        def progress(done: int, total: int, name: str = "") -> None:
            now = time.monotonic()
            if 0 < done < total and now - last[0] < 0.12:   # throttle UI events
                return
            last[0] = now
            self._emit("archive_progress", {"job_id": job_id, "done": int(done),
                                            "total": int(total), "file": str(name or "")})

        def work():
            try:
                r = _run(label, fn, *args, progress=progress, cancel=cancel.is_set, **kwargs)
            finally:
                self._archive_jobs().pop(job_id, None)
            r.setdefault("out_path", r.get("select") or r.get("out_dir") or "")
            r.setdefault("error", "")
            r.setdefault("code", "")
            self._emit("archive_done", {"job_id": job_id, **r})

        threading.Thread(target=work, daemon=True, name=f"archive-{job_id}").start()
        return _ok(job_id=job_id, started=True)

    def archive_cancel(self, job_id: str) -> dict:
        ev = self._archive_jobs().get(str(job_id or ""))
        if ev:
            ev.set()
            return _ok(cancelled=True)
        return _ok(cancelled=False)

    # ── info ─────────────────────────────────────────────────────────────────
    def archive_capabilities(self) -> dict:
        """{zip_password: bool, zip_aes_read: bool, pdf_password: bool}"""
        return _ok(**_at.capabilities())

    def zip_list(self, path: str, password: str = "") -> dict:
        """Entries {index, name, size, compressed, encrypted, date, is_dir} + summary."""
        return _run("Read ZIP", _at.list_zip, path, password or None)

    # Old name (removed in cfb01d6) kept working for any leftover caller.
    def list_zip(self, path: str) -> dict:
        return self.zip_list(path)

    # ── zip ──────────────────────────────────────────────────────────────────
    def zip_paths(self, paths: list, out_name: str = "", password: str = "", job_id: str = "",
                  compression: str = "deflate", level: int = 6) -> dict:
        """Zip the selected files/folders into one new ZIP (async → archive_done)."""
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return _err("Select files or folders to zip")
        if password and not _at.HAVE_PYZIPPER:
            return _err(_at.NEED_PYZIPPER_MSG, "need_pyzipper")
        _log.info("zip_paths: %d item(s) -> %r (password=%s)", len(paths), out_name, bool(password))
        return self._archive_start(job_id, "Zip", _at.zip_paths, paths, out_name or None,
                                   password or None, compression=compression or "deflate",
                                   level=int(level if level is not None else 6))

    def zip_each(self, paths: list, password: str = "", job_id: str = "",
                 compression: str = "deflate", level: int = 6) -> dict:
        """One ZIP per selected item (e.g. 'Zip each subfolder'), async → archive_done
        with {outputs:[...], errors:[...]}."""
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return _err("Nothing to zip")
        if password and not _at.HAVE_PYZIPPER:
            return _err(_at.NEED_PYZIPPER_MSG, "need_pyzipper")

        def run_each(items, progress=None, cancel=None):
            outs, errs = [], []
            for n, p in enumerate(items):
                if cancel and cancel():
                    errs.append({"path": p, "error": "Cancelled"})
                    break
                if progress:
                    progress(n, len(items), os.path.basename(p))
                try:
                    r = _at.zip_paths([p], None, password or None, compression=compression or "deflate",
                                      level=int(level if level is not None else 6), cancel=cancel)
                    outs.append(r["out_path"])
                except _at.ArchiveError as exc:
                    errs.append({"path": p, "error": str(exc), "code": exc.code})
            if progress:
                progress(len(items), len(items), "")
            if not outs and errs:
                raise _at.ArchiveError(errs[0]["error"], errs[0].get("code", "error"))
            return {"outputs": outs, "errors": errs, "out_path": outs[0] if len(outs) == 1 else ""}

        return self._archive_start(job_id, "Zip", run_each, paths)

    # Old name kept for compatibility (single ZIP of one folder).
    def zip_folder(self, path: str, job_id: str = "") -> dict:
        return self.zip_paths([path], "", "", job_id)

    # ── extract ──────────────────────────────────────────────────────────────
    def zip_extract(self, path: str, dest: str = "", password: str = "", members=None,
                    job_id: str = "", mode: str = "folder") -> dict:
        """Extract (async → archive_done). The password is checked first, so a
        missing/wrong one comes back right away as code need_password /
        wrong_password (the UI asks again) instead of as a failed job.
        mode: 'folder' (new folder named after the ZIP) or 'here'."""
        try:
            info = _at.list_zip(path, password or None)
        except _at.ArchiveError as exc:
            return _err(str(exc), exc.code)
        except Exception as exc:
            return _err(f"Could not read the ZIP: {exc}")
        if info.get("aes") and not _at.HAVE_PYZIPPER:
            return _err("This ZIP uses AES encryption. " + _at.NEED_PYZIPPER_MSG, "need_pyzipper")
        if info.get("encrypted"):
            sel = info["entries"]
            if members:
                keys = {str(m) for m in members}
                sel = [e for e in sel if str(e["index"]) in keys or e["name"] in keys
                       or any(e["name"].startswith(k.rstrip("/") + "/") for k in keys)]
            if any(e["encrypted"] for e in sel):
                if not password:
                    return _err("This ZIP is password-protected", "need_password")
                if info.get("password_ok") is False:
                    return _err("Wrong password", "wrong_password")
        _log.info("zip_extract: %s -> %r mode=%s members=%s", path, dest, mode,
                  len(members) if members else "all")
        return self._archive_start(job_id, "Extract", _at.extract_zip, path, dest or None,
                                   password or None, members or None,
                                   mode="here" if mode == "here" else "folder")

    # Old names (removed in cfb01d6) kept working.
    def unzip(self, path: str) -> dict:
        return self.zip_extract(path)

    # ── passwords ────────────────────────────────────────────────────────────
    def zip_set_password(self, path: str, new_password: str, old_password: str = "") -> dict:
        """Add or change → new 'name_protected.zip' (AES-256; needs pyzipper)."""
        _log.info("zip_set_password: %s", path)
        return _run("Protect ZIP", _at.zip_set_password, path, new_password, old_password or None)

    def zip_remove_password(self, path: str, password: str) -> dict:
        """→ new 'name_unlocked.zip'."""
        _log.info("zip_remove_password: %s", path)
        return _run("Remove ZIP password", _at.zip_remove_password, path, password)

    def pdf_is_encrypted(self, path: str) -> dict:
        """{encrypted, needs_password, method}"""
        return _run("PDF check", _at.pdf_is_encrypted, path)

    def pdf_set_password(self, path: str, user_password: str, owner_password: str = "",
                         permissions=None, current_password: str = "") -> dict:
        """→ new 'name_protected.pdf' (AES-256). permissions: {print, copy, edit, annotate}."""
        _log.info("pdf_set_password: %s perms=%s", path, permissions)
        return _run("Protect PDF", _at.pdf_set_password, path, user_password,
                    owner_password or None, permissions if isinstance(permissions, dict) else None,
                    None, current_password or None)

    def pdf_remove_password(self, path: str, password: str) -> dict:
        """→ new 'name_unlocked.pdf'."""
        _log.info("pdf_remove_password: %s", path)
        return _run("Remove PDF password", _at.pdf_remove_password, path, password)

    def pdf_preview_state(self, path: str) -> dict:
        """Cheap check on the preview's cached document:
        {encrypted, needs_password, locked} — locked turns false after
        pdf_unlock_preview. Falls back to pdf_is_encrypted."""
        try:
            import file_ops as _fo
            doc = _fo.get_cached_fitz_doc(path)
            if doc is None:
                r = _at.pdf_is_encrypted(path)
                return _ok(locked=r["needs_password"], **r)
            # NOTE: never read doc.needs_pass on the cached document — on an
            # authenticated (unlocked) PyMuPDF document it resets the key, and
            # every later render/text read fails ("aes padding out of range").
            # is_encrypted is safe: True only while it still needs the password.
            if bool(getattr(doc, "is_encrypted", False)):
                return _ok(encrypted=True, needs_password=True, locked=True, method="")
            r = _at.pdf_is_encrypted(path)          # its own document — safe
            method = r.get("method") or ((doc.metadata or {}).get("encryption") or "")
            return _ok(encrypted=bool(r["needs_password"] or method),
                       needs_password=bool(r["needs_password"]), locked=False, method=method)
        except _at.ArchiveError as exc:
            return _err(str(exc), exc.code)
        except Exception as exc:
            return _err(str(exc))

    def pdf_unlock_preview(self, path: str, password: str) -> dict:
        """Unlock a password-protected PDF for the preview (in memory only — the
        file is not changed). The preview's cached document stays unlocked."""
        try:
            if not _at.pdf_check_password(path, password):
                return _err("Wrong password", "wrong_password")
            import file_ops as _fo
            doc = _fo.get_cached_fitz_doc(path)
            if doc is None:
                return _err("Could not open the PDF")
            if getattr(doc, "is_encrypted", False) and not doc.authenticate(password):
                return _err("Wrong password", "wrong_password")
            return _ok(pages=len(doc))
        except _at.ArchiveError as exc:
            return _err(str(exc), exc.code)
        except Exception as exc:
            _log.error("pdf_unlock_preview failed: %s", exc, exc_info=True)
            return _err(str(exc))
