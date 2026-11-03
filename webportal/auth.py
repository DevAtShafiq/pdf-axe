# -*- coding: utf-8 -*-
"""
auth.py — the single admin login: password hash, signed session cookie,
CSRF tokens and a login rate limit.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

ITERATIONS = 240_000
SESSION_HOURS = 12
MAX_FAILURES = 5            # per IP …
FAILURE_WINDOW = 15 * 60    # … in 15 minutes
MIN_PASSWORD = 10


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


def password_problem(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password.lower() in ("password123", "officeaxe123", "1234567890", "admin12345"):
        return "That password is too easy to guess."
    return ""


class AdminAccount:
    """The admin login {"user", "password_hash", "changed_at", "version"}.

    Kept in admin.json in the data folder, or — on the server — in the
    store's "admin" record (store.get_meta / set_meta). On a fresh server the
    first password comes from OFFICEAXE_ADMIN_PASSWORD_HASH (a hash made with
    ``manage.py hash-password``, kept in Secret Manager).
    """

    def __init__(self, path: str, user: str, store=None):
        self.path, self.user, self.store = path, user, store

    def _read(self) -> dict:
        if self.store is not None:
            doc = self.store.get_meta("admin")
            if doc:
                return doc
            boot = os.environ.get("OFFICEAXE_ADMIN_PASSWORD_HASH", "").strip()
            return {"user": self.user, "password_hash": boot, "version": "env"} if boot else {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, ValueError):
            return {}

    def is_set(self) -> bool:
        return bool(self._read().get("password_hash"))

    def set_password(self, password: str) -> None:
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        doc = {"user": self.user, "password_hash": hash_password(password), "changed_at": int(time.time()),
               "version": secrets.token_hex(8)}
        if self.store is not None:
            self.store.set_meta("admin", doc)
            return
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.replace(tmp, self.path)

    def verify(self, user: str, password: str) -> bool:
        doc = self._read()
        ok_user = hmac.compare_digest(str(user or "").strip().lower(), str(doc.get("user") or self.user).lower())
        ok_pw = check_password(password or "", doc.get("password_hash", ""))
        return bool(doc) and ok_user and ok_pw

    def version(self) -> str:
        """Changes with every new password, so old sessions stop working."""
        return str(self._read().get("version") or "")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Sessions:
    """Stateless signed cookie: user | expiry | nonce | password-version."""
    COOKIE = "oa_admin"

    def __init__(self, secret: bytes, account: AdminAccount):
        self.secret, self.account = secret, account

    def _mac(self, body: bytes) -> bytes:
        return hmac.new(self.secret, body, hashlib.sha256).digest()

    def create(self, user: str) -> str:
        body = json.dumps({"u": user, "exp": int(time.time()) + SESSION_HOURS * 3600,
                           "n": secrets.token_hex(12), "pv": self.account.version()},
                          separators=(",", ":")).encode()
        return _b64(body) + "." + _b64(self._mac(body))

    def read(self, cookie: str) -> Optional[dict]:
        try:
            b, m = cookie.split(".")
            body = _unb64(b)
            if not hmac.compare_digest(self._mac(body), _unb64(m)):
                return None
            data = json.loads(body)
        except (ValueError, AttributeError):
            return None
        if data.get("exp", 0) < time.time():
            return None
        if data.get("pv") != self.account.version():     # password changed → log out everywhere
            return None
        return data

    def csrf(self, session: dict) -> str:
        return hmac.new(self.secret, b"csrf:" + session["n"].encode(), hashlib.sha256).hexdigest()

    def check_csrf(self, session: Optional[dict], token: str) -> bool:
        return bool(session) and hmac.compare_digest(self.csrf(session), str(token or ""))
