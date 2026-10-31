# -*- coding: utf-8 -*-
"""
store_firestore.py — the portal's data in Cloud Firestore (for Firebase /
Cloud Run, where the local SQLite file would be lost on every restart).

Same methods as store.Store. Collections (all prefixed, default "oa_"):
    oa_keys/{key_id}                  one license key
    oa_devices/{key_id}__{device_id}  one PC on a key
    oa_activity/{auto}                what happened
    oa_downloads/{auto}               one per download
    oa_logins/{auto}                  login attempts (rate limit)
    oa_meta/{name}                    admin login etc.

Queries use one equality filter or one order_by only, so Firestore needs no
custom indexes. Volumes are small (an office's keys), so the rest is done
in Python.
"""
from __future__ import annotations

import time
from typing import Optional


def _client():
    from google.cloud import firestore   # installed on the server (requirements.txt)
    return firestore.Client()


class FirestoreStore:
    def __init__(self, client=None, prefix: str = "oa_"):
        self.db = client or _client()
        self.p = prefix

    def _c(self, name: str):
        return self.db.collection(self.p + name)

    @staticmethod
    def _dev_id(key_id: str, device_id: str) -> str:
        return f"{key_id}__{device_id}".replace("/", "_")

    # ── keys ─────────────────────────────────────────────────────────────────
    def add_key(self, row: dict) -> None:
        ref = self._c("keys").document(row["key_id"])
        if ref.get().exists:
            return                                  # like INSERT OR IGNORE
        ref.set({"note": "", "source": "portal", "revoked": 0, "revoked_at": "", **row})

    def get_key(self, key_id: str) -> Optional[dict]:
        snap = self._c("keys").document(key_id).get()
        return snap.to_dict() if snap.exists else None

    def list_keys(self) -> list:
        used, seen = {}, {}
        for d in self._c("devices").stream():
            x = d.to_dict()
            k = x["key_id"]
            if not x.get("released"):
                used[k] = used.get(k, 0) + 1
            seen[k] = max(seen.get(k, ""), x.get("last_seen", ""))
        rows = []
        for s in self._c("keys").stream():
            r = s.to_dict()
            r["devices_used"] = used.get(r["key_id"], 0)
            r["last_seen"] = seen.get(r["key_id"]) or None
            rows.append(r)
        rows.sort(key=lambda r: r["issued_at"], reverse=True)
        return rows

    def set_revoked(self, key_id: str, revoked: bool, at: str) -> bool:
        ref = self._c("keys").document(key_id)
        if not ref.get().exists:
            return False
        ref.update({"revoked": 1 if revoked else 0, "revoked_at": at if revoked else ""})
        return True

    def set_note(self, key_id: str, note: str) -> bool:
        ref = self._c("keys").document(key_id)
        if not ref.get().exists:
            return False
        ref.update({"note": note})
        return True

    def revoked_ids(self) -> list:
        return sorted(s.to_dict()["key_id"] for s in self._c("keys").where("revoked", "==", 1).stream())

    # ── devices ──────────────────────────────────────────────────────────────
    def devices(self, key_id: str, include_released: bool = True) -> list:
        rows = [s.to_dict() for s in self._c("devices").where("key_id", "==", key_id).stream()]
        if not include_released:
            rows = [r for r in rows if not r.get("released")]
        return sorted(rows, key=lambda r: r.get("first_seen", ""))

    def get_device(self, key_id: str, device_id: str) -> Optional[dict]:
        snap = self._c("devices").document(self._dev_id(key_id, device_id)).get()
        return snap.to_dict() if snap.exists else None

    def upsert_device(self, key_id: str, device_id: str, name: str, os_name: str, at: str) -> None:
        ref = self._c("devices").document(self._dev_id(key_id, device_id))
        snap = ref.get()
        first = snap.to_dict().get("first_seen", at) if snap.exists else at
        ref.set({"key_id": key_id, "device_id": device_id, "name": name, "os": os_name,
                 "first_seen": first, "last_seen": at, "released": 0})

    def touch_device(self, key_id: str, device_id: str, at: str) -> None:
        ref = self._c("devices").document(self._dev_id(key_id, device_id))
        if ref.get().exists:
            ref.update({"last_seen": at})

    def release_device(self, key_id: str, device_id: str) -> bool:
        ref = self._c("devices").document(self._dev_id(key_id, device_id))
        snap = ref.get()
        if not snap.exists or snap.to_dict().get("released"):
            return False
        ref.update({"released": 1})
        return True

    # ── activity / downloads ─────────────────────────────────────────────────
    def _tick(self) -> int:
        """Strictly increasing nanoseconds (Windows' clock ticks are coarse)."""
        self._last = max(time.time_ns(), getattr(self, "_last", 0) + 1)
        return self._last

    def log(self, at: str, actor: str, action: str, key_id: str = "", detail: str = "") -> None:
        self._c("activity").add({"at": at, "actor": actor, "action": action, "key_id": key_id,
                                 "detail": detail[:500], "ts": self._tick()})     # ts: order within a second

    def activity(self, limit: int = 200, key_id: str = "") -> list:
        if key_id:
            rows = [s.to_dict() for s in self._c("activity").where("key_id", "==", key_id).stream()]
            rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
            return rows[:limit]
        q = self._c("activity").order_by("ts", direction="DESCENDING").limit(limit)
        return [s.to_dict() for s in q.stream()]

    def count_download(self, at: str) -> None:
        self._c("downloads").add({"at": at})

    def download_count(self) -> int:
        try:
            res = self._c("downloads").count().get()
            return int(res[0][0].value)
        except Exception:
            return sum(1 for _ in self._c("downloads").stream())

    # ── login throttling ─────────────────────────────────────────────────────
    def record_login(self, ip: str, at: float, ok: bool) -> None:
        self._c("logins").add({"ip": ip, "at": at, "ok": 1 if ok else 0})
        for s in self._c("logins").where("ip", "==", ip).stream():   # tidy this IP's old rows
            if s.to_dict().get("at", 0) < at - 86400:
                s.reference.delete()

    def recent_failures(self, ip: str, since: float) -> int:
        return sum(1 for s in self._c("logins").where("ip", "==", ip).stream()
                   if not s.to_dict().get("ok") and s.to_dict().get("at", 0) >= since)

    # ── small settings (admin login) ─────────────────────────────────────────
    def get_meta(self, name: str) -> dict:
        snap = self._c("meta").document(name).get()
        return snap.to_dict() if snap.exists else {}

    def set_meta(self, name: str, data: dict) -> None:
        self._c("meta").document(name).set(data)
