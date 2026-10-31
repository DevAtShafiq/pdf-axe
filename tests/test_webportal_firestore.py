# -*- coding: utf-8 -*-
"""The portal on Firestore (webportal/store_firestore.py), with an in-memory
stand-in for google.cloud.firestore — no network, no Google account."""
from __future__ import annotations

import io
import json
import os
import secrets
import sys
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "webportal"))
sys.path.insert(0, os.path.join(ROOT, "webportal", "firebase"))

from config import Config  # noqa: E402
from store_firestore import FirestoreStore  # noqa: E402
from web import make_app  # noqa: E402


# ── a tiny Firestore ──────────────────────────────────────────────────────────
class _Snap:
    def __init__(self, ref, data):
        self.reference, self._d = ref, data

    @property
    def exists(self):
        return self._d is not None

    def to_dict(self):
        return dict(self._d) if self._d is not None else None


class _Doc:
    def __init__(self, coll, id_):
        self.coll, self.id = coll, id_

    def get(self):
        return _Snap(self, self.coll.data.get(self.id))

    def set(self, d):
        self.coll.data[self.id] = dict(d)

    def update(self, d):
        if self.id not in self.coll.data:
            raise KeyError("no document to update")
        self.coll.data[self.id].update(d)

    def delete(self):
        self.coll.data.pop(self.id, None)


class _Query:
    def __init__(self, coll, filters=(), order=None, lim=None):
        self.coll, self.filters, self.order, self.lim = coll, list(filters), order, lim

    def where(self, field, op, value):
        assert op == "==", "the portal only uses equality filters (no custom indexes)"
        return _Query(self.coll, self.filters + [(field, value)], self.order, self.lim)

    def order_by(self, field, direction="ASCENDING"):
        assert not self.filters, "order_by with a filter would need a custom index"
        return _Query(self.coll, self.filters, (field, direction), self.lim)

    def limit(self, n):
        return _Query(self.coll, self.filters, self.order, n)

    def stream(self):
        rows = [(k, v) for k, v in list(self.coll.data.items()) if all(v.get(f) == x for f, x in self.filters)]
        if self.order:
            rows.sort(key=lambda kv: kv[1].get(self.order[0]), reverse=self.order[1] == "DESCENDING")
        if self.lim is not None:
            rows = rows[:self.lim]
        return [_Snap(_Doc(self.coll, k), v) for k, v in rows]

    def count(self):
        n = len(self.stream())

        class _Agg:
            def get(self_inner):
                return [[type("R", (), {"value": n})()]]
        return _Agg()


class _Coll(_Query):
    def __init__(self):
        self.data = {}
        super().__init__(self)

    def document(self, id_):
        return _Doc(self, id_)

    def add(self, d):
        self.data[uuid.uuid4().hex] = dict(d)


class FakeFirestore:
    def __init__(self):
        self.colls = {}

    def collection(self, name):
        return self.colls.setdefault(name, _Coll())


# ── tests ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICEAXE_ADMIN_PASSWORD_HASH", "")
    seed = secrets.token_bytes(32)
    cfg = Config(data_dir=str(tmp_path / "d"), seed=seed, cookie_name="__session")
    db = FakeFirestore()
    return make_app(cfg, store=FirestoreStore(client=db)), db


def _call(app, method, path, body=None, cookie="", form=None, ip="203.0.113.4"):
    from urllib.parse import urlencode
    data = json.dumps(body).encode() if body is not None else (urlencode(form).encode() if form else b"")
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "REMOTE_ADDR": ip,
           "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data), "HTTP_COOKIE": cookie}
    out = {}
    out["body"] = b"".join(app(env, lambda s, h: out.update(status=int(s.split()[0]), headers=h))).decode()
    return out


