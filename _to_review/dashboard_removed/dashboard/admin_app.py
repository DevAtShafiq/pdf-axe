# -*- coding: utf-8 -*-
"""
admin_app.py — Office Axe admin dashboard (owner's PC only).

    python dashboard/admin_app.py

A small pywebview window over admin_core.py: make giveaway / review / full
keys, copy or share them, see every issued key with its status and expiry,
withdraw keys, export CSV and write the revoked list for the next app build.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import admin_core  # noqa: E402
import license_keys  # noqa: E402  (admin_core puts the project root on sys.path)


def _ok(**kw):
    return dict(ok=True, **kw)


def _err(msg):
    return {"ok": False, "error": str(msg)}


class AdminApi:
    def __init__(self):
        self._window = None

    def status(self):
        return _ok(has_key=admin_core.has_signing_key(), admin_dir=admin_core.admin_dir(),
                   public_key=admin_core.public_key_hex(), app_matches=admin_core.app_matches(),
                   types=list(license_keys.TYPES), default_days=admin_core.DEFAULT_DAYS,
                   default_devices=admin_core.DEFAULT_DEVICES)

    def init_key(self):
        try:
            return _ok(**admin_core.init())
        except Exception as exc:
            return _err(exc)

    def issue(self, key_type, holder, days, devices, plan, note):
        try:
            return _ok(row=admin_core.issue(key_type, holder, int(days or admin_core.DEFAULT_DAYS),
                                            int(devices) if devices else None, plan or "", note or ""))
        except Exception as exc:
            return _err(exc)

    def list_keys(self):
        try:
            return _ok(rows=admin_core.list_keys())
        except Exception as exc:
            return _err(exc)

    def set_revoked(self, key_id, revoked):
        try:
            return _ok(row=admin_core.set_revoked(key_id, bool(revoked)))
        except Exception as exc:
            return _err(exc)

    def export_csv(self):
        try:
            import webview
            res = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename="office-axe-keys.csv",
                                                  file_types=("CSV files (*.csv)",))
            if not res:
                return _ok(cancelled=True)
            path = res if isinstance(res, str) else res[0]
            if not path.lower().endswith(".csv"):
                path += ".csv"
            return _ok(path=path, count=admin_core.export_csv(path))
        except Exception as exc:
            return _err(exc)

    def write_revoked_list(self):
        try:
            return _ok(**admin_core.write_revoked_list())
        except Exception as exc:
            return _err(exc)


def main():
    import webview
    api = AdminApi()
    win = webview.create_window("Office Axe — Admin", os.path.join(HERE, "ui", "index.html"),
                                js_api=api, width=1180, height=820, min_size=(900, 600),
                                background_color="#0e1014")
    api._window = win
    webview.start(gui="edgechromium" if sys.platform == "win32" else None,
                  debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
