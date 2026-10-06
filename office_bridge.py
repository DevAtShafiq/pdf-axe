"""
office_bridge.py — OFFICE + SHARED DRIVE methods of the JS bridge.

SFMBridge (sfm_bridge.py) inherits OfficeBridgeMixin next to CloudBridgeMixin,
so every public method here is exposed to JS as window.pywebview.api.<name>.
It talks to the account server's /office and /shared routes through
cloud_client.CloudClient, reusing CloudBridgeMixin's session, error mapping
(_cloud_err) and plan gate (_require_plan).

The shared drive is the office's Country → Program → Student file tree. Levels:
depth 1 "country", 2 "program", 3 "student", deeper items have level null.

Every method returns {"ok": True, ...} or {"ok": False, "error": "<readable text>", ...}
(need_login / need_subscription / offline / status flags as in cloud_bridge).

Events pushed to JS:
  office_changed            {office_id, reason}          membership / settings / plan changed
  shared_changed            {office_id, paths, actor, action}   something changed in the shared drive
  shared_upload_progress    {job_id, file, index, total, bytes, total_bytes, file_bytes, file_total}
                            bytes/total_bytes = the whole job; file_bytes/file_total = current file
  shared_upload_done        {job_id, ok, uploaded, errors, cancelled}
  shared_download_progress  {job_id, file, index, total, bytes, total_bytes}   (current file)
  shared_download_done      {job_id, ok, files, errors, local_dir}

Desktop-side safety: downloads and "open" never replace a local file — an
existing name gets " (2)", " (3)", … — and nothing here deletes local files.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid

from cloud_bridge import _err, _fmt_size, _ok

_log = logging.getLogger("sfm")

_SKIP_DIRS = {"_to_review", "__pycache__", ".git"}


def _skip_file(name: str) -> bool:
    low = name.lower()
    return (name.startswith((".", "~$")) or name.endswith((".part", ".tmp"))
            or low in ("desktop.ini", "thumbs.db"))


def _rel_parts(path: str) -> list:
    return [s for s in str(path or "").replace("\\", "/").split("/") if s and s not in (".", "..")]


def _join_remote(base: str, rel: str) -> str:
    return "/".join(_rel_parts(base) + _rel_parts(rel))


class OfficeBridgeMixin:
    _shared_jobs: dict = {}          # job_id → threading.Event (cancel flag)
    _shared_jobs_lock = threading.Lock()

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _office_ready(self):
        """None when signed in to a server, else an _err dict."""
        if not self._cloud_server_url():
            return _err("Set the server address first", need_login=True)
        if not self._cloud_token():
            return _err("Sign in first", need_login=True)
        return None

    def _office_call(self, fn, paid: bool = False, **extra) -> dict:
        """Run fn(client) → dict of result fields, mapping errors for the UI."""
        from cloud_client import CloudError
        try:
            gate = self._office_ready()
            if gate:
                return gate
            if paid:
                gate = self._require_plan()
                if gate:
                    return gate
            out = fn(self._cloud_client())
            return _ok(**(out or {}), **extra)
        except CloudError as exc:
            if exc.status == 404 and str(exc) == "You are not in an office yet":
                return self._cloud_err(exc, need_office=True)
            return self._cloud_err(exc)
        except Exception as exc:
            _log.exception("office bridge")
            return _err(str(exc))

    def _office_on_event(self, name: str, data) -> None:
        """Called by CloudBridgeMixin._cloud_on_event for every live event."""
        if name in ("office_changed", "shared_changed"):
            self._emit(name, data if isinstance(data, dict) else {})

    @staticmethod
    def _shared_add_sizes(items):
        for it in items or []:
            if isinstance(it, dict) and not it.get("is_dir"):
                it["size_str"] = _fmt_size(int(it.get("size") or 0))
        return items

    def _shared_new_job(self, job_id: str) -> tuple[str, threading.Event]:
        job_id = str(job_id or "") or uuid.uuid4().hex[:12]
        ev = threading.Event()
        with self._shared_jobs_lock:
            self._shared_jobs[job_id] = ev
        return job_id, ev

    def _shared_end_job(self, job_id: str) -> None:
        with self._shared_jobs_lock:
            self._shared_jobs.pop(job_id, None)

    # ── office ───────────────────────────────────────────────────────────────

    def office_get_state(self) -> dict:
        def run(c):
            st = c.office_state()
            return {"office": st.get("office"), "pending_invites": st.get("pending_invites") or []}
        return self._office_call(run)

    def office_create(self, name: str) -> dict:
        return self._office_call(lambda c: {"office": c.office_create(str(name or ""))})

    def office_rename(self, name: str) -> dict:
        return self._office_call(lambda c: {"office": c.office_rename(str(name or ""))})

    def office_join(self, code: str) -> dict:
        if not str(code or "").strip():
            return _err("Enter the invite code")
        return self._office_call(lambda c: {"office": c.office_join(str(code))})

    def office_accept_invite(self, invite_id) -> dict:
        return self._office_call(lambda c: {"office": c.office_accept_invite(int(invite_id))})

    def office_decline_invite(self, invite_id) -> dict:
        return self._office_call(lambda c: c.office_decline_invite(int(invite_id)))

    def office_leave(self) -> dict:
        return self._office_call(lambda c: c.office_leave())

    def office_transfer(self, user_id) -> dict:
        return self._office_call(lambda c: {"office": c.office_transfer(int(user_id))})

    def office_members(self) -> dict:
        return self._office_call(lambda c: {"members": c.office_members()})

    def office_invite(self, email: str = "", role: str = "staff") -> dict:
        return self._office_call(lambda c: {"invite": c.office_invite(str(email or "").strip(),
                                                                      str(role or "staff"))})

    def office_invites(self) -> dict:
        return self._office_call(lambda c: {"invites": c.office_invites()})

    def office_revoke_invite(self, invite_id) -> dict:
        return self._office_call(lambda c: c.office_revoke_invite(int(invite_id)))

    def office_set_role(self, user_id, role: str) -> dict:
        return self._office_call(lambda c: c.office_set_role(int(user_id), str(role or "")))

    def office_remove_member(self, user_id) -> dict:
        return self._office_call(lambda c: c.office_remove_member(int(user_id)))

    def office_settings_get(self) -> dict:
        return self._office_call(lambda c: {"settings": c.office_settings()})

    def office_settings_set(self, settings) -> dict:
        if not isinstance(settings, dict):
            return _err("Settings must be an object")
        return self._office_call(lambda c: {"settings": c.office_set_settings(settings)})

    # ── shared drive: browse / organise ──────────────────────────────────────

    def shared_list(self, path: str = "") -> dict:
        def run(c):
            r = c.shared_list(str(path or ""))
            self._shared_add_sizes(r.get("items"))
            return r
        return self._office_call(run, paid=True)

    def shared_mkdir(self, parent_path: str, name: str) -> dict:
        return self._office_call(lambda c: c.shared_mkdir(str(parent_path or ""), str(name or "")), paid=True)

    def shared_new_student(self, country: str, program: str, student_name: str) -> dict:
        def run(c):
            r = c.shared_new_student(str(country or ""), str(program or ""), str(student_name or ""))
            out = {"path": r["path"], "created": r.get("created") or []}
            if r.get("existed"):
                out["existed"] = True
            return out
        return self._office_call(run, paid=True)

    def shared_rename(self, path: str, new_name: str) -> dict:
        return self._office_call(lambda c: c.shared_rename(str(path or ""), str(new_name or "")), paid=True)

    def shared_move(self, paths: list, dest_dir: str = "") -> dict:
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return _err("Nothing selected to move")
        return self._office_call(lambda c: c.shared_move(paths, str(dest_dir or "")), paid=True)

    def shared_trash(self, paths: list) -> dict:
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return _err("Nothing selected")
        return self._office_call(lambda c: c.shared_trash(paths), paid=True)

    def shared_trash_list(self) -> dict:
        return self._office_call(lambda c: {"items": self._shared_add_sizes(c.shared_trash_list())}, paid=True)

    def shared_restore(self, ids: list) -> dict:
        ids = [int(i) for i in (ids or []) if str(i).strip().lstrip("-").isdigit()]
        if not ids:
            return _err("Nothing selected to restore")
        return self._office_call(lambda c: c.shared_restore(ids), paid=True)

    def shared_purge(self, ids: list) -> dict:
        """Owner/admin: permanently erase items from the shared trash (server-side)."""
        ids = [int(i) for i in (ids or []) if str(i).strip().lstrip("-").isdigit()]
        if not ids:
            return _err("Nothing selected")
        return self._office_call(lambda c: c.shared_purge(ids), paid=True)

    def shared_search(self, query: str) -> dict:
        q = str(query or "").strip()
        if not q:
            return _ok(items=[])
        return self._office_call(lambda c: {"items": self._shared_add_sizes(c.shared_search(q))}, paid=True)

    def shared_activity(self, limit: int = 50) -> dict:
        try:
            n = int(limit or 50)
        except (TypeError, ValueError):
            n = 50
        return self._office_call(lambda c: {"events": c.shared_activity(n)})

    def shared_usage(self) -> dict:
        def run(c):
            u = c.shared_usage()
            used, quota = int(u.get("used") or 0), int(u.get("quota") or 0)
            return {"used": used, "quota": quota, "used_str": _fmt_size(used), "quota_str": _fmt_size(quota),
                    "max_upload": int(u.get("max_upload") or 0)}
        return self._office_call(run)

    # ── shared drive: transfer ───────────────────────────────────────────────

    def shared_upload(self, local_paths: list, remote_dir: str = "", job_id: str = "",
                      on_conflict: str = "rename") -> dict:
        """Upload files and folders (recursively, keeping their structure) into
        remote_dir. on_conflict: "rename" (default, keep both), "replace" or "error"."""
        paths = [os.path.abspath(str(p)) for p in (local_paths or []) if p]
        if not paths:
            return _err("Nothing selected to upload")
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            return _err(f"Not found: {missing[0]}")
        if on_conflict not in ("rename", "replace", "error"):
            on_conflict = "rename"
        gate = self._office_ready() or self._require_plan()
        if gate:
            return gate
        base = "/".join(_rel_parts(remote_dir))
        job_id, cancel = self._shared_new_job(job_id)

        def plan():
            files, empty_dirs = [], []
            for p in paths:
                if os.path.isfile(p):
                    files.append((p, _join_remote(base, os.path.basename(p))))
                    continue
                top = os.path.basename(p.rstrip("\\/"))
                for root, dirs, names in os.walk(p):
                    dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
                    rel_dir = os.path.relpath(root, p).replace(os.sep, "/")
                    rel_dir = top if rel_dir == "." else f"{top}/{rel_dir}"
                    keep = [n for n in names if not _skip_file(n)]
                    for n in keep:
                        files.append((os.path.join(root, n), _join_remote(base, f"{rel_dir}/{n}")))
                    if not keep and not dirs:
                        empty_dirs.append(_join_remote(base, rel_dir))
            return files, empty_dirs

        def run():
            from cloud_client import CloudError
            uploaded, errors = [], []
            try:
                files, empty_dirs = plan()
                sizes = {}
                for full, _r in files:
                    try:
                        sizes[full] = os.path.getsize(full)
                    except OSError:
                        sizes[full] = 0
                total_bytes = sum(sizes.values())
                done_bytes = 0
                total = len(files)
                client = self._cloud_client()
                limit = 0
                try:
                    limit = int(client.shared_usage().get("max_upload") or 0)
                except Exception:
                    pass
                for d in empty_dirs:
                    if cancel.is_set():
                        break
                    try:
                        client.shared_ensure_dir(d)
                    except CloudError as exc:
                        errors.append({"path": d, "error": self._cloud_err(exc)["error"]})
                for i, (full, remote) in enumerate(files):
                    if cancel.is_set():
                        break
                    size = sizes.get(full, 0)
                    if limit and size > limit:
                        errors.append({"path": full, "error": f"{os.path.basename(full)} is {_fmt_size(size)}; "
                                                              f"the upload limit is {_fmt_size(limit)}"})
                        done_bytes += size
                        continue
                    last = [0.0]

                    def prog(b, _t, _i=i, _f=remote, _base=done_bytes, _size=size):
                        now = time.time()
                        if now - last[0] < 0.2 and b < _t:
                            return
                        last[0] = now
                        fb = min(b, _size)
                        self._emit("shared_upload_progress", {
                            "job_id": job_id, "file": _f, "index": _i, "total": total,
                            "bytes": _base + fb, "total_bytes": total_bytes,
                            "file_bytes": fb, "file_total": _size})

                    prog(0, 1)
                    try:
                        r = client.shared_upload(full, remote, on_conflict=on_conflict, progress=prog)
                        uploaded.append((r.get("file") or {}).get("path") or remote)
                    except CloudError as exc:
                        errors.append({"path": full, "remote": remote, "error": self._cloud_err(exc)["error"]})
                        if exc.status in (0, 401, 402, 507):
                            break
                    except OSError as exc:
                        errors.append({"path": full, "remote": remote, "error": str(exc)})
                    done_bytes += size
                self._emit("shared_upload_done", {"job_id": job_id, "ok": not errors and not cancel.is_set(),
                                                  "uploaded": uploaded, "errors": errors[:100],
                                                  "cancelled": cancel.is_set()})
            except Exception as exc:
                _log.exception("shared upload")
                self._emit("shared_upload_done", {"job_id": job_id, "ok": False, "uploaded": uploaded,
                                                  "errors": errors[:100] + [{"path": "", "error": str(exc)}],
                                                  "cancelled": cancel.is_set()})
            finally:
                self._shared_end_job(job_id)

        self._thread(run)
        return _ok(started=True, job_id=job_id)

    def shared_cancel(self, job_id: str) -> dict:
        """Stop an upload/download job after the current file."""
        with self._shared_jobs_lock:
            ev = self._shared_jobs.get(str(job_id or ""))
        if ev is not None:
            ev.set()
        return _ok(cancelled=ev is not None)

    def shared_download(self, remote_paths: list, local_dir: str, job_id: str = "") -> dict:
        """Download files and folders (with their sub-folders) into local_dir.
        Never overwrites: an existing local file name gets " (2)"."""
        remotes = [str(p) for p in (remote_paths or []) if p]
        if not remotes:
            return _err("Nothing selected to download")
        if not local_dir or not os.path.isdir(local_dir):
            return _err("Choose an existing folder to download into")
        gate = self._office_ready() or self._require_plan()
        if gate:
            return gate
        local_dir = os.path.abspath(local_dir)
        job_id, cancel = self._shared_new_job(job_id)

        def run():
            from cloud_client import CloudError
            saved, errors = [], []
            try:
                client = self._cloud_client()
                todo = []   # (remote file path, local dest)
                for rp in remotes:
                    try:
                        items = client.shared_tree(rp)
                    except CloudError as exc:
                        errors.append({"path": rp, "error": self._cloud_err(exc)["error"]})
                        continue
                    if not items:
                        continue
                    top = items[0]["path"]
                    base_len = len(top.rpartition("/")[0])
                    for it in items:
                        rel = it["path"][base_len:].lstrip("/")
                        dest = os.path.join(local_dir, *_rel_parts(rel))
                        if it.get("is_dir"):
                            os.makedirs(dest, exist_ok=True)
                        else:
                            todo.append((it["path"], dest))
                for i, (rp, dest) in enumerate(todo):
                    if cancel.is_set():
                        break
                    last = [0.0]

                    def prog(b, t, _i=i, _f=rp):
                        now = time.time()
                        if now - last[0] < 0.2 and b < t:
                            return
                        last[0] = now
                        self._emit("shared_download_progress", {"job_id": job_id, "file": _f, "index": _i,
                                                                "total": len(todo), "bytes": b, "total_bytes": t})
                    try:
                        saved.append(client.shared_download(rp, dest, progress=prog, overwrite=False))
                    except CloudError as exc:
                        errors.append({"path": rp, "error": self._cloud_err(exc)["error"]})
                        if exc.status in (0, 401, 402):
                            break
                    except OSError as exc:
                        errors.append({"path": rp, "error": str(exc)})
                self._emit("shared_download_done", {"job_id": job_id, "ok": not errors and not cancel.is_set(),
                                                    "files": saved, "errors": errors[:100],
                                                    "local_dir": local_dir, "cancelled": cancel.is_set()})
            except Exception as exc:
                _log.exception("shared download")
                self._emit("shared_download_done", {"job_id": job_id, "ok": False, "files": saved,
                                                    "errors": errors[:100] + [{"path": "", "error": str(exc)}],
                                                    "local_dir": local_dir, "cancelled": cancel.is_set()})
            finally:
                self._shared_end_job(job_id)

        self._thread(run)
        return _ok(started=True, job_id=job_id)

    # ── open with the default app ────────────────────────────────────────────

    def _shared_cache_root(self) -> str:
        env = os.environ.get("SFM_SHARED_CACHE_DIR", "")
        if env:
            return env
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "OfficeAxe", "shared_cache")

    def _shared_launch(self, path: str) -> None:
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606 — open with the user's default app
        else:   # pragma: no cover (non-Windows dev machines)
            import subprocess
            import sys
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def shared_open(self, remote_path: str) -> dict:
        """Download a shared file into the local cache and open it with its default app.
        A cached copy that still matches the server is reused; a cached copy that
        differs (e.g. edited locally) is kept and the server version lands beside it."""
        def run(c):
            from cloud_client import sha256_file
            items = c.shared_tree(str(remote_path or ""))
            if not items or items[0].get("is_dir"):
                raise ValueError("Choose a file to open")
            item = items[0]
            office = (c.office_state() or {}).get("office") or {}
            dest = os.path.join(self._shared_cache_root(), str(office.get("id") or "office"),
                                *_rel_parts(item["path"]))
            local, cached = dest, False
            if os.path.isfile(dest) and sha256_file(dest) == item.get("sha256"):
                cached = True
            else:
                local = c.shared_download(item["path"], dest, overwrite=False)
            self._shared_launch(local)
            return {"local_path": local, "cached": cached}
        return self._office_call(run, paid=True)
