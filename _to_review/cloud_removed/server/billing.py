"""
billing.py — Monthly subscription billing.

The server talks to the payment gateway only through BillingProvider, so a
different gateway (Paddle, PayPal, Toss…) can be added as another subclass
without touching the API routes.

StripeProvider uses Stripe Checkout (subscription mode) for sign-up, the
Stripe customer portal for cancel / card changes, and webhooks to keep the
local subscription status in sync.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings

# Subscription states that unlock paid features
ACTIVE_STATUSES = {"active", "trialing"}


@dataclass
class SubscriptionUpdate:
    """A normalised subscription change parsed from a webhook."""
    customer_id: str
    subscription_id: str
    status: str
    current_period_end: float     # 0 = unknown, keep the stored value
    user_id: int | None = None    # set when the event carries our own user id
    event_id: str = ""            # provider event id, for idempotency
    event_created: float = 0.0    # provider event time, to ignore stale events
    office_id: int | None = None  # set when the subscription belongs to an office


class BillingError(Exception):
    pass


class BillingProvider:
    name = "none"

    def create_checkout(self, user_id: int, email: str, customer_id: str | None,
                        office_id: int | None = None) -> tuple[str, str]:
        """Return (checkout_url, customer_id). With office_id the subscription is the office's."""
        raise BillingError("Billing is not configured on this server")

    def create_portal(self, customer_id: str) -> str:
        raise BillingError("Billing is not configured on this server")

    def parse_webhook(self, payload: bytes, signature: str) -> SubscriptionUpdate | None:
        raise BillingError("Billing is not configured on this server")


class StripeProvider(BillingProvider):
    name = "stripe"

    def __init__(self, settings: Settings):
        import stripe  # imported lazily so the server runs without it when billing is off
        self._stripe = stripe
        self._s = settings
        stripe.api_key = settings.stripe_secret_key

    def create_checkout(self, user_id, email, customer_id, office_id=None):
        stripe = self._stripe
        meta = {"user_id": str(user_id)}
        if office_id:
            meta["office_id"] = str(office_id)
        if not customer_id:
            cust = stripe.Customer.create(email=email, metadata=meta)
            customer_id = cust["id"]
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            client_reference_id=str(user_id),
            line_items=[{"price": self._s.stripe_price_id, "quantity": 1}],
            subscription_data={"metadata": meta},
            success_url=f"{self._s.public_url}/billing/success",
            cancel_url=f"{self._s.public_url}/billing/cancel",
        )
        return session["url"], customer_id

    def create_portal(self, customer_id):
        portal = self._stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{self._s.public_url}/billing/success",
        )
        return portal["url"]

    def parse_webhook(self, payload, signature):
        stripe = self._stripe
        if not self._s.stripe_webhook_secret:
            raise BillingError("STRIPE_WEBHOOK_SECRET is not set")
        try:
            event = stripe.Webhook.construct_event(payload, signature, self._s.stripe_webhook_secret)
        except Exception as exc:  # bad signature or malformed payload
            raise BillingError(f"Invalid webhook: {exc}") from exc

        event = _plain(event)
        etype = event["type"]
        obj = _plain(event["data"]["object"])
        upd = None
        if etype == "checkout.session.completed":
            sub_id = obj.get("subscription")
            if not sub_id:
                return None
            sub = _plain(stripe.Subscription.retrieve(sub_id))
            upd = _from_subscription(sub, obj.get("client_reference_id"))
        elif etype in ("customer.subscription.created",
                       "customer.subscription.updated",
                       "customer.subscription.deleted"):
            upd = _from_subscription(obj, None)
            if etype == "customer.subscription.deleted":
                upd.status = "canceled"
        elif etype == "invoice.payment_failed":
            upd = _from_failed_invoice(obj)
        if upd is None:
            return None
        upd.event_id = str(event.get("id") or "")
        upd.event_created = float(event.get("created") or 0)
        return upd


def _plain(obj) -> dict:
    """Newer stripe-python objects are not dicts; convert them to plain dicts."""
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def _from_subscription(sub, ref_user_id) -> SubscriptionUpdate:
    meta = sub.get("metadata") or {}
    uid = ref_user_id or meta.get("user_id")
    # current_period_end moved onto subscription items in newer API versions
    period_end = sub.get("current_period_end")
    if not period_end:
        items = (sub.get("items") or {}).get("data") or []
        if items:
            period_end = items[0].get("current_period_end")
    oid = meta.get("office_id")
    return SubscriptionUpdate(
        customer_id=sub.get("customer") or "",
        subscription_id=sub.get("id") or "",
        status=sub.get("status") or "none",
        current_period_end=float(period_end or 0),
        user_id=int(uid) if uid and str(uid).isdigit() else None,
        office_id=int(oid) if oid and str(oid).isdigit() else None,
    )


def _from_failed_invoice(inv) -> SubscriptionUpdate | None:
    """invoice.payment_failed → the subscription is past due (grace period starts)."""
    sub_id = inv.get("subscription")
    if not sub_id:   # newer API versions nest it under parent.subscription_details
        parent = inv.get("parent") or {}
        sub_id = (parent.get("subscription_details") or {}).get("subscription")
    if isinstance(sub_id, dict):
        sub_id = sub_id.get("id")
    if not sub_id:
        return None  # a one-off invoice, not our subscription
    meta = ((inv.get("parent") or {}).get("subscription_details") or {}).get("metadata") or {}
    uid = meta.get("user_id")
    oid = meta.get("office_id")
    return SubscriptionUpdate(
        customer_id=inv.get("customer") or "",
        subscription_id=str(sub_id),
        status="past_due",
        current_period_end=0.0,
        user_id=int(uid) if uid and str(uid).isdigit() else None,
        office_id=int(oid) if oid and str(oid).isdigit() else None,
    )


def make_provider(settings: Settings) -> BillingProvider:
    if settings.billing_configured:
        return StripeProvider(settings)
    return BillingProvider()
