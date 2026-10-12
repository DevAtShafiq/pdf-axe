"""
Regression tests for bugs found in the accounts / cloud / office QA pass (qa/cloud.md).

Run:  python -m pytest server/tests/test_qa_cloud.py -q
"""
from __future__ import annotations

import os

os.environ["SFM_SERVER_NO_AUTOAPP"] = "1"

from server.tests.test_hardening import _sub_client, _up  # noqa: E402


def _paths(c, h, trashed=False):
    return sorted(f["path"] for f in c.get("/files", headers=h,
                                            params={"trashed": trashed}).json()["files"])


# ── cloud folders are case-sensitive (SQLite LIKE is not) ────────────────────

def test_folder_trash_does_not_touch_other_case_folder(tmp_path):
    c, h = _sub_client(tmp_path)
    _up(c, h, "Work/a.txt")
    _up(c, h, "work/b.txt")
    assert c.post("/folders/trash", headers=h, json={"path": "Work"}).json()["trashed"] == 1
    assert _paths(c, h) == ["work/b.txt"]


def test_folder_move_does_not_take_other_case_folder(tmp_path):
    c, h = _sub_client(tmp_path)
    _up(c, h, "Work/a.txt")
    _up(c, h, "work/b.txt")
    r = c.post("/folders/move", headers=h, json={"path": "Work", "new_path": "Archive"})
    assert r.status_code == 200 and r.json()["moved"] == 1
    assert _paths(c, h) == ["Archive/a.txt", "work/b.txt"]


def test_folder_move_up_into_parent_with_same_names(tmp_path):
    # "a/a" → "a": "a/a/a/x" becomes "a/a/x", a name "a/a/x" is leaving at the same time
    c, h = _sub_client(tmp_path)
    _up(c, h, "a/a/x")
    _up(c, h, "a/a/a/x")
    r = c.post("/folders/move", headers=h, json={"path": "a/a", "new_path": "a"})
    assert r.status_code == 200, r.text
    assert _paths(c, h) == ["a/a/x", "a/x"]


# ── office permissions ───────────────────────────────────────────────────────

from server.tests.test_office import _client as _office_client, _invite, _office, _team, ok  # noqa: E402
from server.tests.test_server import _auth, _register  # noqa: E402


def test_admin_cannot_change_another_admins_role(tmp_path):
    c = _office_client(tmp_path, free_plan=True)
    owner, admin, staff = _team(c)
    ms = ok(c.get("/office/members", headers=_auth(owner)))["members"]
    ids = {m["email"]: m["user_id"] for m in ms}
    ok(c.post(f"/office/members/{ids['staff@example.com']}/role", json={"role": "admin"}, headers=_auth(owner)))
    # admin@ may not demote staff@ (now an admin): that would let them remove an admin
    r = c.post(f"/office/members/{ids['staff@example.com']}/role", json={"role": "staff"}, headers=_auth(admin))
    assert r.status_code == 403
    assert c.post(f"/office/members/{ids['staff@example.com']}/remove", headers=_auth(admin)).status_code == 403
    # an admin may step down themselves; the owner may change anyone
    ok(c.post(f"/office/members/{ids['admin@example.com']}/role", json={"role": "staff"}, headers=_auth(admin)))
    ok(c.post(f"/office/members/{ids['staff@example.com']}/role", json={"role": "staff"}, headers=_auth(owner)))


def test_email_invite_code_only_works_for_that_email(tmp_path):
    c = _office_client(tmp_path, free_plan=True)
    owner = _register(c, "owner@example.com")
    _office(c, owner)
    bob = _register(c, "bob@example.com")
    mallory = _register(c, "mallory@example.com")
    code = _invite(c, owner, "bob@example.com", "admin")["code"]
    r = c.post("/office/join", json={"code": code}, headers=_auth(mallory))
    assert r.status_code == 403 and "different email" in r.json()["detail"]
    assert ok(c.get("/office", headers=_auth(mallory)))["office"] is None
    joined = ok(c.post("/office/join", json={"code": code}, headers=_auth(bob)))["office"]
    assert joined["role"] == "admin"
