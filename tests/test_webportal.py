# -*- coding: utf-8 -*-
"""The web portal (webportal/): admin login, CSRF, key pages, the license API,
and the app's online checks against it. No network: requests go straight into
the WSGI app. Every signing key is made fresh in a temp folder."""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "webportal"))

import ed25519_pure  # noqa: E402
import license_client  # noqa: E402
import license_keys as lk  # noqa: E402
import subscription as sub  # noqa: E402
from config import Config  # noqa: E402
from web import make_app  # noqa: E402

PASSWORD = "test-pass-" + secrets.token_hex(4)


class Client:
    """Tiny WSGI test client with a cookie jar."""

    def __init__(self, app, ip="127.0.0.1"):
        self.app, self.ip, self.cookies = app, ip, {}

    def request(self, method, path, data=None, json_body=None, headers=None):
        url = urlsplit(path)
        body = b""
        ctype = ""
        if json_body is not None:
            body, ctype = json.dumps(json_body).encode(), "application/json"
        elif data is not None:
            body, ctype = urlencode(data).encode(), "application/x-www-form-urlencoded"
        env = {"REQUEST_METHOD": method, "PATH_INFO": url.path, "QUERY_STRING": url.query,
               "REMOTE_ADDR": self.ip, "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": ctype,
               "wsgi.input": io.BytesIO(body),
               "HTTP_COOKIE": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        env.update(headers or {})
        out = {}

        def start(status, hdrs):
            out["status"], out["headers"] = int(status.split()[0]), hdrs
        out["body"] = b"".join(self.app(env, start)).decode("utf-8", "replace")
        for k, v in out["headers"]:
            if k == "Set-Cookie":
                name, val = v.split(";")[0].split("=", 1)
                if val:
                    self.cookies[name] = val
                else:
                    self.cookies.pop(name, None)
        out["location"] = dict(out["headers"]).get("Location", "")
        return out

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, data=None, **kw):
        return self.request("POST", path, data=data, **kw)


@pytest.fixture
def portal(tmp_path):
    seed = secrets.token_bytes(32)
    cfg = Config(data_dir=str(tmp_path / "data"), admin_user="admin", cookie_secure=False,
                 trust_proxy=False, download_url="", download_file="", seed=seed)
    app = make_app(cfg)
    return app, seed


def _csrf(html):
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def _login(app):
    c = Client(app)
    c.post("/admin/setup", {"password": PASSWORD, "password2": PASSWORD})
    r = c.post("/admin/login", {"user": "admin", "password": PASSWORD})
    assert r["status"] == 303 and r["location"] == "/admin"
    return c


# ── public pages ──────────────────────────────────────────────────────────────

def test_landing_page_and_download_placeholder(portal):
    app, _ = portal
    c = Client(app, ip="203.0.113.5")
    r = c.get("/")
    assert r["status"] == 200 and "৳999" in r["body"] and "৳9,999" in r["body"] and "৳11,988" in r["body"]
    assert "Download for Windows" in r["body"]
    assert "Content-Security-Policy" in dict(r["headers"])
    assert c.get("/download")["status"] == 200                     # "coming soon" until configured
    assert c.get("/static/portal.css")["status"] == 200
    assert c.get("/static/../config.py")["status"] == 404


def test_download_redirects_and_is_counted(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "d"), download_url="https://example.com/OfficeAxe.zip",
                 seed=secrets.token_bytes(32))
    app = make_app(cfg)
    r = Client(app).get("/download")
    assert r["status"] == 303 and r["location"] == "https://example.com/OfficeAxe.zip"
    assert app.store.download_count() == 1


# ── admin auth ────────────────────────────────────────────────────────────────

