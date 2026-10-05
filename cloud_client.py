"""
cloud_client.py — Desktop-side client for the PDF Axe account server.

Standard library only (urllib), so it adds nothing to the PyInstaller build.

  CloudClient   accounts, subscription links, cloud file list/upload/download
  EventStream   background Server-Sent-Events listener (live updates)
  SyncFolder    keeps a local folder and a cloud folder in step, additively:
                it never removes a file on either side.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Callable


class CloudError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class CloudClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 60.0):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    # ── low-level HTTP ───────────────────────────────────────────────────────

    def _open(self, method: str, path: str, body: bytes | None = None,
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
            raise CloudError(str(detail or exc.reason or f"HTTP {exc.code}"), exc.code) from None
        except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise CloudError(f"Cannot reach the server ({reason})") from None

    def _json(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        with self._open(method, path, body, headers) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

    # ── accounts ─────────────────────────────────────────────────────────────

    def register(self, email: str, password: str, device: str = "") -> dict:
        r = self._json("POST", "/auth/register", {"email": email, "password": password, "device": device})
        self.token = r["token"]
        return r

    def login(self, email: str, password: str, device: str = "") -> dict:
        r = self._json("POST", "/auth/login", {"email": email, "password": password, "device": device})
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

    def upload(self, local_path: str, remote_path: str) -> dict:
        boundary = uuid.uuid4().hex
        fname = os.path.basename(local_path)
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        with open(local_path, "rb") as fh:
            content = fh.read()
        head = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n"
            f"{remote_path}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{urllib.parse.quote(fname)}\"\r\nContent-Type: {ctype}\r\n\r\n"
        ).encode("utf-8")
        body = head + content + f"\r\n--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        with self._open("POST", "/files", body, headers, timeout=max(self.timeout, 600)) as resp:
            return json.loads(resp.read().decode("utf-8"))["file"]

    def download(self, file_id: int, dest_path: str) -> str:
        """Download to dest_path via a temp file, so a failed transfer never
        leaves a half-written file in place."""
        d = os.path.dirname(dest_path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{dest_path}.{uuid.uuid4().hex[:8]}.part"
        with self._open("GET", f"/files/{int(file_id)}/download", timeout=max(self.timeout, 600)) as resp:
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        os.replace(tmp, dest_path)
        return dest_path

    def trash(self, file_id: int) -> dict:
        return self._json("POST", f"/files/{int(file_id)}/trash")["file"]

    def restore(self, file_id: int) -> dict:
        return self._json("POST", f"/files/{int(file_id)}/restore")["file"]


# ── live updates ─────────────────────────────────────────────────────────────

class EventStream:
    """Listens to GET /events in a daemon thread and calls on_event(name, data).

    Reconnects with backoff until stop() is called. on_status(bool) reports
    whether the stream is currently connected.
    """

    def __init__(self, client: CloudClient, on_event: Callable[[str, dict], None],
                 on_status: Callable[[bool], None] | None = None):
        self._client = client
        self._on_event = on_event
        self._on_status = on_status or (lambda _c: None)
        self._stop = threading.Event()
        self._resp = None
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
            try:
                q = "?token=" + urllib.parse.quote(self._client.token)
                self._resp = self._client._open("GET", "/events" + q,
                                                headers={"Accept": "text/event-stream"}, timeout=60)
                self._on_status(True)
                backoff = 1.0
                self._read(self._resp)
            except CloudError as exc:
                if exc.status == 401:
                    self._on_event("session_expired", {})
                    break
            except Exception:
                pass
            finally:
                self._on_status(False)
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)

    def _read(self, resp) -> None:
        event, data = "message", []
        for raw in resp:
            if self._stop.is_set():
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
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


# ── folder sync ──────────────────────────────────────────────────────────────

_STATE_FILE = ".pdfaxe_sync.json"
_SKIP_DIRS = {"_to_review", "__pycache__", ".git"}


def _skip_name(name: str) -> bool:
    return (name.startswith(".") or name.startswith("~$") or name.endswith(".part")
            or name.endswith(".tmp") or name == _STATE_FILE or name.lower() == "desktop.ini"
            or name.lower() == "thumbs.db")


class SyncFolder:
    """Two-way, additive sync between local_dir and the cloud folder `remote_root`.

    Per relative path, with `base` = the hash both sides had at the last sync:
      local only   → upload (unless it was synced before and then trashed in the cloud)
      cloud only   → download (unless it was synced before and then removed locally)
      both, equal  → nothing
      both differ  → whichever side still equals `base` is stale and gets updated;
                     if both changed, the cloud copy is saved next to the local
                     file as "name (cloud copy).ext" and the local file is uploaded.
    Nothing is ever removed on either side.
    """

    def __init__(self, client: CloudClient, local_dir: str, remote_root: str = "",
                 log: Callable[[str], None] | None = None):
        self.client = client
        self.local_dir = os.path.abspath(local_dir)
        self.remote_root = (remote_root or os.path.basename(self.local_dir.rstrip("\\/")) or "Sync").strip("/")
        self.log = log or (lambda _m: None)
        self._state_path = os.path.join(self.local_dir, _STATE_FILE)
        self._lock = threading.Lock()

    def _load_state(self) -> dict:
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("remote_root") == self.remote_root:
                return data.get("files", {})
        except (OSError, ValueError):
            pass
        return {}

    def _save_state(self, files: dict) -> None:
        tmp = self._state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"remote_root": self.remote_root, "files": files}, fh, indent=1)
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

    def run_once(self) -> dict:
        with self._lock:
            return self._run_once()

    def _run_once(self) -> dict:
        os.makedirs(self.local_dir, exist_ok=True)
        state = self._load_state()
        prefix = self.remote_root + "/"
        remote_all = self.client.list_files()
        remote = {f["path"][len(prefix):]: f for f in remote_all if f["path"].startswith(prefix)}
        trashed = {f["path"][len(prefix):] for f in self.client.list_files(trashed=True)
                   if f["path"].startswith(prefix)}
        local = self._scan_local()
        stats = {"uploaded": 0, "downloaded": 0, "conflicts": 0, "errors": 0}

        def up(rel: str, full: str) -> None:
            info = self.client.upload(full, prefix + rel)
            state[rel] = info["sha256"]
            stats["uploaded"] += 1
            self.log(f"↑ {rel}")

        def down(rel: str, f: dict, dest: str | None = None) -> None:
            dest = dest or os.path.join(self.local_dir, *rel.split("/"))
            self.client.download(f["id"], dest)
            stats["downloaded"] += 1
            self.log(f"↓ {rel}")

        for rel in sorted(set(local) | set(remote)):
            try:
                lf, rf = local.get(rel), remote.get(rel)
                base = state.get(rel)
                if lf and not rf:
                    if base and rel in trashed:
                        continue  # removed in the cloud on purpose; leave local alone
                    up(rel, lf)
                elif rf and not lf:
                    if base:
                        continue  # removed locally on purpose; leave cloud alone
                    down(rel, rf)
                    state[rel] = rf["sha256"]
                else:
                    lsha = sha256_file(lf)
                    if lsha == rf["sha256"]:
                        state[rel] = lsha
                    elif base == lsha:
                        down(rel, rf)
                        state[rel] = rf["sha256"]
                    elif base == rf["sha256"]:
                        up(rel, lf)
                    else:
                        stem, ext = os.path.splitext(lf)
                        copy = f"{stem} (cloud copy){ext}"
                        n = 2
                        while os.path.exists(copy):
                            copy = f"{stem} (cloud copy {n}){ext}"
                            n += 1
                        down(rel, rf, copy)
                        up(rel, lf)
                        stats["conflicts"] += 1
            except CloudError as exc:
                stats["errors"] += 1
                self.log(f"✗ {rel}: {exc}")
                if exc.status in (401, 402, 507):
                    break
            except OSError as exc:
                stats["errors"] += 1
                self.log(f"✗ {rel}: {exc}")
        self._save_state(state)
        return stats


class SyncWorker:
    """Runs SyncFolder.run_once on start, on demand (poke) and every `interval` seconds."""

    def __init__(self, sync: SyncFolder, interval: float = 60.0,
                 on_done: Callable[[dict], None] | None = None):
        self.sync = sync
        self.interval = interval
        self.on_done = on_done or (lambda _s: None)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "SyncWorker":
        self._thread.start()
        return self

    def poke(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                stats = self.sync.run_once()
            except Exception as exc:  # keep the worker alive across network blips
                stats = {"error": str(exc)}
            self.on_done(stats)
            self._wake.wait(self.interval)
            self._wake.clear()
            # Let a burst of change events settle before syncing again
            if not self._stop.is_set():
                time.sleep(1.0)
