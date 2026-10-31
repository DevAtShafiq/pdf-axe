# -*- coding: utf-8 -*-
"""
manage.py — run and look after the portal.

    python webportal/manage.py run [--host 127.0.0.1] [--port 8090]
    python webportal/manage.py set-password          # asks twice, never echoed
    python webportal/manage.py import-desktop [path]  # the desktop dashboard's issued_keys.json
    python webportal/manage.py public-key
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from auth import AdminAccount  # noqa: E402
from config import Config  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="manage.py", description="Office Axe portal")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="serve the portal")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=8090)
    sub.add_parser("set-password")
    sub.add_parser("hash-password", help="print a password hash for OFFICEAXE_ADMIN_PASSWORD_HASH")
    i = sub.add_parser("import-desktop")
    i.add_argument("path", nargs="?", default=os.path.join(os.path.expanduser("~"), "OfficeAxeAdmin",
                                                           "issued_keys.json"))
    sub.add_parser("public-key")
    a = ap.parse_args(argv)
    cfg = Config()

    if a.cmd == "hash-password":
        from auth import hash_password, password_problem
        # messages go to the screen (stderr); only the hash goes to stdout (a file)
        for _ in range(3):
            pw = getpass.getpass("New website admin password (at least 10 characters): ")
            if password_problem(pw):
                print("  " + password_problem(pw) + " Try again.", file=sys.stderr)
                continue
            if pw != getpass.getpass("Type the same password again: "):
                print("  The two passwords don't match. Try again.", file=sys.stderr)
                continue
            print(hash_password(pw))
            return 0
        print("  No password was saved.", file=sys.stderr)
        return 1

    if a.cmd == "set-password":
        acct = AdminAccount(cfg.admin_file, cfg.admin_user)
        pw = getpass.getpass(f"New password for '{cfg.admin_user}': ")
        if pw != getpass.getpass("Repeat it: "):
            print("The passwords don't match.")
            return 1
        try:
            acct.set_password(pw)
        except ValueError as exc:
            print(exc)
            return 1
        print("Saved. Existing sessions are logged out.")
        return 0

    from web import make_app
    app = make_app(cfg)
    if a.cmd == "public-key":
        print(app.lic.public_hex, "(matches the app)" if app.lic.app_matches() else "(does NOT match the app)")
    elif a.cmd == "import-desktop":
        print(app.lic.import_desktop(a.path, actor="cli"))
    elif a.cmd == "run":
        url = f"http://{a.host}:{a.port}"
        try:
            from waitress import serve        # production-grade, used in the Docker image
            print(f"Office Axe portal on {url} (waitress)")
            serve(app, host=a.host, port=a.port, threads=8)
        except ImportError:
            from wsgiref.simple_server import WSGIRequestHandler, make_server

            class Quiet(WSGIRequestHandler):
                def log_message(self, fmt, *args):
                    sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))
            print(f"Office Axe portal on {url} (development server)")
            if not app.account.is_set():
                print(f"First run: open {url}/admin/setup on this computer to create the admin password.")
            make_server(a.host, a.port, app, handler_class=Quiet).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
