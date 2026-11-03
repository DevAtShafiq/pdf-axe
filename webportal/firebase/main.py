# -*- coding: utf-8 -*-
"""
Cloud Function (2nd gen, runs on Cloud Run) that serves the Office Axe portal
behind Firebase Hosting. prepare.py copies this file and the portal code into
build/functions; don't run it from here.

Firebase Hosting serves /static/** and the tour video itself; every other
address (/, /admin/**, /api/**, /download) is rewritten to this function.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "webportal"))

# server settings (secrets come from Secret Manager, see `secrets=` below)
os.environ.setdefault("OFFICEAXE_STORE", "firestore")
os.environ.setdefault("OFFICEAXE_COOKIE_SECURE", "1")
os.environ.setdefault("OFFICEAXE_TRUST_PROXY", "1")
os.environ.setdefault("OFFICEAXE_COOKIE_NAME", "__session")     # the only cookie Hosting passes on
os.environ.setdefault("OFFICEAXE_VIDEO_HOSTED", "1")
os.environ.setdefault("OFFICEAXE_PORTAL_DATA", "/tmp/officeaxe")

from firebase_functions import https_fn, options  # noqa: E402
from werkzeug.wrappers import Response  # noqa: E402

REGION = "asia-southeast1"
_app = None


@https_fn.on_request(
    region=REGION,
    secrets=["OFFICEAXE_SIGNING_KEY", "OFFICEAXE_SESSION_SECRET", "OFFICEAXE_ADMIN_PASSWORD_HASH"],
    memory=options.MemoryOption.MB_512,
    max_instances=3,
    timeout_sec=60,
)
def portal(req: https_fn.Request) -> https_fn.Response:
    global _app
    if _app is None:
        from web import make_app
        _app = make_app()
    return Response.from_app(_app, req.environ)
