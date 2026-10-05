"""
app.py — PDF Axe account server.

Provides, for the desktop app:
  • Accounts      POST /auth/register, POST /auth/login, POST /auth/logout, GET /me
  • Subscription  POST /billing/checkout, POST /billing/portal, POST /billing/webhook
  • Cloud storage GET /files, POST /files, GET /files/{id}/download,
                  POST /files/{id}/trash, POST /files/{id}/restore, GET /usage
  • Live updates  GET /events  (Server-Sent Events, one stream per signed-in device)

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .billing import ACTIVE_STATUSES, BillingError, BillingProvider, make_provider
from .config import Settings
from .db import Database

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PBKDF2_ITERS = 200_000


# ── password + token helpers ─────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_remote_path(path: str) -> str:
    """Normalise a cloud path to 'a/b/c.pdf'; reject anything escaping the user's space."""
    p = (path or "").replace("\\", "/").strip().strip("/")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if not parts or any(s == ".." for s in parts) or ":" in parts[0]:
        raise HTTPException(400, "Invalid file path")
    if len("/".join(parts)) > 1000:
        raise HTTPException(400, "File path too long")
    return "/".join(parts)


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


# ── app factory ──────────────────────────────────────────────────────────────

def create_app(settings: Settings | None = None, billing: BillingProvider | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.db_path)
    os.makedirs(settings.storage_dir, exist_ok=True)
    billing = billing or make_provider(settings)
    hub = EventHub()

    app = FastAPI(title="PDF Axe account server")
    app.state.settings = settings
    app.state.db = db
    app.state.hub = hub
    app.state.billing = billing

    # ── auth dependencies ────────────────────────────────────────────────────

    def _user_from_token(token: str):
        if not token:
            raise HTTPException(401, "Not signed in")
        user = db.session_user(_token_hash(token))
        if not user:
            raise HTTPException(401, "Session expired, please sign in again")
        return user

    def current_user(authorization: str = Header(default="")):
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return _user_from_token(token)

    def subscriber(user=Depends(current_user)):
        if not _is_active(user):
            raise HTTPException(402, "An active subscription is required")
        return user

    def _is_active(user) -> bool:
        if user["subscription_status"] not in ACTIVE_STATUSES:
            return False
        end = float(user["current_period_end"] or 0)
        # A small grace window covers webhook delays at renewal time
        return end == 0 or end + 3 * 86400 > time.time()

    def _user_json(user) -> dict:
        return {
            "id": user["id"],
            "email": user["email"],
            "subscription": {
                "status": user["subscription_status"],
                "active": _is_active(user),
                "current_period_end": user["current_period_end"],
                "plan_label": settings.plan_label,
                "billing_available": billing.name != "none",
            },
        }

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

    # ── health ───────────────────────────────────────────────────────────────

    @app.get("/health")
    def health():
        return {"ok": True, "billing": billing.name}

    # ── accounts ─────────────────────────────────────────────────────────────

    @app.post("/auth/register")
    def register(body: Credentials):
        email = body.email.strip().lower()
        if not _EMAIL_RE.match(email):
            raise HTTPException(400, "Enter a valid email address")
        if len(body.password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")
        if db.user_by_email(email):
            raise HTTPException(409, "An account with this email already exists")
        uid = db.create_user(email, hash_password(body.password))
        token = _new_session(uid, body.device)
        return {"token": token, "user": _user_json(db.user_by_id(uid))}

    @app.post("/auth/login")
    def login(body: Credentials):
        user = db.user_by_email(body.email.strip().lower())
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Wrong email or password")
        token = _new_session(user["id"], body.device)
        return {"token": token, "user": _user_json(user)}

    @app.post("/auth/logout")
    def logout(authorization: str = Header(default="")):
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if token:
            db.end_session(_token_hash(token))
        return {"ok": True}

    @app.get("/me")
    def me(user=Depends(current_user)):
        return {"user": _user_json(user)}

    # ── billing ──────────────────────────────────────────────────────────────

    @app.post("/billing/checkout")
    def checkout(user=Depends(current_user)):
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
            upd = billing.parse_webhook(payload, sig)
        except BillingError as exc:
            raise HTTPException(400, str(exc))
        if upd is None:
            return {"ok": True, "ignored": True}
        user = db.user_by_id(upd.user_id) if upd.user_id else None
        if user is None and upd.customer_id:
            user = db.user_by_customer(upd.customer_id)
        if user is None:
            return {"ok": True, "ignored": True}
        if upd.customer_id and not user["stripe_customer_id"]:
            db.set_customer(user["id"], upd.customer_id)
        db.set_subscription(user["id"], upd.subscription_id, upd.status, upd.current_period_end)
        hub.publish(user["id"], "subscription_updated", _user_json(db.user_by_id(user["id"]))["subscription"])
        return {"ok": True}

    @app.get("/billing/success", response_class=HTMLResponse)
    def billing_success():
        return _page("Payment complete", "Your subscription is active. You can close this tab and return to PDF Axe.")

    @app.get("/billing/cancel", response_class=HTMLResponse)
    def billing_cancel():
        return _page("Checkout cancelled", "No payment was taken. You can close this tab and return to PDF Axe.")

    # ── cloud storage ────────────────────────────────────────────────────────

    @app.get("/usage")
    def usage(user=Depends(current_user)):
        return {"used": db.usage_bytes(user["id"]), "quota": settings.storage_quota_mb * 1024 * 1024}

    @app.get("/files")
    def list_files(trashed: bool = False, user=Depends(subscriber)):
        return {"files": [_file_json(r) for r in db.list_files(user["id"], trashed)]}

    @app.post("/files")
    def upload(path: str = Form(...), file: UploadFile = File(...), user=Depends(subscriber)):
        rel = clean_remote_path(path)
        user_dir = os.path.join(settings.storage_dir, str(user["id"]))
        os.makedirs(user_dir, exist_ok=True)
        max_bytes = settings.max_upload_mb * 1024 * 1024
        room = settings.storage_quota_mb * 1024 * 1024 - db.usage_bytes(user["id"])
        existing = db.file_by_path(user["id"], rel)
        if existing:
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
                    if size > max_bytes:
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
        blob = os.path.join(settings.storage_dir, str(user["id"]), row["blob"])
        if not os.path.isfile(blob):
            raise HTTPException(410, "File content is missing on the server")
        return FileResponse(blob, filename=os.path.basename(row["path"]),
                            media_type="application/octet-stream")

    @app.post("/files/{file_id}/trash")
    def trash(file_id: int, user=Depends(subscriber)):
        if not db.set_trashed(user["id"], file_id, True):
            raise HTTPException(404, "File not found")
        row = db.file_by_id(user["id"], file_id)
        hub.publish(user["id"], "file_trashed", _file_json(row))
        return {"file": _file_json(row)}

    @app.post("/files/{file_id}/restore")
    def restore(file_id: int, user=Depends(subscriber)):
        if not db.set_trashed(user["id"], file_id, False):
            raise HTTPException(404, "File not found")
        row = db.file_by_id(user["id"], file_id)
        hub.publish(user["id"], "file_updated", _file_json(row))
        return {"file": _file_json(row)}

    # ── live updates (SSE) ───────────────────────────────────────────────────

    @app.get("/events")
    async def events(request: Request, token: str = Query(default=""),
                     authorization: str = Header(default=""), heartbeat: float = 15.0):
        if not token and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        user = await asyncio.to_thread(_user_from_token, token)
        uid = user["id"]
        sub = hub.subscribe(uid)
        q = sub[1]

        async def stream():
            try:
                yield "event: hello\ndata: {}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=heartbeat)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
            finally:
                hub.unsubscribe(uid, sub)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def _page(title: str, text: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8><title>PDF Axe</title>"
        "<body style=\"font-family:system-ui;max-width:480px;margin:80px auto;text-align:center\">"
        f"<h2>{title}</h2><p>{text}</p></body>"
    )


app = create_app() if os.environ.get("SFM_SERVER_NO_AUTOAPP") != "1" else None
