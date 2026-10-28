"""
subscription.py — Office Axe subscription, devices and payments.

    svc = SubscriptionService.default()
    svc.status()                          → plan, expiry, devices, payment history
    svc.start_checkout("yearly", billing) → {session_id, order, gateway}
    svc.complete_checkout(session_id, {"method": "card"}) → status
    svc.cancel_checkout(session_id)
    svc.remove_device(device_id)

Plans: Monthly ৳999 for 1 month, Yearly ৳9,999 for 1 year (was ৳11,988).
One subscription can be used on up to MAX_DEVICES devices.

Two pieces are meant to be swapped later without touching the UI:

* PaymentGateway — how money is taken. PlaceholderGateway "pays" without
  charging anyone (payments are marked test=True) until a real gateway such
  as SSLCommerz / bKash / Stripe is ready. A real one implements
  create_checkout (returns a session id and, usually, a URL to open) and
  confirm (asks the gateway whether that session was paid).
* Store — where the subscription lives. LocalStore keeps it in a JSON file on
  this PC. With a real gateway it should be a server (one account shared by
  all of the user's devices), exposing the same load / save.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import secrets
import socket
import threading
import time
from typing import Callable, Optional

CURRENCY = "BDT"
MAX_DEVICES = 5
PLANS = {
    "monthly": {"id": "monthly", "name": "Monthly", "price": 999, "months": 1, "period": "month"},
    "yearly": {"id": "yearly", "name": "Yearly", "price": 9999, "compare_at": 11988, "months": 12,
               "period": "year"},
}
CHECKOUT_TTL = 2 * 3600          # a started checkout is forgotten after this
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubscriptionError(Exception):
    def __init__(self, msg: str, code: str = "error"):
        super().__init__(msg)
        self.code = code


def plan_list() -> list:
    out = []
    for p in PLANS.values():
        q = dict(p, currency=CURRENCY)
        if p.get("compare_at"):
            q["save_pct"] = round(100 * (1 - p["price"] / p["compare_at"]))
            q["per_month"] = round(p["price"] / p["months"])
        out.append(q)
    return out


def add_months(ts: float, months: int) -> float:
    """*ts* plus whole calendar months (31 Jan + 1 month → 28/29 Feb)."""
    t = time.localtime(ts)
    y, m = divmod(t.tm_mon - 1 + months, 12)
    y += t.tm_year
    m += 1
    d = min(t.tm_mday, calendar.monthrange(y, m)[1])
    return time.mktime((y, m, d, t.tm_hour, t.tm_min, t.tm_sec, 0, 0, -1))


# ── payment gateways ────────────────────────────────────────────────────────

class PaymentGateway:
    """Base class for a payment gateway."""
    name = "base"
    label = "Payment gateway"
    test_mode = False
    methods: tuple = ()

    def create_checkout(self, order: dict) -> dict:
        """Start paying for *order* ({order_id, plan, amount, currency, billing}).
        Returns {session_id, redirect_url or ""}."""
        raise NotImplementedError

    def confirm(self, session_id: str, payload: dict) -> dict:
        """Whether *session_id* was paid: {paid, transaction_id, method, error}."""
        raise NotImplementedError

    def info(self) -> dict:
        return {"name": self.name, "label": self.label, "test_mode": self.test_mode,
                "methods": [dict(m) for m in self.methods]}


class PlaceholderGateway(PaymentGateway):
    """Stands in for the real gateway: every payment succeeds and costs nothing."""
    name = "placeholder"
    label = "Test payments (no real gateway yet)"
    test_mode = True
    methods = (
        {"id": "card", "label": "Card", "hint": "Visa, Mastercard, Amex"},
        {"id": "bkash", "label": "bKash", "hint": "Mobile wallet"},
        {"id": "nagad", "label": "Nagad", "hint": "Mobile wallet"},
        {"id": "rocket", "label": "Rocket", "hint": "Mobile wallet"},
    )

    def create_checkout(self, order: dict) -> dict:
        return {"session_id": "test_" + secrets.token_hex(8), "redirect_url": ""}

    def confirm(self, session_id: str, payload: dict) -> dict:
        method = str((payload or {}).get("method") or "card")
        if method not in {m["id"] for m in self.methods}:
            return {"paid": False, "error": "Choose a payment method"}
        return {"paid": True, "method": method, "transaction_id": "TEST-" + secrets.token_hex(5).upper()}


# ── storage ─────────────────────────────────────────────────────────────────

class LocalStore:
    """The subscription record as a JSON file (written atomically)."""

    _lock = threading.Lock()

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)


def default_store_path() -> str:
    env = os.environ.get("OFFICEAXE_SUBSCRIPTION_FILE", "").strip()
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sfm_subscription.json")


# ── this device ─────────────────────────────────────────────────────────────

def machine_id() -> str:
    """Windows' MachineGuid (stable per Windows install), '' elsewhere."""
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
            return str(winreg.QueryValueEx(k, "MachineGuid")[0])
    except Exception:
        return ""


