"""
cloud_bridge.py — ACCOUNT / SUBSCRIPTION / CLOUD methods of the JS bridge.

SFMBridge (sfm_bridge.py) inherits CloudBridgeMixin, so every public method
here is exposed to JS as window.pywebview.api.<name>. It talks to the Office Axe
account server (server/app.py) through cloud_client.py.

The session token is never returned to JS. With "keep me signed in" it is
stored in sfm_settings.json as "cloud_token_enc", encrypted with Windows DPAPI
(secure_store.py); otherwise it only lives in memory for this run.

Events pushed to JS:
  account_changed        {reason, subscription?}   sign-in state / plan changed
  cloud_event            {event, data}             every live event from /events
  cloud_live             {connected}               live stream connected or not
  cloud_upload_progress  {index, total, name, bytes, bytes_total, overall_bytes, overall_total}
  cloud_upload_done      {ok, uploaded, failed, skipped, total, errors, cancelled}
  cloud_download_progress{index, total, name, bytes, bytes_total}
  cloud_download_done    {ok, downloaded, failed, errors, paths, dest_dir}
  cloud_sync_state       {state, folder}           syncing / idle / paused / offline / error
  cloud_sync_done        {folder, uploaded, downloaded, conflicts, moved_to_review, …, time}
  cloud_sync_log         {log}
  cloud_files_dropped    {paths}                   files dropped from Explorer onto the Cloud panel
"""
from __future__ import annotations

import logging
import os
import platform
import re
import threading
import time

_log = logging.getLogger("sfm")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
_NEED_PLAN = "An active subscription is required"


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str, **kw) -> dict:
    return {"ok": False, "error": str(msg), **kw}


