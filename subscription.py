# -*- coding: utf-8 -*-
"""
subscription.py — Office Axe subscription, devices and payments.

The app needs an active subscription. One subscription covers up to
``MAX_DEVICES`` computers. Plans are prepaid (no auto-renew): buying again
while a plan is active adds the new period after the current expiry date.

There are two seams so the real services can be dropped in later without
touching the UI or the bridge:

* ``PaymentGateway`` — takes the money. ``PlaceholderGateway`` stands in until
  a real gateway (bKash, Nagad, SSLCommerz, card…) is integrated. In sandbox
  (test) mode it approves every payment without charging anything; with
  sandbox off it reports "unavailable".

  To integrate a gateway, subclass ``PaymentGateway``:
      create_payment(order)        -> {"payment_id", "status", "redirect_url"?}
      verify_payment(id, order)    -> {"status": "paid"|"pending"|"failed",
                                       "reference", "method"}
  and return it from ``make_gateway()``. A hosted-checkout gateway returns a
  ``redirect_url`` (the UI opens it in the browser and then polls
  ``confirm_checkout``); the payment is only applied once ``verify_payment``
  says "paid" — never on the browser redirect alone.

* ``LicenseBackend`` — keeps the subscription record, the device list and the
  payment history. ``LocalLicenseBackend`` stores them in a signed JSON file
  on this PC. A license server (``RemoteLicenseBackend``) should replace it
  when payments go live: device limits, license keys and payment records can
  only be enforced across PCs by a server.

Everything returns plain dicts (JSON-safe) for the pywebview bridge.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import license_keys

# ── Plans ─────────────────────────────────────────────────────────────────────

CURRENCY = "BDT"
CURRENCY_SYMBOL = "৳"
MAX_DEVICES = 5
EXPIRING_SOON_DAYS = 7

PLANS = {
    "monthly": {
        "id": "monthly", "name": "Monthly", "price": 999, "compare_at": None,
        "months": 1, "period_label": "month", "duration_label": "1 month",
    },
    "yearly": {
        "id": "yearly", "name": "Yearly", "price": 9999, "compare_at": 11988,
        "months": 12, "period_label": "year", "duration_label": "1 year",
    },
}

PAYMENT_METHODS = [
    {"id": "bkash", "name": "bKash"},
    {"id": "nagad", "name": "Nagad"},
    {"id": "card", "name": "Card (Visa / Mastercard)"},
]
_METHOD_IDS = {m["id"] for m in PAYMENT_METHODS}

# Sandbox payments are on until a real gateway is wired in. Set
# OFFICEAXE_PAYMENT_SANDBOX=0 to see the "online payment coming soon" state.
ENV_SANDBOX = "OFFICEAXE_PAYMENT_SANDBOX"
ENV_FILE = "OFFICEAXE_SUBSCRIPTION_FILE"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,18}$")


def format_money(amount: int) -> str:
    return f"{CURRENCY_SYMBOL}{amount:,}"


def plan_list() -> list:
    out = []
    for p in PLANS.values():
        d = dict(p)
        d["price_label"] = format_money(p["price"])
        d["compare_at_label"] = format_money(p["compare_at"]) if p["compare_at"] else ""
        save = (p["compare_at"] or 0) - p["price"]
        d["save_amount"] = save if save > 0 else 0
        d["save_label"] = format_money(save) if save > 0 else ""
        d["save_percent"] = round(save * 100 / p["compare_at"]) if save > 0 else 0
        d["monthly_equiv_label"] = format_money(round(p["price"] / p["months"]))
        out.append(d)
    return out


# ── Time helpers ──────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def add_months(dt: datetime, months: int) -> datetime:
    """Same day-of-month *months* later (clamped: Jan 31 + 1 month = Feb 28/29)."""
    m = dt.month - 1 + months
    year, month = dt.year + m // 12, m % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


# ── This device ───────────────────────────────────────────────────────────────

def _machine_guid() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                            0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
            return str(winreg.QueryValueEx(k, "MachineGuid")[0])
    except OSError:
        return ""


def device_id() -> str:
    """Stable, anonymous id of this PC (a hash — the raw machine id never leaves)."""
    raw = _machine_guid() or f"{uuid.getnode():x}-{platform.node()}"
    return "dev_" + hashlib.sha256(("officeaxe:" + raw).encode("utf-8")).hexdigest()[:20]


def device_info() -> dict:
    os_name = f"{platform.system()} {platform.release()}".strip() or "Unknown OS"
    try:        # Windows 11 still reports release "10"; tell them apart by build
        if sys.platform == "win32" and int(platform.version().split(".")[2]) >= 22000:
            os_name = "Windows 11"
    except (IndexError, ValueError):
        pass
    return {"id": device_id(), "name": platform.node() or "This PC", "os": os_name}


# ── Payment gateway seam ──────────────────────────────────────────────────────

class PaymentGateway:
    """Interface for a payment provider. See the module docstring."""
    id = "base"
    name = "Payment gateway"
    live = False          # True when real money moves

    def is_available(self) -> bool:
        return False

    def create_payment(self, order: dict) -> dict:
        raise NotImplementedError

    def verify_payment(self, payment_id: str, order: dict) -> dict:
        raise NotImplementedError


class PlaceholderGateway(PaymentGateway):
    """Stand-in until the real gateway is ready.

    sandbox=True  → every payment is approved instantly, nothing is charged
                    (reference ``TEST-…``); the UI labels this "Test mode".
    sandbox=False → no payments can be made yet.
    """
    id = "placeholder"
    name = "Payment gateway (coming soon)"
    live = False

    def __init__(self, sandbox: bool = True):
        self.sandbox = bool(sandbox)

    def is_available(self) -> bool:
        return self.sandbox

    def create_payment(self, order: dict) -> dict:
        if not self.sandbox:
            return {"status": "unavailable", "payment_id": "",
                    "message": "Online payment is not available yet."}
        return {"status": "pending", "payment_id": "TEST-" + secrets.token_hex(6).upper(),
                "redirect_url": ""}

    def verify_payment(self, payment_id: str, order: dict) -> dict:
        if not self.sandbox:
            return {"status": "failed", "message": "Online payment is not available yet."}
        return {"status": "paid", "reference": payment_id, "method": order.get("method", "")}


def sandbox_enabled() -> bool:
    v = os.environ.get(ENV_SANDBOX, "").strip().lower()
    return v not in ("0", "false", "no", "off")


def make_gateway() -> PaymentGateway:
    """The gateway the app uses. Return the real one here once it is integrated."""
    return PlaceholderGateway(sandbox=sandbox_enabled())


# ── License backend seam ──────────────────────────────────────────────────────

class LicenseError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def _app_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "OfficeAxe")


def default_store_path() -> str:
    return os.environ.get(ENV_FILE) or os.path.join(_app_data_dir(), "subscription.json")


def _empty_record() -> dict:
    return {"version": 1, "subscription": None, "devices": [], "payments": []}


class LicenseBackend:
    """Where the subscription record lives. See the module docstring."""
    remote = False

    def load(self) -> dict:
        raise NotImplementedError

    def save(self, record: dict) -> None:
        raise NotImplementedError

    def verify_key(self, key: str, record: dict) -> bool:
        """True when *key* belongs to a paid subscription."""
        raise NotImplementedError


class LocalLicenseBackend(LicenseBackend):
    """Signed JSON file on this PC (placeholder for the license server).

    The HMAC only catches casual edits; a local file can never be fully
    trusted, which is why the real check belongs on a server.
    """
    remote = False

    def __init__(self, path: Optional[str] = None, secret: Optional[str] = None):
        self.path = path or default_store_path()
        self._key = hashlib.sha256(("oa-license:" + (secret or device_id())).encode()).digest()
        self._lock = threading.Lock()

    def _sign(self, body: dict) -> str:
        data = json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hmac.new(self._key, data.encode("utf-8"), hashlib.sha256).hexdigest()

    def load(self) -> dict:
        with self._lock:
            if not os.path.isfile(self.path):
                return _empty_record()
            try:
                with open(self.path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                raise LicenseError("The subscription file could not be read.", "corrupt")
            body, sig = doc.get("body"), doc.get("sig", "")
            if not isinstance(body, dict) or not hmac.compare_digest(self._sign(body), str(sig)):
                raise LicenseError("The subscription file could not be verified on this PC.", "tampered")
            rec = _empty_record()
            rec.update(body)
            return rec

    def save(self, record: dict) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            doc = {"body": record, "sig": self._sign(record)}
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    def verify_key(self, key: str, record: dict) -> bool:
        sub = record.get("subscription") or {}
        return bool(sub.get("license_key")) and hmac.compare_digest(
            normalize_key(sub["license_key"]), normalize_key(key))


def new_license_key() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "OAXE-" + "-".join(groups)


def normalize_key(key: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(key or "").upper())


def mask_key(key: str) -> str:
    parts = str(key or "").split("-")
    return "-".join(parts[:1] + ["••••"] * max(0, len(parts) - 2) + parts[-1:]) if len(parts) > 2 else key


# ── Manager ───────────────────────────────────────────────────────────────────

class SubscriptionManager:
    """Subscription logic shared by the bridge and the tests."""

    def __init__(self, backend: Optional[LicenseBackend] = None,
                 gateway: Optional[PaymentGateway] = None,
                 device: Optional[dict] = None,
                 now: Optional[Callable[[], datetime]] = None,
                 public_key: Optional[bytes] = None,
                 revoked: Optional[set] = None):
        self.backend = backend or LocalLicenseBackend()
        # signed keys (license_keys.py); tests pass their own key pair / list
        self._public_key = public_key
        self._revoked = revoked
        self.gateway = gateway or make_gateway()
        self.device = device or device_info()
        self._now = now or _utcnow
        self._checkouts: dict = {}
        self._lock = threading.RLock()
        self._active_cache = (0.0, False)

    # ── reading ──────────────────────────────────────────────────────────────
    def _load(self) -> tuple:
        try:
            return self.backend.load(), ""
        except LicenseError as exc:
            return _empty_record(), str(exc)

    def state(self) -> dict:
        """Everything the subscription page needs."""
        with self._lock:
            rec, problem = self._load()
            now = self._now()
            sub = rec.get("subscription") or None
            devices = rec.get("devices") or []
            this_id = self.device["id"]
            registered = any(d.get("id") == this_id for d in devices)

            limit = self._limit(rec)
            status, expires, days_left, plan = "none", None, 0, None
            key_info, key_problem = None, ""
            if sub and sub.get("source") == "key":
                # re-checked every time: catches a key withdrawn in an app update
                try:
                    key_info = self._check_key(sub.get("license_key", ""), check_expiry=False)
                except license_keys.KeyError_ as exc:
                    key_problem = str(exc)
            if sub:
                expires = _parse(sub.get("expires_at"))
                plan = PLANS.get(sub.get("plan"))
                if key_info and not plan:
                    plan = {"id": "key", "name": key_info["type_label"] + " access",
                            "period_label": "", "key": True}
                if key_problem:
                    status = "revoked"
                elif expires and expires > now:
                    secs = (expires - now).total_seconds()
                    days_left = int(-(-secs // 86400))       # round up
                    status = "expiring" if days_left <= EXPIRING_SOON_DAYS else "active"
                else:
                    status = "expired"

            access = status in ("active", "expiring") and registered
            reason = ""
            if problem:
                reason = "invalid"
            elif status in ("active", "expiring") and not registered:
                reason = "device_limit" if len(devices) >= limit else "device_not_registered"
            elif status in ("none", "expired", "revoked"):
                reason = status

            started = _parse(sub.get("period_start")) if sub else None
            total_days = max(1, int(round((expires - started).total_seconds() / 86400))) \
                if (expires and started) else 0
            dev_out = []
            for d in sorted(devices, key=lambda d: d.get("added_at", "")):
                x = dict(d)
                x["current"] = d.get("id") == this_id
                dev_out.append(x)
            payments = sorted(rec.get("payments") or [], key=lambda p: p.get("created_at", ""),
                              reverse=True)
            for p in payments:
                p["amount_label"] = format_money(int(p.get("amount") or 0))
                p["plan_name"] = (PLANS.get(p.get("plan")) or {}).get("name", p.get("plan", ""))

            return {
                "ok": True,
                "access": access,
                "reason": reason,
                "problem": problem or key_problem,
                "status": status,
                "plan": dict(plan) if plan else None,
                "expires_at": _iso(expires) if expires else "",
                "period_start": _iso(started) if started else "",
                "days_left": days_left,
                "period_days": total_days,
                "license_key": (sub or {}).get("license_key", ""),
                "license_key_masked": mask_key((sub or {}).get("license_key", "")),
                "customer": (sub or {}).get("customer") or {},
                "devices": dev_out,
                "device_count": len(devices),
                "max_devices": limit,
                "plan_devices": MAX_DEVICES,
                "key": ({"type": key_info["type"], "type_label": key_info["type_label"],
                         "holder": key_info["holder"], "key_id": key_info["key_id"]}
                        if key_info else None),
                "this_device": dict(self.device, registered=registered),
                "payments": payments,
                "plans": plan_list(),
                "methods": PAYMENT_METHODS,
                "currency": CURRENCY,
                "gateway": {
                    "id": self.gateway.id, "name": self.gateway.name,
                    "available": self.gateway.is_available(),
                    "live": bool(self.gateway.live),
                    "sandbox": bool(getattr(self.gateway, "sandbox", False)) and not self.gateway.live,
                },
                "server": {"remote": bool(self.backend.remote)},
                "now": _iso(now),
            }

    def has_access(self, max_age: float = 15.0) -> bool:
        """Cached check used to gate every bridge call."""
        t, ok = self._active_cache
        if time.monotonic() - t < max_age:
            return ok
        ok = bool(self.state().get("access"))
        self._active_cache = (time.monotonic(), ok)
        return ok

    def _check_key(self, key: str, check_expiry: bool = True) -> dict:
        return license_keys.parse(key, self._public_key, now=self._now(),
                                  revoked=self._revoked, check_expiry=check_expiry)

    @staticmethod
    def _limit(rec: dict) -> int:
        sub = rec.get("subscription") or {}
        return int(sub.get("max_devices") or MAX_DEVICES)

    def _invalidate(self) -> None:
        self._active_cache = (0.0, False)

    # ── checkout ─────────────────────────────────────────────────────────────
    def start_checkout(self, plan_id: str, method: str, customer: Optional[dict] = None) -> dict:
        plan = PLANS.get(str(plan_id or ""))
        if not plan:
            return {"ok": False, "error": "Choose a plan."}
        if method not in _METHOD_IDS:
            return {"ok": False, "error": "Choose a payment method."}
        cust = _clean_customer(customer or {})
        bad = _validate_customer(cust)
        if bad:
            return {"ok": False, "error": bad["error"], "field": bad["field"]}
        if not self.gateway.is_available():
            return {"ok": False, "error": "Online payment is not available yet. It will open here "
                    "as soon as the payment gateway is connected.", "gateway_unavailable": True}
        order = {
            "order_id": "ORD-" + self._now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper(),
            "plan": plan["id"], "amount": plan["price"], "currency": CURRENCY,
            "method": method, "customer": cust, "device_id": self.device["id"],
            "created_at": _iso(self._now()),
        }
        res = self.gateway.create_payment(order) or {}
        if res.get("status") not in ("pending", "paid"):
            return {"ok": False, "error": res.get("message") or "The payment could not be started.",
                    "gateway_unavailable": res.get("status") == "unavailable"}
        checkout_id = "chk_" + secrets.token_hex(8)
        with self._lock:
            self._checkouts[checkout_id] = {"order": order, "payment_id": res.get("payment_id", "")}
        return {"ok": True, "checkout_id": checkout_id, "order_id": order["order_id"],
                "amount": plan["price"], "amount_label": format_money(plan["price"]),
                "plan": plan["id"], "redirect_url": res.get("redirect_url") or "",
                "sandbox": bool(getattr(self.gateway, "sandbox", False)) and not self.gateway.live}

    def confirm_checkout(self, checkout_id: str) -> dict:
        """Ask the gateway whether the payment went through; apply it once."""
        with self._lock:
            chk = self._checkouts.get(str(checkout_id or ""))
            if not chk:
                return {"ok": False, "error": "This payment session has ended. Please start again."}
            order = chk["order"]
            res = self.gateway.verify_payment(chk["payment_id"], order) or {}
            st = res.get("status")
            if st == "pending":
                return {"ok": True, "paid": False, "pending": True}
            if st != "paid":
                self._checkouts.pop(checkout_id, None)
                self._record_payment(order, "failed", res.get("reference") or chk["payment_id"])
                return {"ok": False, "error": res.get("message") or "The payment did not go through. "
                        "No money was taken."}
            self._checkouts.pop(checkout_id, None)
            try:
                receipt = self._apply_payment(order, res.get("reference") or chk["payment_id"],
                                              res.get("method") or order["method"])
            except LicenseError as exc:
                return {"ok": False, "error": str(exc), "code": exc.code}
            return {"ok": True, "paid": True, "receipt": receipt, "state": self.state()}

    def cancel_checkout(self, checkout_id: str) -> dict:
        with self._lock:
            self._checkouts.pop(str(checkout_id or ""), None)
        return {"ok": True}

    def _record_payment(self, order: dict, status: str, reference: str) -> None:
        try:
            rec = self.backend.load()
        except LicenseError:
            return
        rec.setdefault("payments", []).append(self._payment_row(order, status, reference, "", ""))
        self.backend.save(rec)

    def _payment_row(self, order, status, reference, start, end) -> dict:
        return {
            "id": order["order_id"], "created_at": _iso(self._now()), "plan": order["plan"],
            "amount": order["amount"], "currency": order["currency"], "method": order["method"],
            "method_name": next((m["name"] for m in PAYMENT_METHODS if m["id"] == order["method"]),
                                order["method"]),
            "status": status, "reference": reference, "gateway": self.gateway.id,
            "test": not self.gateway.live, "period_start": start, "period_end": end,
        }

    def _apply_payment(self, order: dict, reference: str, method: str) -> dict:
        rec = self.backend.load()
        now = self._now()
        plan = PLANS[order["plan"]]
        sub = rec.get("subscription") or None
        cur_exp = _parse(sub.get("expires_at")) if sub else None
        start = cur_exp if (cur_exp and cur_exp > now) else now
        end = add_months(start, plan["months"])
        if not sub:
            sub = {"id": "sub_" + secrets.token_hex(6), "license_key": new_license_key(),
                   "created_at": _iso(now)}
        if not (cur_exp and cur_exp > now):
            sub["period_start"] = _iso(now)
        sub.update({"plan": plan["id"], "expires_at": _iso(end),
                    "customer": order.get("customer") or sub.get("customer") or {}})
        rec["subscription"] = sub
        order = dict(order, method=method or order["method"])
        rec.setdefault("payments", []).append(self._payment_row(order, "paid", reference,
                                                                _iso(start), _iso(end)))
        self._register(rec, allow_over_limit=False)
        self.backend.save(rec)
        self._invalidate()
        return {"order_id": order["order_id"], "reference": reference, "plan": plan["name"],
                "amount_label": format_money(plan["price"]), "expires_at": _iso(end),
                "license_key": sub["license_key"], "test": not self.gateway.live}

    # ── devices ──────────────────────────────────────────────────────────────
    def _register(self, rec: dict, allow_over_limit: bool = False) -> bool:
        """Add / refresh this PC in *rec*. False when the device limit is full."""
        devices = rec.setdefault("devices", [])
        now = _iso(self._now())
        for d in devices:
            if d.get("id") == self.device["id"]:
                d.update(name=self.device["name"], os=self.device["os"], last_seen=now)
                return True
        if len(devices) >= self._limit(rec) and not allow_over_limit:
            return False
        devices.append({"id": self.device["id"], "name": self.device["name"],
                        "os": self.device["os"], "added_at": now, "last_seen": now})
        return True

    def activate_this_device(self) -> dict:
        with self._lock:
            try:
                rec = self.backend.load()
            except LicenseError as exc:
                return {"ok": False, "error": str(exc)}
            if not self._register(rec):
                return {"ok": False, "error": f"This subscription is already used on {self._limit(rec)} "
                        "devices. Remove one to use Office Axe here.", "device_limit": True}
            self.backend.save(rec)
            self._invalidate()
            return {"ok": True, "state": self.state()}

    def remove_device(self, dev_id: str) -> dict:
        with self._lock:
            try:
                rec = self.backend.load()
            except LicenseError as exc:
                return {"ok": False, "error": str(exc)}
            before = len(rec.get("devices") or [])
            rec["devices"] = [d for d in rec.get("devices") or [] if d.get("id") != dev_id]
            if len(rec["devices"]) == before:
                return {"ok": False, "error": "That device is not on this subscription."}
            self.backend.save(rec)
            self._invalidate()
            return {"ok": True, "state": self.state()}

    def rename_device(self, dev_id: str, name: str) -> dict:
        name = str(name or "").strip()[:60]
        if not name:
            return {"ok": False, "error": "Enter a device name."}
        with self._lock:
            try:
                rec = self.backend.load()
            except LicenseError as exc:
                return {"ok": False, "error": str(exc)}
            for d in rec.get("devices") or []:
                if d.get("id") == dev_id:
                    d["name"] = name
                    self.backend.save(rec)
                    return {"ok": True, "state": self.state()}
            return {"ok": False, "error": "That device is not on this subscription."}

    def activate_key(self, key: str) -> dict:
        """Use an existing subscription on this PC (license key from another PC)."""
        if license_keys.looks_signed(key):
            return self._activate_signed_key(key)
        if len(normalize_key(key)) < 16:
            return {"ok": False, "error": "Enter the license key from your receipt (it starts with OAXE-)."}
        with self._lock:
            try:
                rec = self.backend.load()
            except LicenseError as exc:
                return {"ok": False, "error": str(exc)}
            if not self.backend.verify_key(key, rec):
                if not self.backend.remote:
                    return {"ok": False, "error": "This key can't be checked yet. Activating a "
                            "subscription on another PC starts when the license server goes live "
                            "with online payments.", "server_needed": True}
                return {"ok": False, "error": "That license key was not found."}
            if not self._register(rec):
                return {"ok": False, "error": f"This subscription is already used on {self._limit(rec)} "
                        "devices. Remove one first.", "device_limit": True}
            self.backend.save(rec)
            self._invalidate()
            return {"ok": True, "state": self.state()}

    def _activate_signed_key(self, key: str) -> dict:
        """A key made with the admin dashboard: checked offline, works on any PC."""
        try:
            info = self._check_key(key)
        except license_keys.KeyError_ as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        with self._lock:
            try:
                rec = self.backend.load()
            except LicenseError as exc:
                return {"ok": False, "error": str(exc)}
            now = self._now()
            sub = rec.get("subscription") or None
            same = bool(sub) and sub.get("key_id") == info["key_id"]
            cur_exp = _parse(sub.get("expires_at")) if sub else None
            if sub and not same and cur_exp and cur_exp > now and cur_exp >= info["expires_at"]:
                return {"ok": False, "error": "Your current subscription already lasts longer "
                        f"(until {cur_exp:%d %b %Y}), so this key isn’t needed on this PC."}
            if not same:
                rec["subscription"] = {
                    "id": "key_" + info["key_id"], "source": "key",
                    "license_key": license_keys.canonical(key),
                    "key_id": info["key_id"], "key_type": info["type"],
                    "plan": info["plan"], "max_devices": info["max_devices"],
                    "customer": {"name": info["holder"]},
                    "created_at": _iso(now), "period_start": _iso(now),
                    "expires_at": _iso(info["expires_at"]),
                }
                # devices of the previous subscription don't count against this key
                rec["devices"] = []
            if not self._register(rec):
                return {"ok": False, "error": f"This key is already used on {self._limit(rec)} "
                        "device(s) on this PC's list.", "device_limit": True}
            self.backend.save(rec)
            self._invalidate()
            return {"ok": True, "state": self.state()}


# ── customer details ──────────────────────────────────────────────────────────

def _clean_customer(c: dict) -> dict:
    return {
        "name": str(c.get("name") or "").strip()[:80],
        "email": str(c.get("email") or "").strip()[:120],
        "phone": str(c.get("phone") or "").strip().translate(_BN_DIGITS)[:24],
    }


def _validate_customer(c: dict) -> Optional[dict]:
    if len(c["name"]) < 2:
        return {"field": "name", "error": "Enter your full name."}
    if not _EMAIL_RE.match(c["email"]):
        return {"field": "email", "error": "Enter a valid email address for the receipt."}
    if not _PHONE_RE.match(c["phone"]):
        return {"field": "phone", "error": "Enter a valid mobile number, e.g. 01XXXXXXXXX."}
    return None
