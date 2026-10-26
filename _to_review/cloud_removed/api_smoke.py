"""
qa/e2e/api_smoke.py — end-to-end check of every account / cloud / office route
through the real desktop client (cloud_client.CloudClient) against a running
server.

    python qa/e2e/api_smoke.py http://127.0.0.1:8045 [--paid]

Without --paid the server must run with SFM_FREE_PLAN=1. With --paid (server
without SFM_FREE_PLAN) it checks that paid routes answer 402 and that free
routes keep working. Creates throw-away accounts qa-<random>@example.test;
passwords are random and never printed. Prints one line per check and exits 1
on any failure.
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cloud_client import CloudClient, CloudError  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    return bool(cond)


def expect_error(name: str, fn, status: int, contains: str = ""):
    try:
        fn()
    except CloudError as exc:
        ok = exc.status == status and contains.lower() in str(exc).lower()
        return check(name, ok, f"got {exc.status} {exc}")
    return check(name, False, "no error raised")


def new_user(url: str, tag: str):
    c = CloudClient(url, device=f"qa-{tag}")
    email = f"qa-{tag}-{secrets.token_hex(3)}@example.test"
    pw = secrets.token_urlsafe(12)
    c.register(email, pw)
    return c, email, pw


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8045"
    paid = "--paid" in sys.argv
    tmp = tempfile.mkdtemp(prefix="qa_cloud_")

    h = CloudClient(url).health()
    check("health", h.get("ok") and h.get("free_plan") is (not paid), str(h))

    a, a_email, a_pw = new_user(url, "owner")
    b, b_email, _b_pw = new_user(url, "staff")
    me = a.me()
    check("register + me", me["email"] == a_email)
    check("subscription flag", me["subscription"]["active"] is (not paid), str(me["subscription"]))

    # login / logout / sessions
    a2 = CloudClient(url, device="qa-second-device")
    a2.login(a_email.upper(), a_pw)
    check("login is case-insensitive on email", a2.me()["email"] == a_email)
    sess = a.sessions()
    check("two sessions listed", len(sess) == 2 and sum(1 for s in sess if s["current"]) == 1)
    other = [s for s in sess if not s["current"]][0]
    a.revoke_session(other["id"])
    expect_error("revoked device is signed out", a2.me, 401)
    expect_error("wrong password", lambda: CloudClient(url).login(a_email, "wrong-" + a_pw), 401, "wrong")
    expect_error("duplicate register", lambda: CloudClient(url).register(a_email, a_pw), 409)
    expect_error("short password", lambda: CloudClient(url).register("x" + a_email, "short"), 400)
    expect_error("bad email", lambda: CloudClient(url).register("not-an-email", a_pw), 400)
    expect_error("no token", lambda: CloudClient(url).me(), 401)
    tmpc = CloudClient(url)
    tmpc.login(a_email, a_pw)
    tmpc.logout()
    check("logout clears token", tmpc.token == "")

    # billing (MOCK: billing is not configured on this server; nothing reaches Stripe)
    if paid:
        expect_error("checkout without billing", a.checkout_url, 503, "not configured")
    else:
        expect_error("checkout on free server", a.checkout_url, 400, "free")
    expect_error("portal without customer", a.portal_url, 400)

    # personal cloud files
    f1 = os.path.join(tmp, "Résumé ñ.txt")
    with open(f1, "w", encoding="utf-8") as fh:
        fh.write("hello")
    if paid:
        expect_error("upload needs plan", lambda: a.upload(f1, "Docs/a.txt"), 402)
        expect_error("list needs plan", a.list_files, 402)
        check("usage works without plan", "quota" in a.usage())
    else:
        info = a.upload(f1, "Docs/Résumé ñ.txt")
        check("upload unicode path", info["path"] == "Docs/Résumé ñ.txt" and info["size"] == 5)
        files = a.list_files()
        check("list", [f["path"] for f in files] == ["Docs/Résumé ñ.txt"])
        dest = os.path.join(tmp, "dl", "x.txt")
        a.download(info["id"], dest)
        check("download", open(dest, encoding="utf-8").read() == "hello")
        moved = a.move(info["id"], "Docs/Sub/renamed.txt")
        check("rename/move file", moved["path"] == "Docs/Sub/renamed.txt")
        check("move folder", a.move_folder("Docs", "Archive/Docs") == 1)
        check("trash folder", a.trash_folder("Archive") == 1)
        check("trash list", [f["path"] for f in a.list_files(trashed=True)] == ["Archive/Docs/Sub/renamed.txt"])
        a.restore(info["id"])
        check("restore", [f["path"] for f in a.list_files()] == ["Archive/Docs/Sub/renamed.txt"])
        a.trash(info["id"])
        check("trash file", a.list_files() == [])
        expect_error("other user cannot download", lambda: b.download(info["id"], dest + "2"), 404)
        expect_error("bad path", lambda: a.upload(f1, "a/../b.txt"), 400)
        u = a.usage()
        check("usage excludes trash", u["used"] == 0 and u["quota"] > 0)

    # office
    o = a.office_create("QA Office")
    check("office create", o["role"] == "owner" and o["member_count"] == 1)
    inv = a.office_invite(b_email, "staff")
    st = b.office_state()
    check("invite pending for invitee", [i["id"] for i in st["pending_invites"]] == [inv["id"]])
    joined = b.office_accept_invite(inv["id"])
    check("accept invite", joined["role"] == "staff" and joined["member_count"] == 2)
    ms = a.office_members()
    check("members", sorted(m["email"] for m in ms) == sorted([a_email, b_email]))
    b_id = [m["user_id"] for m in ms if m["email"] == b_email][0]
    a.office_set_settings({"countries": ["Nepal"], "programs": ["MBBS"],
                           "student_subfolders": ["Passport", "Visa/Scans"], "enforce_hierarchy": True})
    check("settings", a.office_settings()["student_subfolders"] == ["Passport", "Visa/Scans"])

    if paid:
        expect_error("shared drive needs plan", lambda: a.shared_list(""), 402)
        expect_error("staff shared drive needs plan", lambda: b.shared_list(""), 402)
        check("activity works without plan", isinstance(a.shared_activity(), list))
    else:
        a.shared_mkdir("", "Nepal")
        a.shared_mkdir("Nepal", "MBBS")
        expect_error("staff cannot add a country", lambda: b.shared_mkdir("", "India"), 403)
        expect_error("staff new student in unknown country", lambda: b.shared_new_student("India", "BBA", "X"), 403)
        r = b.shared_new_student("nepal", "mbbs", "Rahim Uddin")
        check("staff new student (case-insensitive parents)", r["path"] == "Nepal/MBBS/Rahim Uddin"
              and "Nepal/MBBS/Rahim Uddin/Visa/Scans" in r["created"], str(r))
        lst = a.shared_list("Nepal/MBBS")
        check("owner sees staff's student", [i["name"] for i in lst["items"]] == ["Rahim Uddin"]
              and lst["items"][0]["level"] == "student")
        expect_error("file above student refused", lambda: b.shared_upload(f1, "Nepal/MBBS/x.txt"), 400)
        up = b.shared_upload(f1, "Nepal/MBBS/Rahim Uddin/Passport/passport.txt")
        check("staff upload", up["file"]["path"] == "Nepal/MBBS/Rahim Uddin/Passport/passport.txt")
        up2 = a.shared_upload(f1, "Nepal/MBBS/Rahim Uddin/Passport/passport.txt", on_conflict="rename")
        check("upload conflict keeps both", up2["file"]["name"] == "passport (2).txt" and up2["renamed"])
        got = a.shared_download("Nepal/MBBS/Rahim Uddin/Passport/passport.txt", os.path.join(tmp, "sh", "p.txt"))
        check("owner downloads staff file", open(got, encoding="utf-8").read() == "hello")
        got2 = a.shared_download("Nepal/MBBS/Rahim Uddin/Passport/passport.txt", os.path.join(tmp, "sh", "p.txt"))
        check("download never overwrites", got2.endswith("p (2).txt"))
        r = b.shared_rename("Nepal/MBBS/Rahim Uddin", "Rahim Uddin Khan")
        check("staff renames student", r["path"] == "Nepal/MBBS/Rahim Uddin Khan")
        expect_error("staff cannot rename a program", lambda: b.shared_rename("Nepal/MBBS", "M"), 403)
        a.shared_mkdir("Nepal", "BDS")
        r = b.shared_move(["Nepal/MBBS/Rahim Uddin Khan"], "Nepal/BDS")
        check("staff moves student to another program", r["moved"][0]["to"] == "Nepal/BDS/Rahim Uddin Khan")
        check("search", [i["path"] for i in a.shared_search("rahim")] == ["Nepal/BDS/Rahim Uddin Khan"])
        tree = a.shared_tree("Nepal/BDS")
        check("tree", any(i["path"].endswith("passport (2).txt") for i in tree))
        r = b.shared_trash(["Nepal/BDS/Rahim Uddin Khan"])
        check("staff trashes student", r["trashed"] == ["Nepal/BDS/Rahim Uddin Khan"])
        tl = a.shared_trash_list()
        check("trash list shows it with its contents", len(tl) == 1 and tl[0]["count"] >= 6, str(tl))
        r = a.shared_restore([tl[0]["id"]])
        check("restore", r["restored"][0]["path"] == "Nepal/BDS/Rahim Uddin Khan")
        acts = [e["action"] for e in a.shared_activity(100)]
        check("activity log", {"new_student", "upload", "rename", "move", "trash", "restore"} <= set(acts), str(acts))
        u = a.shared_usage()
        check("shared usage", u["used"] == 10 and u["quota"] > 0, str(u))

    # roles, transfer, leave
    expect_error("staff cannot invite", lambda: b.office_invite("", "staff"), 403)
    a.office_set_role(b_id, "admin")
    check("promote to admin", b.office_state()["office"]["role"] == "admin")
    a.office_transfer(b_id)
    check("transfer ownership", b.office_state()["office"]["role"] == "owner"
          and a.office_state()["office"]["role"] == "admin")
    a.office_leave()
    check("leave", a.office_state()["office"] is None)
    code = b.office_invite("", "staff")["code"]
    a.office_join(code.lower().replace("-", " "))
    check("rejoin with code", a.office_state()["office"]["role"] == "staff")
    b.office_remove_member([m["user_id"] for m in b.office_members() if m["email"] == a_email][0])
    check("removed member loses access", a.office_state()["office"] is None)
    b.office_leave()
    check("last member leaves (office archived)", b.office_state()["office"] is None)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
