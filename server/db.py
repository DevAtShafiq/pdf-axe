"""
db.py — SQLite storage for users, sessions, subscriptions and cloud files.

One connection per call (sqlite3 is cheap to open) guarded by a process-wide
lock for writes, which is plenty for a single-instance server.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT    NOT NULL UNIQUE,
    password_hash       TEXT    NOT NULL,
    created_at          REAL    NOT NULL,
    stripe_customer_id  TEXT,
    subscription_id     TEXT,
    subscription_status TEXT    NOT NULL DEFAULT 'none',
    current_period_end  REAL    NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    created_at  REAL    NOT NULL,
    expires_at  REAL    NOT NULL,
    device      TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    path        TEXT    NOT NULL,
    blob        TEXT    NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT    NOT NULL,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    trashed     INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS files_user_path ON files(user_id, path);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with self.connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            with self._lock:
                yield con
                con.commit()
        finally:
            con.close()

    # ── users ────────────────────────────────────────────────────────────────

    def create_user(self, email: str, password_hash: str) -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO users(email, password_hash, created_at) VALUES (?,?,?)",
                (email, password_hash, time.time()),
            )
            return int(cur.lastrowid)

    def user_by_email(self, email: str):
        with self.connect() as con:
            return con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

    def user_by_id(self, user_id: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def user_by_customer(self, customer_id: str):
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM users WHERE stripe_customer_id=?", (customer_id,)
            ).fetchone()

    def set_customer(self, user_id: int, customer_id: str) -> None:
        with self.connect() as con:
            con.execute("UPDATE users SET stripe_customer_id=? WHERE id=?", (customer_id, user_id))

    def set_subscription(self, user_id: int, sub_id: str, status: str, period_end: float) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE users SET subscription_id=?, subscription_status=?, current_period_end=? WHERE id=?",
                (sub_id, status, float(period_end or 0), user_id),
            )

    # ── sessions ─────────────────────────────────────────────────────────────

    def create_session(self, token_hash: str, user_id: int, ttl_s: float, device: str = "") -> None:
        now = time.time()
        with self.connect() as con:
            con.execute(
                "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, device) VALUES (?,?,?,?,?)",
                (token_hash, user_id, now, now + ttl_s, device[:120]),
            )

    def session_user(self, token_hash: str):
        with self.connect() as con:
            return con.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.expires_at>?",
                (token_hash, time.time()),
            ).fetchone()

    def end_session(self, token_hash: str) -> None:
        # Expire rather than remove, so the row stays as an audit record
        with self.connect() as con:
            con.execute("UPDATE sessions SET expires_at=0 WHERE token_hash=?", (token_hash,))

    # ── files ────────────────────────────────────────────────────────────────

    def list_files(self, user_id: int, trashed: bool = False):
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM files WHERE user_id=? AND trashed=? ORDER BY path",
                (user_id, 1 if trashed else 0),
            ).fetchall()

    def file_by_id(self, user_id: int, file_id: int):
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM files WHERE user_id=? AND id=?", (user_id, file_id)
            ).fetchone()

    def file_by_path(self, user_id: int, path: str):
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM files WHERE user_id=? AND path=?", (user_id, path)
            ).fetchone()

    def usage_bytes(self, user_id: int) -> int:
        with self.connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(size),0) AS n FROM files WHERE user_id=?", (user_id,)
            ).fetchone()
            return int(row["n"])

    def upsert_file(self, user_id: int, path: str, blob: str, size: int, sha256: str) -> int:
        """Insert or replace the record for path. The old blob stays on disk."""
        now = time.time()
        with self.connect() as con:
            row = con.execute(
                "SELECT id FROM files WHERE user_id=? AND path=?", (user_id, path)
            ).fetchone()
            if row:
                con.execute(
                    "UPDATE files SET blob=?, size=?, sha256=?, updated_at=?, trashed=0 WHERE id=?",
                    (blob, size, sha256, now, row["id"]),
                )
                return int(row["id"])
            cur = con.execute(
                "INSERT INTO files(user_id, path, blob, size, sha256, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (user_id, path, blob, size, sha256, now, now),
            )
            return int(cur.lastrowid)

    def set_trashed(self, user_id: int, file_id: int, trashed: bool) -> bool:
        with self.connect() as con:
            cur = con.execute(
                "UPDATE files SET trashed=?, updated_at=? WHERE user_id=? AND id=?",
                (1 if trashed else 0, time.time(), user_id, file_id),
            )
            return cur.rowcount > 0
