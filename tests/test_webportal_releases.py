# -*- coding: utf-8 -*-
"""The release manager merged into the portal (webportal/releases.py): it must
write exactly what the earlier release-manager site wrote, because installed
OfficeAxe copies read meta/current for "Update Now"."""
from __future__ import annotations

import io
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "webportal"))

from config import Config  # noqa: E402
from minidb import MiniFirestore  # noqa: E402
from releases import ReleaseError, ReleaseManager, parse_version  # noqa: E402
from store_firestore import FirestoreStore  # noqa: E402
from web import make_app  # noqa: E402

CURRENT_FIELDS = {"product", "version", "versionCode", "releaseNotes", "downloadUrl", "fileName", "fileSize",
                  "sha256", "mandatory", "releaseId", "publishedAt"}
RELEASE_FIELDS = {"version", "versionCode", "releaseNotes", "downloadUrl", "fileName", "fileSize", "storagePath",
                  "sha256", "mandatory", "status", "isCurrent", "createdAt", "updatedAt", "publishedAt", "updatedBy"}


def test_version_numbers_like_the_earlier_site():
    assert parse_version("1.4.2") == {"version": "1.4.2", "versionCode": 1_004_002_000}
    assert parse_version("v2.0.0.7") == {"version": "2.0.0.7", "versionCode": 2_000_000_007}
    assert parse_version("1") is None and parse_version("1.2.3.4.5") is None and parse_version("1.1000") is None


def test_publish_writes_the_same_documents():
    db = MiniFirestore()
    rm = ReleaseManager(db)
    r1 = rm.create("1.0.0", "First", "https://example.com/dl/OfficeAxe-1.0.0.exe", False, "admin@portal", publish=True)
    assert set(db.collection("releases").document(r1["id"]).get().to_dict()) == RELEASE_FIELDS
    assert len(r1["id"]) == 20 and r1["id"].isalnum()
    cur = rm.current()
    assert set(cur) == CURRENT_FIELDS
    assert cur["product"] == "OfficeAxe" and cur["version"] == "1.0.0" and cur["releaseId"] == r1["id"]
    assert cur["fileName"] == "OfficeAxe-1.0.0.exe" and cur["mandatory"] is False and isinstance(cur["versionCode"], int)
    # a newer version replaces the live one; the old one is no longer current
    r2 = rm.create("1.1.0", "Second", "https://example.com/dl/OfficeAxe-1.1.0.exe", True, "admin@portal")
    assert rm.get(r2["id"])["status"] == "draft" and rm.current()["version"] == "1.0.0"
    rm.publish(r2["id"], "admin@portal")
    assert rm.current()["version"] == "1.1.0" and rm.current()["mandatory"] is True
    assert rm.get(r1["id"])["isCurrent"] is False and rm.get(r2["id"])["isCurrent"] is True
    # editing the live release updates meta/current too, keeping its publish time
    t = rm.current()["publishedAt"]
    rm.edit(r2["id"], "Second, fixed notes", "https://example.com/dl/OfficeAxe-1.1.0b.exe", True, "admin@portal")
    assert rm.current()["releaseNotes"] == "Second, fixed notes" and rm.current()["publishedAt"] == t
    assert rm.current()["fileName"] == "OfficeAxe-1.1.0b.exe"
    # deleting the live release removes meta/current (no update offered)
    rm.delete(r2["id"])
    assert rm.current() == {} and rm.get(r1["id"])


def test_the_earlier_sites_rules():
    rm = ReleaseManager(MiniFirestore())
    rm.create("1.2.0", "x", "https://e.com/a.exe", False, "a", publish=True)
    with pytest.raises(ReleaseError, match="already exists"):
        rm.create("1.2.0", "again", "https://e.com/b.exe", False, "a")
    with pytest.raises(ReleaseError, match="older or equal"):
        rm.create("1.1.9", "older", "https://e.com/c.exe", False, "a", publish=True)
    assert rm.create("1.1.9", "older", "https://e.com/c.exe", False, "a", publish=True, allow_older=True)
    with pytest.raises(ReleaseError, match="download URL"):
        rm.create("2.0.0", "no link", "", False, "a", publish=True)
    d = rm.create("2.0.0", "draft without a link", "", False, "a")
    with pytest.raises(ReleaseError, match="download URL"):
        rm.publish(d["id"], "a")
    with pytest.raises(ReleaseError, match="https"):
        rm.create("2.0.1", "bad link", "javascript:alert(1)", False, "a")