def _fmt_size(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class CloudBridgeMixin:
    _cloud_lock = threading.RLock()
    _cloud_stream = None          # cloud_client.EventStream
    _cloud_stream_up = False
    _cloud_sync_worker = None     # cloud_client.SyncWorker
    _cloud_sync_folder = ""
    _cloud_sync_last = None
    _cloud_user_mem = None        # last user dict seen from the server
    _cloud_mem_token = ""         # token for this run (always set while signed in)
    _cloud_max_upload = 0
    _cloud_drop_installed = False
    _cloud_upload_cancel = None   # threading.Event of the running upload batch

    # ── settings / token ─────────────────────────────────────────────────────

    def _cloud_server_url(self) -> str:
        url = self._load_settings().get("server_url") or os.environ.get("SFM_SERVER_URL", "")
        return str(url or "").strip().rstrip("/")

    def _cloud_token(self) -> str:
        if self._cloud_mem_token:
            return self._cloud_mem_token
        from secure_store import protect, unprotect
        s = self._load_settings()
        enc = str(s.get("cloud_token_enc") or "")
        if enc:
            tok = unprotect(enc)
            if tok:
                self._cloud_mem_token = tok
            return tok
        legacy = str(s.get("cloud_token") or "")
        if legacy:   # written by an older version in plain text → encrypt it now
            try:
                self._save_settings({"cloud_token_enc": protect(legacy), "cloud_token": ""})
            except Exception as exc:
                _log.warning("cloud: could not migrate token: %s", exc)
            self._cloud_mem_token = legacy
        return legacy

    def _cloud_set_token(self, token: str, remember: bool = True) -> None:
        from secure_store import protect
        self._cloud_mem_token = token or ""
        self._save_settings({"cloud_token_enc": protect(token) if (token and remember) else "",
                             "cloud_token": ""})

    @property
    def _cloud_user_cache(self):
        if self._cloud_user_mem is None:
            u = self._load_settings().get("cloud_last_user")
            if isinstance(u, dict) and u:
                self._cloud_user_mem = u
        return self._cloud_user_mem

    @_cloud_user_cache.setter
    def _cloud_user_cache(self, user) -> None:
        self._cloud_user_mem = user
        try:
            keep = {"email": user.get("email"), "subscription": user.get("subscription")} if user else {}
            self._save_settings({"cloud_last_user": keep})
        except Exception:
            pass

    def _cloud_client(self, timeout: float = 20.0):
        from cloud_client import CloudClient
        return CloudClient(self._cloud_server_url(), self._cloud_token(), timeout=timeout,
                           device=platform.node() or "desktop")

    def _cloud_err(self, exc, **kw) -> dict:
        status = getattr(exc, "status", 0) or 0
        msg = str(exc)
        extra = {"status": status}
        if status == 402:
            extra["need_subscription"] = True
        elif status == 401:
            extra["need_login"] = True
        elif status == 429:
            extra["retry_after"] = getattr(exc, "retry_after", 0)
        elif status == 0:
            extra["offline"] = True
            url = self._cloud_server_url()
            msg = (f"Can't reach the server{' at ' + url if url else ''}. "
                   "Check your internet connection or the server address.")
        elif status >= 500:
            msg = f"The server had a problem ({msg}). Please try again in a moment."
        extra.update(kw)
        return _err(msg, **extra)

    def _cloud_clear_session(self, reason: str = "signed_out") -> None:
        self._cloud_stop_services()
        self._cloud_user_mem = None
        self._cloud_mem_token = ""
        try:
            self._save_settings({"cloud_token_enc": "", "cloud_token": "", "cloud_last_user": {}})
        except Exception as exc:
            _log.error("cloud: could not clear token: %s", exc)
        self._emit("account_changed", {"reason": reason})

    @staticmethod
    def _cloud_is_active(user, offline: bool = False) -> bool:
        try:
            sub = (user or {}).get("subscription") or {}
            if not sub.get("active"):
                return False
            if offline and not sub.get("free_plan"):
                # Trust the last known state only until it would have lapsed
                now = time.time()
                end = float(sub.get("current_period_end") or 0)
                grace = float(sub.get("grace_until") or 0)
                if sub.get("status") == "past_due":
                    return grace > now
                return end == 0 or end + 3 * 86400 > now
            return True
        except Exception:
            return False

    # ── live events ──────────────────────────────────────────────────────────

    def _cloud_on_event(self, name: str, data) -> None:
        self._emit("cloud_event", {"event": name, "data": data})
        if name == "subscription_updated":
            if isinstance(data, dict):
                base = self._cloud_user_cache if isinstance(self._cloud_user_cache, dict) else {}
                self._cloud_user_cache = {**base, "subscription": data}
            self._emit("account_changed", {"reason": "subscription_updated", "subscription": data})
            if isinstance(data, dict) and data.get("active"):
                self._thread(self._cloud_resume_sync)
            else:
                self._cloud_stop_sync_worker()
        elif name in ("file_updated", "file_trashed", "file_moved", "folder_moved", "folder_trashed"):
            w = self._cloud_sync_worker
            if w is not None:
                w.poke()
        elif name == "session_expired":
            self._thread(self._cloud_clear_session, "session_expired")

    def _cloud_on_status(self, connected: bool) -> None:
        if bool(connected) != self._cloud_stream_up:
            self._cloud_stream_up = bool(connected)
            self._emit("cloud_live", {"connected": bool(connected)})
            w = self._cloud_sync_worker
            if connected and w is not None:
                w.poke()   # catch up on anything missed while disconnected

    def _cloud_start_stream(self) -> None:
        from cloud_client import EventStream
        with self._cloud_lock:
            s = self._cloud_stream
            if s is not None and not s.stopped:
                return
            client = self._cloud_client()
            if not client.base_url or not client.token:
                return
            self._cloud_stream = EventStream(client, self._cloud_on_event, self._cloud_on_status).start()
            _log.info("cloud: event stream started")

    def _cloud_stop_services(self) -> None:
        with self._cloud_lock:
            s, self._cloud_stream = self._cloud_stream, None
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass
        self._cloud_on_status(False)
        self._cloud_stop_sync_worker()

    def _cloud_ensure_services(self, user=None) -> None:
        """Start the live stream, and resume folder sync when the plan is active."""
        self._cloud_start_stream()
        if self._cloud_is_active(user if user is not None else self._cloud_user_cache):
            self._cloud_resume_sync()

    # ── folder sync helpers ──────────────────────────────────────────────────

    def _cloud_resume_sync(self) -> None:
        folder = str(self._load_settings().get("cloud_sync_folder") or "")
        if folder and os.path.isdir(folder) and self._cloud_token():
            self._cloud_start_sync_worker(folder)

    def _cloud_start_sync_worker(self, folder: str) -> None:
        from cloud_client import SyncFolder, SyncWorker
        folder = os.path.abspath(folder)
        with self._cloud_lock:
            w = self._cloud_sync_worker
            if w is not None and self._cloud_sync_folder == folder:
                return
            if w is not None:
                w.stop()

            def _done(stats, _folder=folder):
                self._cloud_sync_last = {**stats, "time": time.time()}
                self._emit("cloud_sync_done", {"folder": _folder, **self._cloud_sync_last})

            def _state(state, _folder=folder):
                self._emit("cloud_sync_state", {"state": state, "folder": _folder})

            paused = bool(self._load_settings().get("cloud_sync_paused"))
            client = self._cloud_client()
            sync = SyncFolder(client, folder, device=client.device, max_upload=self._cloud_max_upload,
                              log=lambda m: self._emit("cloud_sync_log", {"log": str(m)}))
            self._cloud_sync_folder = folder
            self._cloud_sync_last = None
            self._cloud_sync_worker = SyncWorker(sync, interval=60.0, on_done=_done,
                                                 on_state=_state, paused=paused).start()
            _log.info("cloud: sync worker started for %s (paused=%s)", folder, paused)

    def _cloud_stop_sync_worker(self) -> None:
        with self._cloud_lock:
            w, self._cloud_sync_worker = self._cloud_sync_worker, None
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass

    def _require_plan(self):
        """Gate for paid features (cloud, AI suit). None when allowed, else an _err dict.

        With no account server configured the app runs unrestricted. With one
        configured, the signed-in user's subscription must be active. When the
        server cannot be reached, the last known plan state is trusted until
        the paid period (plus grace) would have ended.
        """
        if not self._cloud_server_url():
            return None
        if not self._cloud_token():
            return _err(_NEED_PLAN, need_subscription=True, need_login=True)
        offline = False
        try:
            user = self._cloud_client(timeout=8).me()
            self._cloud_user_cache = user
        except Exception as exc:
            if getattr(exc, "status", None) == 401:
                self._cloud_clear_session("session_expired")
                return _err(_NEED_PLAN, need_subscription=True, need_login=True)
            user = self._cloud_user_cache
            offline = True
        if self._cloud_is_active(user, offline=offline):
            return None
        return _err(_NEED_PLAN, need_subscription=True)

    # ── account ──────────────────────────────────────────────────────────────

    def account_get_state(self) -> dict:
        try:
            url = self._cloud_server_url()
            st = {"configured": bool(url), "server_url": url, "logged_in": False,
                  "user": None, "offline": False, "live": self._cloud_stream_up,
                  "remembered": bool(self._load_settings().get("cloud_token_enc"))}
            if not url:
                return _ok(**st)
            from cloud_client import CloudError
            if not self._cloud_token():
                try:   # for the sign-in screen: reachability + plan price
                    h = self._cloud_client(timeout=4).health()
                    st["server"] = {k: h.get(k) for k in ("billing", "plan_label", "free_plan", "max_upload")}
                    self._cloud_max_upload = int(h.get("max_upload") or 0)
                except CloudError:
                    st["offline"] = True
                return _ok(**st)
            try:
                user = self._cloud_client(timeout=8).me()
            except CloudError as exc:
                if exc.status == 401:
                    self._cloud_clear_session("session_expired")
                    return _ok(**st)
                if exc.status == 0:
                    st.update(logged_in=True, offline=True, user=self._cloud_user_cache)
                    self._cloud_start_stream()   # reconnects on its own when back
                    if self._cloud_is_active(self._cloud_user_cache, offline=True):
                        self._cloud_resume_sync()
                    return _ok(**st)
                return self._cloud_err(exc, **st)
            self._cloud_user_cache = user
            st.update(logged_in=True, user=user)
            self._cloud_ensure_services(user)
            st["live"] = self._cloud_stream_up
            return _ok(**st)
        except Exception as exc:
            return _err(str(exc))

    def account_set_server(self, url: str) -> dict:
        try:
            url = str(url or "").strip().rstrip("/")
            if url and "://" not in url:
                host = url.split("/")[0].split(":")[0].lower()
                local = host in ("localhost", "127.0.0.1") or host.startswith(("192.168.", "10."))
                url = ("http://" if local else "https://") + url
            if url and not url.lower().startswith(("http://", "https://")):
                return _err("Server address must start with http:// or https://")
            if url != self._cloud_server_url():
                # A token belongs to one server: sign out of the old one locally
                had_token = bool(self._cloud_token())
                self._cloud_stop_services()
                self._cloud_user_mem = None
                self._cloud_mem_token = ""
                self._save_settings({"server_url": url, "cloud_token": "", "cloud_token_enc": "",
                                     "cloud_last_user": {}})
                if had_token:
                    self._emit("account_changed", {"reason": "server_changed"})
            reachable = False
            if url:
                try:
                    self._cloud_client(timeout=5).health()
                    reachable = True
                except Exception:
                    reachable = False
            return _ok(server_url=url, configured=bool(url), reachable=reachable)
        except Exception as exc:
            return _err(str(exc))

    def _account_auth(self, kind: str, email: str, password: str, remember: bool = True) -> dict:
        from cloud_client import CloudError
        email = str(email or "").strip()
        password = str(password or "")
        if not self._cloud_server_url():
            return _err("Set the server address first", field="server")
        if not email or not password:
            return _err("Enter your email and password", field="email" if not email else "password")
        if not _EMAIL_RE.match(email):
            return _err("Enter a valid email address", field="email")
        if kind == "register" and len(password) < MIN_PASSWORD:
            return _err(f"Password must be at least {MIN_PASSWORD} characters", field="password")
        try:
            client = self._cloud_client()
            client.token = ""
            fn = client.register if kind == "register" else client.login
            r = fn(email, password)
        except CloudError as exc:
            if exc.status == 401 and kind == "login":
                return _err("Wrong email or password", status=401, field="password")
            if exc.status == 409:
                return _err(str(exc), status=409, field="email")
            return self._cloud_err(exc)
        self._cloud_stop_services()
        self._cloud_set_token(client.token, remember=bool(remember))
        user = r.get("user")
        self._cloud_user_cache = user
        self._cloud_ensure_services(user)
        self._emit("account_changed", {"reason": kind})
        return _ok(user=user)

    def account_register(self, email: str, password: str, remember: bool = True) -> dict:
        try:
            return self._account_auth("register", email, password, remember)
        except Exception as exc:
            return _err(str(exc))

    def account_login(self, email: str, password: str, remember: bool = True) -> dict:
        try:
            return self._account_auth("login", email, password, remember)
        except Exception as exc:
            return _err(str(exc))

    def account_logout(self) -> dict:
        try:
            self._cloud_stop_services()
            try:
                self._cloud_client(timeout=8).logout()   # revokes the session on the server
            except Exception as exc:   # still sign out locally when offline
                _log.info("cloud: server logout failed: %s", exc)
            self._cloud_clear_session("signed_out")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def account_list_devices(self) -> dict:
        from cloud_client import CloudError
        try:
            return _ok(devices=self._cloud_client(timeout=10).sessions())
        except CloudError as exc:
            return self._cloud_err(exc)
        except Exception as exc:
            return _err(str(exc))

    def account_revoke_device(self, session_id) -> dict:
        """Sign out another device (or this one) by its session id."""
        from cloud_client import CloudError
        try:
            client = self._cloud_client(timeout=10)
            current = any(d.get("current") and int(d.get("id")) == int(session_id)
                          for d in client.sessions())
            if current:
                return self.account_logout()
            client.revoke_session(int(session_id))
            return _ok()
        except CloudError as exc:
            return self._cloud_err(exc)
        except Exception as exc:
            return _err(str(exc))

    # ── billing ──────────────────────────────────────────────────────────────

    def _billing_open(self, which: str) -> dict:
        from cloud_client import CloudError
        try:
            c = self._cloud_client()
            url = c.checkout_url() if which == "checkout" else c.portal_url()
        except CloudError as exc:
            return self._cloud_err(exc)
        import webbrowser
        webbrowser.open_new_tab(url)
        return _ok(url=url)

    def billing_open_checkout(self) -> dict:
        try:
            return self._billing_open("checkout")
        except Exception as exc:
            return _err(str(exc))

    def billing_open_portal(self) -> dict:
        try:
            return self._billing_open("portal")
        except Exception as exc:
            return _err(str(exc))

    # ── cloud files ──────────────────────────────────────────────────────────

    def cloud_list(self, trashed: bool = False) -> dict:
        from cloud_client import CloudError
        try:
            files = self._cloud_client().list_files(trashed=bool(trashed))
            for f in files:
                f["size_str"] = _fmt_size(int(f.get("size") or 0))
            return _ok(files=files, trashed=bool(trashed))
        except CloudError as exc:
            return self._cloud_err(exc)
        except Exception as exc:
            return _err(str(exc))

    def cloud_usage(self) -> dict:
        from cloud_client import CloudError
        try:
            u = self._cloud_client().usage()
            used, quota = int(u.get("used") or 0), int(u.get("quota") or 0)
            self._cloud_max_upload = int(u.get("max_upload") or 0)
            return _ok(used=used, quota=quota, used_str=_fmt_size(used), quota_str=_fmt_size(quota),
                       max_upload=self._cloud_max_upload, max_upload_str=_fmt_size(self._cloud_max_upload))
        except CloudError as exc:
            return self._cloud_err(exc)
        except Exception as exc:
            return _err(str(exc))

    def cloud_pick_files(self) -> dict:
        """Native multi-select open dialog (all file types) for cloud upload."""
        try:
            import webview
            win = self._window
            if win is None:
                return _err("Window not ready")
            result = win.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                            file_types=("All files (*.*)",))
            if result:
                return _ok(paths=[os.path.normpath(p) for p in result])
            return _ok(paths=[], cancelled=True)
        except Exception as exc:
            return _err(str(exc))

    def cloud_enable_drop(self) -> dict:
        """Let files dragged from Explorer onto the Cloud panel be uploaded.

        pywebview only reveals full paths of dropped files to a Python-side
        DOM drop handler; it pushes them to JS as `cloud_files_dropped`."""
        try:
            if self._cloud_drop_installed:
                return _ok(installed=True)
            win = self._window
            if win is None:
                return _err("Window not ready")
            from webview.dom import DOMEventHandler

            def _on_drop(e):
                try:
                    files = (e.get("dataTransfer") or {}).get("files") or []
                    paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
                    if paths:
                        self._emit("cloud_files_dropped", {"paths": paths})
                except Exception as exc:
                    _log.error("cloud drop: %s", exc)

            el = win.dom.get_element("#panel-cloud")
            if el is None:
                return _err("Cloud panel not found")
            el.events.drop += DOMEventHandler(_on_drop, True, True)
            self._cloud_drop_installed = True
            return _ok(installed=True)
        except Exception as exc:
            return _err(str(exc))

    def _cloud_fetch_max_upload(self, client) -> int:
        if not self._cloud_max_upload:
            try:
                self._cloud_max_upload = int(client.usage().get("max_upload") or 0)
            except Exception:
                pass
        return self._cloud_max_upload

    def cloud_upload(self, paths: list, remote_dir: str = "") -> dict:
        from cloud_client import CloudError
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return _err("Nothing selected to upload")
        base = "/".join(s for s in str(remote_dir or "").replace("\\", "/").split("/") if s and s != ".")
        cancel = threading.Event()
        self._cloud_upload_cancel = cancel

        def _jobs():
            for p in paths:
                p = os.path.abspath(p)
                if os.path.isfile(p):
                    yield p, os.path.basename(p)
                elif os.path.isdir(p):
                    top = os.path.basename(p.rstrip("\\/"))
                    for root, dirs, files in os.walk(p):
                        dirs[:] = [d for d in dirs
                                   if not d.startswith(".") and d not in ("_to_review", "__pycache__")]
                        for name in files:
                            if name.startswith((".", "~$")) or name.endswith((".part", ".tmp")):
                                continue
                            full = os.path.join(root, name)
                            rel = os.path.relpath(full, p).replace(os.sep, "/")
                            yield full, f"{top}/{rel}"

        def _run():
            uploaded, errors, skipped, total = 0, [], 0, 0
            try:
                jobs = list(_jobs())
                total = len(jobs)
                sizes = {}
                for full, _rel in jobs:
                    try:
                        sizes[full] = os.path.getsize(full)
                    except OSError:
                        sizes[full] = 0
                overall_total = sum(sizes.values())
                overall_done = 0
                client = self._cloud_client()
                limit = self._cloud_fetch_max_upload(client)
                for i, (full, rel) in enumerate(jobs):
                    if cancel.is_set():
                        break
                    remote = f"{base}/{rel}" if base else rel
                    size = sizes.get(full, 0)
                    if limit and size > limit:
                        skipped += 1
                        errors.append({"path": full, "error":
                                       f"{os.path.basename(full)} is {_fmt_size(size)}; "
                                       f"the upload limit is {_fmt_size(limit)}"})
                        overall_done += size
                        continue
                    last = [0.0]

                    def _prog(done, tot, _i=i, _rel=rel, _base=overall_done, _size=size):
                        now = time.time()
                        if now - last[0] < 0.2 and done < tot:
                            return
                        last[0] = now
                        self._emit("cloud_upload_progress", {
                            "index": _i, "total": total, "name": _rel,
                            "bytes": min(done, _size), "bytes_total": _size,
                            "overall_bytes": _base + min(done, _size), "overall_total": overall_total})

                    _prog(0, 1)
                    try:
                        client.upload(full, remote, progress=_prog)
                        uploaded += 1
                    except CloudError as exc:
                        errors.append({"path": full, "error": self._cloud_err(exc)["error"]})
                        if exc.status in (0, 401, 402, 507):
                            break
                    except OSError as exc:
                        errors.append({"path": full, "error": str(exc)})
                    overall_done += size
                self._emit("cloud_upload_done", {"ok": not errors and not cancel.is_set(),
                                                 "uploaded": uploaded, "failed": len(errors),
                                                 "skipped": skipped, "total": total,
                                                 "errors": errors[:50], "cancelled": cancel.is_set()})
            except Exception as exc:
                self._emit("cloud_upload_done", {"ok": False, "uploaded": uploaded,
                                                 "failed": len(errors) + 1, "total": total,
                                                 "errors": errors[:50], "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def cloud_upload_cancel(self) -> dict:
        """Stop after the file currently being uploaded."""
        ev = self._cloud_upload_cancel
        if ev is not None:
            ev.set()
        return _ok()

    @staticmethod
    def _cloud_unique_path(path: str) -> str:
        """Never overwrite: 'a.pdf' → 'a (2).pdf', 'a (3).pdf', …"""
        if not os.path.exists(path):
            return path
        stem, ext = os.path.splitext(path)
        n = 2
        while os.path.exists(f"{stem} ({n}){ext}"):
            n += 1
        return f"{stem} ({n}){ext}"

    def cloud_download(self, file_ids: list, dest_dir: str, strip_prefix: str = "") -> dict:
        """Download files into dest_dir. With strip_prefix (e.g. "Docs/" when
        downloading the folder Docs/Sub), sub-folders below it are recreated."""
        from cloud_client import CloudError
        ids = []
        for i in (file_ids or []):
            try:
                ids.append(int(i))
            except (TypeError, ValueError):
                pass
        if not ids:
            return _err("Nothing selected to download")
        if not dest_dir or not os.path.isdir(dest_dir):
            return _err("Choose an existing folder to download into")
        strip = str(strip_prefix or "").replace("\\", "/")

        def _target(path: str) -> str:
            p = path.replace("\\", "/")
            rel = p[len(strip):] if strip and p.startswith(strip) else p.split("/")[-1]
            parts = [s for s in rel.split("/") if s and s not in (".", "..")]
            return os.path.join(dest_dir, *parts) if parts else os.path.join(dest_dir, "file")

        def _run():
            done, errors, saved = 0, [], []
            try:
                client = self._cloud_client()
                by_id = {f["id"]: f for f in client.list_files()}
                for idx, fid in enumerate(ids):
                    f = by_id.get(fid)
                    if not f:
                        errors.append({"id": fid, "error": "File not found in the cloud"})
                        continue
                    dest = self._cloud_unique_path(_target(f["path"]))
                    last = [0.0]

                    def _prog(b, tot, _i=idx, _name=f["path"]):
                        now = time.time()
                        if now - last[0] < 0.2 and b < tot:
                            return
                        last[0] = now
                        self._emit("cloud_download_progress", {"index": _i, "total": len(ids),
                                                               "name": _name, "bytes": b, "bytes_total": tot})
                    try:
                        client.download(fid, dest, progress=_prog)
                        done += 1
                        saved.append(dest)
                    except CloudError as exc:
                        errors.append({"id": fid, "path": f["path"], "error": self._cloud_err(exc)["error"]})
                        if exc.status in (0, 401, 402):
                            break
                    except OSError as exc:
                        errors.append({"id": fid, "path": f["path"], "error": str(exc)})
                self._emit("cloud_download_done", {"ok": not errors, "downloaded": done,
                                                   "failed": len(errors), "errors": errors[:50],
                                                   "paths": saved, "dest_dir": dest_dir})
            except Exception as exc:
                self._emit("cloud_download_done", {"ok": False, "downloaded": done,
                                                   "failed": len(errors) + 1, "errors": errors[:50],
                                                   "paths": saved, "dest_dir": dest_dir,
                                                   "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def _cloud_each(self, ids: list, op: str) -> dict:
        from cloud_client import CloudError
        try:
            client = self._cloud_client()
            fn = client.trash if op == "trash" else client.restore
            done, errors = 0, []
            for i in (ids or []):
                try:
                    fn(int(i))
                    done += 1
                except CloudError as exc:
                    errors.append({"id": i, "error": self._cloud_err(exc)["error"]})
                    if exc.status in (0, 401, 402):
                        break
                except (TypeError, ValueError):
                    errors.append({"id": i, "error": "Invalid file id"})
            if errors and not done:
                return _err(errors[0]["error"], done=0, errors=errors)
            return _ok(done=done, errors=errors)
        except Exception as exc:
            return _err(str(exc))

    def cloud_trash(self, ids: list) -> dict:
        """Move cloud files to the cloud trash (restorable; nothing is erased)."""
        return self._cloud_each(ids, "trash")

    def cloud_restore(self, ids: list) -> dict:
        return self._cloud_each(ids, "restore")

    def _cloud_call(self, fn, *args) -> dict:
        from cloud_client import CloudError
        try:
            return _ok(result=fn(self._cloud_client(), *args))
        except CloudError as exc:
            return self._cloud_err(exc)
        except Exception as exc:
            return _err(str(exc))

    def cloud_move(self, file_id, new_path: str) -> dict:
        """Rename or move one cloud file to new_path (e.g. 'Docs/new name.pdf')."""
        return self._cloud_call(lambda c, i, p: c.move(int(i), str(p)), file_id, new_path)

    def cloud_move_folder(self, path: str, new_path: str) -> dict:
        return self._cloud_call(lambda c, a, b: c.move_folder(str(a), str(b)), path, new_path)

    def cloud_trash_folder(self, path: str) -> dict:
        return self._cloud_call(lambda c, a: c.trash_folder(str(a)), path)

    # ── auto-sync folder ─────────────────────────────────────────────────────

    def cloud_sync_start(self, local_folder: str) -> dict:
        try:
            folder = os.path.abspath(str(local_folder).strip()) if local_folder else ""
            if not folder or not os.path.isdir(folder):
                return _err("Choose an existing folder to sync")
            if not self._cloud_token():
                return _err("Sign in first", need_login=True)
            gate = self._require_plan()
            if gate:
                return gate
            self._save_settings({"cloud_sync_folder": folder, "cloud_sync_paused": False})
            self._cloud_start_sync_worker(folder)
            self._cloud_start_stream()
            w = self._cloud_sync_worker
            return _ok(folder=folder, remote_root=w.sync.remote_root if w else "")
        except Exception as exc:
            return _err(str(exc))

    def cloud_sync_stop(self) -> dict:
        try:
            self._cloud_stop_sync_worker()
            self._save_settings({"cloud_sync_folder": "", "cloud_sync_paused": False})
            self._emit("cloud_sync_state", {"state": "stopped", "folder": ""})
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def cloud_sync_pause(self) -> dict:
        try:
            self._save_settings({"cloud_sync_paused": True})
            w = self._cloud_sync_worker
            if w is not None:
                w.pause()
            return _ok(paused=True)
        except Exception as exc:
            return _err(str(exc))

    def cloud_sync_resume(self) -> dict:
        try:
            self._save_settings({"cloud_sync_paused": False})
            w = self._cloud_sync_worker
            if w is not None:
                w.resume()
            else:
                self._cloud_resume_sync()
            return _ok(paused=False)
        except Exception as exc:
            return _err(str(exc))

    def cloud_sync_now(self) -> dict:
        w = self._cloud_sync_worker
        if w is None:
            return _err("Auto-sync is not running")
        w.poke()
        return _ok()

    def cloud_sync_status(self) -> dict:
        try:
            w = self._cloud_sync_worker
            s = self._load_settings()
            folder = str(s.get("cloud_sync_folder") or "")
            return _ok(running=w is not None,
                       paused=bool(w.paused) if w else bool(s.get("cloud_sync_paused")),
                       state=(w.state if w else ("stopped" if not folder else "idle")),
                       folder=(self._cloud_sync_folder if w else folder),
                       remote_root=(w.sync.remote_root if w else ""),
                       last=self._cloud_sync_last, live=self._cloud_stream_up)
        except Exception as exc:
            return _err(str(exc))
