# -*- coding: utf-8 -*-
"""
license_keys.py — signed license keys that any PC can check offline.

The owner makes keys with the admin dashboard (dashboard/). Each key carries
its own details and an Ed25519 signature made with the owner's private key,
which never leaves the owner's PC. The app only holds the public key below,
so it can tell a real key from a made-up or edited one without a server.

Key text:  OAXE-XXXXXXXX-XXXXXXXX-…   (base32 of payload + 64-byte signature)

Payload (version 1, big-endian):
    version u8 | type u8 | plan u8 | max_devices u8 | key_id 6 bytes
    | issued_at u32 (unix s) | expires_at u32 (unix s) | name_len u8 | name (UTF-8)

What a key can't do without a server (also stated in the dashboard):
* The device limit is only counted on each PC, not across PCs.
* Revoking a key takes effect when an app update ships with the key's id in
  revoked_keys.json (the dashboard writes that file).
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import struct
import sys
from datetime import datetime, timezone
from typing import Optional

import ed25519_pure

PREFIX = "OAXE"
VERSION = 1
SIG_CONTEXT = b"OfficeAxe-license-v1:"

# The owner's public key (dashboard/admin_core.py init prints it). Keys signed
# by any other private key are rejected.
PUBLIC_KEY_HEX = "7056198f4c704f0adde2b4b156e0b7135823a7988784b45fe1a7659afbbbf3a2"

TYPES = ("review", "trial", "full")
TYPE_LABELS = {"review": "Review", "trial": "Trial", "full": "Full licence"}
PLAN_IDS = ("", "monthly", "yearly")
MAX_NAME_BYTES = 48

_HEAD = struct.Struct(">BBBB6sIIB")


class KeyError_(ValueError):
    """A key that can't be used, with a message for the user."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def normalize(key: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(key or "").upper())


def looks_signed(key: str) -> bool:
    """Signed keys are far longer than the old 16-character random keys."""
    n = normalize(key)
    return n.startswith(PREFIX) and len(n) > 60


def _encode(raw: bytes) -> str:
    b32 = base64.b32encode(raw).decode("ascii").rstrip("=")
    return PREFIX + "-" + "-".join(b32[i:i + 8] for i in range(0, len(b32), 8))


def _decode(key: str) -> bytes:
    n = normalize(key)
    if not n.startswith(PREFIX):
        raise KeyError_("That isn’t an Office Axe license key.", "format")
    body = n[len(PREFIX):]
    try:
        return base64.b32decode(body + "=" * (-len(body) % 8))
    except (ValueError, TypeError):
        raise KeyError_("That license key is incomplete or mistyped. Copy it again in full.", "format")


def canonical(key: str) -> str:
    """The key in its standard grouped form (however it was pasted)."""
    return _encode(_decode(key))


def _clean_name(name: str) -> bytes:
    raw = str(name or "").strip().encode("utf-8")[:MAX_NAME_BYTES]
    return raw.decode("utf-8", "ignore").encode("utf-8")      # never cut a character in half


def make_key(private_seed: bytes, *, key_type: str, holder: str, expires_at: int,
             max_devices: int = 1, plan: str = "", issued_at: Optional[int] = None,
             key_id: Optional[str] = None) -> str:
    """Sign a new key. Owner's PC only (needs the private key)."""
    if key_type not in TYPES:
        raise ValueError(f"key_type must be one of {TYPES}")
    if plan not in PLAN_IDS:
        raise ValueError(f"plan must be one of {PLAN_IDS}")
    if not 1 <= int(max_devices) <= 255:
        raise ValueError("max_devices must be 1–255")
    kid = bytes.fromhex(key_id) if key_id else secrets.token_bytes(6)
    if len(kid) != 6:
        raise ValueError("key_id is 12 hex characters")
    issued = int(issued_at if issued_at is not None else datetime.now(timezone.utc).timestamp())
    name = _clean_name(holder)
    payload = _HEAD.pack(VERSION, TYPES.index(key_type), PLAN_IDS.index(plan), int(max_devices),
                         kid, issued, int(expires_at), len(name)) + name
    return _encode(payload + ed25519_pure.sign(private_seed, SIG_CONTEXT + payload))


def _bundle_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def revoked_ids(path: Optional[str] = None) -> set:
    """Key ids listed in revoked_keys.json (shipped with each app update)."""
    path = path or os.path.join(_bundle_dir(), "revoked_keys.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    ids = data.get("revoked", []) if isinstance(data, dict) else data
    return {str(x.get("id") if isinstance(x, dict) else x).lower() for x in ids or []}


def parse(key: str, public_key: Optional[bytes] = None, *, now: Optional[datetime] = None,
          revoked: Optional[set] = None, check_expiry: bool = True) -> dict:
    """Check a key and return its details. Raises KeyError_ when it can't be used."""
    pub = public_key if public_key is not None else bytes.fromhex(PUBLIC_KEY_HEX or "00" * 32)
    raw = _decode(key)
    if len(raw) < _HEAD.size + 64:
        raise KeyError_("That license key is incomplete or mistyped. Copy it again in full.", "format")
    payload, sig = raw[:-64], raw[-64:]
    if not PUBLIC_KEY_HEX and public_key is None:
        raise KeyError_("This copy of Office Axe can’t check license keys yet.", "no_public_key")
    if not ed25519_pure.verify(pub, SIG_CONTEXT + payload, sig):
        raise KeyError_("That license key isn’t valid. Check that it was copied in full.", "signature")
    ver, typ, plan, devices, kid, issued, expires, nlen = _HEAD.unpack_from(payload)
    if ver != VERSION or typ >= len(TYPES) or plan >= len(PLAN_IDS) \
            or len(payload) != _HEAD.size + nlen:
        raise KeyError_("This license key needs a newer version of Office Axe.", "version")
    info = {
        "key_id": kid.hex(),
        "type": TYPES[typ],
        "type_label": TYPE_LABELS[TYPES[typ]],
        "plan": PLAN_IDS[plan],
        "max_devices": devices,
        "holder": payload[_HEAD.size:].decode("utf-8", "replace"),
        "issued_at": datetime.fromtimestamp(issued, timezone.utc),
        "expires_at": datetime.fromtimestamp(expires, timezone.utc),
    }
    if (revoked if revoked is not None else revoked_ids()) & {info["key_id"]}:
        raise KeyError_("This license key has been withdrawn. Contact Office Axe for a new one.", "revoked")
    if check_expiry and info["expires_at"] <= (now or datetime.now(timezone.utc)):
        raise KeyError_("This license key has expired.", "expired")
    return info
