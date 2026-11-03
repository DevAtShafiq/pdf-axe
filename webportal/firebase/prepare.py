# -*- coding: utf-8 -*-
"""
prepare.py — put together what Firebase deploys, in webportal/firebase/build/:

    build/public/      Firebase Hosting: static/ (css, js, images, captions)
                       media/office-axe-demo.mp4 (the tour video) and
                       files/OfficeAxe-Setup.exe (the Windows installer)
    build/functions/   the Cloud Function: main.py, requirements.txt, the
                       portal code, license_keys.py, ed25519_pure.py,
                       revoked_keys.json and a .env with the public settings

    python webportal/firebase/prepare.py [--download-url URL] [--venv]

--venv also creates build/functions/venv with the requirements installed
(the Firebase CLI needs it to read the function before deploying).
Existing files in build/ are overwritten, never removed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL = os.path.dirname(HERE)
ROOT = os.path.dirname(PORTAL)
BUILD = os.path.join(HERE, "build")

PORTAL_FILES = ["web.py", "auth.py", "config.py", "licensing.py", "store.py", "store_firestore.py", "wsgi.py",
                "releases.py", "minidb.py"]
ROOT_FILES = ["license_keys.py", "ed25519_pure.py", "revoked_keys.json"]
# installer/version.txt is the installer's version (OfficeAxe.iss gets it too); a
# deploy with a newer one becomes the live release by itself (auto release)
VERSION_FILE = os.path.join(ROOT, "installer", "version.txt")
INSTALLER_URL = "https://officeaxe-8a44f.web.app/files/OfficeAxe-Setup.exe"


def _copy(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def installer_env(hosted: str) -> dict:
    """Settings that let the portal publish the deployed installer on its own
    (auto release) — empty when there's no installer or version."""
    try:
        with open(VERSION_FILE, encoding="utf-8") as fh:
            version = fh.read().strip()
    except OSError:
        return {}
    if not version or not os.path.isfile(hosted):
        return {}
    h = hashlib.sha256()
    with open(hosted, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return {"OFFICEAXE_INSTALLER_VERSION": version, "OFFICEAXE_INSTALLER_URL": INSTALLER_URL,
            "OFFICEAXE_INSTALLER_SIZE": os.path.getsize(hosted), "OFFICEAXE_INSTALLER_SHA256": h.hexdigest()}


def build(download_url: str = "", venv: bool = False) -> dict:
    pub = os.path.join(BUILD, "public")
    fn = os.path.join(BUILD, "functions")
    # Hosting
    shutil.copytree(os.path.join(PORTAL, "static"), os.path.join(pub, "static"), dirs_exist_ok=True)
    video = os.path.join(ROOT, "promotion", "office-axe-demo.mp4")
    if os.path.isfile(video):
        _copy(video, os.path.join(pub, "media", "office-axe-demo.mp4"))
    # the Windows installer (installer/OfficeAxe.iss); a copy already in build/ stays when it isn't built here
    setup = os.path.join(ROOT, "installer", "Output", "OfficeAxe-Setup.exe")
    hosted = os.path.join(pub, "files", "OfficeAxe-Setup.exe")
    if os.path.isfile(setup):
        _copy(setup, hosted)
    elif not os.path.isfile(hosted):
        print("warning: installer/Output/OfficeAxe-Setup.exe not found; /files/OfficeAxe-Setup.exe won't be deployed")
    installer = installer_env(hosted)
    # Function
    _copy(os.path.join(HERE, "main.py"), os.path.join(fn, "main.py"))
    _copy(os.path.join(HERE, "requirements.txt"), os.path.join(fn, "requirements.txt"))
    for f in PORTAL_FILES:
        _copy(os.path.join(PORTAL, f), os.path.join(fn, "webportal", f))
    # the landing page reads chapters / slide captions from static/
    shutil.copytree(os.path.join(PORTAL, "static"), os.path.join(fn, "webportal", "static"), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.jpg", "*.png"))
    for f in ROOT_FILES:
        _copy(os.path.join(ROOT, f), os.path.join(fn, f))
    with open(os.path.join(fn, ".env"), "w", encoding="utf-8") as fh:      # public settings only
        fh.write(f"OFFICEAXE_DOWNLOAD_URL={download_url}\n")
        for k, v in installer.items():
            fh.write(f"{k}={v}\n")
    if venv and not os.path.isdir(os.path.join(fn, "venv")):
        subprocess.run([sys.executable, "-m", "venv", os.path.join(fn, "venv")], check=True)
        py = os.path.join(fn, "venv", "Scripts" if os.name == "nt" else "bin", "python")
        subprocess.run([py, "-m", "pip", "install", "-q", "-r", os.path.join(fn, "requirements.txt")], check=True)
    return {"public": pub, "functions": fn}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-url", default="")
    ap.add_argument("--venv", action="store_true")
    a = ap.parse_args()
    print(build(a.download_url, a.venv))