def test_setup_only_from_this_computer_and_only_once(portal):
    app, _ = portal
    remote = Client(app, ip="203.0.113.9")
    assert remote.get("/admin")["location"] == "/admin/setup"
    assert remote.post("/admin/setup", {"password": PASSWORD, "password2": PASSWORD})["status"] == 403
    proxied = Client(app)
    assert proxied.post("/admin/setup", {"password": PASSWORD, "password2": PASSWORD},
                        headers={"HTTP_X_FORWARDED_FOR": "198.51.100.1"})["status"] == 403
    local = Client(app)
    assert "at least 10" in local.post("/admin/setup", {"password": "short", "password2": "short"})["body"]
    assert local.post("/admin/setup", {"password": PASSWORD, "password2": PASSWORD})["status"] == 303
    # once set, setup can't replace it
    local.post("/admin/setup", {"password": "another-pass-123", "password2": "another-pass-123"})
    assert app.account.verify("admin", PASSWORD)


def test_login_rate_limit_session_and_csrf(portal):
    app, _ = portal
    c = _login(app)
    assert c.get("/admin")["status"] == 200
    # forms without the CSRF token are refused
    assert c.post("/admin/keys", {"type": "review", "holder": "X"})["status"] == 403
    # a forged cookie is ignored
    bad = Client(app)
    bad.cookies["oa_admin"] = c.cookies["oa_admin"][:-4] + "AAAA"
    assert bad.get("/admin")["location"] == "/admin/login"
    # 5 wrong passwords from one IP lock that IP out
    attacker = Client(app, ip="203.0.113.7")
    for _ in range(5):
        assert attacker.post("/admin/login", {"user": "admin", "password": "nope"})["status"] == 401
    r = attacker.post("/admin/login", {"user": "admin", "password": PASSWORD})
    assert "Too many attempts" in r["body"]
    # logout clears the cookie
    page = c.get("/admin")["body"]
    c.post("/admin/logout", {"csrf": _csrf(page)})
    assert c.get("/admin")["location"] == "/admin/login"


def test_changing_the_password_logs_out_old_sessions(portal):
    app, _ = portal
    c = _login(app)
    page = c.get("/admin/settings")["body"]
    r = c.post("/admin/settings", {"csrf": _csrf(page), "current": PASSWORD,
                                   "new": "brand-new-pass-1", "new2": "brand-new-pass-1"})
    assert r["location"] == "/admin/login"
    assert c.get("/admin")["location"] == "/admin/login"
    assert app.account.verify("admin", "brand-new-pass-1")


# ── admin key pages ───────────────────────────────────────────────────────────

def test_make_view_revoke_release_and_export(portal):
    app, seed = portal
    c = _login(app)
    csrf = _csrf(c.get("/admin")["body"])
    r = c.post("/admin/keys", {"csrf": csrf, "type": "full", "holder": "Acme <Ltd>", "days": "365",
                               "devices": "2", "plan": "yearly", "note": "first client"})
    assert r["status"] == 303
    key_id = r["location"].split("/")[3].split("?")[0]
    page = c.get(r["location"])["body"]
    assert "Acme &lt;Ltd&gt;" in page and "<Ltd>" not in page              # escaped
    key = app.store.get_key(key_id)["key"]
    assert lk.parse(key, ed25519_pure.public_key(seed), revoked=set())["max_devices"] == 2
    # validation errors come back on the form
    assert "Enter who the key is for" in c.post("/admin/keys", {"csrf": csrf, "type": "review", "holder": ""})["body"]

    dev = {"key": key, "device_id": "dev_1", "device_name": "PC 1", "nonce": "n1"}
    assert app.lic.api("activate", dev)["payload"]["ok"]
    assert "PC 1" in c.get(f"/admin/keys/{key_id}")["body"]
    c.post(f"/admin/keys/{key_id}/release", {"csrf": csrf, "device_id": "dev_1"})
    assert app.lic.api("check", dev)["payload"]["status"] == "not_activated"
    c.post(f"/admin/keys/{key_id}/revoke", {"csrf": csrf})
    assert app.lic.key_view(key_id)["status"] == "revoked"
    assert key_id in c.get("/admin/revoked_keys.json")["body"]
    assert c.get("/api/v1/revoked")["body"].count(key_id) == 1
    csv_text = c.get("/admin/export.csv")["body"]
    assert "Acme <Ltd>" in csv_text and "revoked" in csv_text
    assert c.get("/admin?status=revoked")["body"].count(f"/admin/keys/{key_id}") == 1
    assert "issue" in c.get("/admin/activity")["body"]


