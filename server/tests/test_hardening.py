"""
Security / robustness tests for the account server, the cloud client and sync.

Run:  python -m pytest server/tests -q
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import pytest

os.environ["SFM_SERVER_NO_AUTOAPP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.billing import StripeProvider  # noqa: E402
from server.tests.test_server import (FakeBilling, _activate, _auth, _register,  # noqa: E402,F401
                                      _settings, _wait, live_server)


def _client(tmp_path, **kw):
    return TestClient(create_app(_settings(tmp_path, **kw), FakeBilling()))


def _webhook(c, **fields):
    body = {"customer_id": "cus_1", "subscription_id": "sub_1", "status": "active",
            "current_period_end": time.time() + 30 * 86400, "user_id": 1}
    body.update(fields)
    r = c.post("/billing/webhook", content=json.dumps(body), headers={"stripe-signature": "good"})
    assert r.status_code == 200, r.text
    return r.json()


def _sub(c, token):
    return c.get("/me", headers=_auth(token)).json()["user"]["subscription"]


# ── sign-in hardening ────────────────────────────────────────────────────────

def test_login_rate_limited_per_email(tmp_path):
    c = _client(tmp_path, login_max_attempts=3, login_ip_max_attempts=100)
    _register(c, "rl@example.com")
    for _ in range(3):
        assert c.post("/auth/login", json={"email": "rl@example.com", "password": "wrong-pass"}).status_code == 401
    r = c.post("/auth/login", json={"email": "rl@example.com", "password": "password123"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert "Too many" in r.json()["detail"]
    # A different account from the same address is not locked by this one
    _register(c, "other@example.com")
    assert c.post("/auth/login", json={"email": "other@example.com", "password": "password123"}).status_code == 200


def test_login_rate_limited_per_ip(tmp_path):
    c = _client(tmp_path, login_max_attempts=100, login_ip_max_attempts=4)
    for i in range(4):
        c.post("/auth/login", json={"email": f"nobody{i}@example.com", "password": "whatever1"})
    assert c.post("/auth/login", json={"email": "x@example.com", "password": "whatever1"}).status_code == 429


def test_successful_login_resets_email_counter(tmp_path):
    c = _client(tmp_path, login_max_attempts=3)
    _register(c, "ok@example.com")
    for _ in range(2):
        c.post("/auth/login", json={"email": "ok@example.com", "password": "bad-pass"})
    assert c.post("/auth/login", json={"email": "ok@example.com", "password": "password123"}).status_code == 200
    for _ in range(2):
        assert c.post("/auth/login", json={"email": "ok@example.com", "password": "bad-pass"}).status_code == 401


def test_register_rate_limited_per_ip(tmp_path):
    c = _client(tmp_path, register_per_hour=2)
    _register(c, "r1@example.com")
    _register(c, "r2@example.com")
    assert c.post("/auth/register", json={"email": "r3@example.com", "password": "password123"}).status_code == 429


def test_password_length_limits(tmp_path):
    c = _client(tmp_path)
    assert c.post("/auth/register", json={"email": "l@example.com", "password": "x" * 300}).status_code == 400
    _register(c, "l@example.com")
    assert c.post("/auth/login", json={"email": "l@example.com", "password": "x" * 5000}).status_code == 401


def test_sessions_list_and_revoke(tmp_path):
    c = _client(tmp_path)
    t1 = _register(c)
    t2 = c.post("/auth/login", json={"email": "a@example.com", "password": "password123",
                                     "device": "Laptop"}).json()["token"]
    sess = c.get("/auth/sessions", headers=_auth(t1)).json()["sessions"]
    assert len(sess) == 2
    assert [s["current"] for s in sess if s["device"] == "Laptop"] == [False]
    laptop = next(s for s in sess if s["device"] == "Laptop")
    assert "token_hash" not in laptop
    assert c.post(f"/auth/sessions/{laptop['id']}/revoke", headers=_auth(t1)).status_code == 200
    assert c.get("/me", headers=_auth(t2)).status_code == 401
    assert c.get("/me", headers=_auth(t1)).status_code == 200
    # Another user cannot revoke my sessions
    t3 = _register(c, "b@example.com")
    mine = c.get("/auth/sessions", headers=_auth(t1)).json()["sessions"][0]["id"]
    assert c.post(f"/auth/sessions/{mine}/revoke", headers=_auth(t3)).status_code == 404


# ── live events auth ─────────────────────────────────────────────────────────

def test_events_reject_token_in_url(tmp_path):
    c = _client(tmp_path)
    t = _register(c)
    r = c.get(f"/events?token={t}")
    assert r.status_code == 400 and "Authorization header" in r.json()["detail"]
    assert c.get("/events").status_code == 401


def test_event_stream_header_auth_and_revocation(live_server):
    from cloud_client import CloudClient, EventStream
    url, app = live_server
    a = CloudClient(url)
    a.register("ev@example.com", "password123")
    b = CloudClient(url)
    b.login("ev@example.com", "password123", device="Other PC")
    got = []
    stream = EventStream(b, lambda e, d: got.append(e)).start()
    assert _wait(lambda: app.state.hub.subscriber_count(1) == 1)
    other = next(s for s in a.sessions() if s["device"] == "Other PC")
    a.revoke_session(other["id"])
    assert _wait(lambda: "session_expired" in got)
    assert _wait(lambda: stream.stopped)
    assert _wait(lambda: app.state.hub.subscriber_count(1) == 0)


# ── billing ──────────────────────────────────────────────────────────────────

def test_webhook_idempotent_and_ignores_stale_events(tmp_path):
    c = _client(tmp_path)
    t = _register(c)
    now = time.time()
    assert _webhook(c, event_id="evt_1", event_created=now, status="active") == {"ok": True}
    assert _webhook(c, event_id="evt_1", event_created=now, status="canceled")["duplicate"]
    assert _sub(c, t)["status"] == "active"
    # An older event delivered late must not undo a newer one
    assert _webhook(c, event_id="evt_0", event_created=now - 60, status="incomplete")["stale"]
    assert _sub(c, t)["status"] == "active"
    _webhook(c, event_id="evt_2", event_created=now + 60, status="canceled")
    assert _sub(c, t)["active"] is False


def test_past_due_grace_period(tmp_path):
    c = _client(tmp_path, past_due_grace_days=7)
    t = _register(c)
    _webhook(c, status="active")
    _webhook(c, status="past_due", current_period_end=0)
    s = _sub(c, t)
    assert s["status"] == "past_due" and s["active"] is True
    assert s["grace_until"] > time.time() + 6 * 86400
    assert s["current_period_end"] > time.time()          # kept from the earlier event
    assert c.get("/files", headers=_auth(t)).status_code == 200
    _webhook(c, status="active")
    assert _sub(c, t)["grace_until"] == 0


def test_past_due_after_grace_is_inactive(tmp_path):
    c = _client(tmp_path, past_due_grace_days=0)
    t = _register(c)
    _webhook(c, status="past_due")
    assert _sub(c, t)["active"] is False
    assert c.get("/files", headers=_auth(t)).status_code == 402


def test_checkout_refused_when_already_subscribed(tmp_path):
    c = _client(tmp_path)
    t = _register(c)
    _webhook(c, status="active")
    r = c.post("/billing/checkout", headers=_auth(t))
    assert r.status_code == 409 and "Manage billing" in r.json()["detail"]


def test_free_plan_unlocks_without_billing(tmp_path):
    from server.billing import BillingProvider
    c = TestClient(create_app(_settings(tmp_path, free_plan=True), BillingProvider()))
    t = _register(c)
    s = _sub(c, t)
    assert s["active"] and s["free_plan"] and s["billing_available"] is False
    assert c.get("/files", headers=_auth(t)).status_code == 200
    assert c.get("/health").json()["free_plan"] is True


def _stripe_sig(secret: bytes, payload: bytes) -> str:
    ts = int(time.time())
    sig = hmac.new(secret, f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _stripe_provider(tmp_path):
    s = _settings(tmp_path, stripe_secret_key="sk_test_x", stripe_price_id="price_x",
                  stripe_webhook_secret="whsec_test")
    return StripeProvider(s)


def test_stripe_invoice_payment_failed_means_past_due(tmp_path):
    prov = _stripe_provider(tmp_path)
    payload = json.dumps({
        "id": "evt_inv", "object": "event", "type": "invoice.payment_failed", "created": 1700000000,
        "data": {"object": {"id": "in_1", "object": "invoice", "customer": "cus_9",
                            "parent": {"subscription_details": {"subscription": "sub_9",
                                                                "metadata": {"user_id": "4"}}}}},
    }).encode()
    upd = prov.parse_webhook(payload, _stripe_sig(b"whsec_test", payload))
    assert (upd.status, upd.subscription_id, upd.customer_id, upd.user_id) == ("past_due", "sub_9", "cus_9", 4)
    assert (upd.event_id, upd.event_created, upd.current_period_end) == ("evt_inv", 1700000000, 0)
    # A one-off invoice (no subscription) is ignored
    payload = json.dumps({"id": "evt_x", "object": "event", "type": "invoice.payment_failed",
                          "data": {"object": {"id": "in_2", "object": "invoice", "customer": "c"}}}).encode()
    assert prov.parse_webhook(payload, _stripe_sig(b"whsec_test", payload)) is None


def test_stripe_checkout_completed_fetches_subscription(tmp_path, monkeypatch):
    prov = _stripe_provider(tmp_path)
    end = int(time.time()) + 86400
    calls = []

    def fake_retrieve(sub_id):
        calls.append(sub_id)
        return {"id": sub_id, "customer": "cus_5", "status": "active", "metadata": {},
                "items": {"data": [{"current_period_end": end}]}}
    monkeypatch.setattr(prov._stripe.Subscription, "retrieve", staticmethod(fake_retrieve))
    payload = json.dumps({
        "id": "evt_cs", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "object": "checkout.session", "subscription": "sub_5",
                            "client_reference_id": "5", "customer": "cus_5"}},
    }).encode()
    upd = prov.parse_webhook(payload, _stripe_sig(b"whsec_test", payload))
    assert calls == ["sub_5"]
    assert (upd.user_id, upd.status, upd.current_period_end, upd.event_id) == (5, "active", end, "evt_cs")


def test_stripe_subscription_deleted_is_canceled(tmp_path):
    prov = _stripe_provider(tmp_path)
    payload = json.dumps({
        "id": "evt_del", "object": "event", "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_1", "object": "subscription", "customer": "cus_1",
                            "status": "canceled", "metadata": {"user_id": "1"}}},
    }).encode()
    assert prov.parse_webhook(payload, _stripe_sig(b"whsec_test", payload)).status == "canceled"


# ── storage ──────────────────────────────────────────────────────────────────

def _sub_client(tmp_path, **kw):
    c = _client(tmp_path, **kw)
    t = _register(c)
    _activate(c)
    return c, _auth(t)


def _up(c, h, path, data=b"x", **form):
    return c.post("/files", headers=h, data={"path": path, **form}, files={"file": ("f", data)})


@pytest.mark.parametrize("bad", ["a/b:c.txt", "CON.txt", "docs/aux", "name.", "trailing /x",
                                 "x\x01y", 'q"uote', "a/../b"])
def test_rejects_unsafe_names(tmp_path, bad):
    c, h = _sub_client(tmp_path)
    assert _up(c, h, bad).status_code == 400


def test_backslashes_normalised(tmp_path):
    c, h = _sub_client(tmp_path)
    assert _up(c, h, "Docs\\sub\\a.txt").json()["file"]["path"] == "Docs/sub/a.txt"
    # A UNC-looking path stays inside the user's own space
    assert _up(c, h, "\\\\server\\share\\x").json()["file"]["path"] == "server/share/x"


def test_oversized_upload_rejected_early(tmp_path):
    c, h = _sub_client(tmp_path, max_upload_mb=1)
    r = _up(c, h, "big.bin", b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert c.get("/usage", headers=h).json()["max_upload"] == 1024 * 1024


def test_upload_base_sha_detects_concurrent_change(tmp_path):
    c, h = _sub_client(tmp_path)
    f = _up(c, h, "a.txt", b"one").json()["file"]
    _up(c, h, "a.txt", b"two")                       # another device changed it
    r = _up(c, h, "a.txt", b"three", base_sha256=f["sha256"])
    assert r.status_code == 409
    two = hashlib.sha256(b"two").hexdigest()
    assert _up(c, h, "a.txt", b"three", base_sha256=two).status_code == 200


def test_move_rename_and_conflicts(tmp_path):
    c, h = _sub_client(tmp_path)
    a = _up(c, h, "Docs/a.txt").json()["file"]
    _up(c, h, "Docs/b.txt")
    r = c.post(f"/files/{a['id']}/move", headers=h, json={"path": "Docs/renamed.txt"})
    assert r.status_code == 200 and r.json()["file"]["path"] == "Docs/renamed.txt"
    assert c.post(f"/files/{a['id']}/move", headers=h, json={"path": "Docs/b.txt"}).status_code == 409
    assert c.post(f"/files/{a['id']}/move", headers=h, json={"path": "../x"}).status_code == 400
    assert c.post("/files/9999/move", headers=h, json={"path": "z.txt"}).status_code == 404


def test_folder_move_and_trash(tmp_path):
    c, h = _sub_client(tmp_path)
    for p in ("Work/a.txt", "Work/sub/b.txt", "Workshop/c.txt", "Other/a.txt"):
        _up(c, h, p)
    assert c.post("/folders/move", headers=h, json={"path": "Work", "new_path": "Work/inner"}).status_code == 400
    assert c.post("/folders/move", headers=h, json={"path": "Work", "new_path": "Other"}).status_code == 409
    r = c.post("/folders/move", headers=h, json={"path": "Work", "new_path": "Archive/Work"})
    assert r.json()["moved"] == 2
    paths = sorted(f["path"] for f in c.get("/files", headers=h).json()["files"])
    assert paths == ["Archive/Work/a.txt", "Archive/Work/sub/b.txt", "Other/a.txt", "Workshop/c.txt"]
    assert c.post("/folders/trash", headers=h, json={"path": "Archive"}).json()["trashed"] == 2
    paths = sorted(f["path"] for f in c.get("/files", headers=h).json()["files"])
    assert paths == ["Other/a.txt", "Workshop/c.txt"]
    # LIKE wildcards in folder names are literal
    _up(c, h, "100%/x.txt")
    _up(c, h, "100abc/y.txt")
    assert c.post("/folders/trash", headers=h, json={"path": "100%"}).json()["trashed"] == 1


def test_usage_excludes_trash_and_restore_respects_quota(tmp_path):
    c, h = _sub_client(tmp_path, storage_quota_mb=1)
    half = b"x" * (600 * 1024)
    f = _up(c, h, "a.bin", half).json()["file"]
    c.post(f"/files/{f['id']}/trash", headers=h)
    assert c.get("/usage", headers=h).json()["used"] == 0
    assert _up(c, h, "b.bin", half).status_code == 200
    assert c.post(f"/files/{f['id']}/restore", headers=h).status_code == 507


def test_per_user_isolation_for_every_file_route(tmp_path):
    c = _client(tmp_path)
    t1, t2 = _register(c, "u1@example.com"), _register(c, "u2@example.com")
    _activate(c, 1)
    _activate(c, 2)
    h1, h2 = _auth(t1), _auth(t2)
    f = _up(c, h1, "secret/a.txt", b"s").json()["file"]
    for method, url, body in (("get", f"/files/{f['id']}/download", None),
                              ("post", f"/files/{f['id']}/trash", None),
                              ("post", f"/files/{f['id']}/restore", None),
                              ("post", f"/files/{f['id']}/move", {"path": "mine.txt"})):
        r = getattr(c, method)(url, headers=h2, **({"json": body} if body else {}))
        assert r.status_code == 404, (url, r.status_code)
    assert c.post("/folders/move", headers=h2, json={"path": "secret", "new_path": "x"}).status_code == 404
    assert c.post("/folders/trash", headers=h2, json={"path": "secret"}).json()["trashed"] == 0
    # Same path for user 2 is a separate file
    _up(c, h2, "secret/a.txt", b"mine")
    assert c.get(f"/files/{f['id']}/download", headers=h1).content == b"s"
    # Blobs live in per-user folders
    assert os.listdir(os.path.join(c.app.state.settings.storage_dir, "1"))
    assert os.listdir(os.path.join(c.app.state.settings.storage_dir, "2"))


# ── desktop client + sync ────────────────────────────────────────────────────

def test_client_progress_and_errors(live_server, tmp_path):
    from cloud_client import CloudClient, CloudError
    url, app = live_server
    c = CloudClient(url)
    c.register("prog@example.com", "password123")
    _activate(TestClient(app))
    src = tmp_path / "big.bin"
    src.write_bytes(os.urandom(3 * 1024 * 1024 + 7))
    seen = []
    f = c.upload(str(src), "big.bin", progress=lambda d, t: seen.append((d, t)))
    assert f["size"] == 3 * 1024 * 1024 + 7
    assert seen[-1][0] == seen[-1][1] and len(seen) > 2
    got = []
    c.download(f["id"], str(tmp_path / "back.bin"), progress=lambda d, t: got.append((d, t)))
    assert (tmp_path / "back.bin").read_bytes() == src.read_bytes()
    assert got[-1] == (f["size"], f["size"])
    with pytest.raises(CloudError) as ei:
        c.login("prog@example.com", "nope-nope")
    assert ei.value.status == 401
    with pytest.raises(CloudError) as ei:
        CloudClient("http://127.0.0.1:1").health()
    assert ei.value.status == 0


def test_conflict_name_and_review_move(tmp_path):
    from cloud_client import conflict_name, move_to_review
    n = conflict_name("docs/a.pdf", "MY:PC", when=time.mktime((2026, 10, 5, 14, 32, 0, 0, 0, -1)))
    assert n == "docs/a (conflict MY-PC 2026-10-05 1432).pdf"
    f = tmp_path / "x.txt"
    f.write_text("1")
    first = move_to_review(str(f))
    f.write_text("2")
    second = move_to_review(str(f))
    assert first.endswith(os.path.join("_to_review", "x.txt"))
    assert second.endswith(os.path.join("_to_review", "x (2).txt"))
    assert not f.exists()


class _FakeCloud:
    """In-memory stand-in for CloudClient used to test SyncFolder decisions."""
    device = "TEST"

    def __init__(self):
        self.files, self.trashed, self.next_id = {}, [], 1

    def usage(self):
        return {"max_upload": 10}

    def list_files(self, trashed=False):
        return [] if trashed else list(self.files.values())

    def upload(self, local, remote, base_sha256="", progress=None):
        data = open(local, "rb").read()
        f = {"id": self.next_id, "path": remote, "sha256": hashlib.sha256(data).hexdigest(), "data": data}
        self.next_id += 1
        self.files[remote] = f
        return f

    def download(self, fid, dest, progress=None):
        f = next(x for x in self.files.values() if x["id"] == fid)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(f["data"])

    def trash(self, fid):
        path = next(p for p, x in self.files.items() if x["id"] == fid)
        self.trashed.append(self.files.pop(path))


def test_sync_holds_mass_local_deletions(tmp_path):
    from cloud_client import SyncFolder
    root = tmp_path / "Box"
    root.mkdir()
    for i in range(15):
        (root / f"f{i}.txt").write_text(str(i))
    cloud = _FakeCloud()
    s = SyncFolder(cloud, str(root))
    assert s.run_once()["uploaded"] == 15
    review = tmp_path / "elsewhere"
    review.mkdir()
    for i in range(15):
        os.replace(root / f"f{i}.txt", review / f"f{i}.txt")
    st = s.run_once()
    assert st["held_deletions"] == 15 and st["trashed_remote"] == 0 and not cloud.trashed
    # A small, deliberate removal still propagates
    os.replace(review / "f0.txt", root / "f0.txt")
    for i in range(1, 15):
        os.replace(review / f"f{i}.txt", root / f"f{i}.txt")
    os.replace(root / "f3.txt", review / "f3.txt")
    st = s.run_once()
    assert st["trashed_remote"] == 1 and [f["path"] for f in cloud.trashed] == ["Box/f3.txt"]


def test_sync_skips_files_over_upload_limit(tmp_path):
    from cloud_client import SyncFolder
    root = tmp_path / "Lim"
    root.mkdir()
    (root / "small.txt").write_text("ok")
    (root / "big.txt").write_text("x" * 50)
    cloud = _FakeCloud()
    logs = []
    st = SyncFolder(cloud, str(root), log=logs.append).run_once()
    assert st["uploaded"] == 1 and st["errors"] == 1
    assert any("too large" in m for m in logs)


def test_sync_worker_pause_resume(tmp_path):
    from cloud_client import SyncFolder, SyncWorker
    root = tmp_path / "W"
    root.mkdir()
    cloud = _FakeCloud()
    runs, states = [], []
    w = SyncWorker(SyncFolder(cloud, str(root)), interval=60, local_check=0.2,
                   on_done=runs.append, on_state=states.append, paused=True).start()
    try:
        time.sleep(0.5)
        assert runs == [] and w.state == "paused"
        w.resume()
        assert _wait(lambda: len(runs) == 1)
        (root / "new.txt").write_text("hello")          # local change → sync without a poke
        assert _wait(lambda: "W/new.txt" in cloud.files, timeout=10)
        w.pause()
        assert _wait(lambda: w.state == "paused")
        assert "syncing" in states and "idle" in states
    finally:
        w.stop()
