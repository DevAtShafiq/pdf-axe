"""
Tests for the account server and the desktop cloud client.

Run:  python -m pytest server/tests -q
"""
from __future__ import annotations

import os
import socket
import threading
import time

import pytest

os.environ["SFM_SERVER_NO_AUTOAPP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.billing import BillingError, BillingProvider, SubscriptionUpdate  # noqa: E402
from server.config import Settings  # noqa: E402


class FakeBilling(BillingProvider):
    """Stands in for Stripe: checkout returns a fake URL, webhooks are JSON."""
    name = "fake"

    def create_checkout(self, user_id, email, customer_id):
        return f"https://pay.example/checkout/{user_id}", customer_id or f"cus_{user_id}"

    def create_portal(self, customer_id):
        return f"https://pay.example/portal/{customer_id}"

    def parse_webhook(self, payload, signature):
        import json
        if signature != "good":
            raise BillingError("bad signature")
        d = json.loads(payload)
        return SubscriptionUpdate(**d)


def _settings(tmp_path, **kw) -> Settings:
    s = Settings()
    s.db_path = str(tmp_path / "db.sqlite3")
    s.storage_dir = str(tmp_path / "storage")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


@pytest.fixture()
def app(tmp_path):
    return create_app(_settings(tmp_path), FakeBilling())


@pytest.fixture()
def client(app):
    return TestClient(app)


def _register(client, email="a@example.com", pw="password123"):
    r = client.post("/auth/register", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _activate(client, user_id=1, status="active", period_end=None):
    import json
    body = json.dumps({
        "customer_id": f"cus_{user_id}", "subscription_id": "sub_1", "status": status,
        "current_period_end": period_end if period_end is not None else time.time() + 30 * 86400,
        "user_id": user_id,
    })
    r = client.post("/billing/webhook", content=body, headers={"stripe-signature": "good"})
    assert r.status_code == 200, r.text


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── accounts ─────────────────────────────────────────────────────────────────

def test_register_login_me_logout(client):
    token = _register(client)
    me = client.get("/me", headers=_auth(token)).json()["user"]
    assert me["email"] == "a@example.com"
    assert me["subscription"]["active"] is False

    assert client.post("/auth/register", json={"email": "A@example.com", "password": "password123"}).status_code == 409
    assert client.post("/auth/login", json={"email": "a@example.com", "password": "nope-nope"}).status_code == 401
    t2 = client.post("/auth/login", json={"email": "a@example.com", "password": "password123"}).json()["token"]
    assert t2 != token

    client.post("/auth/logout", headers=_auth(t2))
    assert client.get("/me", headers=_auth(t2)).status_code == 401
    assert client.get("/me", headers=_auth(token)).status_code == 200


def test_register_validation(client):
    assert client.post("/auth/register", json={"email": "bad", "password": "password123"}).status_code == 400
    assert client.post("/auth/register", json={"email": "x@y.com", "password": "short"}).status_code == 400


# ── billing ──────────────────────────────────────────────────────────────────

def test_checkout_and_webhook_activate_subscription(client):
    token = _register(client)
    r = client.post("/billing/checkout", headers=_auth(token))
    assert r.json()["url"] == "https://pay.example/checkout/1"
    assert client.post("/billing/portal", headers=_auth(token)).json()["url"].endswith("cus_1")

    bad = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "bad"})
    assert bad.status_code == 400

    _activate(client)
    sub = client.get("/me", headers=_auth(token)).json()["user"]["subscription"]
    assert sub["active"] is True and sub["status"] == "active"

    _activate(client, status="canceled")
    assert client.get("/me", headers=_auth(token)).json()["user"]["subscription"]["active"] is False


def test_expired_period_is_inactive(client):
    token = _register(client)
    _activate(client, period_end=time.time() - 10 * 86400)
    assert client.get("/me", headers=_auth(token)).json()["user"]["subscription"]["active"] is False


def test_billing_unconfigured_returns_503(tmp_path):
    c = TestClient(create_app(_settings(tmp_path), BillingProvider()))
    token = _register(c)
    assert c.post("/billing/checkout", headers=_auth(token)).status_code == 503


# ── storage ──────────────────────────────────────────────────────────────────

def test_storage_requires_subscription(client):
    token = _register(client)
    assert client.get("/files", headers=_auth(token)).status_code == 402


def test_upload_list_download_trash_restore(client):
    token = _register(client)
    _activate(client)
    h = _auth(token)
    r = client.post("/files", headers=h, data={"path": "Docs/a.txt"}, files={"file": ("a.txt", b"hello")})
    assert r.status_code == 200, r.text
    f = r.json()["file"]
    assert f["path"] == "Docs/a.txt" and f["size"] == 5

    # Replacing the same path keeps one record
    r = client.post("/files", headers=h, data={"path": "Docs/a.txt"}, files={"file": ("a.txt", b"hello2")})
    assert r.json()["file"]["id"] == f["id"]
    assert [x["path"] for x in client.get("/files", headers=h).json()["files"]] == ["Docs/a.txt"]
    assert client.get(f"/files/{f['id']}/download", headers=h).content == b"hello2"

    client.post(f"/files/{f['id']}/trash", headers=h)
    assert client.get("/files", headers=h).json()["files"] == []
    assert len(client.get("/files?trashed=true", headers=h).json()["files"]) == 1
    client.post(f"/files/{f['id']}/restore", headers=h)
    assert len(client.get("/files", headers=h).json()["files"]) == 1


