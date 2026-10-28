"""subscription.py — plans, checkout with the placeholder gateway, devices, history."""
import time

import pytest

import subscription as sub

BILL = {"name": "Shafiq", "email": "shafiq@example.com", "phone": "+880 1711 000000"}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _svc(tmp_path, dev="dev-A", clock=None):
    return sub.SubscriptionService(sub.LocalStore(str(tmp_path / "sub.json")), sub.PlaceholderGateway(),
                                   device_id=dev, device_label=f"PC {dev}", now=clock or time.time)


def _buy(s, plan="monthly", method="bkash"):
    co = s.start_checkout(plan, BILL)
    return s.complete_checkout(co["session_id"], {"method": method})


def test_plans_and_prices():
    plans = {p["id"]: p for p in sub.plan_list()}
    assert plans["monthly"]["price"] == 999 and plans["monthly"]["months"] == 1
    y = plans["yearly"]
    assert (y["price"], y["compare_at"], y["months"], y["currency"]) == (9999, 11988, 12, "BDT")
    assert y["save_pct"] == 17 and y["per_month"] == 833


def test_no_subscription_is_locked(tmp_path):
    st = _svc(tmp_path).status()
    assert st["state"] == "none" and not st["active"] and st["devices"] == [] and st["payments"] == []
    assert st["gateway"]["test_mode"] and st["max_devices"] == 5


def test_buy_monthly_activates_and_records_payment(tmp_path):
    clock = Clock(time.mktime((2026, 1, 31, 10, 0, 0, 0, 0, -1)))
    s = _svc(tmp_path, clock=clock)
    st = _buy(s, "monthly", "bkash")
    assert st["active"] and st["state"] == "active" and st["plan"]["id"] == "monthly"
    assert time.localtime(st["expires_at"])[:3] == (2026, 2, 28)          # end of February
    [p] = st["payments"]
    assert p["amount"] == 999 and p["currency"] == "BDT" and p["status"] == "paid" and p["method"] == "bkash"
    assert p["test"] is True and p["transaction_id"].startswith("TEST-") and p["id"].startswith("OA-")
    assert st["account"]["email"] == BILL["email"]
    assert [d["current"] for d in st["devices"]] == [True]
    # a fresh service on the same file sees the same thing
    assert _svc(tmp_path, clock=clock).status()["active"]


def test_renewing_early_adds_to_remaining_time(tmp_path):
    clock = Clock(time.mktime((2026, 3, 10, 9, 0, 0, 0, 0, -1)))
    s = _svc(tmp_path, clock=clock)
    _buy(s, "monthly")
    clock.t += 5 * 86400
    st = _buy(s, "yearly")
    assert time.localtime(st["expires_at"])[:3] == (2027, 4, 10)
    assert [p["plan"] for p in st["payments"]] == ["yearly", "monthly"]          # newest first
    assert st["payments"][0]["amount"] == 9999


def test_expiry_locks_again(tmp_path):
    clock = Clock(time.time())
    s = _svc(tmp_path, clock=clock)
    _buy(s)
    clock.t += 40 * 86400
    st = s.status()
    assert st["state"] == "expired" and not st["active"] and st["days_left"] == 0


def test_five_devices_at_most(tmp_path):
    clock = Clock(time.time())
    _buy(_svc(tmp_path, "dev-0", clock))
    for i in range(1, 5):
        assert _svc(tmp_path, f"dev-{i}", clock).status()["active"]
    sixth = _svc(tmp_path, "dev-5", clock)
    st = sixth.status()
    assert st["state"] == "device_limit" and not st["active"] and len(st["devices"]) == 5
    # freeing a slot lets the sixth PC in
    st = sixth.remove_device("dev-2")
    assert len(st["devices"]) == 4
    st = sixth.status()
    assert st["active"] and len(st["devices"]) == 5 and any(d["current"] for d in st["devices"])
    with pytest.raises(sub.SubscriptionError):
        sixth.remove_device("nope")


def test_checkout_validation_and_cancel(tmp_path):
    s = _svc(tmp_path)
    with pytest.raises(sub.SubscriptionError, match="plan"):
        s.start_checkout("weekly", BILL)
    with pytest.raises(sub.SubscriptionError, match="email"):
        s.start_checkout("monthly", dict(BILL, email="nope"))
    with pytest.raises(sub.SubscriptionError, match="name"):
        s.start_checkout("monthly", dict(BILL, name=" "))
    co = s.start_checkout("yearly", BILL)
    assert co["order"]["amount"] == 9999 and co["redirect_url"] == ""
    with pytest.raises(sub.SubscriptionError, match="method"):
        s.complete_checkout(co["session_id"], {"method": "cash"})
    s.cancel_checkout(co["session_id"])
    with pytest.raises(sub.SubscriptionError, match="expired"):
        s.complete_checkout(co["session_id"], {"method": "card"})
    assert not s.status()["active"]


def test_real_gateway_can_be_plugged_in(tmp_path):
    class Fake(sub.PaymentGateway):
        name, label = "fake", "Fake gateway"
        methods = ({"id": "card", "label": "Card"},)
        paid = set()

        def create_checkout(self, order):
            return {"session_id": "S1", "redirect_url": "https://pay.example/S1"}

        def confirm(self, session_id, payload):
            if session_id not in self.paid:
                return {"paid": False, "error": "Not paid yet"}
            return {"paid": True, "method": "card", "transaction_id": "TX9"}

    g = Fake()
    s = sub.SubscriptionService(sub.LocalStore(str(tmp_path / "s.json")), g, device_id="d1")
    co = s.start_checkout("monthly", BILL)
    assert co["redirect_url"].startswith("https://") and not co["gateway"]["test_mode"]
    with pytest.raises(sub.SubscriptionError, match="Not paid yet"):
        s.complete_checkout("S1", {})
    g.paid.add("S1")
    st = s.complete_checkout("S1", {})
    assert st["active"] and st["payments"][0]["test"] is False and st["payments"][0]["transaction_id"] == "TX9"


def test_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICEAXE_SUBSCRIPTION_FILE", str(tmp_path / "b.json"))
    import subscription_bridge as sb
    b = sb.SubscriptionBridgeMixin()
    r = b.subscription_status()
    assert r["ok"] and r["state"] == "none"
    assert b.subscription_checkout("monthly", {"name": "x", "email": "bad"})["code"] == "bad_billing"
    co = b.subscription_checkout("yearly", BILL)
    r = b.subscription_pay(co["session_id"], {"method": "card"})
    assert r["ok"] and r["active"] and r["plan"]["id"] == "yearly"
