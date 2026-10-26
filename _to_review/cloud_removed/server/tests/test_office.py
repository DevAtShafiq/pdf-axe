"""
Tests for offices (membership, invites, roles, billing) and the office shared drive.

Run:  python -m pytest server/tests -q
"""
from __future__ import annotations

import io
import json
import os
import time

import pytest

os.environ["SFM_SERVER_NO_AUTOAPP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.office import normalise_code  # noqa: E402
from server.tests.test_server import (FakeBilling, _auth, _register, _settings,  # noqa: E402,F401
                                      _wait, live_server)


def _client(tmp_path, **kw):
    kw.setdefault("register_per_hour", 1000)
    return TestClient(create_app(_settings(tmp_path, **kw), FakeBilling()))


@pytest.fixture()
def c(tmp_path):
    return _client(tmp_path, free_plan=True)


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def _office(c, token, name="Head Office"):
    return ok(c.post("/office", json={"name": name}, headers=_auth(token)))["office"]


def _invite(c, token, email="", role="staff"):
    return ok(c.post("/office/invites", json={"email": email, "role": role}, headers=_auth(token)))["invite"]


def _team(c):
    """owner + admin + staff in one office; returns tokens."""
    owner = _register(c, "owner@example.com")
    _office(c, owner)
    admin = _register(c, "admin@example.com")
    staff = _register(c, "staff@example.com")
    ok(c.post("/office/join", json={"code": _invite(c, owner, role="admin")["code"]}, headers=_auth(admin)))
    ok(c.post("/office/join", json={"code": _invite(c, owner)["code"]}, headers=_auth(staff)))
    return owner, admin, staff


def _up(c, token, path, data=b"hello", **form):
    return c.post("/shared/upload", data={"path": path, **form},
                  files={"file": ("f", io.BytesIO(data))}, headers=_auth(token))


def _ls(c, token, path=""):
    return ok(c.get("/shared/list", params={"path": path}, headers=_auth(token)))


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_office_lifecycle(c):
    t = _register(c, "boss@example.com")
    st = ok(c.get("/office", headers=_auth(t)))
    assert st == {"office": None, "pending_invites": []}
    assert c.get("/shared/list", headers=_auth(t)).status_code == 404

    o = _office(c, t, "  Global   Study  ")
    assert o["name"] == "Global Study" and o["role"] == "owner" and o["member_count"] == 1
    assert o["settings"]["enforce_hierarchy"] is True and o["plan"]["active"] is True
    assert c.post("/office", json={"name": "Second"}, headers=_auth(t)).status_code == 409
    assert c.post("/office", json={"name": "  "}, headers=_auth(_register(c, "x@example.com"))).status_code == 400

    assert ok(c.post("/office/rename", json={"name": "GS Ltd"}, headers=_auth(t)))["office"]["name"] == "GS Ltd"
    me = ok(c.get("/me", headers=_auth(t)))["user"]
    assert me["office"] == {"id": o["id"], "name": "GS Ltd", "role": "owner"}

    # Owner alone can leave; the office is archived
    ok(c.post("/office/leave", headers=_auth(t)))
    assert ok(c.get("/office", headers=_auth(t)))["office"] is None
    assert _office(c, t, "New")["id"] != o["id"]


def test_invites_code_email_expiry_revoke(tmp_path):
    c = _client(tmp_path, free_plan=True)
    owner = _register(c, "owner@example.com")
    office = _office(c, owner)
    bob = _register(c, "bob@example.com")

    # Email invite shows up as pending for bob, who can accept it
    inv = _invite(c, owner, "Bob@Example.com", "admin")
    assert inv["email"] == "bob@example.com" and inv["role"] == "admin"
    assert inv["code"].startswith("OFX-") and len(inv["code"]) == 13
    assert inv["expires_at"] > time.time() + 6.9 * 86400
    pend = ok(c.get("/office", headers=_auth(bob)))["pending_invites"]
    assert pend == [{"id": inv["id"], "office_name": "Head Office", "role": "admin",
                     "invited_by_email": "owner@example.com", "expires_at": inv["expires_at"]}]
    assert [i["id"] for i in ok(c.get("/office/invites", headers=_auth(owner)))["invites"]] == [inv["id"]]
    joined = ok(c.post(f"/office/invites/{inv['id']}/accept", headers=_auth(bob)))["office"]
    assert joined["id"] == office["id"] and joined["role"] == "admin" and joined["member_count"] == 2
    assert ok(c.get("/office/invites", headers=_auth(owner)))["invites"] == []

    # Someone else's email invite cannot be accepted by another user
    carol = _register(c, "carol@example.com")
    inv2 = _invite(c, owner, "dave@example.com")
    assert c.post(f"/office/invites/{inv2['id']}/accept", headers=_auth(carol)).status_code == 404

    # Code join is case/format tolerant and single use
    code = _invite(c, owner)["code"]
    assert normalise_code(code.lower().replace("-", " ")) == code
    ok(c.post("/office/join", json={"code": code.lower()}, headers=_auth(carol)))
    eve = _register(c, "eve@example.com")
    assert c.post("/office/join", json={"code": code}, headers=_auth(eve)).status_code == 404

    # Revoke
    code3 = _invite(c, owner)["code"]
    rid = [i for i in ok(c.get("/office/invites", headers=_auth(owner)))["invites"] if i["code"] == code3][0]["id"]
    ok(c.post(f"/office/invites/{rid}/revoke", headers=_auth(owner)))
    assert c.post("/office/join", json={"code": code3}, headers=_auth(eve)).status_code == 404

    # Expiry
    code4 = _invite(c, owner)["code"]
    db = c.app.state.db
    with db.connect() as con:
        con.execute("UPDATE office_invites SET expires_at=? WHERE code=?", (time.time() - 1, code4))
    r = c.post("/office/join", json={"code": code4}, headers=_auth(eve))
    assert r.status_code == 404 and "expired" in r.json()["detail"]

    # Decline
    inv5 = _invite(c, owner, "eve@example.com")
    ok(c.post(f"/office/invites/{inv5['id']}/decline", headers=_auth(eve)))
    assert ok(c.get("/office", headers=_auth(eve)))["pending_invites"] == []

    # Already in an office
    code6 = _invite(c, owner)["code"]
    assert c.post("/office/join", json={"code": code6}, headers=_auth(bob)).status_code == 409
    # Staff cannot invite; bad role refused
    assert c.post("/office/invites", json={"role": "owner"}, headers=_auth(owner)).status_code == 400
    assert c.post("/office/invites", json={"email": "nope"}, headers=_auth(owner)).status_code == 400


def test_join_code_guessing_is_throttled(c):
    t = _register(c, "guess@example.com")
    for _ in range(10):
        assert c.post("/office/join", json={"code": "OFX-AAAA-AAAA"}, headers=_auth(t)).status_code == 404
    assert c.post("/office/join", json={"code": "OFX-AAAA-AAAA"}, headers=_auth(t)).status_code == 429


def test_roles_members_transfer(c):
    owner, admin, staff = _team(c)
    ms = ok(c.get("/office/members", headers=_auth(staff)))["members"]
    assert [(m["email"], m["role"]) for m in ms] == [
        ("owner@example.com", "owner"), ("admin@example.com", "admin"), ("staff@example.com", "staff")]
    assert [m["is_you"] for m in ms] == [False, False, True]
    assert all(m["last_active"] > 0 for m in ms)
    ids = {m["email"]: m["user_id"] for m in ms}

    # Staff cannot manage
    assert c.post("/office/invites", json={}, headers=_auth(staff)).status_code == 403
    assert c.post(f"/office/members/{ids['admin@example.com']}/remove", headers=_auth(staff)).status_code == 403
    assert c.post("/office/settings", json={"settings": {}}, headers=_auth(staff)).status_code == 403
    # Nobody removes the owner; admins cannot remove admins
    assert c.post(f"/office/members/{ids['owner@example.com']}/remove", headers=_auth(admin)).status_code == 403
    assert c.post(f"/office/members/{ids['owner@example.com']}/role", json={"role": "staff"},
                  headers=_auth(admin)).status_code == 400
    # Role change
    ok(c.post(f"/office/members/{ids['staff@example.com']}/role", json={"role": "admin"}, headers=_auth(admin)))
    assert ok(c.get("/office", headers=_auth(staff)))["office"]["role"] == "admin"
    ok(c.post(f"/office/members/{ids['staff@example.com']}/role", json={"role": "staff"}, headers=_auth(owner)))

    # Owner can't leave while others are there; transfer first
    r = c.post("/office/leave", headers=_auth(owner))
    assert r.status_code == 409 and "Transfer" in r.json()["detail"]
    assert c.post("/office/transfer", json={"user_id": ids["admin@example.com"]},
                  headers=_auth(admin)).status_code == 403
    ok(c.post("/office/transfer", json={"user_id": ids["admin@example.com"]}, headers=_auth(owner)))
    assert ok(c.get("/office", headers=_auth(admin)))["office"]["role"] == "owner"
    assert ok(c.get("/office", headers=_auth(owner)))["office"]["role"] == "admin"
    ok(c.post("/office/leave", headers=_auth(owner)))

    # Remove a member: they lose access
    ok(c.post(f"/office/members/{ids['staff@example.com']}/remove", headers=_auth(admin)))
    assert c.get("/shared/list", headers=_auth(staff)).status_code == 404
    assert ok(c.get("/office", headers=_auth(admin)))["office"]["member_count"] == 1


def test_settings_validation(c):
    owner, admin, _staff = _team(c)
    s = ok(c.post("/office/settings", json={"settings": {
        "countries": ["India", "Nepal", "india", " "],
        "programs": {"India": ["BSc", "MBA"], "*": ["Foundation"]},
        "student_subfolders": ["Passport", "Offer/Scans"],
        "enforce_hierarchy": False, "unknown": 1}}, headers=_auth(admin)))["settings"]
    assert s == {"countries": ["India", "Nepal"], "programs": {"India": ["BSc", "MBA"], "*": ["Foundation"]},
                 "student_subfolders": ["Passport", "Offer/Scans"], "enforce_hierarchy": False}
    s = ok(c.post("/office/settings", json={"settings": {"programs": ["A", "B"]}}, headers=_auth(owner)))["settings"]
    assert s["programs"] == ["A", "B"] and s["countries"] == ["India", "Nepal"]
    assert ok(c.get("/office/settings", headers=_auth(_staff)))["settings"] == s
    for bad in ({"countries": "India"}, {"countries": ["a/b"]}, {"countries": ["CON"]},
                {"enforce_hierarchy": "yes"}, {"student_subfolders": ["../x"]}, {"programs": [1]}):
        assert c.post("/office/settings", json={"settings": bad}, headers=_auth(owner)).status_code == 400, bad


# ── shared drive ─────────────────────────────────────────────────────────────

def test_hierarchy_permissions_and_levels(c):
    owner, admin, staff = _team(c)
    ok(c.post("/office/settings", json={"settings": {"student_subfolders": ["Passport", "Docs/Scans"]}},
              headers=_auth(owner)))

    # Staff cannot create Country/Program folders when enforced
    r = c.post("/shared/mkdir", json={"parent": "", "name": "India"}, headers=_auth(staff))
    assert r.status_code == 403 and "Country" in r.json()["detail"]
    r = c.post("/shared/new-student", json={"country": "India", "program": "BSc", "student": "Asha"},
               headers=_auth(staff))
    assert r.status_code == 403 and r.json()["detail"] == "Ask an admin to add the country/program first"

    # Admin creates the levels
    ok(c.post("/shared/mkdir", json={"parent": "", "name": "India"}, headers=_auth(admin)))
    assert c.post("/shared/mkdir", json={"parent": "", "name": "india"}, headers=_auth(admin)).status_code == 409
    ok(c.post("/shared/mkdir", json={"parent": "India", "name": "BSc"}, headers=_auth(admin)))
    # Staff creates a student (case-insensitive match of existing levels)
    r = ok(c.post("/shared/new-student", json={"country": "INDIA", "program": "bsc", "student": "Asha Rai"},
                  headers=_auth(staff)))
    assert r["path"] == "India/BSc/Asha Rai" and r["existed"] is False
    assert r["created"] == ["India/BSc/Asha Rai", "India/BSc/Asha Rai/Passport",
                            "India/BSc/Asha Rai/Docs", "India/BSc/Asha Rai/Docs/Scans"]
    r = ok(c.post("/shared/new-student", json={"country": "India", "program": "BSc", "student": "asha rai"},
                  headers=_auth(staff)))
    assert r == {"path": "India/BSc/Asha Rai", "existed": True, "created": []}
    # Staff mkdir at student level works and gets the template sub-folders
    r = ok(c.post("/shared/mkdir", json={"parent": "India/BSc", "name": "Bina"}, headers=_auth(staff)))
    assert "India/BSc/Bina/Passport" in r["created"]

    # Levels in listings
    root = _ls(c, staff)
    assert root["level"] is None and root["child_level"] == "country" and root["can_create_folder"] is False
    assert [(i["name"], i["level"], i["child_count"]) for i in root["items"]] == [("India", "country", 1)]
    prog = _ls(c, staff, "india/bsc")
    assert prog["path"] == "India/BSc" and prog["level"] == "program" and prog["can_upload"] is False
    assert prog["breadcrumb"] == [{"name": "India", "path": "India", "level": "country"},
                                  {"name": "BSc", "path": "India/BSc", "level": "program"}]
    assert [(i["name"], i["level"], i["child_count"]) for i in prog["items"]] == [
        ("Asha Rai", "student", 2), ("Bina", "student", 2)]
    stu = _ls(c, staff, "India/BSc/Asha Rai")
    assert stu["level"] == "student" and stu["can_upload"] is True and stu["can_create_folder"] is True
    assert all(i["level"] is None for i in stu["items"])

    # Files only inside a student
    r = _up(c, admin, "India/BSc/loose.pdf")
    assert r.status_code == 400 and "student" in r.json()["detail"]
    up = ok(_up(c, staff, "India/BSc/Asha Rai/Passport/p.pdf", b"PDF"))["file"]
    assert up["created_by"] == "staff@example.com" and up["size"] == 3 and up["level"] is None
    # Uploading into a new sub-folder of a student creates it
    ok(_up(c, staff, "India/BSc/Asha Rai/New/x.txt"))
    # ...but not new Country/Program levels for staff
    assert _up(c, staff, "Nepal/BBA/Ram/x.txt").status_code == 403

    # Staff cannot rename/trash/move Country or Program folders; admin can
    assert c.post("/shared/rename", json={"path": "India", "new_name": "IN"}, headers=_auth(staff)).status_code == 403
    assert c.post("/shared/trash", json={"paths": ["India/BSc"]}, headers=_auth(staff)).status_code == 403
    # Staff renames a student, files follow
    ok(c.post("/shared/rename", json={"path": "India/BSc/Bina", "new_name": "Bina K"}, headers=_auth(staff)))
    assert [i["name"] for i in _ls(c, staff, "India/BSc/Bina K")["items"]] == ["Docs", "Passport"]

    # Without enforcement staff may create anything
    ok(c.post("/office/settings", json={"settings": {"enforce_hierarchy": False}}, headers=_auth(owner)))
    ok(c.post("/shared/mkdir", json={"parent": "", "name": "Nepal"}, headers=_auth(staff)))
    ok(_up(c, staff, "readme.txt"))


def test_upload_conflicts_download_rename_move(c):
    owner, admin, staff = _team(c)
    ok(c.post("/shared/new-student", json={"country": "UK", "program": "MSc", "student": "Tom"}, headers=_auth(owner)))
    ok(c.post("/shared/new-student", json={"country": "UK", "program": "MSc", "student": "Ann"}, headers=_auth(owner)))
    f = ok(_up(c, staff, "UK/MSc/Tom/cv.pdf", b"v1"))["file"]
    # Stale base → 409; right base → replace
    assert _up(c, admin, "UK/MSc/Tom/cv.pdf", b"v2", base_sha256="0" * 64).status_code == 409
    f2 = ok(_up(c, admin, "UK/MSc/Tom/cv.pdf", b"v2", base_sha256=f["sha256"]))["file"]
    assert f2["id"] == f["id"] and f2["updated_by"] == "admin@example.com" and f2["created_by"] == "staff@example.com"
    # Keep both / refuse
    r = ok(_up(c, staff, "UK/MSc/Tom/cv.pdf", b"v3", on_conflict="rename"))
    assert r["file"]["path"] == "UK/MSc/Tom/cv (2).pdf" and r["renamed"] is True
    assert _up(c, staff, "UK/MSc/Tom/CV.pdf", b"x", on_conflict="error").status_code == 409
    # A folder name cannot be overwritten by a file
    ok(c.post("/shared/mkdir", json={"parent": "UK/MSc/Tom", "name": "docs"}, headers=_auth(staff)))
    assert _up(c, staff, "UK/MSc/Tom/docs", b"x").status_code == 409

    d = c.get("/shared/download", params={"path": "uk/msc/tom/cv.pdf"}, headers=_auth(staff))
    assert d.status_code == 200 and d.content == b"v2"
    assert c.get("/shared/download", params={"path": "UK/MSc/Tom/none.pdf"}, headers=_auth(staff)).status_code == 404

    # Rename clash, case-only rename
    assert c.post("/shared/rename", json={"path": "UK/MSc/Tom/cv.pdf", "new_name": "cv (2).pdf"},
                  headers=_auth(staff)).status_code == 409
    ok(c.post("/shared/rename", json={"path": "UK/MSc/Tom/cv.pdf", "new_name": "CV.pdf"}, headers=_auth(staff)))
    assert "CV.pdf" in [i["name"] for i in _ls(c, staff, "UK/MSc/Tom")["items"]]

    # Move files to another student; enforce: files cannot go above student level
    r = ok(c.post("/shared/move", json={"paths": ["UK/MSc/Tom/CV.pdf", "UK/MSc/Tom/missing"],
                                        "dest": "UK/MSc/Ann"}, headers=_auth(staff)))
    assert r["moved"] == [{"from": "UK/MSc/Tom/CV.pdf", "to": "UK/MSc/Ann/CV.pdf"}]
    assert r["errors"][0]["status"] == 404
    r = c.post("/shared/move", json={"paths": ["UK/MSc/Ann/CV.pdf"], "dest": "UK/MSc"}, headers=_auth(admin))
    assert r.status_code == 400
    # Move a student folder to another program (admin creates program); folder into itself refused
    ok(c.post("/shared/mkdir", json={"parent": "UK", "name": "PhD"}, headers=_auth(admin)))
    ok(c.post("/shared/move", json={"paths": ["UK/MSc/Tom"], "dest": "UK/PhD"}, headers=_auth(staff)))
    assert "cv (2).pdf" in [i["name"] for i in _ls(c, staff, "UK/PhD/Tom")["items"]]
    assert c.post("/shared/move", json={"paths": ["UK/PhD/Tom"], "dest": "UK/PhD/Tom/docs"},
                  headers=_auth(staff)).status_code == 400
    # Staff cannot move a student up to program level (would create a Program)
    assert c.post("/shared/move", json={"paths": ["UK/PhD/Tom"], "dest": "UK"},
                  headers=_auth(staff)).status_code == 403

    # Tree for downloads
    tree = ok(c.get("/shared/tree", params={"path": "UK/PhD/Tom"}, headers=_auth(staff)))["items"]
    assert {i["path"] for i in tree} >= {"UK/PhD/Tom", "UK/PhD/Tom/cv (2).pdf", "UK/PhD/Tom/docs"}


def test_trash_restore_purge_activity_search_usage(c):
    owner, admin, staff = _team(c)
    ok(c.post("/shared/new-student", json={"country": "AU", "program": "BA", "student": "Kim"}, headers=_auth(owner)))
    ok(_up(c, staff, "AU/BA/Kim/a.txt", b"12345"))
    ok(_up(c, staff, "AU/BA/Kim/sub/b.txt", b"123"))
    assert ok(c.get("/shared/usage", headers=_auth(staff)))["used"] == 8

    r = ok(c.post("/shared/trash", json={"paths": ["AU/BA/Kim"]}, headers=_auth(staff)))
    assert r["trashed"] == ["AU/BA/Kim"]
    assert _ls(c, staff, "AU/BA")["items"] == []
    assert ok(c.get("/shared/usage", headers=_auth(staff)))["used"] == 0
    tl = ok(c.get("/shared/trash", headers=_auth(admin)))["items"]
    assert len(tl) == 1 and tl[0]["path"] == "AU/BA/Kim" and tl[0]["deleted_by"] == "staff@example.com"
    assert tl[0]["count"] == 4 and tl[0]["size"] == 8

    # A new "Kim" is created meanwhile → restored one is renamed, contents intact
    ok(c.post("/shared/new-student", json={"country": "AU", "program": "BA", "student": "Kim"}, headers=_auth(staff)))
    r = ok(c.post("/shared/restore", json={"ids": [tl[0]["id"]]}, headers=_auth(staff)))
    assert r["restored"][0]["path"] == "AU/BA/Kim (restored)"
    assert c.get("/shared/download", params={"path": "AU/BA/Kim (restored)/sub/b.txt"},
                 headers=_auth(staff)).content == b"123"
    assert ok(c.get("/shared/trash", headers=_auth(admin)))["items"] == []

    # Trash a file; restoring after its folder was trashed too recreates the folder
    ok(c.post("/shared/trash", json={"paths": ["AU/BA/Kim (restored)/sub/b.txt"]}, headers=_auth(staff)))
    ok(c.post("/shared/trash", json={"paths": ["AU/BA/Kim (restored)"]}, headers=_auth(staff)))
    items = {i["name"]: i for i in ok(c.get("/shared/trash", headers=_auth(staff)))["items"]}
    ok(c.post("/shared/restore", json={"ids": [items["b.txt"]["id"]]}, headers=_auth(staff)))
    assert [i["name"] for i in _ls(c, staff, "AU/BA/Kim (restored)/sub")["items"]] == ["b.txt"]

    # Purge: staff refused, admin allowed; content blob removed from server storage
    kim = items["Kim (restored)"]["id"]
    assert c.post("/shared/purge", json={"ids": [kim]}, headers=_auth(staff)).status_code == 403
    st = c.app.state.settings
    odir = os.path.join(st.storage_dir, f"office_{ok(c.get('/office', headers=_auth(owner)))['office']['id']}")
    before = len([n for n in os.listdir(odir) if not n.endswith((".part", ".failed"))])
    ok(c.post("/shared/purge", json={"ids": [kim]}, headers=_auth(admin)))
    after = len([n for n in os.listdir(odir) if not n.endswith((".part", ".failed"))])
    assert after == before - 1   # a.txt's blob; b.txt was restored and keeps its blob
    assert c.post("/shared/restore", json={"ids": [kim]}, headers=_auth(staff)).status_code == 404

    # Search
    res = ok(c.get("/shared/search", params={"q": "kim"}, headers=_auth(staff)))["items"]
    assert [(i["path"], i["level"]) for i in res] == [("AU/BA/Kim", "student"), ("AU/BA/Kim (restored)", "student")]
    res = ok(c.get("/shared/search", params={"q": "b.t"}, headers=_auth(staff)))["items"]
    assert [i["path"] for i in res] == ["AU/BA/Kim (restored)/sub/b.txt"]
    assert ok(c.get("/shared/search", params={"q": "%"}, headers=_auth(staff)))["items"] == []

    # Activity
    ev = ok(c.get("/shared/activity", params={"limit": 100}, headers=_auth(staff)))["events"]
    actions = [e["action"] for e in ev]
    for a in ("new_student", "upload", "trash", "restore", "purge", "member_joined", "office_created"):
        assert a in actions, a
    assert ev[0]["action"] == "purge" and ev[0]["user"] == "admin@example.com"
    assert ok(c.get("/shared/activity", params={"limit": 2}, headers=_auth(staff)))["events"] == ev[:2]


def test_activity_is_capped(tmp_path):
    c = _client(tmp_path, free_plan=True, office_activity_keep=10)
    t = _register(c, "o@example.com")
    _office(c, t)
    ok(c.post("/office/settings", json={"settings": {"enforce_hierarchy": False}}, headers=_auth(t)))
    for i in range(25):
        ok(c.post("/shared/mkdir", json={"parent": "", "name": f"F{i}"}, headers=_auth(t)))
    ev = ok(c.get("/shared/activity", params={"limit": 500}, headers=_auth(t)))["events"]
    assert len(ev) == 10 and ev[0]["path"] == "F24"


def test_isolation_and_traversal(c):
    a = _register(c, "a@example.com")
    b = _register(c, "b@example.com")
    _office(c, a, "A")
    _office(c, b, "B")
    for t in (a, b):
        ok(c.post("/office/settings", json={"settings": {"enforce_hierarchy": False}}, headers=_auth(t)))
    ok(_up(c, a, "secret/plan.txt", b"A-only"))
    assert c.get("/shared/download", params={"path": "secret/plan.txt"}, headers=_auth(b)).status_code == 404
    assert c.get("/shared/list", params={"path": "secret"}, headers=_auth(b)).status_code == 404
    assert ok(c.get("/shared/search", params={"q": "plan"}, headers=_auth(b)))["items"] == []
    assert ok(c.get("/shared/tree", headers=_auth(b)))["items"] == []
    assert c.post("/shared/trash", json={"paths": ["secret"]}, headers=_auth(b)).status_code == 404
    with c.app.state.db.connect() as con:
        aid = con.execute("SELECT id FROM shared_items WHERE path='secret'").fetchone()["id"]
    ok(c.post("/shared/trash", json={"paths": ["secret"]}, headers=_auth(a)))
    assert c.post("/shared/restore", json={"ids": [aid]}, headers=_auth(b)).status_code == 404
    assert ok(c.get("/shared/activity", headers=_auth(b)))["events"][0]["action"] == "settings_changed"
    # Path traversal / Windows-unsafe names
    for bad in ("../x.txt", "a/../../x.txt", "a/b:c.txt", "con/x.txt", "a/x. ", "a\\..\\x"):
        assert _up(c, b, bad).status_code == 400, bad
    for name in ("..", "a/b", "x?", "nul"):
        assert c.post("/shared/mkdir", json={"parent": "", "name": name}, headers=_auth(b)).status_code == 400
    assert c.get("/shared/list", params={"path": "../.."}, headers=_auth(b)).status_code == 400


def test_quota_and_upload_limit(tmp_path):
    c = _client(tmp_path, free_plan=True, office_quota_mb=1, max_upload_mb=1)
    t = _register(c, "q@example.com")
    _office(c, t)
    ok(c.post("/shared/new-student", json={"country": "C", "program": "P", "student": "S"}, headers=_auth(t)))
    ok(_up(c, t, "C/P/S/a.bin", b"x" * 700_000))
    r = _up(c, t, "C/P/S/b.bin", b"x" * 400_000)
    assert r.status_code == 507
    # Replacing a file frees its old size
    ok(_up(c, t, "C/P/S/a.bin", b"y" * 900_000))
    assert ok(c.get("/shared/usage", headers=_auth(t))) == {"used": 900_000, "quota": 1024 * 1024,
                                                          "max_upload": 1024 * 1024}
    # Restore refused when it no longer fits
    ok(c.post("/shared/trash", json={"paths": ["C/P/S/a.bin"]}, headers=_auth(t)))
    ok(_up(c, t, "C/P/S/c.bin", b"z" * 500_000))
    tid = ok(c.get("/shared/trash", headers=_auth(t)))["items"][0]["id"]
    assert c.post("/shared/restore", json={"ids": [tid]}, headers=_auth(t)).status_code == 507


# ── billing ──────────────────────────────────────────────────────────────────

def _hook(c, **fields):
    body = {"customer_id": "", "subscription_id": "sub_o", "status": "active",
            "current_period_end": time.time() + 30 * 86400}
    body.update(fields)
    r = c.post("/billing/webhook", content=json.dumps(body), headers={"stripe-signature": "good"})
    assert r.status_code == 200, r.text
    return r.json()


def test_office_billing(tmp_path):
    c = _client(tmp_path)   # paid server
    owner, admin, staff = _team(c)
    loner = _register(c, "loner@example.com")
    oid = ok(c.get("/office", headers=_auth(owner)))["office"]["id"]

    # Office not paid → shared drive locked for everyone
    assert c.get("/shared/list", headers=_auth(staff)).status_code == 402
    assert ok(c.get("/office", headers=_auth(staff)))["office"]["plan"]["active"] is False
    sub = ok(c.get("/me", headers=_auth(staff)))["user"]["subscription"]
    assert sub["active"] is False and sub["source"] == "office" and sub["can_manage_billing"] is False

    # Only the owner checks out, for the office
    assert c.post("/billing/checkout", headers=_auth(staff)).status_code == 403
    assert ok(c.post("/billing/checkout", headers=_auth(owner)))["url"] == f"https://pay.example/checkout/office/{oid}"
    with c.app.state.db.connect() as con:
        assert con.execute("SELECT stripe_customer_id FROM offices WHERE id=?", (oid,)).fetchone()[0] == f"cus_office_{oid}"

    # Webhook (office id in metadata) activates every member
    assert _hook(c, office_id=oid, customer_id=f"cus_office_{oid}", event_id="e1", event_created=100)["office_id"] == oid
    for t in (owner, admin, staff):
        s = ok(c.get("/me", headers=_auth(t)))["user"]["subscription"]
        assert s["active"] is True and s["source"] == "office" and s["office_id"] == oid
    assert c.get("/shared/list", headers=_auth(staff)).status_code == 200
    assert c.get("/files", headers=_auth(staff)).status_code == 200   # private "My files" too
    # A user without an office keeps the per-user model
    assert ok(c.get("/me", headers=_auth(loner)))["user"]["subscription"]["active"] is False
    assert c.get("/files", headers=_auth(loner)).status_code == 402
    assert ok(c.post("/billing/checkout", headers=_auth(loner)))["url"].endswith("/checkout/4")

    assert c.post("/billing/checkout", headers=_auth(owner)).status_code == 409
    assert ok(c.post("/billing/portal", headers=_auth(owner)))["url"].endswith(f"cus_office_{oid}")
    assert c.post("/billing/portal", headers=_auth(staff)).status_code in (400, 403)

    # Later events found by customer id: past_due keeps a grace period, canceled locks
    _hook(c, customer_id=f"cus_office_{oid}", status="past_due", event_id="e2", event_created=200)
    s = ok(c.get("/office", headers=_auth(staff)))["office"]["plan"]
    assert s["status"] == "past_due" and s["active"] is True and s["grace_until"] > time.time()
    assert _hook(c, customer_id=f"cus_office_{oid}", status="active", event_id="e0", event_created=50)["stale"]
    _hook(c, subscription_id="sub_o", status="canceled", event_id="e3", event_created=300)
    assert c.get("/shared/list", headers=_auth(staff)).status_code == 402

    # Personal plan of the loner is untouched by office events
    _hook(c, customer_id="cus_4", user_id=4, subscription_id="sub_l", event_id="e4")
    assert c.get("/files", headers=_auth(loner)).status_code == 200


def test_free_plan_everyone_active(c):
    t = _register(c, "f@example.com")
    assert ok(c.get("/me", headers=_auth(t)))["user"]["subscription"]["active"] is True
    _office(c, t)
    assert ok(c.get("/office", headers=_auth(t)))["office"]["plan"]["free_plan"] is True
    assert c.get("/shared/list", headers=_auth(t)).status_code == 200


# ── live events ──────────────────────────────────────────────────────────────

def test_sse_broadcast_reaches_other_members(live_server, tmp_path):
    from cloud_client import CloudClient, EventStream

    url, app = live_server
    app.state.settings.free_plan = True
    a, b = CloudClient(url), CloudClient(url)
    a.register("one@example.com", "password123")
    b.register("two@example.com", "password123")
    a._json("POST", "/office", {"name": "Live"})
    code = a._json("POST", "/office/invites", {"role": "staff"})["invite"]["code"]

    got = []
    s = EventStream(b, lambda e, d: got.append((e, d))).start()
    try:
        assert _wait(lambda: app.state.hub.subscriber_count(2) == 1)
        b._json("POST", "/office/join", {"code": code})
        assert _wait(lambda: any(e == "office_changed" and d["reason"] == "member_joined" for e, d in got))
        a._json("POST", "/shared/new-student", {"country": "X", "program": "Y", "student": "Z"})
        assert _wait(lambda: any(e == "shared_changed" for e, _ in got))
        ev = [d for e, d in got if e == "shared_changed"][0]
        assert ev["actor"] == "one@example.com" and ev["action"] == "new_student"
        assert ev["paths"] == ["X", "X/Y", "X/Y/Z"]
    finally:
        s.stop()