def test_users_cannot_see_each_other(client):
    t1 = _register(client, "one@example.com")
    t2 = _register(client, "two@example.com")
    _activate(client, 1)
    _activate(client, 2)
    f = client.post("/files", headers=_auth(t1), data={"path": "x.txt"},
                    files={"file": ("x.txt", b"secret")}).json()["file"]
    assert client.get(f"/files/{f['id']}/download", headers=_auth(t2)).status_code == 404
    assert client.get("/files", headers=_auth(t2)).json()["files"] == []


@pytest.mark.parametrize("bad", ["../x.txt", "a/../../x", "C:/x.txt", "", "/"])
def test_rejects_bad_paths(client, bad):
    token = _register(client)
    _activate(client)
    r = client.post("/files", headers=_auth(token), data={"path": bad}, files={"file": ("x", b"1")})
    assert r.status_code in (400, 422)


def test_quota_enforced(tmp_path):
    c = TestClient(create_app(_settings(tmp_path, storage_quota_mb=1), FakeBilling()))
    token = _register(c)
    _activate(c)
    big = b"x" * (1024 * 1024 + 1)
    r = c.post("/files", headers=_auth(token), data={"path": "big.bin"}, files={"file": ("big.bin", big)})
    assert r.status_code == 507


# ── end-to-end with the real desktop client over HTTP ────────────────────────

@pytest.fixture()
def live_server(tmp_path):
    import uvicorn
    app = create_app(_settings(tmp_path), FakeBilling())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    t.join(5)


def _wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_client_events_and_sync(live_server, tmp_path):
    from cloud_client import CloudClient, CloudError, EventStream, SyncFolder

    url, app = live_server
    c = CloudClient(url)
    c.register("sync@example.com", "password123")
    with pytest.raises(CloudError) as ei:
        c.list_files()
    assert ei.value.status == 402
    TestClient(app).post("/billing/webhook", headers={"stripe-signature": "good"}, content=(
        '{"customer_id":"cus_1","subscription_id":"s","status":"active",'
        f'"current_period_end":{time.time() + 86400},"user_id":1}}'))
    assert c.me()["subscription"]["active"]

    # Live events reach a second device
    events = []
    other = CloudClient(url)
    other.login("sync@example.com", "password123")
    stream = EventStream(other, lambda e, d: events.append((e, d))).start()
    assert _wait(lambda: app.state.hub.subscriber_count(1) == 1)

    # Device A: local folder with two files
    dev_a = tmp_path / "A" / "Work"
    (dev_a / "sub").mkdir(parents=True)
    (dev_a / "one.txt").write_text("one")
    (dev_a / "sub" / "two.txt").write_text("two")
    stats = SyncFolder(c, str(dev_a)).run_once()
    assert stats["uploaded"] == 2
    assert _wait(lambda: len([e for e in events if e[0] == "file_updated"]) >= 2)

    # Device B: empty folder, same cloud folder name → downloads both
    dev_b = tmp_path / "B" / "Work"
    sb = SyncFolder(other, str(dev_b))
    assert sb.run_once()["downloaded"] == 2
    assert (dev_b / "sub" / "two.txt").read_text() == "two"

    # Edit on B propagates to A, which still has the old copy
    (dev_b / "one.txt").write_text("one-edited")
    assert sb.run_once()["uploaded"] == 1
    sa = SyncFolder(c, str(dev_a))
    assert sa.run_once()["downloaded"] == 1
    assert (dev_a / "one.txt").read_text() == "one-edited"

    # Both sides edit → conflict copy, nothing lost
    (dev_a / "one.txt").write_text("A-version")
    (dev_b / "one.txt").write_text("B-version")
    sb.run_once()
    st = sa.run_once()
    assert st["conflicts"] == 1
    assert (dev_a / "one (cloud copy).txt").read_text() == "B-version"
    assert (dev_a / "one.txt").read_text() == "A-version"

    # A file trashed in the cloud is not re-uploaded from a synced device
    two = next(f for f in c.list_files() if f["path"] == "Work/sub/two.txt")
    c.trash(two["id"])
    sa.run_once()
    assert all(f["path"] != "Work/sub/two.txt" for f in c.list_files())
    assert (dev_a / "sub" / "two.txt").exists()

    stream.stop()


def test_stripe_webhook_signature_and_parsing(tmp_path):
    import hashlib
    import hmac
    import json
    from server.billing import StripeProvider

    s = _settings(tmp_path, stripe_secret_key="sk_test_x", stripe_price_id="price_x",
                  stripe_webhook_secret="whsec_test")
    prov = StripeProvider(s)
    end = int(time.time()) + 86400
    payload = json.dumps({
        "id": "evt_1", "object": "event", "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_9", "object": "subscription", "customer": "cus_9",
                            "status": "active", "metadata": {"user_id": "7"},
                            "items": {"data": [{"current_period_end": end}]}}},
    }).encode()
    ts = int(time.time())
    sig = hmac.new(b"whsec_test", f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    upd = prov.parse_webhook(payload, f"t={ts},v1={sig}")
    assert (upd.user_id, upd.customer_id, upd.status, upd.current_period_end) == (7, "cus_9", "active", end)
    with pytest.raises(BillingError):
        prov.parse_webhook(payload, f"t={ts},v1={'0' * 64}")
