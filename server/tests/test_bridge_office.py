"""
Headless checks for the OFFICE / SHARED DRIVE methods on SFMBridge
(office_bridge.py), run against a live account server on a random port.

Run:  python -m pytest server/tests -q
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

os.environ["SFM_SERVER_NO_AUTOAPP"] = "1"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from server.tests.test_bridge_account import _wait, live_server  # noqa: E402,F401


@pytest.fixture()
def make_bridge(tmp_path, monkeypatch):
    import sfm_bridge
    monkeypatch.delenv("SFM_SERVER_URL", raising=False)
    monkeypatch.setenv("SFM_SHARED_CACHE_DIR", str(tmp_path / "cache"))
    made = []

    def make(name):
        b = sfm_bridge.SFMBridge()
        b._SETTINGS_FILE = str(tmp_path / f"settings_{name}.json")
        b._window = None
        b.test_events = []
        b._emit = lambda event, payload=None, _b=b: _b.test_events.append((event, payload))
        b.test_launched = []
        b._shared_launch = lambda path, _b=b: _b.test_launched.append(path)
        made.append(b)
        return b

    yield make
    for b in made:
        b._cloud_stop_services()


def _events(b, name):
    return [p for e, p in b.test_events if e == name]


def _ok(r):
    assert r["ok"], r
    return r


def test_office_bridge_flow(live_server, make_bridge, tmp_path):
    url, app = live_server
    owner, staff = make_bridge("owner"), make_bridge("staff")
    for b in (owner, staff):
        _ok(b.account_set_server(url))

    # Not signed in → readable error
    r = owner.office_get_state()
    assert not r["ok"] and r["need_login"]

    _ok(owner.account_register("owner@example.com", "password123"))
    _ok(staff.account_register("staff@example.com", "password123"))
    st = _ok(owner.office_get_state())
    assert st["office"] is None and st["pending_invites"] == []
    r = owner.shared_list()
    assert not r["ok"] and r.get("need_subscription")   # paid feature, no plan yet

    office = _ok(owner.office_create("Bright Future Consultancy"))["office"]
    assert office["role"] == "owner" and office["plan"]["active"] is False
    assert set(office["plan"]) >= {"active", "status", "plan_label", "billing_available",
                                   "free_plan", "current_period_end"}
    assert not owner.office_create("Again")["ok"]

    inv = _ok(owner.office_invite("staff@example.com", "staff"))["invite"]
    assert set(inv) >= {"id", "code", "email", "role", "expires_at"}
    assert [i["id"] for i in _ok(owner.office_invites())["invites"]] == [inv["id"]]
    pend = _ok(staff.office_get_state())["pending_invites"]
    assert pend == [{"id": inv["id"], "office_name": "Bright Future Consultancy", "role": "staff",
                     "invited_by_email": "owner@example.com", "expires_at": inv["expires_at"]}]
    assert _ok(staff.office_accept_invite(inv["id"]))["office"]["role"] == "staff"
    assert _wait(lambda: any(p.get("reason") == "member_joined" for p in _events(owner, "office_changed")))

    # Office plan: webhook for the office unlocks the shared drive for both
    assert not staff.shared_list()["ok"]
    body = json.dumps({"customer_id": "cus_o", "subscription_id": "sub_o", "status": "active",
                       "current_period_end": time.time() + 30 * 86400, "office_id": office["id"]})
    assert TestClient(app).post("/billing/webhook", content=body,
                                headers={"stripe-signature": "good"}).status_code == 200
    assert _wait(lambda: any(p.get("reason") == "plan" for p in _events(staff, "office_changed")))
    assert staff._require_plan() is None
    assert _ok(staff.office_get_state())["office"]["plan"]["active"] is True

    _ok(owner.office_settings_set({"countries": ["India"], "programs": {"India": ["BSc"]},
                                   "student_subfolders": ["Passport"]}))
    assert _ok(staff.office_settings_get())["settings"]["student_subfolders"] == ["Passport"]
    assert not staff.office_settings_set({"countries": []})["ok"]

    # Hierarchy: staff needs the Country/Program first
    r = staff.shared_new_student("India", "BSc", "Asha")
    assert not r["ok"] and r["error"] == "Ask an admin to add the country/program first"
    r = _ok(owner.shared_new_student("India", "BSc", "Asha"))
    assert r["path"] == "India/BSc/Asha" and "India/BSc/Asha/Passport" in r["created"]
    r = _ok(staff.shared_new_student("India", "BSc", "asha"))
    assert r["existed"] is True and r["path"] == "India/BSc/Asha"
    _ok(staff.shared_mkdir("India/BSc", "Bina"))
    assert not staff.shared_mkdir("", "Nepal")["ok"]

    lst = _ok(staff.shared_list("India/BSc"))
    assert lst["level"] == "program"
    assert [(i["name"], i["level"]) for i in lst["items"]] == [("Asha", "student"), ("Bina", "student")]
    assert lst["breadcrumb"][-1] == {"name": "BSc", "path": "India/BSc", "level": "program"}

    # Upload a file and a folder tree (with an empty sub-folder) into a student
    src = tmp_path / "src"
    (src / "Docs" / "Scans").mkdir(parents=True)
    (src / "Docs" / "Empty").mkdir()
    (src / "cv.pdf").write_bytes(b"CV-1")
    (src / "Docs" / "Scans" / "p1.jpg").write_bytes(b"JPG")
    owner.test_events.clear()
    r = _ok(staff.shared_upload([str(src / "cv.pdf"), str(src / "Docs")], "India/BSc/Asha", job_id="up1"))
    assert r == {"ok": True, "started": True, "job_id": "up1"}
    assert _wait(lambda: _events(staff, "shared_upload_done"))
    done = _events(staff, "shared_upload_done")[0]
    assert done["ok"] and done["job_id"] == "up1", done
    assert sorted(done["uploaded"]) == ["India/BSc/Asha/Docs/Scans/p1.jpg", "India/BSc/Asha/cv.pdf"]
    prog = _events(staff, "shared_upload_progress")
    assert prog and set(prog[-1]) >= {"job_id", "file", "index", "total", "bytes", "total_bytes"}
    names = [i["name"] for i in _ok(staff.shared_list("India/BSc/Asha"))["items"]]
    assert names == ["Docs", "Passport", "cv.pdf"]
    assert [i["name"] for i in _ok(staff.shared_list("India/BSc/Asha/Docs"))["items"]] == ["Empty", "Scans"]
    cv = [i for i in _ok(staff.shared_list("India/BSc/Asha"))["items"] if i["name"] == "cv.pdf"][0]
    assert cv["created_by"] == "staff@example.com" and cv["size_str"] == "4 B"
    # The other member hears about it live
    assert _wait(lambda: any(p["actor"] == "staff@example.com" for p in _events(owner, "shared_changed")))

    # Same name again → kept both (rename), never silently replaced
    staff.test_events.clear()
    (tmp_path / "cv.pdf").write_bytes(b"CV-2")
    _ok(staff.shared_upload([str(tmp_path / "cv.pdf")], "India/BSc/Asha"))
    assert _wait(lambda: _events(staff, "shared_upload_done"))
    assert _events(staff, "shared_upload_done")[0]["uploaded"] == ["India/BSc/Asha/cv (2).pdf"]
    # Files can't go above student level
    staff.test_events.clear()
    _ok(staff.shared_upload([str(tmp_path / "cv.pdf")], "India/BSc"))
    assert _wait(lambda: _events(staff, "shared_upload_done"))
    d = _events(staff, "shared_upload_done")[0]
    assert not d["ok"] and "student" in d["errors"][0]["error"]

    # Download a folder twice: structure kept, nothing overwritten
    dl = tmp_path / "dl"
    dl.mkdir()
    for n in (1, 2):
        owner.test_events.clear()
        _ok(owner.shared_download(["India/BSc/Asha/Docs", "India/BSc/Asha/cv.pdf"], str(dl), job_id=f"d{n}"))
        assert _wait(lambda: _events(owner, "shared_download_done"))
        dd = _events(owner, "shared_download_done")[0]
        assert dd["ok"] and dd["job_id"] == f"d{n}" and len(dd["files"]) == 2, dd
    assert (dl / "cv.pdf").read_bytes() == b"CV-1" and (dl / "cv (2).pdf").read_bytes() == b"CV-1"
    assert (dl / "Docs" / "Scans" / "p1.jpg").read_bytes() == b"JPG"
    assert (dl / "Docs" / "Scans" / "p1 (2).jpg").exists() and (dl / "Docs" / "Empty").is_dir()

    # Open: cached under the office id; a locally edited cache copy is kept
    r = _ok(staff.shared_open("India/BSc/Asha/cv.pdf"))
    cache = tmp_path / "cache" / str(office["id"]) / "India" / "BSc" / "Asha" / "cv.pdf"
    assert r["local_path"] == str(cache) and staff.test_launched == [str(cache)]
    assert _ok(staff.shared_open("India/BSc/Asha/cv.pdf"))["cached"] is True
    cache.write_bytes(b"edited locally")
    r = _ok(staff.shared_open("India/BSc/Asha/cv.pdf"))
    assert r["local_path"].endswith("cv (2).pdf") and cache.read_bytes() == b"edited locally"
    assert not staff.shared_open("India/BSc/Asha/Docs")["ok"]

    # Rename / move / trash / restore / search / activity / usage
    _ok(staff.shared_rename("India/BSc/Asha/cv (2).pdf", "cv old.pdf"))
    r = _ok(staff.shared_move(["India/BSc/Asha/cv old.pdf"], "India/BSc/Bina"))
    assert r["moved"] == [{"from": "India/BSc/Asha/cv old.pdf", "to": "India/BSc/Bina/cv old.pdf"}]
    r = staff.shared_rename("India", "IN")
    assert not r["ok"] and r["status"] == 403
    _ok(staff.shared_trash(["India/BSc/Bina"]))
    tl = _ok(owner.shared_trash_list())["items"]
    assert [(t["path"], t["deleted_by"]) for t in tl] == [("India/BSc/Bina", "staff@example.com")]
    assert set(tl[0]) >= {"id", "name", "path", "deleted_at", "deleted_by"}
    r = _ok(owner.shared_restore([tl[0]["id"]]))
    assert r["restored"][0]["path"] == "India/BSc/Bina"
    hits = _ok(staff.shared_search("cv"))["items"]
    assert {h["path"] for h in hits} == {"India/BSc/Asha/cv.pdf", "India/BSc/Bina/cv old.pdf"}
    assert _ok(staff.shared_search(""))["items"] == []
    acts = [e["action"] for e in _ok(staff.shared_activity(200))["events"]]
    for a in ("upload", "rename", "move", "trash", "restore", "new_student", "create_folder"):
        assert a in acts, a
    u = _ok(staff.shared_usage())
    assert u["used"] == 4 + 3 + 4 and u["quota"] > 0

    # Members, roles, leave/transfer rules
    ms = _ok(owner.office_members())["members"]
    sid = [m["user_id"] for m in ms if m["email"] == "staff@example.com"][0]
    assert [m["is_you"] for m in ms] == [True, False]
    r = owner.office_leave()
    assert not r["ok"] and "Transfer" in r["error"]
    _ok(owner.office_set_role(sid, "admin"))
    assert _ok(staff.office_get_state())["office"]["role"] == "admin"
    assert not staff.office_remove_member(ms[0]["user_id"])["ok"]   # can't remove the owner
    _ok(owner.office_transfer(sid))
    assert _ok(owner.office_get_state())["office"]["role"] == "admin"
    _ok(staff.office_remove_member(ms[0]["user_id"]))
    st = _ok(owner.office_get_state())
    assert st["office"] is None
    r = owner.shared_list()
    assert not r["ok"]
    # Code join + decline + revoke through the bridge
    code_inv = _ok(staff.office_invite("", "staff"))["invite"]
    _ok(staff.office_revoke_invite(code_inv["id"]))
    assert not owner.office_join(code_inv["code"])["ok"]
    code_inv = _ok(staff.office_invite("owner@example.com", "staff"))["invite"]
    _ok(owner.office_decline_invite(code_inv["id"]))
    code_inv = _ok(staff.office_invite("", "admin"))["invite"]
    assert _ok(owner.office_join(code_inv["code"].lower()))["office"]["role"] == "admin"
    assert _ok(owner.office_rename("BFC"))["office"]["name"] == "BFC"


def test_office_bridge_without_server(make_bridge):
    b = make_bridge("none")
    r = b.office_get_state()
    assert not r["ok"] and r["need_login"]
    assert not b.shared_upload([], "")["ok"]
    assert not b.shared_download(["x"], "Z:/definitely/missing")["ok"]
