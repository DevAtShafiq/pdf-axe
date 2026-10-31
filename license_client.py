# -*- coding: utf-8 -*-
"""
license_client.py — the app's side of the portal's license API.

When a portal address is set (``SERVER_URL`` below, or the environment
variable OFFICEAXE_LICENSE_SERVER), signed keys are also checked online:
on activation and then about once a day. The portal enforces the device limit
across PCs and revocation at once. Its answers are signed with the owner's
key and echo a fresh nonce, so a fake server can't approve anything.

No internet → the app keeps working for ``GRACE_DAYS`` after the last good
check. With no portal address set, keys work fully offline as before.
"""
from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request
from typing import Callable, Optional

import ed25519_pure
import license_keys

# The live portal, e.g. "https://portal.example.com". Empty = offline keys only.
SERVER_URL = "https://officeaxe-8a44f.web.app"
ENV_SERVER = "OFFICEAXE_LICENSE_SERVER"
GRACE_DAYS = 7
CHECK_EVERY_HOURS = 24
RESPONSE_CONTEXT = b"OfficeAxe-portal-v1:"

Transport = Callable[[str, bytes, float], bytes]


class Offline(Exception):
    """The portal couldn't be reached, or its answer couldn't be trusted."""


def server_url() -> str:
    return (os.environ.get(ENV_SERVER) or SERVER_URL or "").strip().rstrip("/")


def enabled() -> bool:
    return bool(server_url())


def _urllib_transport(url: str, data: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "OfficeAxe"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 (https URL we set)
        return r.read(64 * 1024)


def _verify(public: bytes, doc: dict) -> Optional[dict]:
    try:
        payload, sig = doc["payload"], bytes.fromhex(doc["sig"])
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (KeyError, TypeError, ValueError):
        return None
    return payload if ed25519_pure.verify(public, RESPONSE_CONTEXT + body.encode(), sig) else None


def call(action: str, key: str, device: dict, *, transport: Optional[Transport] = None,
         public_key: Optional[bytes] = None, timeout: float = 8.0, url: Optional[str] = None) -> dict:
    """activate / check / deactivate. Returns the portal's verified payload
    ({"ok", "status", "message", ...}) or raises Offline."""
    base = url or server_url()
    if not base:
        raise Offline("No license server is set.")
    nonce = secrets.token_hex(16)
    body = json.dumps({"key": key, "device_id": device.get("id", ""), "device_name": device.get("name", ""),
                       "os": device.get("os", ""), "nonce": nonce}).encode("utf-8")
    try:
        raw = (transport or _urllib_transport)(f"{base}/api/v1/{action}", body, timeout)
        doc = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Offline(str(exc))
    pub = public_key if public_key is not None else bytes.fromhex(license_keys.PUBLIC_KEY_HEX)
    payload = _verify(pub, doc) if isinstance(doc, dict) else None
    if not payload or payload.get("nonce") != nonce or payload.get("device_id") != device.get("id", ""):
        raise Offline("The license server's answer could not be verified.")
    return payload
