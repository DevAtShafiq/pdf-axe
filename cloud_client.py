"""
cloud_client.py — Desktop-side client for the PDF Axe account server.

Standard library only (urllib), so it adds nothing to the PyInstaller build.

  CloudClient   accounts, devices, subscription links, cloud file operations
                (streamed uploads/downloads with progress callbacks)
  EventStream   background Server-Sent-Events listener (live updates); the
                session token travels in the Authorization header
  SyncFolder    two-way sync of a local folder with a cloud folder. It never
                deletes a local file: when a file is removed in the cloud, the
                local copy is moved into a `_to_review/` folder beside it.
  SyncWorker    runs SyncFolder on start, on local changes, on live pokes and on
                a timer; can be paused/resumed and backs off while offline
"""
from __future__ import annotations

import datetime
import hashlib
import json
import mimetypes
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Callable

Progress = Callable[[int, int], None]   # (bytes_done, bytes_total)


class CloudError(Exception):
    def __init__(self, message: str, status: int = 0, retry_after: float = 0.0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


class _MultipartReader:
    """File-like multipart body: streams head + file + tail without loading
    the file into memory, reporting progress as http.client reads it."""

    def __init__(self, head: bytes, path: str, tail: bytes, progress: Progress | None):
        self._parts = [head, None, tail]
        self._path = path
        self._fh = None
        self._idx = 0
        self._pos = 0
        self.file_size = os.path.getsize(path)
        self.total = len(head) + self.file_size + len(tail)
        self._sent = 0
        self._progress = progress

    def read(self, n: int = -1) -> bytes:
        out = b""
        while self._idx < 3 and (n < 0 or len(out) < n):
            want = -1 if n < 0 else n - len(out)
            if self._idx == 1:
                if self._fh is None:
                    self._fh = open(self._path, "rb")
                chunk = self._fh.read(want if want > 0 else 1024 * 1024)
                if not chunk:
                    self._fh.close()
                    self._idx += 1
                    continue
            else:
                part = self._parts[self._idx]
                chunk = part[self._pos:] if want < 0 else part[self._pos:self._pos + want]
                self._pos += len(chunk)
                if self._pos >= len(part):
                    self._idx += 1
                    self._pos = 0
                if not chunk:
                    continue
            out += chunk
        self._sent += len(out)
        if self._progress and out:
            try:
                self._progress(min(self._sent, self.total), self.total)
            except Exception:
                pass
        return out

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()


class CloudClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 60.0, device: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self.device = device or socket.gethostname() or "desktop"

    # ── low-level HTTP ───────────────────────────────────────────────────────

    def _open(self, method: str, path: str, body=None,
              headers: dict | None = None, timeout: float | None = None):
        if not self.base_url:
            raise CloudError("No server address is set")
        hdrs = {"Accept": "application/json"}
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        hdrs.update(headers or {})
        req = urllib.request.Request(self.base_url + path, data=body, method=method, headers=hdrs)
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("detail", "")
            except Exception:
                pass
            if isinstance(detail, list):   # FastAPI validation errors
                detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
            retry = 0.0
            try:
                retry = float(exc.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                pass
            raise CloudError(str(detail or exc.reason or f"HTTP {exc.code}"), exc.code, retry) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise CloudError(f"Cannot reach the server ({reason})") from None

    def _json(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        with self._open(method, path, body, headers, timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

    # ── accounts ─────────────────────────────────────────────────────────────

    def register(self, email: str, password: str, device: str = "") -> dict:
        r = self._json("POST", "/auth/register",
                       {"email": email, "password": password, "device": device or self.device})
        self.token = r["token"]
        return r

    def login(self, email: str, password: str, device: str = "") -> dict:
        r = self._json("POST", "/auth/login",
                       {"email": email, "password": password, "device": device or self.device})
        self.token = r["token"]
        return r

    def logout(self) -> None:
        try:
            if self.token:
                self._json("POST", "/auth/logout")
        finally:
            self.token = ""

    def me(self) -> dict:
        return self._json("GET", "/me")["user"]

    def health(self) -> dict:
        return self._json("GET", "/health")

    def sessions(self) -> list[dict]:
        return self._json("GET", "/auth/sessions")["sessions"]

    def revoke_session(self, session_id: int) -> None:
        self._json("POST", f"/auth/sessions/{int(session_id)}/revoke")

    # ── billing ──────────────────────────────────────────────────────────────

    def checkout_url(self) -> str:
        return self._json("POST", "/billing/checkout")["url"]

    def portal_url(self) -> str:
        return self._json("POST", "/billing/portal")["url"]

    # ── files ────────────────────────────────────────────────────────────────

    def usage(self) -> dict:
        return self._json("GET", "/usage")

    def list_files(self, trashed: bool = False) -> list[dict]:
        q = "?trashed=true" if trashed else ""
        return self._json("GET", "/files" + q)["files"]

    def upload(self, local_path: str, remote_path: str, base_sha256: str = "",
               progress: Progress | None = None) -> dict:
        boundary = uuid.uuid4().hex
        fname = os.path.basename(local_path)
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        head = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n"
            f"{remote_path}\r\n"
        )
        if base_sha256:
            head += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"base_sha256\"\r\n\r\n"
                     f"{base_sha256}\r\n")
        head += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{urllib.parse.quote(fname)}\"\r\nContent-Type: {ctype}\r\n\r\n"
        )
        body = _MultipartReader(head.encode("utf-8"), local_path,
                                f"\r\n--{boundary}--\r\n".encode(), progress)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
                   "Content-Length": str(body.total)}
        try:
            with self._open("POST", "/files", body, headers, timeout=max(self.timeout, 600)) as resp:
                return json.loads(resp.read().decode("utf-8"))["file"]
        finally:
            body.close()

    def download(self, file_id: int, dest_path: str, progress: Progress | None = None) -> str:
        """Download to dest_path via a temp file, so a failed transfer never
        leaves a half-written file in place."""
        d = os.path.dirname(dest_path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{dest_path}.{uuid.uuid4().hex[:8]}.part"
        with self._open("GET", f"/files/{int(file_id)}/download", timeout=max(self.timeout, 600)) as resp:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            done = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        try:
                            progress(done, total or done)
                        except Exception:
                            pass
        os.replace(tmp, dest_path)
        return dest_path

    def trash(self, file_id: int) -> dict:
        return self._json("POST", f"/files/{int(file_id)}/trash")["file"]

    def restore(self, file_id: int) -> dict:
        return self._json("POST", f"/files/{int(file_id)}/restore")["file"]

    def move(self, file_id: int, new_path: str) -> dict:
        return self._json("POST", f"/files/{int(file_id)}/move", {"path": new_path})["file"]

    def move_folder(self, path: str, new_path: str) -> int:
        return int(self._json("POST", "/folders/move", {"path": path, "new_path": new_path})["moved"])

    def trash_folder(self, path: str) -> int:
        return int(self._json("POST", "/folders/trash", {"path": path})["trashed"])


# ── live updates ─────────────────────────────────────────────────────────────

class EventStream:
    """Listens to GET /events in a daemon thread and calls on_event(name, data).

    Authenticates with the Authorization header (the token is never put in the
    URL, so it cannot end up in access logs). Reconnects with exponential
    backoff plus jitter until stop() is called. on_status(bool) reports whether
    the stream is currently connected. A revoked or expired session produces
    on_event("session_expired", {}) and ends the stream.
    """

    MAX_BACKOFF = 30.0

    def __init__(self, client: CloudClient, on_event: Callable[[str, dict], None],
                 on_status: Callable[[bool], None] | None = None, read_timeout: float = 60.0):
        self._client = client
        self._on_event = on_event
        self._on_status = on_status or (lambda _c: None)
        self._read_timeout = read_timeout
        self._stop = threading.Event()
        self._resp = None
        self.next_retry = 0.0   # seconds until the next reconnect attempt (0 = connected/now)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "EventStream":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            expired = False
            try:
                self._resp = self._client._open("GET", "/events",
                                                headers={"Accept": "text/event-stream"},
                                                timeout=self._read_timeout)
                self._on_status(True)
                backoff = 1.0
                expired = self._read(self._resp)
            except CloudError as exc:
                if exc.status == 401:
                    expired = True
            except Exception:
                pass
            finally:
                self._on_status(False)
            if expired:
                if not self._stop.is_set():
                    self._on_event("session_expired", {})
                self._stop.set()
                break
            delay = backoff * (0.75 + random.random() * 0.5)
            self.next_retry = delay
            if self._stop.wait(delay):
                break
            self.next_retry = 0.0
            backoff = min(backoff * 2, self.MAX_BACKOFF)

    def _read(self, resp) -> bool:
        """Dispatch events until the stream ends. True if the session expired."""
        event, data = "message", []
        for raw in resp:
            if self._stop.is_set():
                return False
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if event == "session_expired":
                    return True
                if data:
                    try:
                        payload = json.loads("\n".join(data))
                    except ValueError:
                        payload = {"raw": "\n".join(data)}
                    if event != "hello":
                        self._on_event(event, payload)
                event, data = "message", []
            elif line.startswith(":"):
                continue
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        return False


# ── folder sync ──────────────────────────────────────────────────────────────

_STATE_FILE = ".pdfaxe_sync.json"
REVIEW_DIR = "_to_review"
_SKIP_DIRS = {REVIEW_DIR, "__pycache__", ".git"}
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _skip_name(name: str) -> bool:
    return (name.startswith(".") or name.startswith("~$") or name.endswith(".part")
            or name.endswith(".tmp") or name == _STATE_FILE or name.lower() == "desktop.ini"
            or name.lower() == "thumbs.db")


def conflict_name(rel: str, device: str, when: float | None = None) -> str:
    """'docs/a.pdf' → 'docs/a (conflict LAPTOP 2026-10-05 1432).pdf'"""
    folder, _, name = rel.rpartition("/")
    stem, ext = os.path.splitext(name)
    dev = _UNSAFE.sub("-", device or "device").strip(" .") or "device"
    stamp = datetime.datetime.fromtimestamp(when or time.time()).strftime("%Y-%m-%d %H%M")
    new = f"{stem} (conflict {dev} {stamp}){ext}"
    return f"{folder}/{new}" if folder else new


def move_to_review(path: str) -> str:
    """Move a file into a `_to_review/` folder beside it (never deletes).
    Returns the new path."""
    parent, name = os.path.split(path)
    review = os.path.join(parent, REVIEW_DIR)
    os.makedirs(review, exist_ok=True)
    dest = os.path.join(review, name)
    stem, ext = os.path.splitext(name)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(review, f"{stem} ({n}){ext}")
        n += 1
    os.replace(path, dest)
    return dest


class SyncError(Exception):
    pass


class SyncFolder:
    """Two-way sync between local_dir and the cloud folder `remote_root`.

    Per relative path, with `base` = the hash both sides had at the last sync:
      local only, never synced      → upload
      local only, synced before     → removed in the cloud: the local copy is
                                      moved to `_to_review/` (or re-uploaded if
                                      it was edited locally since)
      cloud only, never synced      → download
      cloud only, synced before     → removed locally: the cloud copy goes to the
                                      (restorable) cloud trash, unless it changed
                                      in the cloud since, then it is downloaded
      both equal                    → nothing
      one side still equals base    → the other side's change is copied over
      both changed                  → conflict: keep both. The local edit is
                                      renamed "name (conflict <device> <date>).ext"
                                      and uploaded; the cloud version takes the
                                      original name locally.
    A local file is never deleted. A sudden large wave of local removals (for
    example an unplugged drive) is held back instead of trashing cloud files.
    """

    MASS_DELETE_MIN = 10        # hold back when more than this many…
    MASS_DELETE_RATIO = 0.5     # …and more than this share of synced files vanish locally

    def __init__(self, client: CloudClient, local_dir: str, remote_root: str = "",
                 log: Callable[[str], None] | None = None, device: str = "",
                 max_upload: int = 0):
        self.client = client
        self.local_dir = os.path.abspath(local_dir)
        self.remote_root = (remote_root or os.path.basename(self.local_dir.rstrip("\\/")) or "Sync").strip("/")
        self.log = log or (lambda _m: None)
        self.device = device or client.device
        self.max_upload = max_upload
        self._state_path = os.path.join(self.local_dir, _STATE_FILE)
        self._lock = threading.Lock()

    # ── state ────────────────────────────────────────────────────────────────

    def _load_state(self) -> tuple[dict, dict]:
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("remote_root") == self.remote_root:
                return data.get("files", {}), data.get("meta", {})
        except (OSError, ValueError):
            pass
        return {}, {}

    def _save_state(self, files: dict, meta: dict) -> None:
        tmp = self._state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"remote_root": self.remote_root, "files": files, "meta": meta}, fh, indent=1)
        os.replace(tmp, self._state_path)

    def _scan_local(self) -> dict[str, str]:
        out = {}
        for root, dirs, files in os.walk(self.local_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if _skip_name(name):
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, self.local_dir).replace(os.sep, "/")
                out[rel] = full
        return out

    def fingerprint(self) -> str:
        """Cheap summary of the local tree (names, sizes, mtimes) to spot changes."""
        h = hashlib.sha1()
        try:
            for rel, full in sorted(self._scan_local().items()):
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                h.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8", "replace"))
        except OSError:
            return ""
        return h.hexdigest()

    # ── run ──────────────────────────────────────────────────────────────────

    def run_once(self) -> dict:
        with self._lock:
            return self._run_once()

    def _run_once(self) -> dict:
        if not os.path.isdir(self.local_dir):
            raise SyncError(f"The sync folder is missing: {self.local_dir}")
        state, meta = self._load_state()
        prefix = self.remote_root + "/"
        if not self.max_upload:
            try:   # skip too-large files up front instead of streaming them to a 413
                self.max_upload = int(self.client.usage().get("max_upload") or 0)
            except CloudError:
                pass
        remote = {f["path"][len(prefix):]: f for f in self.client.list_files()
                  if f["path"].startswith(prefix)}
        local = self._scan_local()
        stats = {"uploaded": 0, "downloaded": 0, "conflicts": 0, "errors": 0,
                 "moved_to_review": 0, "trashed_remote": 0, "held_deletions": 0}

        def lsha(rel: str) -> str:
            full = local[rel]
            st = os.stat(full)
            m = meta.get(rel)
            if m and m[0] == st.st_size and m[1] == st.st_mtime_ns:
                return m[2]
            digest = sha256_file(full)
            meta[rel] = [st.st_size, st.st_mtime_ns, digest]
            return digest

        def up(rel: str, full: str, base_sha: str = "") -> None:
            size = os.path.getsize(full)
            if self.max_upload and size > self.max_upload:
                raise CloudError(f"too large to upload ({fmt_mb(size)}, limit {fmt_mb(self.max_upload)})", 413)
            info = self.client.upload(full, prefix + rel, base_sha256=base_sha)
            state[rel] = info["sha256"]
            stats["uploaded"] += 1
            self.log(f"↑ {rel}")

        def down(rel: str, f: dict) -> None:
            dest = os.path.join(self.local_dir, *rel.split("/"))
            self.client.download(f["id"], dest)
            state[rel] = f["sha256"]
            meta.pop(rel, None)
            stats["downloaded"] += 1
            self.log(f"↓ {rel}")

        # Local removals of synced, unchanged-in-cloud files → candidates for cloud trash
        gone_locally = [rel for rel, f in remote.items()
                        if rel not in local and state.get(rel) and state[rel] == f["sha256"]]
        hold = (len(gone_locally) > self.MASS_DELETE_MIN
                and len(gone_locally) > self.MASS_DELETE_RATIO * max(1, len(state)))
        if hold:
            stats["held_deletions"] = len(gone_locally)
            self.log(f"⚠ {len(gone_locally)} synced files are missing locally — "
                     "not removing them from the cloud. Restore them, or remove them in the Cloud panel.")

        for rel in sorted(set(local) | set(remote)):
            try:
                lf, rf = local.get(rel), remote.get(rel)
                base = state.get(rel)
                if lf and not rf:
                    if not base:
                        up(rel, lf)
                    elif lsha(rel) == base:
                        # Removed (or renamed) in the cloud: keep the local copy for review
                        dest = move_to_review(lf)
                        state.pop(rel, None)
                        meta.pop(rel, None)
                        stats["moved_to_review"] += 1
                        self.log(f"→ {rel} removed in the cloud; moved to {os.path.relpath(dest, self.local_dir)}")
                    else:
                        up(rel, lf)   # edited here after it was removed there: the edit wins
                elif rf and not lf:
                    if not base:
                        down(rel, rf)
                    elif rf["sha256"] != base:
                        down(rel, rf)   # changed in the cloud after it was removed here
                    elif hold:
                        continue
                    else:
                        self.client.trash(rf["id"])
                        state.pop(rel, None)
                        meta.pop(rel, None)
                        stats["trashed_remote"] += 1
                        self.log(f"🗑 {rel} removed here; moved to the cloud trash")
                else:
                    ls = lsha(rel)
                    if ls == rf["sha256"]:
                        state[rel] = ls
                    elif base == ls:
                        down(rel, rf)
                    elif base == rf["sha256"]:
                        up(rel, lf, base_sha=rf["sha256"])
                    else:
                        self._conflict(rel, lf, rf, state, meta, stats, up, down)
            except CloudError as exc:
                stats["errors"] += 1
                self.log(f"✗ {rel}: {exc}")
                if exc.status in (0, 401, 402, 507):
                    break
            except OSError as exc:
                stats["errors"] += 1
                self.log(f"✗ {rel}: {exc}")
        for rel in list(state):
            if rel not in local and rel not in remote:
                state.pop(rel, None)
        for rel in list(meta):
            if rel not in state:
                meta.pop(rel, None)
        self._save_state(state, meta)
        return stats

    def _conflict(self, rel, lf, rf, state, meta, stats, up, down) -> None:
        """Both sides changed: keep both. The local edit becomes a conflict copy."""
        new_rel = conflict_name(rel, self.device)
        n = 2
        while os.path.exists(os.path.join(self.local_dir, *new_rel.split("/"))):
            stem, ext = os.path.splitext(conflict_name(rel, self.device))
            new_rel = f"{stem[:-1]} {n}){ext}"
            n += 1
        new_full = os.path.join(self.local_dir, *new_rel.split("/"))
        os.replace(lf, new_full)          # rename only — the local edit is kept
        meta.pop(rel, None)
        stats["conflicts"] += 1
        self.log(f"⚡ {rel} changed on both sides; your copy kept as {new_rel}")
        up(new_rel, new_full)
        down(rel, rf)


