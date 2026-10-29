# -*- coding: utf-8 -*-
"""
subscription_bridge.py — subscription / payment methods of the JS bridge.

Mixed into SFMBridge. Besides the ``subscription_*`` methods it installs the
access gate: when ``enforce_subscription`` is on (main_webview turns it on for
the real app window), every other bridge method answers
``{ok: false, need_subscription: true}`` until the subscription is active on
this PC. Tests build SFMBridge directly and leave the gate off.
"""
from __future__ import annotations

import functools
import inspect
import threading
import webbrowser

import subscription as _sub

# Callable from JS without an active subscription.
_OPEN_METHODS = {"log_js", "get_settings", "save_settings", "set_window",
                 "shell_deliver", "shell_take_pending"}   # hand-over only; the tools stay gated


def _gated(name, fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if getattr(self, "enforce_subscription", False) and not self._subscription().has_access():
            return {"ok": False, "need_subscription": True,
                    "error": "An active Office Axe subscription is needed."}
        return fn(self, *args, **kwargs)
    wrapper._oa_gated = True
    return wrapper


class SubscriptionBridgeMixin:
    enforce_subscription = False
    _sub_mgr = None
    _sub_lock = threading.Lock()

    def __init_subclass__(cls, **kwargs):
        """Wrap every public bridge method of the final class with the gate."""
        super().__init_subclass__(**kwargs)
        for name in dir(cls):
            if name.startswith("_") or name.startswith("subscription_") or name in _OPEN_METHODS:
                continue
            raw = inspect.getattr_static(cls, name, None)
            if isinstance(raw, (staticmethod, classmethod, property, type)):
                continue
            fn = getattr(cls, name, None)
            if inspect.isfunction(fn) and not getattr(fn, "_oa_gated", False):
                setattr(cls, name, _gated(name, fn))

    def _subscription(self) -> "_sub.SubscriptionManager":
        if self._sub_mgr is None:
            with self._sub_lock:
                if self._sub_mgr is None:
                    self._sub_mgr = _sub.SubscriptionManager()
        return self._sub_mgr

    # ── JS API ───────────────────────────────────────────────────────────────
    def subscription_state(self) -> dict:
        """Status, plan, expiry, devices, payment history, plans and gateway info."""
        try:
            return self._subscription().state()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def subscription_checkout(self, plan: str, method: str, customer: dict = None) -> dict:
        """Start a payment. {ok, checkout_id, redirect_url, sandbox} or {ok:false, error, field?}."""
        res = self._subscription().start_checkout(plan, method, customer or {})
        if res.get("ok") and res.get("redirect_url"):
            try:
                webbrowser.open_new_tab(res["redirect_url"])
            except Exception:
                pass
        return res

    def subscription_confirm(self, checkout_id: str) -> dict:
        """Check a started payment. {ok, paid, receipt, state} / {ok, pending} / {ok:false, error}."""
        res = self._subscription().confirm_checkout(checkout_id)
        if res.get("paid"):
            self._emit("subscription_changed", res.get("state"))
        return res

    def subscription_cancel_checkout(self, checkout_id: str) -> dict:
        return self._subscription().cancel_checkout(checkout_id)

    def subscription_activate_device(self) -> dict:
        res = self._subscription().activate_this_device()
        if res.get("ok"):
            self._emit("subscription_changed", res.get("state"))
        return res

    def subscription_remove_device(self, device_id: str) -> dict:
        res = self._subscription().remove_device(device_id)
        if res.get("ok"):
            self._emit("subscription_changed", res.get("state"))
        return res

    def subscription_rename_device(self, device_id: str, name: str) -> dict:
        return self._subscription().rename_device(device_id, name)

    def subscription_activate_key(self, key: str) -> dict:
        res = self._subscription().activate_key(key)
        if res.get("ok"):
            self._emit("subscription_changed", res.get("state"))
        return res