def device_name() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "This PC"


# ── the service ─────────────────────────────────────────────────────────────

class SubscriptionService:
    def __init__(self, store, gateway: PaymentGateway, device_id: str = "", device_label: str = "",
                 now: Callable[[], float] = time.time):
        self.store = store
        self.gateway = gateway
        self._device_id = device_id
        self.device_label = device_label or device_name()
        self.now = now
        self._lock = threading.RLock()

    @classmethod
    def default(cls) -> "SubscriptionService":
        return cls(LocalStore(default_store_path()), PlaceholderGateway(), machine_id())

    # ── helpers ─────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        d = self.store.load()
        d.setdefault("subscription", None)
        d.setdefault("devices", [])
        d.setdefault("payments", [])
        d.setdefault("pending", {})
        return d

    def device_id(self, d: dict) -> str:
        if not self._device_id:
            if not d.get("local_device_id"):
                d["local_device_id"] = "dev_" + secrets.token_hex(8)
            self._device_id = d["local_device_id"]
        return self._device_id

    def _active(self, d: dict) -> bool:
        sub = d.get("subscription") or {}
        return bool(sub) and float(sub.get("expires_at") or 0) > self.now()

    def _register_device(self, d: dict) -> bool:
        """Add or refresh this device. False when the plan's device slots are full."""
        me, now = self.device_id(d), self.now()
        for dev in d["devices"]:
            if dev.get("id") == me:
                dev["last_seen"] = now
                dev["name"] = self.device_label
                return True
        if len(d["devices"]) >= MAX_DEVICES:
            return False
        d["devices"].append({"id": me, "name": self.device_label, "added_at": now, "last_seen": now})
        return True

    def _view(self, d: dict) -> dict:
        now, me = self.now(), self.device_id(d)
        sub = d.get("subscription")
        active = self._active(d)
        registered = any(x.get("id") == me for x in d["devices"])
        if not sub:
            state = "none"
        elif not active:
            state = "expired"
        elif not registered:
            state = "device_limit"
        else:
            state = "active"
        plan = PLANS.get((sub or {}).get("plan") or "")
        exp = float((sub or {}).get("expires_at") or 0)
        return {
            "state": state,
            "active": state == "active",
            "plan": dict(plan, currency=CURRENCY) if plan else None,
            "started_at": (sub or {}).get("started_at"),
            "expires_at": exp or None,
            "days_left": max(0, int((exp - now + 86399) // 86400)) if exp else 0,
            "account": d.get("account") or {},
            "device_id": me,
            "devices": [dict(x, current=x.get("id") == me) for x in d["devices"]],
            "max_devices": MAX_DEVICES,
            "payments": sorted(d["payments"], key=lambda p: -float(p.get("date") or 0)),
            "plans": plan_list(),
            "gateway": self.gateway.info(),
            "now": now,
        }

    # ── API ─────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        with self._lock:
            d = self._load()
            before = json.dumps(d, sort_keys=True)
            self.device_id(d)
            if self._active(d):
                self._register_device(d)
            # forget checkouts that were never finished
            cutoff = self.now() - CHECKOUT_TTL
            d["pending"] = {k: v for k, v in d["pending"].items() if float(v.get("created_at") or 0) > cutoff}
            if json.dumps(d, sort_keys=True) != before:
                self.store.save(d)
            return self._view(d)

    def start_checkout(self, plan_id: str, billing: Optional[dict] = None) -> dict:
        plan = PLANS.get(str(plan_id or ""))
        if not plan:
            raise SubscriptionError("Choose a plan", "bad_plan")
        b = {k: str((billing or {}).get(k) or "").strip() for k in ("name", "email", "phone")}
        if not b["name"]:
            raise SubscriptionError("Enter your name", "bad_billing")
        if not _EMAIL.match(b["email"]):
            raise SubscriptionError("Enter a valid email address", "bad_billing")
        if b["phone"] and not re.fullmatch(r"\+?[0-9 ()-]{6,20}", b["phone"]):
            raise SubscriptionError("Enter a valid phone number", "bad_billing")
        with self._lock:
            d = self._load()
            order = {"order_id": "OA-" + time.strftime("%Y%m%d", time.localtime(self.now())) + "-"
                                 + secrets.token_hex(3).upper(),
                     "plan": plan["id"], "amount": plan["price"], "currency": CURRENCY,
                     "billing": b, "created_at": self.now()}
            r = self.gateway.create_checkout(order)
            sid = str(r.get("session_id") or "")
            if not sid:
                raise SubscriptionError("The payment gateway did not start a payment", "gateway")
            order["session_id"] = sid
            d["pending"][sid] = order
            self.store.save(d)
            return {"session_id": sid, "redirect_url": r.get("redirect_url") or "", "order": order,
                    "gateway": self.gateway.info()}

    def complete_checkout(self, session_id: str, payload: Optional[dict] = None) -> dict:
        with self._lock:
            d = self._load()
            order = d["pending"].get(str(session_id or ""))
            if not order:
                raise SubscriptionError("This payment has expired. Start again.", "no_checkout")
            res = self.gateway.confirm(session_id, payload or {})
            if not res.get("paid"):
                raise SubscriptionError(res.get("error") or "The payment did not go through", "not_paid")
            plan, now = PLANS[order["plan"]], self.now()
            sub = d.get("subscription") or {}
            # paying early adds to the time left; after expiry it starts today
            start = float(sub["expires_at"]) if self._active(d) else now
            d["subscription"] = {"plan": plan["id"], "status": "active",
                                 "started_at": sub.get("started_at") if self._active(d) else now,
                                 "expires_at": add_months(start, plan["months"])}
            d["account"] = order["billing"]
            d["payments"].append({
                "id": order["order_id"], "date": now, "plan": plan["id"], "plan_name": plan["name"],
                "amount": order["amount"], "currency": order["currency"], "status": "paid",
                "method": res.get("method") or "", "gateway": self.gateway.name,
                "transaction_id": res.get("transaction_id") or "", "test": self.gateway.test_mode,
                "period_start": start, "period_end": d["subscription"]["expires_at"],
            })
            d["pending"].pop(session_id, None)
            self.device_id(d)
            self._register_device(d)
            self.store.save(d)
            return self._view(d)

    def cancel_checkout(self, session_id: str) -> dict:
        with self._lock:
            d = self._load()
            if d["pending"].pop(str(session_id or ""), None) is not None:
                self.store.save(d)
            return self._view(d)

    def remove_device(self, device_id: str) -> dict:
        """Free a device slot for another PC."""
        with self._lock:
            d = self._load()
            n = len(d["devices"])
            d["devices"] = [x for x in d["devices"] if x.get("id") != device_id]
            if len(d["devices"]) == n:
                raise SubscriptionError("That device is not on this subscription", "no_device")
            self.store.save(d)
            return self._view(d)