def test_import_from_the_desktop_dashboard(portal, tmp_path):
    app, seed = portal
    exp = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    mine = lk.make_key(seed, key_type="review", holder="Employee", expires_at=exp)
    other = lk.make_key(secrets.token_bytes(32), key_type="review", holder="Stranger", expires_at=exp)
    path = tmp_path / "issued_keys.json"
    path.write_text(json.dumps({"keys": [{"key": mine, "note": "review"}, {"key": other}]}), encoding="utf-8")
    assert app.lic.import_desktop(str(path)) == {"added": 1, "skipped": 1}
    assert app.lic.import_desktop(str(path)) == {"added": 0, "skipped": 2}
    rows = app.lic.list_keys()
    assert [r["holder"] for r in rows] == ["Employee"] and rows[0]["source"] == "desktop"


# ── license API ───────────────────────────────────────────────────────────────

def test_api_enforces_device_limit_revocation_and_expiry(portal):
    app, seed = portal
    row = app.lic.issue("review", "Tester", days=30, max_devices=1)
    c = Client(app, ip="203.0.113.20")
    call = lambda action, dev: json.loads(c.post(f"/api/v1/{action}", json_body={
        "key": row["key"], "device_id": dev, "device_name": dev, "nonce": "x"})["body"])["payload"]
    assert call("activate", "pc_a")["ok"]
    assert call("activate", "pc_a")["ok"]                      # same PC again is fine
    second = call("activate", "pc_b")
    assert not second["ok"] and second["status"] == "device_limit"
    assert call("check", "pc_b")["status"] == "not_activated"
    assert call("deactivate", "pc_a")["status"] == "released"
    assert call("activate", "pc_b")["ok"]                      # the freed slot can be used
    app.lic.set_revoked(row["key_id"], True)
    assert call("check", "pc_b")["status"] == "revoked"
    # a key made offline (never seen by the portal) is accepted and recorded
    exp = int((datetime.now(timezone.utc) + timedelta(days=5)).timestamp())
    offline = lk.make_key(seed, key_type="trial", holder="Walk-in", expires_at=exp)
    r = json.loads(c.post("/api/v1/activate", json_body={"key": offline, "device_id": "pc_z", "nonce": "y"})["body"])
    assert r["payload"]["ok"] and app.store.get_key(r["payload"]["key_id"])["source"] == "seen"
    # expired and forged keys are refused
    old = lk.make_key(seed, key_type="trial", holder="Old", expires_at=exp - 10 * 86400,
                      issued_at=exp - 20 * 86400)
    assert app.lic.api("activate", {"key": old, "device_id": "pc"})["payload"]["status"] == "expired"
    fake = lk.make_key(secrets.token_bytes(32), key_type="full", holder="Fake", expires_at=exp)
    assert app.lic.api("activate", {"key": fake, "device_id": "pc"})["payload"]["status"] == "invalid"


# ── the app's online checks ───────────────────────────────────────────────────

def _app_manager(tmp_path, app, seed, clock, online=True, name="a"):
    def transport(url, data, timeout):
        if not online_box["up"]:
            raise OSError("no internet")
        path = urlsplit(url).path
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": path, "QUERY_STRING": "", "REMOTE_ADDR": "198.51.100.3",
               "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)}
        return b"".join(app(env, lambda s, h: None))
    online_box = {"up": online}
    backend = sub.LocalLicenseBackend(str(tmp_path / f"sub-{name}.json"), secret="t")
    m = sub.SubscriptionManager(backend=backend, gateway=sub.PlaceholderGateway(sandbox=True),
                                device={"id": f"dev_{name}", "name": f"PC {name}", "os": "Windows 11"},
                                now=lambda: clock["t"], public_key=ed25519_pure.public_key(seed),
                                revoked=set(), online={"url": "https://portal.test", "transport": transport})
    return m, online_box