class SyncWorker:
    """Runs SyncFolder.run_once on start, on demand (poke), when the local folder
    changes (checked every `local_check` seconds) and every `interval` seconds.

    on_state(state) gets "syncing", "idle", "paused", "offline" or "error".
    While offline it retries with backoff (5 s → 2 min) and a poke retries now.
    """

    def __init__(self, sync: SyncFolder, interval: float = 60.0,
                 on_done: Callable[[dict], None] | None = None,
                 on_state: Callable[[str], None] | None = None,
                 local_check: float = 10.0, paused: bool = False):
        self.sync = sync
        self.interval = interval
        self.local_check = local_check
        self.on_done = on_done or (lambda _s: None)
        self.on_state = on_state or (lambda _s: None)
        self.state = "paused" if paused else "idle"
        self._paused = threading.Event()
        if paused:
            self._paused.set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "SyncWorker":
        self._thread.start()
        return self

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def poke(self) -> None:
        self._wake.set()

    def pause(self) -> None:
        self._paused.set()
        self._set_state("paused")

    def resume(self) -> None:
        self._paused.clear()
        self._set_state("idle")
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _set_state(self, st: str) -> None:
        if st != self.state:
            self.state = st
            try:
                self.on_state(st)
            except Exception:
                pass

    def _run(self) -> None:
        offline_wait = 5.0
        while not self._stop.is_set():
            if self._paused.is_set():
                self._set_state("paused")
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._set_state("syncing")
            wait = self.interval
            try:
                stats = self.sync.run_once()
                offline = False
            except CloudError as exc:
                stats = {"error": str(exc), "status": exc.status}
                offline = exc.status == 0
            except Exception as exc:  # keep the worker alive across blips
                stats = {"error": str(exc)}
                offline = False
            if self._stop.is_set():
                break
            if offline:
                self._set_state("offline")
                wait = offline_wait
                offline_wait = min(offline_wait * 2, 120.0)
            else:
                offline_wait = 5.0
                self._set_state("error" if stats.get("error") or stats.get("errors") else "idle")
            self.on_done(stats)
            self._idle(wait)
            # Let a burst of change events settle before syncing again
            if not self._stop.is_set():
                self._stop.wait(1.0)

    def _idle(self, wait: float) -> None:
        """Sleep up to `wait` s; wake early on poke, resume or a local change."""
        fp = self.sync.fingerprint() if self.local_check else ""
        end = time.time() + wait
        while not self._stop.is_set():
            left = end - time.time()
            if left <= 0:
                return
            if self._wake.wait(min(left, self.local_check or left)):
                self._wake.clear()
                return
            if self.local_check and not self._paused.is_set() and self.sync.fingerprint() != fp:
                return
