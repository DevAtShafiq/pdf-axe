# -*- coding: utf-8 -*-
"""
config.py — portal settings, all from environment variables (see README).

    OFFICEAXE_PORTAL_DATA        data folder (database, admin login, secrets)
                                 default: webportal/data (git-ignored)
    OFFICEAXE_SIGNING_KEY        the Ed25519 private seed, 64 hex characters, or
    OFFICEAXE_SIGNING_KEY_FILE   a file holding it (hex, or the desktop
                                 dashboard's signing_key.json)
                                 default: ~/OfficeAxeAdmin/signing_key.json
    OFFICEAXE_ADMIN_USER         admin user name (default "admin")
    OFFICEAXE_COOKIE_SECURE      1 = cookies only over HTTPS (set it in production)
    OFFICEAXE_TRUST_PROXY        1 = read the visitor's IP from X-Forwarded-For
                                 (only behind your own proxy, e.g. Caddy)
    OFFICEAXE_DOWNLOAD_URL       where the Download button goes, or
    OFFICEAXE_DOWNLOAD_FILE      a file the portal serves itself
                                 default: <data>/downloads/OfficeAxe.zip if present
    OFFICEAXE_VIDEO_FILE         the demo video on the landing page
                                 default: promotion/office-axe-demo.mp4
    OFFICEAXE_COOKIE_NAME        admin session cookie (Firebase Hosting: __session)
    OFFICEAXE_VIDEO_HOSTED       1 = the video is served next to the portal (Hosting)
    OFFICEAXE_STORE              sqlite (default, a file in the data folder) or
                                 firestore (Firebase / Cloud Run)
    OFFICEAXE_SESSION_SECRET     64 hex characters; required on a server with
                                 several instances (else kept in the data folder)
    OFFICEAXE_ADMIN_PASSWORD_HASH  first admin password on a new server
                                 (manage.py hash-password)
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def read_seed(text: str) -> bytes:
    """Hex seed, or the desktop dashboard's signing_key.json."""
    text = text.strip()
    if text.startswith("{"):
        text = json.loads(text)["private_seed_hex"]
    seed = bytes.fromhex(text)
    if len(seed) != 32:
        raise ValueError("The signing key must be 32 bytes (64 hex characters).")
    return seed


@dataclass
class Config:
    data_dir: str = field(default_factory=lambda: os.environ.get("OFFICEAXE_PORTAL_DATA")
                          or os.path.join(HERE, "data"))
    admin_user: str = field(default_factory=lambda: os.environ.get("OFFICEAXE_ADMIN_USER") or "admin")
    cookie_secure: bool = field(default_factory=lambda: _flag("OFFICEAXE_COOKIE_SECURE"))
    trust_proxy: bool = field(default_factory=lambda: _flag("OFFICEAXE_TRUST_PROXY"))
    download_url: str = field(default_factory=lambda: os.environ.get("OFFICEAXE_DOWNLOAD_URL") or "")
    download_file: str = field(default_factory=lambda: os.environ.get("OFFICEAXE_DOWNLOAD_FILE") or "")
    video_file: str = field(default_factory=lambda: os.environ.get("OFFICEAXE_VIDEO_FILE") or os.path.join(
        os.path.dirname(HERE), "promotion", "office-axe-demo.mp4"))
    # Firebase Hosting passes only a cookie named "__session" on to the server
    cookie_name: str = field(default_factory=lambda: os.environ.get("OFFICEAXE_COOKIE_NAME") or "oa_admin")
    # the tour video is served by Firebase Hosting, not by the portal
    video_hosted: bool = field(default_factory=lambda: _flag("OFFICEAXE_VIDEO_HOSTED"))
    store_kind: str = field(default_factory=lambda: (os.environ.get("OFFICEAXE_STORE") or "sqlite").lower())
    seed: bytes = b""

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        if not self.download_file:
            self.download_file = os.path.join(self.data_dir, "downloads", "OfficeAxe.zip")

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "portal.sqlite3")

    @property
    def admin_file(self) -> str:
        return os.path.join(self.data_dir, "admin.json")

    def load_seed(self) -> bytes:
        if self.seed:
            return self.seed
        env = os.environ.get("OFFICEAXE_SIGNING_KEY")
        if env:
            return read_seed(env)
        path = os.environ.get("OFFICEAXE_SIGNING_KEY_FILE") or \
            os.path.join(os.path.expanduser("~"), "OfficeAxeAdmin", "signing_key.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return read_seed(fh.read())
        except FileNotFoundError:
            raise RuntimeError("No signing key. Set OFFICEAXE_SIGNING_KEY or OFFICEAXE_SIGNING_KEY_FILE "
                               f"(looked for {path}).")

    def session_secret(self) -> bytes:
        """From OFFICEAXE_SESSION_SECRET, else random per installation, kept in the data folder."""
        env = os.environ.get("OFFICEAXE_SESSION_SECRET", "").strip()
        if env:
            return bytes.fromhex(env)
        path = os.path.join(self.data_dir, "session_secret")
        try:
            with open(path, encoding="ascii") as fh:
                return bytes.fromhex(fh.read().strip())
        except (FileNotFoundError, ValueError):
            secret = secrets.token_bytes(32)
            with open(path, "w", encoding="ascii") as fh:
                fh.write(secret.hex())
            return secret