def test_app_activation_counts_pcs_across_computers(portal, tmp_path):
    app, seed = portal
    clock = {"t": datetime.now(timezone.utc)}
    row = app.lic.issue("review", "Employee", days=30, max_devices=1)
    a, _ = _app_manager(tmp_path, app, seed, clock, name="a")
    b, _ = _app_manager(tmp_path, app, seed, clock, name="b")
    assert a.activate_key(row["key"])["ok"]
    r = b.activate_key(row["key"])
    assert not r["ok"] and r["code"] == "device_limit"
    assert len(app.lic.key_view(row["key_id"])["devices"]) == 1


def test_app_learns_about_revocation_at_the_next_check(portal, tmp_path):
    app, seed = portal
    clock = {"t": datetime.now(timezone.utc)}
    row = app.lic.issue("full", "Client", days=60, max_devices=5)
    m, _ = _app_manager(tmp_path, app, seed, clock)
    assert m.activate_key(row["key"])["state"]["access"]
    app.lic.set_revoked(row["key_id"], True)
    clock["t"] += timedelta(hours=25)               # next daily check
    m._last_online_try = -1e9
    st = m.state()
    assert st["status"] == "revoked" and not st["access"] and "withdrawn" in st["problem"]
    app.lic.set_revoked(row["key_id"], False)        # restoring works at the next check too
    assert m.recheck_online()["access"]


def test_app_works_offline_for_seven_days_then_asks_for_a_check(portal, tmp_path):
    app, seed = portal
    clock = {"t": datetime.now(timezone.utc)}
    row = app.lic.issue("full", "Client", days=60, max_devices=5)
    m, net = _app_manager(tmp_path, app, seed, clock)
    assert m.activate_key(row["key"])["ok"]
    net["up"] = False
    clock["t"] += timedelta(days=6)
    m._last_online_try = -1e9
    assert m.state()["access"]                       # still inside the grace period
    clock["t"] += timedelta(days=2)
    m._last_online_try = -1e9
    st = m.state()
    assert st["status"] == "check_needed" and not st["access"] and st["reason"] == "check_needed"
    net["up"] = True
    assert m.recheck_online()["access"]


def test_app_activates_offline_when_the_portal_is_unreachable(portal, tmp_path):
    app, seed = portal
    clock = {"t": datetime.now(timezone.utc)}
    row = app.lic.issue("review", "Employee", days=30)
    m, net = _app_manager(tmp_path, app, seed, clock, online=False)
    assert m.activate_key(row["key"])["state"]["access"]
    net["up"] = True
    clock["t"] += timedelta(minutes=10)
    m._last_online_try = -1e9
    assert m.state()["access"]
    assert len(app.lic.key_view(row["key_id"])["devices"]) == 1     # registered once back online


def test_forged_server_answers_are_not_trusted(portal, tmp_path):
    app, seed = portal
    row = app.lic.issue("review", "Employee", days=30)
    evil_seed = secrets.token_bytes(32)

    def evil(url, data, timeout):          # a fake server signing with its own key
        req = json.loads(data)
        payload = {"ok": False, "status": "revoked", "message": "fake", "nonce": req["nonce"],
                   "device_id": req["device_id"]}
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return json.dumps({"payload": payload, "sig": ed25519_pure.sign(
            evil_seed, license_client.RESPONSE_CONTEXT + body).hex()}).encode()
    with pytest.raises(license_client.Offline):
        license_client.call("check", row["key"], {"id": "dev"}, transport=evil, url="https://x",
                            public_key=ed25519_pure.public_key(seed))
