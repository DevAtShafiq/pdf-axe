# -*- coding: utf-8 -*-
"""
admin_core.py — the owner's key office: make, list and withdraw license keys.

Everything lives in the admin folder on the owner's PC, never in the repo:
    signing_key.json   the Ed25519 private key (back it up; keep it private)
    issued_keys.json   every key made, with holder, expiry, note, revoked flag

Command line (run from the project folder):
    python dashboard/admin_core.py init
    python dashboard/admin_core.py new --type review --name "Rahim" --days 30
    python dashboard/admin_core.py list
    python dashboard/admin_core.py revoke <key_id>
    python dashboard/admin_core.py export keys.csv
    python dashboard/admin_core.py revoked-list        # writes revoked_keys.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ed25519_pure  # noqa: E402
import license_keys  # noqa: E402

ENV_DIR = "OFFICEAXE_ADMIN_DIR"
DEFAULT_DAYS = 30
DEFAULT_DEVICES = {"review": 1, "trial": 1, "full": 2}


def admin_dir() -> str:
    return os.environ.get(ENV_DIR) or os.path.join(os.path.expanduser("~"), "OfficeAxeAdmin")


def _path(name: str) -> str:
    return os.path.join(admin_dir(), name)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


# ── signing key ───────────────────────────────────────────────────────────────

def has_signing_key() -> bool:
    return os.path.isfile(_path("signing_key.json"))


def init() -> dict:
    """Create the signing key once. An existing key is never replaced."""
    path = _path("signing_key.json")
    created = False
    if not os.path.isfile(path):
        seed = secrets.token_bytes(32)
        _write_json(path, {"private_seed_hex": seed.hex(),
                           "public_key_hex": ed25519_pure.public_key(seed).hex(),
                           "created_at": _iso(_now()),
                           "note": "Office Axe license signing key. Keep this file private and "
                                   "backed up. Anyone with it can make valid keys; without it no "
                                   "new keys can be made for the current app."})
        created = True
    return {"created": created, "path": path, "public_key_hex": public_key_hex()}


def _seed() -> bytes:
    doc = _read_json(_path("signing_key.json"), None)
    if not doc:
        raise RuntimeError("No signing key yet. Run: python dashboard/admin_core.py init")
    return bytes.fromhex(doc["private_seed_hex"])


def public_key_hex() -> str:
    doc = _read_json(_path("signing_key.json"), None) or {}
    return doc.get("public_key_hex", "")


def app_matches() -> bool:
    """True when the app's built-in public key is this signing key's."""
    return bool(public_key_hex()) and license_keys.PUBLIC_KEY_HEX.lower() == public_key_hex().lower()


# ── issued keys ───────────────────────────────────────────────────────────────

def _register() -> list:
    return _read_json(_path("issued_keys.json"), {"keys": []}).get("keys", [])


def _save_register(rows: list) -> None:
    _write_json(_path("issued_keys.json"), {"version": 1, "keys": rows})


def issue(key_type: str = "review", holder: str = "", days: int = DEFAULT_DAYS,
          max_devices: Optional[int] = None, plan: str = "", note: str = "") -> dict:
    holder = str(holder or "").strip()
    if not holder:
        raise ValueError("Enter who the key is for.")
    days = int(days)
    if not 1 <= days <= 3660:
        raise ValueError("Days must be between 1 and 3660.")
    devices = int(max_devices or DEFAULT_DEVICES.get(key_type, 1))
    now = _now()
    expires = now + timedelta(days=days)
    key_id = secrets.token_hex(6)
    key = license_keys.make_key(_seed(), key_type=key_type, holder=holder,
                                expires_at=int(expires.timestamp()), max_devices=devices,
                                plan=plan, issued_at=int(now.timestamp()), key_id=key_id)
    row = {"key_id": key_id, "key": key, "type": key_type, "holder": holder, "plan": plan,
           "max_devices": devices, "days": days, "issued_at": _iso(now),
           "expires_at": _iso(expires), "note": str(note or "").strip()[:300],
           "revoked": False, "revoked_at": ""}
    rows = _register()
    rows.append(row)
    _save_register(rows)
    return _with_status(row, now)


def _with_status(row: dict, now: datetime) -> dict:
    r = dict(row)
    exp = datetime.fromisoformat(r["expires_at"].replace("Z", "+00:00"))
    if r.get("revoked"):
        r["status"] = "revoked"
    elif exp <= now:
        r["status"] = "expired"
    else:
        r["status"] = "active"
    r["days_left"] = max(0, -(-int((exp - now).total_seconds()) // 86400))
    return r


def list_keys() -> list:
    now = _now()
    return [_with_status(r, now) for r in sorted(_register(), key=lambda r: r["issued_at"], reverse=True)]


def set_revoked(key_id: str, revoked: bool = True) -> dict:
    rows = _register()
    for r in rows:
        if r["key_id"] == key_id:
            r["revoked"] = bool(revoked)
            r["revoked_at"] = _iso(_now()) if revoked else ""
            _save_register(rows)
            return _with_status(r, _now())
    raise ValueError("No key with that id.")


CSV_FIELDS = ["key_id", "status", "type", "holder", "plan", "max_devices", "issued_at",
              "expires_at", "days_left", "revoked_at", "note", "key"]


def export_csv(path: str) -> int:
    rows = list_keys()
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:     # BOM: Excel reads UTF-8
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_revoked_list(path: Optional[str] = None) -> dict:
    """revoked_keys.json for the next app build (ids only, nothing secret)."""
    path = path or os.path.join(ROOT, "revoked_keys.json")
    ids = sorted({r["key_id"] for r in _register() if r.get("revoked")})
    _write_json(path, {"version": 1, "updated_at": _iso(_now()), "revoked": ids})
    return {"path": path, "count": len(ids)}


# ── command line ──────────────────────────────────────────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="admin_core.py", description="Office Axe license keys")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    n = sub.add_parser("new")
    n.add_argument("--type", default="review", choices=license_keys.TYPES)
    n.add_argument("--name", required=True)
    n.add_argument("--days", type=int, default=DEFAULT_DAYS)
    n.add_argument("--devices", type=int)
    n.add_argument("--plan", default="", choices=license_keys.PLAN_IDS)
    n.add_argument("--note", default="")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("key_id")
    r.add_argument("--undo", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("csv_path")
    sub.add_parser("revoked-list")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")        # names may be Bangla
    except (AttributeError, ValueError):
        pass

    if a.cmd == "init":
        res = init()
        print(("Created" if res["created"] else "Already exists") + f": {res['path']}")
        print("Public key:", res["public_key_hex"])
        if not app_matches():
            print("Put this public key in license_keys.py (PUBLIC_KEY_HEX) and rebuild the app.")
    elif a.cmd == "new":
        row = issue(a.type, a.name, a.days, a.devices, a.plan, a.note)
        print(f"{row['type']} key for {row['holder']} · id {row['key_id']} · until {row['expires_at']}")
        print(row["key"])
    elif a.cmd == "list":
        for row in list_keys():
            print(f"{row['key_id']}  {row['status']:<8} {row['type']:<7} {row['expires_at']}  {row['holder']}")
    elif a.cmd == "revoke":
        row = set_revoked(a.key_id, not a.undo)
        print(f"{row['key_id']}: {row['status']}. Run 'revoked-list' and rebuild the app to ship it.")
    elif a.cmd == "export":
        print(f"{export_csv(a.csv_path)} keys written to {a.csv_path}")
    elif a.cmd == "revoked-list":
        res = write_revoked_list()
        print(f"{res['count']} revoked id(s) written to {res['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
