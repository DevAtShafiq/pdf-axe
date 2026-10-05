"""
app.py — Office Axe account server.

Provides, for the desktop app:
  • Accounts      POST /auth/register, POST /auth/login, POST /auth/logout, GET /me,
                  GET /auth/sessions, POST /auth/sessions/{id}/revoke
  • Subscription  POST /billing/checkout, POST /billing/portal, POST /billing/webhook
  • Cloud storage GET /files, POST /files, GET /files/{id}/download,
                  POST /files/{id}/move, POST /files/{id}/trash, POST /files/{id}/restore,
                  POST /folders/move, POST /folders/trash, GET /usage
  • Live updates  GET /events  (Server-Sent Events, one stream per signed-in device;
                  the session token goes in the Authorization header, never the URL)

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from collections import deque

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .billing import ACTIVE_STATUSES, BillingError, BillingProvider, make_provider
from .config import Settings
from .db import Database

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PBKDF2_ITERS = 200_000
MIN_PASSWORD = 8
MAX_PASSWORD = 256
# Statuses that mean "this user already has a Stripe subscription"
_HAS_SUBSCRIPTION = ACTIVE_STATUSES | {"past_due", "unpaid", "incomplete"}


# ── password + token helpers ─────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk, bytes.fromhex(dk_hex))
    except Exception:
        return False


# Verified against when the email is unknown, so a wrong email costs the same
# time as a wrong password and response timing does not reveal accounts.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bearer(authorization: str) -> str:
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


_BAD_CHARS = set('<>:"|?*')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def clean_remote_path(path: str) -> str:
    """Normalise a cloud path to 'a/b/c.pdf'; reject anything escaping the user's
    space or that could not be saved as a file name on Windows."""
    p = (path or "").replace("\\", "/").strip().strip("/")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if not parts:
        raise HTTPException(400, "Invalid file path")
    for s in parts:
        if s == "..":
            raise HTTPException(400, "Invalid file path")
        if any(c in _BAD_CHARS or ord(c) < 32 for c in s):
            raise HTTPException(400, 'Names cannot contain < > : " | ? * or control characters')
        if s != s.rstrip(" ."):
            raise HTTPException(400, "Names cannot end with a space or a dot")
        if s.split(".")[0].lower() in _RESERVED:
            raise HTTPException(400, f'"{s}" is a reserved name on Windows')
        if len(s) > 255:
            raise HTTPException(400, "File name too long")
    joined = "/".join(parts)
    if len(joined) > 1000:
        raise HTTPException(400, "File path too long")
    return joined


# ── sign-in throttling ───────────────────────────────────────────────────────

class RateLimiter:
    """Sliding-window counter per key (in-process; fine for one instance)."""

    def __init__(self):
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, window: float, now: float) -> deque:
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - window:
            q.popleft()
        return q

    def retry_after(self, key: str, limit: int, window: float) -> float:
        """Seconds to wait before another attempt is allowed (0 = allowed now)."""
        now = time.time()
        with self._lock:
            q = self._prune(key, window, now)
            if limit > 0 and len(q) >= limit:
                return max(1.0, q[0] + window - now)
            return 0.0

    def hit(self, key: str, window: float) -> None:
        now = time.time()
        with self._lock:
            self._prune(key, window, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def _too_many(wait: float, what: str = "sign-in attempts") -> HTTPException:
    mins = max(1, math.ceil(wait / 60))
    return HTTPException(
        429, f"Too many {what}. Try again in {mins} minute{'s' if mins != 1 else ''}.",
        headers={"Retry-After": str(int(math.ceil(wait)))},
    )


# ── live update hub ──────────────────────────────────────────────────────────

class EventHub:
    """Fan-out of per-user events to every open /events stream.

    publish() may be called from worker threads (sync routes run in a
    threadpool), so each subscriber remembers its own event loop.
    """

    def __init__(self):
        self._subs: dict[int, set] = {}
        self._lock = threading.Lock()

    def subscribe(self, user_id: int):
        sub = (asyncio.get_running_loop(), asyncio.Queue(maxsize=200))
        with self._lock:
            self._subs.setdefault(user_id, set()).add(sub)
        return sub

    def unsubscribe(self, user_id: int, sub) -> None:
        with self._lock:
            self._subs.get(user_id, set()).discard(sub)

    def publish(self, user_id: int, event: str, data: dict) -> None:
        msg = {"event": event, "data": data, "ts": time.time()}
        with self._lock:
            subs = list(self._subs.get(user_id, ()))
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put_drop_oldest, q, msg)
            except RuntimeError:  # loop closed
                pass

    def subscriber_count(self, user_id: int) -> int:
        with self._lock:
            return len(self._subs.get(user_id, ()))


def _put_drop_oldest(q: asyncio.Queue, msg: dict) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(msg)


# ── request models ───────────────────────────────────────────────────────────

class Credentials(BaseModel):
    email: str
    password: str
    device: str = ""


class MoveBody(BaseModel):
    path: str


class FolderMoveBody(BaseModel):
    path: str
    new_path: str


class FolderBody(BaseModel):
    path: str


# ── app factory ──────────────────────────────────────────────────────────────

def create_app(settings: Settings | None = None, billing: BillingProvider | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.db_path)
    os.makedirs(settings.storage_dir, exist_ok=True)
    billing = billing or make_provider(settings)
    hub = EventHub()
    limiter = RateLimiter()
    login_window = settings.login_window_minutes * 60

    app = FastAPI(title="Office Axe account server")
    app.state.settings = settings
    app.state.db = db
    app.state.hub = hub
    app.state.billing = billing
    app.state.limiter = limiter

    max_upload_bytes = settings.max_upload_mb * 1024 * 1024

    @app.middleware("http")
    async def _reject_oversized_uploads(request: Request, call_next):
        # Refuse a too-large upload before its body is read and spooled to disk
        if request.method == "POST" and request.url.path == "/files":
            try:
                length = int(request.headers.get("content-length") or 0)
            except ValueError:
                length = 0
            if length > max_upload_bytes + 64 * 1024:   # allow for multipart framing
                return JSONResponse({"detail": f"File is larger than {settings.max_upload_mb} MB"},
                                    status_code=413)
        return await call_next(request)

    # ── auth dependencies ────────────────────────────────────────────────────

    def _client_ip(request: Request) -> str:
        # uvicorn --proxy-headers already resolves X-Forwarded-For from trusted proxies
        return (request.client.host if request.client else "") or "unknown"

    def _session_from_token(token: str):
        if not token:
            raise HTTPException(401, "Not signed in")
        th = _token_hash(token)
        user = db.session_user(th)
        if not user:
            raise HTTPException(401, "Session expired, please sign in again")
        db.touch_session(th)
        return user, th

    def current_session(authorization: str = Header(default="")):
        return _session_from_token(_bearer(authorization))

    def current_user(sess=Depends(current_session)):
        return sess[0]

    def subscriber(user=Depends(current_user)):
        if not _is_active(user):
            raise HTTPException(402, "An active subscription is required")
        return user

    def _grace_until(user) -> float:
        if user["subscription_status"] != "past_due":
            return 0.0
        since = float(user["past_due_since"] or 0) or time.time()
        return since + settings.past_due_grace_days * 86400

    def _is_active(user) -> bool:
        if settings.free_plan:
            return True
        status = user["subscription_status"]
        if status == "past_due":
            return _grace_until(user) > time.time()
        if status not in ACTIVE_STATUSES:
            return False
        end = float(user["current_period_end"] or 0)
        # A small grace window covers webhook delays at renewal time
        return end == 0 or end + 3 * 86400 > time.time()

    def _sub_json(user) -> dict:
        return {
            "status": user["subscription_status"],
            "active": _is_active(user),
            "current_period_end": user["current_period_end"],
            "grace_until": _grace_until(user),
            "plan_label": settings.plan_label,
            "billing_available": billing.name != "none",
            "free_plan": bool(settings.free_plan),
        }

    def _user_json(user) -> dict:
        return {"id": user["id"], "email": user["email"], "subscription": _sub_json(user)}

    def _file_json(row) -> dict:
        return {
            "id": row["id"], "path": row["path"], "size": row["size"],
            "sha256": row["sha256"], "updated_at": row["updated_at"],
            "trashed": bool(row["trashed"]),
        }

    def _new_session(user_id: int, device: str) -> str:
        token = secrets.token_urlsafe(32)
        db.create_session(_token_hash(token), user_id, settings.session_days * 86400, device)
        return token

    def _quota_bytes() -> int:
        return settings.storage_quota_mb * 1024 * 1024

    # ── health ───────────────────────────────────────────────────────────────

    @app.get("/health")
    def health():
        return {"ok": True, "billing": billing.name, "plan_label": settings.plan_label,
                "free_plan": bool(settings.free_plan), "max_upload": max_upload_bytes,
                "min_password": MIN_PASSWORD}

    # ── accounts ─────────────────────────────────────────────────────────────

    @app.post("/auth/register")
    def register(body: Credentials, request: Request):
        ip_key = f"register-ip:{_client_ip(request)}"
        wait = limiter.retry_after(ip_key, settings.register_per_hour, 3600)
        if wait:
            raise _too_many(wait, "new accounts from this network")
        email = body.email.strip().lower()
        if len(email) > 254 or not _EMAIL_RE.match(email):
            raise HTTPException(400, "Enter a valid email address")
        if len(body.password) < MIN_PASSWORD:
            raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters")
        if len(body.password) > MAX_PASSWORD:
            raise HTTPException(400, f"Password must be at most {MAX_PASSWORD} characters")
        if db.user_by_email(email):
            raise HTTPException(409, "An account with this email already exists")
        limiter.hit(ip_key, 3600)
        uid = db.create_user(email, hash_password(body.password))
        token = _new_session(uid, body.device)
        return {"token": token, "user": _user_json(db.user_by_id(uid))}

    @app.post("/auth/login")
    def login(body: Credentials, request: Request):
        email = body.email.strip().lower()
        keys = ((f"login-email:{email}", settings.login_max_attempts),
                (f"login-ip:{_client_ip(request)}", settings.login_ip_max_attempts))
        for key, limit in keys:
            wait = limiter.retry_after(key, limit, login_window)
            if wait:
                raise _too_many(wait)
        user = db.user_by_email(email)
        pw = body.password if len(body.password) <= MAX_PASSWORD else ""
        ok = verify_password(pw, user["password_hash"] if user else _DUMMY_HASH)
        if not user or not ok or not pw:
            for key, _limit in keys:
                limiter.hit(key, login_window)
            raise HTTPException(401, "Wrong email or password")
        limiter.clear(keys[0][0])
        token = _new_session(user["id"], body.device)
        return {"token": token, "user": _user_json(user)}

    @app.post("/auth/logout")
    def logout(authorization: str = Header(default="")):
        token = _bearer(authorization)
        if token:
            th = _token_hash(token)
            user = db.session_user(th)
            db.end_session(th)
            if user:
                hub.publish(user["id"], "session_revoked", {})
        return {"ok": True}

    @app.get("/auth/sessions")
    def sessions(sess=Depends(current_session)):
        user, th = sess
        return {"sessions": [{
            "id": r["id"], "device": r["device"], "created_at": r["created_at"],
            "last_seen": r["last_seen"], "expires_at": r["expires_at"],
            "current": hmac.compare_digest(r["token_hash"], th),
        } for r in db.list_sessions(user["id"])]}

    @app.post("/auth/sessions/{session_id}/revoke")
    def revoke_session(session_id: int, user=Depends(current_user)):
        if not db.revoke_session(user["id"], session_id):
            raise HTTPException(404, "Session not found")
        hub.publish(user["id"], "session_revoked", {"id": session_id})
        return {"ok": True}

    @app.get("/me")
    def me(user=Depends(current_user)):
        return {"user": _user_json(user)}

    # ── billing ──────────────────────────────────────────────────────────────

    @app.post("/billing/checkout")
    def checkout(user=Depends(current_user)):
        if settings.free_plan:
            raise HTTPException(400, "This server includes every feature for free")
        if user["subscription_id"] and user["subscription_status"] in _HAS_SUBSCRIPTION:
            raise HTTPException(409, "You already have a subscription. Use Manage billing to change it.")
        try:
            url, customer_id = billing.create_checkout(user["id"], user["email"], user["stripe_customer_id"])
        except BillingError as exc:
            raise HTTPException(503, str(exc))
        if customer_id and customer_id != user["stripe_customer_id"]:
            db.set_customer(user["id"], customer_id)
        return {"url": url}

    @app.post("/billing/portal")
    def portal(user=Depends(current_user)):
        if not user["stripe_customer_id"]:
            raise HTTPException(400, "No subscription to manage yet")
        try:
            return {"url": billing.create_portal(user["stripe_customer_id"])}
        except BillingError as exc:
            raise HTTPException(503, str(exc))

    @app.post("/billing/webhook")
    async def webhook(request: Request):
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")
        try:
            upd = await asyncio.to_thread(billing.parse_webhook, payload, sig)
        except BillingError as exc:
            raise HTTPException(400, str(exc))
        if upd is None:
            return {"ok": True, "ignored": True}
        return await asyncio.to_thread(_apply_update, upd)

    def _apply_update(upd) -> dict:
        if upd.event_id and db.webhook_seen(upd.event_id):
            return {"ok": True, "duplicate": True}
        user = db.user_by_id(upd.user_id) if upd.user_id else None
        if user is None and upd.customer_id:
            user = db.user_by_customer(upd.customer_id)
        if user is None:
            if upd.event_id:
                db.record_webhook_event(upd.event_id, upd.status)
            return {"ok": True, "ignored": True}
        if upd.event_created and float(user["sub_event_ts"] or 0) > upd.event_created:
            if upd.event_id:
                db.record_webhook_event(upd.event_id, upd.status)
            return {"ok": True, "stale": True}   # an older event arrived late
        if upd.customer_id and not user["stripe_customer_id"]:
            db.set_customer(user["id"], upd.customer_id)
        if upd.status == "past_due":
            already = user["subscription_status"] == "past_due" and float(user["past_due_since"] or 0)
            past_due_since = float(user["past_due_since"]) if already else time.time()
        else:
            past_due_since = 0.0
        db.set_subscription(
            user["id"],
            upd.subscription_id or user["subscription_id"] or "",
            upd.status,
            upd.current_period_end or float(user["current_period_end"] or 0),
            past_due_since=past_due_since,
            event_ts=upd.event_created or None,
        )
        if upd.event_id:
            db.record_webhook_event(upd.event_id, upd.status)
        hub.publish(user["id"], "subscription_updated", _sub_json(db.user_by_id(user["id"])))
        return {"ok": True}

    @app.get("/billing/success", response_class=HTMLResponse)
    def billing_success():
        return _page("Payment complete", "Your subscription is active. You can close this tab and return to Office Axe.")

    @app.get("/billing/cancel", response_class=HTMLResponse)
    def billing_cancel():
        return _page("Checkout cancelled", "No payment was taken. You can close this tab and return to Office Axe.")

    # ── cloud storage ────────────────────────────────────────────────────────

    @app.get("/usage")
    def usage(user=Depends(current_user)):
        return {"used": db.usage_bytes(user["id"]), "quota": _quota_bytes(), "max_upload": max_upload_bytes}

    @app.get("/files")
    def list_files(trashed: bool = False, user=Depends(subscriber)):
        return {"files": [_file_json(r) for r in db.list_files(user["id"], trashed)]}

    @app.post("/files")
    def upload(path: str = Form(...), file: UploadFile = File(...), base_sha256: str = Form(default=""),
               user=Depends(subscriber)):
        rel = clean_remote_path(path)
        user_dir = os.path.join(settings.storage_dir, str(int(user["id"])))
        os.makedirs(user_dir, exist_ok=True)
        existing = db.file_by_path(user["id"], rel)
        if base_sha256 and existing and not existing["trashed"] and existing["sha256"] != base_sha256:
            raise HTTPException(409, "The file changed in the cloud since you last synced it")
        room = _quota_bytes() - db.usage_bytes(user["id"])
        if existing and not existing["trashed"]:
            room += existing["size"]  # replacing a file frees its old size

        h = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(dir=user_dir, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_upload_bytes:
                        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
                    if size > room:
                        raise HTTPException(507, "Cloud storage is full")
                    h.update(chunk)
                    out.write(chunk)
            blob = uuid.uuid4().hex
            os.replace(tmp, os.path.join(user_dir, blob))
        except BaseException:
            # Keep the partial upload aside rather than removing it
            if os.path.exists(tmp):
                os.replace(tmp, tmp + ".failed")
            raise
        fid = db.upsert_file(user["id"], rel, blob, size, h.hexdigest())
        row = db.file_by_id(user["id"], fid)
        hub.publish(user["id"], "file_updated", _file_json(row))
        return {"file": _file_json(row)}

    @app.get("/files/{file_id}/download")
    def download(file_id: int, user=Depends(subscriber)):
        row = db.file_by_id(user["id"], file_id)
        if not row:
            raise HTTPException(404, "File not found")
        user_dir = os.path.realpath(os.path.join(settings.storage_dir, str(int(user["id"]))))
        blob = os.path.realpath(os.path.join(user_dir, row["blob"]))
        if os.path.dirname(blob) != user_dir:
            raise HTTPException(404, "File not found")
        if not os.path.isfile(blob):
            raise HTTPException(410, "File content is missing on the server")
        return FileResponse(blob, filename=os.path.basename(row["path"]),
                            media_type="application/octet-stream")

    @app.post("/files/{file_id}/move")
    def move(file_id: int, body: MoveBody, user=Depends(subscriber)):
        row = db.file_by_id(user["id"], file_id)
        if not row:
            raise HTTPException(404, "File not found")
        new = clean_remote_path(body.path)
        if new == row["path"]:
            return {"file": _file_json(row)}
        clash = db.file_by_path(user["id"], new)
        if clash:
            raise HTTPException(409, "A file with that name already exists"
                                + (" in the cloud trash" if clash["trashed"] else ""))
        if not db.move_file(user["id"], file_id, new):
            raise HTTPException(409, "A file with that name already exists")
        moved = db.file_by_id(user["id"], file_id)
        hub.publish(user["id"], "file_moved", {**_file_json(moved), "old_path": row["path"]})
        return {"file": _file_json(moved)}

    @app.post("/files/{file_id}/trash")
    def trash(file_id: int, user=Depends(subscriber)):
        if not db.set_trashed(user["id"], file_id, True):
            raise HTTPException(404, "File not found")
        row = db.file_by_id(user["id"], file_id)
        hub.publish(user["id"], "file_trashed", _file_json(row))
        return {"file": _file_json(row)}

    @app.post("/files/{file_id}/restore")
    def restore(file_id: int, user=Depends(subscriber)):
        row = db.file_by_id(user["id"], file_id)
        if not row:
            raise HTTPException(404, "File not found")
        if row["trashed"] and db.usage_bytes(user["id"]) + row["size"] > _quota_bytes():
            raise HTTPException(507, "Cloud storage is full")
        db.set_trashed(user["id"], file_id, False)
        row = db.file_by_id(user["id"], file_id)
        hub.publish(user["id"], "file_updated", _file_json(row))
        return {"file": _file_json(row)}

    @app.post("/folders/move")
    def move_folder(body: FolderMoveBody, user=Depends(subscriber)):
        old, new = clean_remote_path(body.path), clean_remote_path(body.new_path)
        if new == old:
            return {"moved": 0}
        if new.startswith(old + "/"):
            raise HTTPException(400, "A folder cannot be moved into itself")
        rows = db.files_under(user["id"], old, trashed=None)
        if not rows:
            raise HTTPException(404, "Folder not found")
        moving = {r["id"] for r in rows}
        moves = []
        for r in rows:
            target = new + r["path"][len(old):]
            clash = db.file_by_path(user["id"], target)
            if clash and clash["id"] not in moving:
                raise HTTPException(409, f"{target} already exists")
            moves.append((r["id"], target))
        if not db.move_many(user["id"], moves):
            raise HTTPException(409, "Some names in the destination are already taken")
        hub.publish(user["id"], "folder_moved", {"path": old, "new_path": new, "count": len(moves)})
        return {"moved": len(moves)}

    @app.post("/folders/trash")
    def trash_folder(body: FolderBody, user=Depends(subscriber)):
        folder = clean_remote_path(body.path)
        rows = db.files_under(user["id"], folder, trashed=False)
        for r in rows:
            db.set_trashed(user["id"], r["id"], True)
        if rows:
            hub.publish(user["id"], "folder_trashed", {"path": folder, "count": len(rows)})
        return {"trashed": len(rows)}

    # ── live updates (SSE) ───────────────────────────────────────────────────

    @app.get("/events")
    async def events(request: Request, authorization: str = Header(default=""),
                     token: str = Query(default=""), heartbeat: float = 15.0):
        if token:
            # Tokens in URLs end up in access logs and proxies' logs
            raise HTTPException(400, "Send the session token in the Authorization header, not the URL")
        user, th = await asyncio.to_thread(_session_from_token, _bearer(authorization))
        uid = user["id"]
        heartbeat = min(max(float(heartbeat), 0.05), 60.0)
        sub = hub.subscribe(uid)
        q = sub[1]

        async def still_valid() -> bool:
            return await asyncio.to_thread(db.session_valid, th)

        async def stream():
            try:
                yield "event: hello\ndata: {}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=heartbeat)
                    except asyncio.TimeoutError:
                        if not await still_valid():
                            yield "event: session_expired\ndata: {}\n\n"
                            break
                        yield ": ping\n\n"
                        continue
                    if msg["event"] == "session_revoked" and not await still_valid():
                        yield "event: session_expired\ndata: {}\n\n"
                        break
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
            finally:
                hub.unsubscribe(uid, sub)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def _page(title: str, text: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8><title>Office Axe</title>"
        "<body style=\"font-family:system-ui;max-width:480px;margin:80px auto;text-align:center\">"
        f"<h2>{title}</h2><p>{text}</p></body>"
    )


app = create_app() if os.environ.get("SFM_SERVER_NO_AUTOAPP") != "1" else None
