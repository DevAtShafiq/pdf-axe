"""
qa/e2e/ui_office.py ??UI-level test of sign-up, sign-in, the Office drive and
sharing between two staff, driving two running Office Axe windows over CDP.

    python qa/e2e/ui_office.py <owner_port> <staff_port> <owner_settings.json> <staff_settings.json>

Both windows must be signed out, pointed at the same free-plan server
(SFM_SERVER_URL) and started with --remote-debugging-port. Each window must
run from its own working copy (its own sfm_settings.json). Throw-away
accounts are created; passwords are random and never printed.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_cloud import page_for  # noqa: E402

RESULTS = []
SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_scratch")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    return bool(cond)


def wait_for(page, js, timeout=15.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = page.evaluate(f"(async () => {{ {js} }})()")
            if last:
                return last
        except Exception as exc:  # page busy / navigating
            last = str(exc)
        time.sleep(0.25)
    return last if last else False


def js(page, src):
    return page.evaluate(f"(async () => {{ {src} }})()")


def toasts(page):
    return js(page, "return [...document.querySelectorAll('.toast')].map(t => t.innerText).join(' | ')")


def register(page, email, pw, confirm=None):
    page.click("#acct-overlay .acct-tab[data-tab=register]")
    page.fill("#acct-email", email)
    page.fill("#acct-password", pw)
    page.fill("#acct-confirm", pw if confirm is None else confirm)
    page.click("#acct-submit")


def main():
    pa, pb, set_a, set_b = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
    tag = secrets.token_hex(3)
    ea, eb = f"ui-owner-{tag}@example.test", f"ui-staff-{tag}@example.test"
    pwa, pwb = secrets.token_urlsafe(12), secrets.token_urlsafe(12)
    tmp = tempfile.mkdtemp(prefix="qa_ui_")
    with sync_playwright() as pw:
        _ba, A = page_for(pw, pa)
        _bb, B = page_for(pw, pb)
        for p in (A, B):
            p.reload()
            wait_for(p, "return typeof Account !== 'undefined' && Account.state.loaded")

        # ?? sign-up form validation (owner window) ??????????????????????????
        check("overlay shown when signed out", A.is_visible("#acct-overlay:not(.hidden)"))
        register(A, "not-an-email", pwa)
        check("bad email shows field error", "email" in (A.inner_text("#acct-email-err") or "").lower())
        register(A, ea, "short")
        check("short password shows field error", "8" in A.inner_text("#acct-password-err"))
        register(A, ea, pwa, confirm=pwa + "x")
        check("password mismatch shows error", "match" in A.inner_text("#acct-confirm-err").lower())
        register(A, ea, pwa)
        ok = wait_for(A, "return Account.state.logged_in")
        check("register signs in", ok)
        s = json.load(open(set_a, encoding="utf-8"))
        check("token stored DPAPI-encrypted", str(s.get("cloud_token_enc", "")).startswith("dpapi:")
              and not s.get("cloud_token"))
        check("token never exposed to JS", "token" not in json.dumps(js(A, "return SFM.accountGetState()")).lower()
              .replace("cloud_token", ""))

        # duplicate email + wrong password (staff window)
        register(B, ea, pwb)
        wait_for(B, "return !document.querySelector('#acct-submit').disabled && "
                    "(document.querySelector('#acct-email-err').innerText || document.querySelector('#acct-error').innerText)")
        check("duplicate email error", "already exists" in (B.inner_text("#acct-email-err") + B.inner_text("#acct-error")))
        B.click("#acct-overlay .acct-tab[data-tab=login]")
        B.fill("#acct-email", ea)
        B.fill("#acct-password", "wrong-password-1")
        B.click("#acct-submit")
        wait_for(B, "return document.querySelector('#acct-error').innerText")
        check("wrong password error", "wrong email or password" in B.inner_text("#acct-error").lower())
        # staff signs up without "keep me signed in"
        B.uncheck("#acct-remember")
        register(B, eb, pwb)
        check("staff registers", wait_for(B, "return Account.state.logged_in"))
        s = json.load(open(set_b, encoding="utf-8"))
        check("not remembered ??no token on disk", not s.get("cloud_token_enc"))

        # ?? owner creates the office ????????????????????????????????????????
        js(A, "Office.openDrive(); return 1")
        wait_for(A, "return !!document.querySelector('#ofx-create-name')")
        A.fill("#ofx-create-name", "UI Test Office")
        A.click("#ofx-create-btn")
        check("setup wizard opens", wait_for(A, "return !!document.querySelector('#ofx-setup-body')"))
        for chip, val in (("countries", "Nepal"), ("programs", "MBBS")):
            A.fill(f"#ofx-setup-body [data-chip-in={chip}]", val)
            A.press(f"#ofx-setup-body [data-chip-in={chip}]", "Enter")
        A.click("#modal-ofx-setup .modal-footer .btn-primary")   # Next (saves + creates folders)
        wait_for(A, "return document.querySelector('#ofx-setup-body [data-inv-email=\"0\"]')")
        A.fill("#ofx-setup-body [data-inv-email='0']", eb)
        A.click("#modal-ofx-setup .modal-footer .btn-primary")   # Send invites
        code = wait_for(A, "const d = document.querySelector('#ofx-setup-body .result-detail'); return d && d.innerText")
        check("invite code shown in wizard", bool(code) and str(code).startswith("OFX-"), str(code))
        A.click("#modal-ofx-setup .modal-footer .btn-primary")   # Finish
        wait_for(A, "return !!document.querySelector('#ofx-tree')")
        tree = wait_for(A, "return [...document.querySelectorAll('#ofx-tree .ofx-node-name')].map(n=>n.innerText).join('|').includes('Nepal') && document.querySelector('#ofx-tree').innerText")
        check("country folder created by wizard", tree and "Nepal" in str(tree), str(tree))

        # ?? staff accepts the invitation ????????????????????????????????????
        js(B, "Office.openDrive(); return 1")
        btn = wait_for(B, "return !!document.querySelector('[data-act=accept-invite]')")
        check("staff sees invitation", btn)
        B.click("[data-act=accept-invite]")
        check("staff joined", wait_for(B, "return Office.state.office && Office.state.office.role === 'staff'"))
        wait_for(B, "return !!document.querySelector('#ofx-tree')")
        acts = js(B, "return [...document.querySelectorAll('#ofx-actions [data-act]')].map(b=>b.dataset.act)")
        check("staff has no Add country at top level", "add-country" not in acts and "new-student" in acts, str(acts))
        check("owner member count live-updates", wait_for(A, "return Office.state.office.member_count === 2"))

        # ?? staff creates a student through the dialog ??????????????????????
        B.click("#ofx-actions [data-act=new-student]")
        wait_for(B, "return !!document.querySelector('#ns-name')")
        wait_for(B, "return document.querySelector('#ns-program').value === 'MBBS'")
        B.fill("#ns-name", "Rahim Uddin")
        B.click("#modal-ofx-new-student .modal-footer .btn-primary")
        check("staff created student and opened it",
              wait_for(B, "return Office.state.cwd === 'Nepal/MBBS/Rahim Uddin'"))
        live = wait_for(A, "return Office.state.lists['Nepal/MBBS'] ? 1 : (Office.state.expanded.add('Nepal'), 0)", 2)
        js(A, "Office.state.cwd=''; return 1")
        seen = wait_for(A, "const r = await SFM.sharedList('Nepal/MBBS'); return r.items.map(i=>i.name).includes('Rahim Uddin')")
        check("owner sees the staff member's student", seen)

        # upload into the student from the staff window (files picked via the bridge)
        f = os.path.join(tmp, "passport scan.pdf")
        with open(f, "wb") as fh:
            fh.write(b"%PDF-1.4\n" + os.urandom(2000))
        js(B, f"Office.uploadHere([{json.dumps(f)}]); return 1")
        done = wait_for(B, "return Office.state.lists['Nepal/MBBS/Rahim Uddin'] && Office.state.lists['Nepal/MBBS/Rahim Uddin'].items.some(i => i.name === 'passport scan.pdf')")
        check("staff upload appears in list", done)
        t = wait_for(B, "const t=[...document.querySelectorAll('.toast')].map(t=>t.innerText).join('|'); return t.includes('Uploaded') && t", 5)
        check("upload toast counts files (not the path list)", t and "Uploaded 1 file to" in t, str(t))
        # owner window gets a live shared_changed and can open the folder
        js(A, "Office.state.view='drive'; return 1")
        A.evaluate("() => { Office.openDrive(); }")
        ok = wait_for(A, "const r = await SFM.sharedList('Nepal/MBBS/Rahim Uddin'); return r.items.some(i => i.name === 'passport scan.pdf')")
        check("owner sees uploaded file", ok)

        # admin drag onto a Country folder is refused client-side (regression for canUploadInto)
        check("owner cannot upload into a country", js(A, "return Office.state.office.settings.enforce_hierarchy") is True)
        js(A, f"Office.uploadHere && (Office.state.cwd = 'Nepal'); Office.uploadHere([{json.dumps(f)}]); return 1")
        t = wait_for(A, "const t = [...document.querySelectorAll('.toast')].map(t=>t.innerText).join('|'); return t.includes('student folder') && t", 5)
        check("owner gets 'open a student folder' warning", t, toasts(A))
        js(A, "Office.state.cwd = ''; return 1")

        # staff trashes the student; owner restores it from the trash view
        r = js(B, "return SFM.sharedTrash(['Nepal/MBBS/Rahim Uddin'])")
        check("staff trash", r.get("ok") is not False, str(r))
        js(A, "Office.openDrive(); return 1")
        A.click("#ofx-btn-trash")
        wait_for(A, "return document.querySelectorAll('[data-trash]').length > 0")
        A.click("[data-trash]")
        A.click("#ofx-main [data-act=restore]")
        msg = wait_for(A, "const t=[...document.querySelectorAll('.toast')].map(t=>t.innerText).join('|'); return t.includes('Restored') && t")
        check("restore toast reports the real count", msg and "Restored 1 item" in msg, str(msg))
        A.screenshot(path=os.path.join(SHOTS, "ui_owner_trash.png"))

        # People & settings: owner sees staff, can change role
        js(A, "Office.openManage(); return 1")
        rows = wait_for(A, "return document.querySelectorAll('#ofx-m-people .ofx-mrow:not(.ofx-mrow-head)').length")
        check("people list shows 2 members", rows == 2, str(rows))
        A.screenshot(path=os.path.join(SHOTS, "ui_owner_people.png"))
        B.screenshot(path=os.path.join(SHOTS, "ui_staff_drive.png"))

        # ?? sign out / sign in again ????????????????????????????????????????
        js(B, "return SFM.accountLogout()")
        js(B, "await Account.refresh(); return 1")
        check("staff signed out ??overlay", wait_for(B, "return !Account.state.logged_in && !document.querySelector('#acct-overlay').classList.contains('hidden')"))
        B.click("#acct-overlay .acct-tab[data-tab=login]")
        B.fill("#acct-email", eb.upper())
        B.fill("#acct-password", pwb)
        B.click("#acct-submit")
        check("staff signs back in (email case-insensitive)", wait_for(B, "return Account.state.logged_in"))
        check("office restored after sign-in", wait_for(B, "return Office.state.office && Office.state.office.name === 'UI Test Office'"))

        # devices list: owner revokes nothing, just lists
        d = js(A, "return SFM.accountListDevices()")
        check("devices listed", d.get("ok") and len(d.get("devices", [])) >= 1)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} UI checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