def test_existing_data_from_the_earlier_site_is_read(tmp_path):
    """Documents written by the earlier site (Firestore timestamps, a Storage installer)."""
    db = MiniFirestore()
    ts = datetime(2026, 10, 27, 7, 10, tzinfo=timezone.utc)
    db.collection("releases").document("AbCdEfGhIjKlMnOpQrSt").set({
        "version": "1.3.0", "versionCode": 1_003_000_000, "releaseNotes": "Old notes",
        "downloadUrl": "https://firebasestorage.googleapis.com/v0/b/x/o/releases%2FAbC%2FOfficeAxe-1.3.0.exe?alt=media",
        "fileName": "OfficeAxe-1.3.0.exe", "fileSize": 65_000_000, "storagePath": "releases/AbC/OfficeAxe-1.3.0.exe",
        "sha256": "ab" * 32, "mandatory": False, "status": "published", "isCurrent": True,
        "createdAt": ts, "updatedAt": ts, "publishedAt": ts, "updatedBy": "owner@example.com"})
    db.collection("meta").document("current").set({"product": "OfficeAxe", "version": "1.3.0", "versionCode": 1_003_000_000,
                                                    "releaseId": "AbCdEfGhIjKlMnOpQrSt", "downloadUrl": "https://x/y.exe",
                                                    "publishedAt": ts})
    db.collection("docs").document("g1").set({"title": "Install", "body": "Run the exe", "createdAt": ts})
    db.collection("activity").add({"kind": "download", "version": "1.3.0", "releaseId": "x", "detail": "",
                                   "createdAt": datetime.now(timezone.utc)})
    rm = ReleaseManager(db)
    assert [r["version"] for r in rm.list()] == ["1.3.0"]
    assert rm.guides()[0]["title"] == "Install"
    assert rm.download_stats()["downloads"] == 1
    # an edit with the same link keeps the Storage file details
    rm.edit("AbCdEfGhIjKlMnOpQrSt", "New notes", rm.get("AbCdEfGhIjKlMnOpQrSt")["downloadUrl"], False, "admin@portal")
    r = rm.get("AbCdEfGhIjKlMnOpQrSt")
    assert r["sha256"] == "ab" * 32 and r["fileSize"] == 65_000_000
    note = rm.delete("AbCdEfGhIjKlMnOpQrSt")
    assert "still in Firebase Storage" in note


# ── through the web pages ─────────────────────────────────────────────────────
def _req(app, method, path, form=None, cookie="", ip="127.0.0.1"):
    data = urlencode(form).encode() if form else b""
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "REMOTE_ADDR": ip,
           "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data), "HTTP_COOKIE": cookie}
    out = {}
    out["body"] = b"".join(app(env, lambda s, h: out.update(status=int(s.split()[0]), headers=h))).decode()
    out["location"] = dict(out["headers"]).get("Location", "")
    return out


@pytest.fixture
def site(tmp_path):
    db = MiniFirestore()
    cfg = Config(data_dir=str(tmp_path / "d"), seed=secrets.token_bytes(32), cookie_name="__session")
    app = make_app(cfg, store=FirestoreStore(client=db))
    pw = "admin-pass-123"
    app.account.set_password(pw)
    r = _req(app, "POST", "/admin/login", {"user": "admin", "password": pw})
    cookie = [v for k, v in r["headers"] if k == "Set-Cookie"][0].split(";")[0]
    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', _req(app, "GET", "/admin/releases", cookie=cookie)["body"]).group(1)
    return app, db, cookie, csrf


def test_admin_publishes_and_the_public_pages_follow(site):
    app, db, cookie, csrf = site
    r = _req(app, "POST", "/admin/releases", {"csrf": csrf, "version": "1.5.0", "notes": "Faster PDF merge",
                                               "url": "https://example.com/OfficeAxe-1.5.0.exe", "action": "publish"}, cookie)
    assert r["status"] == 303
    assert db.collection("meta").document("current").get().to_dict()["version"] == "1.5.0"
    home = _req(app, "GET", "/")["body"]
    assert "v1.5.0" in home and "Faster PDF merge" in home
    d = _req(app, "GET", "/download")
    assert d["status"] == 303 and d["location"] == "https://example.com/OfficeAxe-1.5.0.exe"
    kinds = [s.to_dict()["kind"] for s in db.collection("activity").stream()]
    assert kinds == ["download"]                                   # same record the old site kept
    # guides show on /document/
    _req(app, "POST", "/admin/guides", {"csrf": csrf, "title": "Activate a key", "body": "Paste it <b>here</b>"}, cookie)
    doc = _req(app, "GET", "/document/")["body"]
    assert "Activate a key" in doc and "&lt;b&gt;here&lt;/b&gt;" in doc
    assert "৳9,999" in _req(app, "GET", "/pricing/")["body"]
    assert _req(app, "GET", "/login/")["location"] == "/admin/login"
    assert _req(app, "GET", "/dashboard/")["location"] == "/admin/releases"
    # errors come back on the form; no CSRF → refused
    bad = _req(app, "POST", "/admin/releases", {"csrf": csrf, "version": "x", "notes": "n", "action": "draft"}, cookie)
    assert "numeric version" in bad["body"]
    assert _req(app, "POST", "/admin/releases", {"version": "1.6.0", "notes": "n"}, cookie)["status"] == 403
    # our audit log records it
    assert any(a["action"] == "release_publish" for a in app.store.activity(20))
