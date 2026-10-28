"""
subscription_bridge.py — subscription methods of the JS bridge (subscription.py).

    subscription_status()                         → {ok, state, active, plan, expires_at, devices, payments, plans, gateway}
    subscription_checkout(plan, billing)          → {ok, session_id, redirect_url, order, gateway}
    subscription_pay(session_id, payload)         → status after paying
    subscription_cancel_checkout(session_id)      → status
    subscription_remove_device(device_id)         → status
"""
from __future__ import annotations

import logging
import webbrowser

import subscription as _sub

_log = logging.getLogger("sfm.subscription")


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str, code: str = "") -> dict:
    d = {"ok": False, "error": str(msg)}
    if code:
        d["code"] = code
    return d


class SubscriptionBridgeMixin:
    _subscription_svc = None

    def _subscription(self) -> "_sub.SubscriptionService":
        if self._subscription_svc is None:
            self._subscription_svc = _sub.SubscriptionService.default()
        return self._subscription_svc

    def _sub_run(self, label, fn, *args) -> dict:
        try:
            return _ok(**fn(*args))
        except _sub.SubscriptionError as exc:
            return _err(str(exc), exc.code)
        except Exception as exc:  # noqa: BLE001
            _log.error("%s failed: %s", label, exc, exc_info=True)
            return _err(f"{label} failed: {exc}")

    def subscription_status(self) -> dict:
        return self._sub_run("Subscription status", self._subscription().status)

    def subscription_checkout(self, plan: str, billing: dict = None) -> dict:
        r = self._sub_run("Checkout", self._subscription().start_checkout, plan, billing or {})
        if r.get("ok") and r.get("redirect_url"):
            # a real gateway's hosted payment page opens in the browser
            try:
                webbrowser.open(r["redirect_url"])
            except Exception:  # noqa: BLE001
                pass
        return r

    def subscription_pay(self, session_id: str, payload: dict = None) -> dict:
        return self._sub_run("Payment", self._subscription().complete_checkout, session_id, payload or {})

    def subscription_cancel_checkout(self, session_id: str) -> dict:
        return self._sub_run("Cancel payment", self._subscription().cancel_checkout, session_id)

    def subscription_remove_device(self, device_id: str) -> dict:
        return self._sub_run("Remove device", self._subscription().remove_device, device_id)
