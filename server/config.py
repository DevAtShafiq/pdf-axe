"""
config.py — Settings for the PDF Axe account server, read from the environment.

Every value can be set in the process environment or in server/.env
(KEY=value lines). Nothing secret has a default.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    path = os.path.join(_HERE, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = val.strip().strip('"').strip("'")


_load_dotenv()


@dataclass
class Settings:
    # Where the SQLite database and uploaded files live
    db_path: str = field(default_factory=lambda: os.environ.get(
        "SFM_DB_PATH", os.path.join(_HERE, "data", "pdfaxe.sqlite3")))
    storage_dir: str = field(default_factory=lambda: os.environ.get(
        "SFM_STORAGE_DIR", os.path.join(_HERE, "data", "storage")))
    # Public base URL of this server (used for Stripe success/cancel pages)
    public_url: str = field(default_factory=lambda: os.environ.get(
        "SFM_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"))
    # Session lifetime
    session_days: int = field(default_factory=lambda: int(os.environ.get("SFM_SESSION_DAYS", "30")))
    # Per-user cloud storage quota for subscribers
    storage_quota_mb: int = field(default_factory=lambda: int(os.environ.get("SFM_STORAGE_QUOTA_MB", "5120")))
    # Largest single upload
    max_upload_mb: int = field(default_factory=lambda: int(os.environ.get("SFM_MAX_UPLOAD_MB", "200")))

    # Stripe (monthly subscription)
    stripe_secret_key: str = field(default_factory=lambda: os.environ.get("STRIPE_SECRET_KEY", ""))
    stripe_webhook_secret: str = field(default_factory=lambda: os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
    stripe_price_id: str = field(default_factory=lambda: os.environ.get("STRIPE_PRICE_ID", ""))
    # Shown in the app next to the Subscribe button, e.g. "$9.99 / month"
    plan_label: str = field(default_factory=lambda: os.environ.get("SFM_PLAN_LABEL", "Monthly plan"))

    @property
    def billing_configured(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_price_id)