def test_keys_devices_and_revocation_live_in_firestore(app):
    portal, db = app
    row = portal.lic.issue("review", "Employee", days=30, max_devices=1, note="first")
    assert db.colls["oa_keys"].data[row["key_id"]]["holder"] == "Employee"
    act = lambda dev, a="activate": json.loads(_call(portal, "POST", f"/api/v1/{a}", {
        "key": row["key"], "device_id": dev, "nonce": "n"})["body"])["payload"]
    assert act("pc1")["ok"]
    assert act("pc2")["status"] == "device_limit"
    assert portal.lic.key_view(row["key_id"])["devices_used"] == 1
    assert portal.lic.release_device(row["key_id"], "pc1")
    assert act("pc2")["ok"]
    portal.lic.set_revoked(row["key_id"], True)
    assert act("pc2", "check")["status"] == "revoked"
    assert portal.store.revoked_ids() == [row["key_id"]]
    rows = portal.lic.list_keys()
    assert rows[0]["devices_used"] == 1 and rows[0]["last_seen"]
    assert [a["action"] for a in portal.store.activity(10, row["key_id"])][:1] == ["revoke"]
    assert portal.store.download_count() == 0
    portal.store.count_download("2026-10-31T00:00:00Z")
    assert portal.store.download_count() == 1


def test_admin_login_from_the_bootstrap_hash_then_stored_in_firestore(app, monkeypatch):
    portal, db = app
    from auth import hash_password
    monkeypatch.setenv("OFFICEAXE_ADMIN_PASSWORD_HASH", hash_password("first-pass-123"))
    assert portal.account.is_set() and portal.account.verify("admin", "first-pass-123")
    # remote visitors never get the setup page on a server
    assert _call(portal, "GET", "/admin/setup")["status"] in (303, 403)
    r = _call(portal, "POST", "/admin/login", form={"user": "admin", "password": "first-pass-123"})
    assert r["status"] == 303
    cookie = [v for k, v in r["headers"] if k == "Set-Cookie"][0]
    assert cookie.startswith("__session=")                 # the one cookie Firebase Hosting passes on
    sess = cookie.split(";")[0]
    assert _call(portal, "GET", "/admin", cookie=sess)["status"] == 200
    portal.account.set_password("second-pass-456")         # a change is kept in Firestore
    assert db.colls["oa_meta"].data["admin"]["password_hash"].startswith("pbkdf2_sha256$")
    assert portal.account.verify("admin", "second-pass-456") and not portal.account.verify("admin", "first-pass-123")
    assert _call(portal, "GET", "/admin", cookie=sess)["status"] == 303   # old session ended


def test_login_rate_limit_on_firestore(app, monkeypatch):
    portal, _ = app
    from auth import hash_password
    monkeypatch.setenv("OFFICEAXE_ADMIN_PASSWORD_HASH", hash_password("first-pass-123"))
    for _ in range(5):
        _call(portal, "POST", "/admin/login", form={"user": "admin", "password": "nope"}, ip="198.51.100.9")
    r = _call(portal, "POST", "/admin/login", form={"user": "admin", "password": "first-pass-123"}, ip="198.51.100.9")
    assert "Too many attempts" in r["body"]


def test_prepare_builds_the_hosting_and_function_bundle(tmp_path, monkeypatch):
    import prepare
    monkeypatch.setattr(prepare, "BUILD", str(tmp_path / "build"))
    out = prepare.build(download_url="https://example.com/OfficeAxe.zip")
    fn, pub = out["functions"], out["public"]
    for f in ("main.py", "requirements.txt", "license_keys.py", "ed25519_pure.py", "revoked_keys.json",
              "webportal/web.py", "webportal/store_firestore.py", "webportal/static/video/chapters.json"):
        assert os.path.isfile(os.path.join(fn, f)), f
    assert os.path.isfile(os.path.join(pub, "static", "portal.css"))
    assert open(os.path.join(fn, ".env"), encoding="utf-8").read().strip() == \
        "OFFICEAXE_DOWNLOAD_URL=https://example.com/OfficeAxe.zip"
    cfg = json.load(open(os.path.join(ROOT, "webportal", "firebase", "firebase.json"), encoding="utf-8"))
    assert cfg["hosting"]["rewrites"][0]["function"]["functionId"] == "portal"
    assert "venv" in cfg["functions"][0]["ignore"]
