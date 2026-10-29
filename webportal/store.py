# -*- coding: utf-8 -*-
"""
store.py — the portal's SQLite database: keys, devices, downloads, activity
and login attempts. Plain sqlite3, one connection per call (safe under any
WSGI server).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    key_id      TEXT PRIMARY KEY,
    key         TEXT NOT NULL,
    type        TEXT NOT NULL,
    holder      TEXT NOT NULL,
    plan        TEXT NOT NULL DEFAULT '',
    max_devices INTEGER NOT NULL,
    issued_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'portal',
    revoked     INTEGER NOT NULL DEFAULT 0,
    revoked_at  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS devices (
    key_id      TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    os          TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    released    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_id, device_id)
);
CREATE TABLE IF NOT EXISTS activity (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    key_id  TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS downloads (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    ip  TEXT NOT NULL,
    at  REAL NOT NULL,
    ok  INTEGER NOT NULL
);
"""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


class Store:
    def __init__(self, path: str):
        self.path = path
        self._init_lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with self.db() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── keys ─────────────────────────────────────────────────────────────────
    def add_key(self, row: dict) -> None:
        with self.db() as c:
            c.execute("INSERT OR IGNORE INTO keys (key_id, key, type, holder, plan, max_devices, "
                      "issued_at, expires_at, note, source, revoked, revoked_at) "
                      "VALUES (:key_id, :key, :type, :holder, :plan, :max_devices, :issued_at, "
                      ":expires_at, :note, :source, :revoked, :revoked_at)",
                      {"note": "", "source": "portal", "revoked": 0, "revoked_at": "", **row})

    def get_key(self, key_id: str) -> Optional[dict]:
        with self.db() as c:
            r = c.execute("SELECT * FROM keys WHERE key_id = ?", (key_id,)).fetchone()
        return dict(r) if r else None

    def list_keys(self) -> list:
        with self.db() as c:
            rows = c.execute(
                "SELECT k.*, (SELECT COUNT(*) FROM devices d WHERE d.key_id = k.key_id AND d.released = 0) "
                "AS devices_used, (SELECT MAX(last_seen) FROM devices d WHERE d.key_id = k.key_id) AS last_seen "
                "FROM keys k ORDER BY issued_at DESC").fetchall()
        return [dict(r) for r in rows]

    def set_revoked(self, key_id: str, revoked: bool, at: str) -> bool:
        with self.db() as c:
            cur = c.execute("UPDATE keys SET revoked = ?, revoked_at = ? WHERE key_id = ?",
                            (1 if revoked else 0, at if revoked else "", key_id))
            return cur.rowcount > 0

    def set_note(self, key_id: str, note: str) -> bool:
        with self.db() as c:
            return c.execute("UPDATE keys SET note = ? WHERE key_id = ?", (note, key_id)).rowcount > 0

    def revoked_ids(self) -> list:
        with self.db() as c:
            return [r[0] for r in c.execute("SELECT key_id FROM keys WHERE revoked = 1 ORDER BY key_id")]

    # ── devices ──────────────────────────────────────────────────────────────
    def devices(self, key_id: str, include_released: bool = True) -> list:
        q = "SELECT * FROM devices WHERE key_id = ?" + ("" if include_released else " AND released = 0")
        with self.db() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY first_seen", (key_id,))]

    def get_device(self, key_id: str, device_id: str) -> Optional[dict]:
        with self.db() as c:
            r = c.execute("SELECT * FROM devices WHERE key_id = ? AND device_id = ?",
                          (key_id, device_id)).fetchone()
        return dict(r) if r else None

    def upsert_device(self, key_id: str, device_id: str, name: str, os_name: str, at: str) -> None:
        with self.db() as c:
            c.execute("INSERT INTO devices (key_id, device_id, name, os, first_seen, last_seen, released) "
                      "VALUES (?, ?, ?, ?, ?, ?, 0) ON CONFLICT(key_id, device_id) DO UPDATE SET "
                      "name = excluded.name, os = excluded.os, last_seen = excluded.last_seen, released = 0",
                      (key_id, device_id, name, os_name, at, at))

    def touch_device(self, key_id: str, device_id: str, at: str) -> None:
        with self.db() as c:
            c.execute("UPDATE devices SET last_seen = ? WHERE key_id = ? AND device_id = ?",
                      (at, key_id, device_id))

    def release_device(self, key_id: str, device_id: str) -> bool:
        with self.db() as c:
            return c.execute("UPDATE devices SET released = 1 WHERE key_id = ? AND device_id = ? "
                             "AND released = 0", (key_id, device_id)).rowcount > 0

    # ── activity / downloads ─────────────────────────────────────────────────
    def log(self, at: str, actor: str, action: str, key_id: str = "", detail: str = "") -> None:
        with self.db() as c:
            c.execute("INSERT INTO activity (at, actor, action, key_id, detail) VALUES (?, ?, ?, ?, ?)",
                      (at, actor, action, key_id, detail[:500]))

    def activity(self, limit: int = 200, key_id: str = "") -> list:
        with self.db() as c:
            if key_id:
                rows = c.execute("SELECT * FROM activity WHERE key_id = ? ORDER BY id DESC LIMIT ?",
                                 (key_id, limit))
            else:
                rows = c.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def count_download(self, at: str) -> None:
        with self.db() as c:
            c.execute("INSERT INTO downloads (at) VALUES (?)", (at,))

    def download_count(self) -> int:
        with self.db() as c:
            return c.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]

    # ── login throttling ─────────────────────────────────────────────────────
    def record_login(self, ip: str, at: float, ok: bool) -> None:
        with self.db() as c:
            c.execute("INSERT INTO login_attempts (ip, at, ok) VALUES (?, ?, ?)", (ip, at, 1 if ok else 0))
            c.execute("DELETE FROM login_attempts WHERE at < ?", (at - 86400,))   # old rows only

    def recent_failures(self, ip: str, since: float) -> int:
        with self.db() as c:
            return c.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND ok = 0 AND at >= ?",
                             (ip, since)).fetchone()[0]
