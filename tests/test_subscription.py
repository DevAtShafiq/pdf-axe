# -*- coding: utf-8 -*-
"""Subscription: plans, sandbox checkout, expiry, device limit, bridge gate."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subscription as sub  # noqa: E402

CUST = {"name": "Rahim Uddin", "email": "rahim@example.com", "phone": "01712345678"}


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def _dev(i):
    return {"id": f"dev_{i}", "name": f"PC-{i}", "os": "Windows 11"}


@pytest.fixture
def clock():
    return Clock(datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc))


def _mgr(tmp_path, clock, device=None, sandbox=True):
    backend = sub.LocalLicenseBackend(str(tmp_path / "sub.json"), secret="test")
    return sub.SubscriptionManager(backend=backend, gateway=sub.PlaceholderGateway(sandbox),
                                   device=device or _dev(1), now=clock)


def _buy(m, plan="monthly", method="bkash"):
    r = m.start_checkout(plan, method, CUST)
    assert r["ok"], r
    c = m.confirm_checkout(r["checkout_id"])
    assert c["ok"] and c["paid"], c
    return c


def test_plans_and_prices():
    plans = {p["id"]: p for p in sub.plan_list()}
    assert plans["monthly"]["price"] == 999 and plans["monthly"]["price_label"] == "৳999"
    y = plans["yearly"]
    assert y["price_label"] == "৳9,999" and y["compare_at_label"] == "৳11,988"
    assert y["save_label"] == "৳1,989" and y["save_percent"] == 17


def test_no_subscription_blocks_access(tmp_path, clock):
    s = _mgr(tmp_path, clock).state()
    assert s["access"] is False and s["status"] == "none" and s["reason"] == "none"
    assert s["max_devices"] == 2 and s["gateway"]["sandbox"] is True


def test_sandbox_checkout_activates_and_records_payment(tmp_path, clock):
    m = _mgr(tmp_path, clock)
    c = _buy(m, "monthly")
    s = c["state"]
    assert s["access"] and s["status"] == "active"
    assert s["expires_at"] == "2026-02-28T10:00:00Z"          # Jan 31 + 1 month, clamped
    assert c["receipt"]["license_key"].startswith("OAXE-") and c["receipt"]["test"] is True
    assert len(s["payments"]) == 1 and s["payments"][0]["status"] == "paid"
    assert s["payments"][0]["amount"] == 999 and s["payments"][0]["reference"].startswith("TEST-")
    assert s["device_count"] == 1 and s["devices"][0]["current"]


def test_renewal_extends_from_current_expiry(tmp_path, clock):
    m = _mgr(tmp_path, clock)
    _buy(m, "monthly")
    clock.dt += timedelta(days=10)
    s = _buy(m, "yearly")["state"]
    assert s["expires_at"] == "2027-02-28T10:00:00Z"
    assert s["plan"]["id"] == "yearly" and len(s["payments"]) == 2


def test_expiry_and_expiring_soon(tmp_path, clock):
    m = _mgr(tmp_path, clock)
    _buy(m)
    clock.dt = datetime(2026, 2, 23, 10, 0, tzinfo=timezone.utc)
    s = m.state()
    assert s["status"] == "expiring" and s["days_left"] == 5 and s["access"]
    clock.dt = datetime(2026, 2, 28, 10, 0, 1, tzinfo=timezone.utc)
    s = m.state()
    assert s["status"] == "expired" and not s["access"] and s["reason"] == "expired"


def test_checkout_validation(tmp_path, clock):
    m = _mgr(tmp_path, clock)
    assert not m.start_checkout("weekly", "bkash", CUST)["ok"]
    assert not m.start_checkout("monthly", "paypal", CUST)["ok"]
    r = m.start_checkout("monthly", "card", dict(CUST, email="nope"))
    assert not r["ok"] and r["field"] == "email"


def test_bangla_digits_in_phone(tmp_path, clock):
    m = _mgr(tmp_path, clock)
    r = m.start_checkout("monthly", "bkash", dict(CUST, phone="০১৭১২৩৪৫৬৭৮"))
    assert r["ok"], r
    assert m._checkouts[r["checkout_id"]]["order"]["customer"]["phone"] == "01712345678"


def test_checkout_confirms_only_once(tmp_path, clock):
    m = _mgr(tmp_path, clock)
    r = m.start_checkout("monthly", "bkash", CUST)
    assert m.confirm_checkout(r["checkout_id"])["paid"]
    assert not m.confirm_checkout(r["checkout_id"])["ok"]
    assert len(m.state()["payments"]) == 1


def test_gateway_unavailable_without_sandbox(tmp_path, clock):
    m = _mgr(tmp_path, clock, sandbox=False)
    assert m.state()["gateway"]["available"] is False
    r = m.start_checkout("monthly", "bkash", CUST)
    assert not r["ok"] and r["gateway_unavailable"]


def test_device_limit_of_two(tmp_path, clock):
    """Buy one, get one: one subscription works on 2 PCs."""
    path = str(tmp_path / "sub.json")
    owner = _mgr(tmp_path, clock)
    _buy(owner)
    backend = sub.LocalLicenseBackend(path, secret="test")
    rec = backend.load()
    rec["devices"].append(dict(_dev(2), added_at="2026-01-31T10:00:00Z", last_seen=""))
    backend.save(rec)

    third = sub.SubscriptionManager(backend=backend, gateway=sub.PlaceholderGateway(True),
                                    device=_dev(3), now=clock)
    s = third.state()
    assert s["max_devices"] == 2
    assert s["status"] == "active" and not s["access"] and s["reason"] == "device_limit"
    assert not third.activate_this_device()["ok"]
    key = s["license_key"]
    assert third.activate_key(key)["device_limit"]

    assert third.remove_device("dev_2")["ok"]
    r = third.activate_key(key)
    assert r["ok"] and r["state"]["access"] and r["state"]["device_count"] == 2


def test_unknown_key_needs_server(tmp_path, clock):
    r = _mgr(tmp_path, clock).activate_key("OAXE-AAAA-BBBB-CCCC-DDDD")
    assert not r["ok"] and r["server_needed"]


def test_tampered_file_is_rejected(tmp_path, clock):
    m = _mgr(tmp_path, clock)
    _buy(m)
    p = tmp_path / "sub.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["body"]["subscription"]["expires_at"] = "2099-01-01T00:00:00Z"
    p.write_text(json.dumps(doc), encoding="utf-8")
    s = m.state()
    assert not s["access"] and s["reason"] == "invalid"


def test_bridge_gate(tmp_path, monkeypatch, clock):
    monkeypatch.setenv(sub.ENV_FILE, str(tmp_path / "bridge.json"))
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    assert "need_subscription" not in b.get_home()            # gate is off by default (tests)
    b.enforce_subscription = True
    r = b.get_home()
    assert r["ok"] is False and r["need_subscription"]
    assert b.subscription_state()["ok"]                       # subscription_* stay open
    ck = b.subscription_checkout("monthly", "nagad", CUST)
    assert ck["ok"]
    assert b.subscription_confirm(ck["checkout_id"])["paid"]
    assert b.get_home().get("need_subscription") is None
