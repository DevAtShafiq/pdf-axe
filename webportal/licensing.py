# -*- coding: utf-8 -*-
"""
licensing.py — what the portal does with keys, independent of HTTP.

* Admin: make keys (same signed format as license_keys.py, so they also work
  offline), revoke / restore, release a device, import the desktop
  dashboard's issued_keys.json, CSV export.
* License API: activate / check / deactivate. The device limit and revocation
  are enforced here, across every PC. Each answer is signed with the same
  Ed25519 key and echoes the app's nonce, so the app can trust it even if
  someone points it at a fake server.
"""
from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ed25519_pure  # noqa: E402
import license_keys  # noqa: E402
from store import Store, iso, parse_iso  # noqa: E402

RESPONSE_CONTEXT = b"OfficeAxe-portal-v1:"
DEFAULT_DAYS = 30
DEFAULT_DEVICES = {"review": 1, "trial": 1, "full": 5}


def sign_payload(seed: bytes, payload: dict) -> dict:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {"payload": payload, "sig": ed25519_pure.sign(seed, RESPONSE_CONTEXT + body.encode()).hex()}


def _clip(s, n: int) -> str:
    return str(s or "").strip()[:n]


class Licensing:
    def __init__(self, store: Store, seed: bytes, now: Optional[Callable[[], datetime]] = None):
        self.store = store
        self.seed = seed
        self.public = ed25519_pure.public_key(seed)
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def public_hex(self) -> str:
        return self.public.hex()

    def app_matches(self) -> bool:
        return license_keys.PUBLIC_KEY_HEX.lower() == self.public_hex

    # ── admin ────────────────────────────────────────────────────────────────
    def issue(self, key_type: str, holder: str, days: int = DEFAULT_DAYS,
              max_devices: Optional[int] = None, plan: str = "", note: str = "",
              actor: str = "admin") -> dict:
        holder = _clip(holder, license_keys.MAX_NAME_BYTES)
        if not holder:
            raise ValueError("Enter who the key is for.")
        if key_type not in license_keys.TYPES:
            raise ValueError("Choose a key type.")
        if plan not in license_keys.PLAN_IDS:
            raise ValueError("Choose a plan.")
        days = int(days)
        if not 1 <= days <= 3660:
            raise ValueError("Days must be between 1 and 3660.")
        devices = int(max_devices or DEFAULT_DEVICES[key_type])
        if not 1 <= devices <= 255:
            raise ValueError("Devices must be between 1 and 255.")
        now = self._now()
        expires = now + timedelta(days=days)
        key_id = secrets.token_hex(6)
        key = license_keys.make_key(self.seed, key_type=key_type, holder=holder,
                                    expires_at=int(expires.timestamp()), max_devices=devices,
                                    plan=plan, issued_at=int(now.timestamp()), key_id=key_id)
        info = license_keys.parse(key, self.public, revoked=set(), check_expiry=False)
        row = {"key_id": key_id, "key": key, "type": key_type, "holder": info["holder"],
               "plan": plan, "max_devices": devices, "issued_at": iso(now),
               "expires_at": iso(expires), "note": _clip(note, 300), "source": "portal"}
        self.store.add_key(row)
        self.store.log(iso(now), actor, "issue", key_id, f"{key_type} for {info['holder']}, {days} days")
        return self.key_view(key_id)

    def status_of(self, row: dict) -> str:
        if row.get("revoked"):
            return "revoked"
        return "expired" if parse_iso(row["expires_at"]) <= self._now() else "active"

    def key_view(self, key_id: str) -> Optional[dict]:
        row = self.store.get_key(key_id)
        if not row:
            return None
        row["status"] = self.status_of(row)
        secs = (parse_iso(row["expires_at"]) - self._now()).total_seconds()
        row["days_left"] = max(0, int(-(-secs // 86400)))
        row["devices"] = self.store.devices(key_id)
        row["devices_used"] = sum(1 for d in row["devices"] if not d["released"])
        return row

    def list_keys(self, q: str = "", status: str = "") -> list:
        now = self._now()
        out = []
        q = (q or "").strip().lower()
        for r in self.store.list_keys():
            r["status"] = self.status_of(r)
            secs = (parse_iso(r["expires_at"]) - now).total_seconds()
            r["days_left"] = max(0, int(-(-secs // 86400)))
            if status and r["status"] != status:
                continue
            if q and q not in " ".join([r["holder"], r["key_id"], r["note"], r["type"], r["plan"]]).lower():
                continue
            out.append(r)
        return out

    def stats(self) -> dict:
        rows = self.list_keys()
        return {
            "total": len(rows),
            "active": sum(r["status"] == "active" for r in rows),
            "expiring": sum(r["status"] == "active" and r["days_left"] <= 7 for r in rows),
            "revoked": sum(r["status"] == "revoked" for r in rows),
            "devices": sum(r["devices_used"] or 0 for r in rows),
            "downloads": self.store.download_count(),
        }

    def set_revoked(self, key_id: str, revoked: bool, actor: str = "admin") -> bool:
        ok = self.store.set_revoked(key_id, revoked, iso(self._now()))
        if ok:
            self.store.log(iso(self._now()), actor, "revoke" if revoked else "restore", key_id)
        return ok

    def set_note(self, key_id: str, note: str, actor: str = "admin") -> bool:
        ok = self.store.set_note(key_id, _clip(note, 300))
        if ok:
            self.store.log(iso(self._now()), actor, "note", key_id)
        return ok

    def release_device(self, key_id: str, device_id: str, actor: str = "admin") -> bool:
        ok = self.store.release_device(key_id, device_id)
        if ok:
            self.store.log(iso(self._now()), actor, "release_device", key_id, device_id)
        return ok

    def import_desktop(self, path: str, actor: str = "admin") -> dict:
        """Bring in the desktop dashboard's issued_keys.json (keys signed by this same key)."""
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh).get("keys", [])
        added = skipped = 0
        for r in rows:
            try:
                info = license_keys.parse(r["key"], self.public, revoked=set(), check_expiry=False)
            except (KeyError, license_keys.KeyError_):
                skipped += 1           # signed by another key, or damaged
                continue
            if self.store.get_key(info["key_id"]):
                skipped += 1
                continue
            self.store.add_key({
                "key_id": info["key_id"], "key": license_keys.canonical(r["key"]), "type": info["type"],
                "holder": info["holder"], "plan": info["plan"], "max_devices": info["max_devices"],
                "issued_at": iso(info["issued_at"]), "expires_at": iso(info["expires_at"]),
                "note": _clip(r.get("note"), 300), "source": "desktop",
                "revoked": 1 if r.get("revoked") else 0, "revoked_at": r.get("revoked_at") or ""})
            added += 1
        self.store.log(iso(self._now()), actor, "import", "", f"{added} added, {skipped} skipped")
        return {"added": added, "skipped": skipped}

    CSV_FIELDS = ["key_id", "status", "type", "holder", "plan", "max_devices", "devices_used",
                  "issued_at", "expires_at", "days_left", "last_seen", "revoked_at", "source", "note", "key"]

    def export_csv(self) -> str:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=self.CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(self.list_keys())
        return "﻿" + buf.getvalue()                 # BOM: Excel reads UTF-8

    def revoked_json(self) -> str:
        return json.dumps({"version": 1, "updated_at": iso(self._now()),
                           "revoked": self.store.revoked_ids()}, indent=1)

    # ── license API ──────────────────────────────────────────────────────────
    def _answer(self, req: dict, ok: bool, status: str, message: str = "", **extra) -> dict:
        payload = {"ok": ok, "status": status, "message": message,
                   "nonce": _clip(req.get("nonce"), 64), "device_id": _clip(req.get("device_id"), 64),
                   "server_time": iso(self._now())}
        payload.update(extra)
        return sign_payload(self.seed, payload)

    def _known_key(self, req: dict):
        """(row, info) for a valid signed key, adding keys made offline on first sight."""
        key = str(req.get("key") or "")
        info = license_keys.parse(key, self.public, revoked=set(), check_expiry=False)
        row = self.store.get_key(info["key_id"])
        if not row:
            self.store.add_key({
                "key_id": info["key_id"], "key": license_keys.canonical(key), "type": info["type"],
                "holder": info["holder"], "plan": info["plan"], "max_devices": info["max_devices"],
                "issued_at": iso(info["issued_at"]), "expires_at": iso(info["expires_at"]),
                "source": "seen"})
            row = self.store.get_key(info["key_id"])
        return row, info

    def api(self, action: str, req: dict, ip: str = "") -> dict:
        if not isinstance(req, dict):
            req = {}
        device_id = _clip(req.get("device_id"), 64)
        if not device_id:
            return self._answer(req, False, "invalid", "This PC could not be identified.")
        try:
            row, info = self._known_key(req)
        except license_keys.KeyError_ as exc:
            return self._answer(req, False, "invalid", str(exc))
        key_id = row["key_id"]
        base = {"key_id": key_id, "expires_at": row["expires_at"], "max_devices": row["max_devices"]}
        now = iso(self._now())
        if row["revoked"]:
            return self._answer(req, False, "revoked", "This license key has been withdrawn. "
                                "Contact Office Axe for a new one.", **base)
        if parse_iso(row["expires_at"]) <= self._now():
            return self._answer(req, False, "expired", "This license key has expired.", **base)

        dev = self.store.get_device(key_id, device_id)
        active = [d for d in self.store.devices(key_id, include_released=False)]
        if action == "activate":
            if not (dev and not dev["released"]) and len(active) >= row["max_devices"]:
                return self._answer(req, False, "device_limit",
                                    f"This key is already used on {row['max_devices']} PC(s). "
                                    "Ask for a PC to be removed from it first.",
                                    devices_used=len(active), **base)
            self.store.upsert_device(key_id, device_id, _clip(req.get("device_name"), 60),
                                     _clip(req.get("os"), 40), now)
            if not dev or dev["released"]:
                self.store.log(now, "api", "activate", key_id, f"{_clip(req.get('device_name'), 60)} {ip}")
            used = len(self.store.devices(key_id, include_released=False))
            return self._answer(req, True, "active", devices_used=used, **base)
        if action == "check":
            if not dev or dev["released"]:
                return self._answer(req, False, "not_activated", "This PC was removed from the license "
                                    "key. Activate the key again to keep using Office Axe here.", **base)
            self.store.touch_device(key_id, device_id, now)
            return self._answer(req, True, "active", devices_used=len(active), **base)
        if action == "deactivate":
            if dev and not dev["released"]:
                self.store.release_device(key_id, device_id)
                self.store.log(now, "api", "deactivate", key_id, device_id)
            return self._answer(req, True, "released", **base)
        return self._answer(req, False, "invalid", "Unknown request.")
