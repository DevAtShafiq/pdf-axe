"""
Headless checks for the ACCOUNT / CLOUD methods on SFMBridge (sfm_bridge.py),
run against a live account server.

Run:  python -m pytest server/tests -q
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

import pytest

os.environ["SFM_SERVER_NO_AUTOAPP"] = "1"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.tests.test_server import FakeBilling, _settings  # noqa: E402


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


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    import sfm_bridge
    monkeypatch.delenv("SFM_SERVER_URL", raising=False)
    monkeypatch.setattr(sfm_bridge.SFMBridge, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: opened.append(url) or True)
    b = sfm_bridge.SFMBridge()
    b._window = None
    events = []
    b._emit = lambda event, payload=None: events.append((event, payload))
    b.test_events = events
    b.test_opened = opened
    yield b
    b._cloud_stop_services()


def _activate(app, user_id=1, status="active"):
    body = json.dumps({"customer_id": f"cus_{user_id}", "subscription_id": "sub_1", "status": status,
                       "current_period_end": time.time() + 30 * 86400, "user_id": user_id})
    r = TestClient(app).post("/billing/webhook", content=body, headers={"stripe-signature": "good"})
    assert r.status_code == 200, r.text


def _names(b):
    return [e for e, _ in b.test_events]


def test_unconfigured_does_not_block(bridge):
    st = bridge.account_get_state()
    assert st["ok"] and st["configured"] is False and st["logged_in"] is False
    assert bridge._require_plan() is None   # no server → features unrestricted


def test_account_flow_and_cloud(live_server, bridge, tmp_path):
    url, app = live_server
    b = bridge

    r = b.account_set_server(url)
    assert r["ok"] and r["reachable"] and r["server_url"] == url
    st = b.account_get_state()
    assert st["configured"] and not st["logged_in"]

    # Plan gate: configured but not signed in
    gate = b._require_plan()
    assert gate and gate["error"] == "An active subscription is required"

    # Register, token never leaks to JS
    r = b.account_register("bridge@example.com", "password123")
    assert r["ok"], r
    safe = b.get_settings()["settings"]
    assert "cloud_token" not in safe and "cloud_token_enc" not in safe
    stored = b._load_settings()
    assert stored["cloud_token_enc"] and not stored.get("cloud_token")
    if sys.platform == "win32":   # DPAPI: never stored in clear text
        assert stored["cloud_token_enc"].startswith("dpapi:")
        assert b._cloud_token() not in stored["cloud_token_enc"]
    assert "account_changed" in _names(b)

    st = b.account_get_state()
    assert st["logged_in"] and st["user"]["email"] == "bridge@example.com"
    assert st["user"]["subscription"]["active"] is False
    assert _wait(lambda: app.state.hub.subscriber_count(1) == 1)   # live stream up

    # Not subscribed → files are locked, plan gate refuses
    r = b.cloud_list()
    assert not r["ok"] and r["status"] == 402 and r["need_subscription"]
    assert b._require_plan()["error"] == "An active subscription is required"

    # Checkout opens the browser
    r = b.billing_open_checkout()
    assert r["ok"] and b.test_opened[-1] == "https://pay.example/checkout/1"

    # Webhook → live subscription_updated → account_changed
    b.test_events.clear()
    _activate(app)
    assert _wait(lambda: any(e == "cloud_event" and p["event"] == "subscription_updated"
                             for e, p in b.test_events))
    assert _wait(lambda: "account_changed" in _names(b))
    assert b._require_plan() is None
    assert b.account_get_state()["user"]["subscription"]["active"] is True

    r = b.billing_open_portal()
    assert r["ok"] and b.test_opened[-1].endswith("cus_1")

    # Upload two files and a folder
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "a.txt").write_text("alpha")
    (src / "docs" / "b.txt").write_text("bravo")
    b.test_events.clear()
    assert b.cloud_upload([str(src / "a.txt"), str(src / "docs")], "Inbox")["ok"]
    assert _wait(lambda: "cloud_upload_done" in _names(b))
    done = [p for e, p in b.test_events if e == "cloud_upload_done"][0]
    assert done["ok"] and done["uploaded"] == 2, done
    assert _wait(lambda: len([1 for e, p in b.test_events
                              if e == "cloud_event" and p["event"] == "file_updated"]) >= 2)

    files = b.cloud_list()["files"]
    paths = sorted(f["path"] for f in files)
    assert paths == ["Inbox/a.txt", "Inbox/docs/b.txt"]
    u = b.cloud_usage()
    assert u["ok"] and u["used"] == 10 and u["quota"] > 0

    # Download twice into the same folder → second copy gets " (2)"
    dest = tmp_path / "dl"
    dest.mkdir()
    a_id = next(f["id"] for f in files if f["path"] == "Inbox/a.txt")
    for n in (1, 2):
        b.test_events.clear()
        assert b.cloud_download([a_id], str(dest))["ok"]
        assert _wait(lambda: "cloud_download_done" in _names(b))
        dd = [p for e, p in b.test_events if e == "cloud_download_done"][0]
        assert dd["ok"] and dd["downloaded"] == 1, dd
    assert (dest / "a.txt").read_text() == "alpha"
    assert (dest / "a (2).txt").read_text() == "alpha"

    # Trash and restore
    b.test_events.clear()
    assert b.cloud_trash([a_id])["done"] == 1
    assert [f["id"] for f in b.cloud_list(True)["files"]] == [a_id]
    assert _wait(lambda: any(e == "cloud_event" and p["event"] == "file_trashed" for e, p in b.test_events))
    assert b.cloud_restore([a_id])["done"] == 1
    assert b.cloud_list(True)["files"] == []

    # Auto-sync folder
    work = tmp_path / "Work"
    work.mkdir()
    (work / "w.txt").write_text("work")
    b.test_events.clear()
    r = b.cloud_sync_start(str(work))
    assert r["ok"] and r["remote_root"] == "Work", r
    assert _wait(lambda: "cloud_sync_done" in _names(b))
    stats = [p for e, p in b.test_events if e == "cloud_sync_done"][0]
    assert stats["uploaded"] == 1 and stats["errors"] == 0, stats
    st = b.cloud_sync_status()
    assert st["running"] and st["folder"] == str(work) and st["last"]["uploaded"] == 1
    assert b._load_settings()["cloud_sync_folder"] == str(work)

    # A file uploaded from elsewhere reaches the synced folder via the live poke
    other = tmp_path / "other.txt"
    other.write_text("from another device")
    from cloud_client import CloudClient
    c2 = CloudClient(url)
    c2.login("bridge@example.com", "password123")
    c2.upload(str(other), "Work/remote.txt")
    assert _wait(lambda: (work / "remote.txt").exists(), timeout=15)

    # Logout stops everything but keeps the sync folder for next sign-in
    assert b.account_logout()["ok"]
    assert b._load_settings()["cloud_token_enc"] == "" and b._cloud_token() == ""
    assert b.cloud_sync_status()["running"] is False
    assert b._load_settings()["cloud_sync_folder"] == str(work)
    assert not b.account_get_state()["logged_in"]
    assert _wait(lambda: app.state.hub.subscriber_count(1) == 0)   # live stream closed

    # Sign in again → sync auto-resumes
    r = b.account_login("bridge@example.com", "password123")
    assert r["ok"]
    assert _wait(lambda: b.cloud_sync_status()["running"])
    assert b.account_login("bridge@example.com", "wrong-password")["status"] == 401

    b.cloud_sync_stop()
    assert b._load_settings()["cloud_sync_folder"] == ""


def test_invalid_token_is_cleared(live_server, bridge):
    url, _app = live_server
    b = bridge
    b.account_set_server(url)
    b._save_settings({"cloud_token": "not-a-real-token"})
    st = b.account_get_state()
    assert st["ok"] and not st["logged_in"]
    assert b._load_settings()["cloud_token_enc"] == "" and b._cloud_token() == ""
    assert "account_changed" in _names(b)


def test_offline_server(bridge):
    b = bridge
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    r = b.account_set_server(f"127.0.0.1:{port}")
    assert r["ok"] and r["server_url"] == f"http://127.0.0.1:{port}" and r["reachable"] is False
    b._save_settings({"cloud_token": "tok"})
    st = b.account_get_state()
    assert st["ok"] and st["logged_in"] and st["offline"]
    r = b.account_login("x@example.com", "password123")
    assert not r["ok"] and r["offline"]
