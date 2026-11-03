# -*- coding: utf-8 -*-
"""Test of the retired desktop admin dashboard (kept for review, not run by CI)."""
import csv, os, sys
import pytest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "dashboard"))
import license_keys as lk  # noqa: E402


# ── dashboard core ────────────────────────────────────────────────────────────

@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICEAXE_ADMIN_DIR", str(tmp_path / "admin"))
    import admin_core
    return admin_core


def test_dashboard_issue_list_revoke_export(admin, tmp_path):
    with pytest.raises(RuntimeError):
        admin.issue("review", "Nobody")
    first = admin.init()
    assert first["created"] and admin.init()["created"] is False        # never replaced
    assert admin.init()["public_key_hex"] == first["public_key_hex"]
    pub = bytes.fromhex(first["public_key_hex"])

    row = admin.issue("review", "Employee", note="for the review")
    assert row["days"] == 30 and row["max_devices"] == 1 and row["status"] == "active"
    info = lk.parse(row["key"], pub, revoked=set())
    assert info["key_id"] == row["key_id"] and info["holder"] == "Employee"
    admin.issue("full", "Client", days=365, plan="yearly")
    assert {r["holder"] for r in admin.list_keys()} == {"Client", "Employee"}

    assert admin.set_revoked(row["key_id"])["status"] == "revoked"
    out = admin.write_revoked_list(str(tmp_path / "revoked_keys.json"))
    assert out["count"] == 1
    assert lk.revoked_ids(out["path"]) == {row["key_id"]}
    assert admin.set_revoked(row["key_id"], False)["status"] == "active"

    n = admin.export_csv(str(tmp_path / "keys.csv"))
    with open(tmp_path / "keys.csv", encoding="utf-8-sig") as fh:
        data = list(csv.DictReader(fh))
    assert n == 2 and {d["holder"] for d in data} == {"Employee", "Client"}

    with pytest.raises(ValueError):
        admin.issue("review", "  ")
    with pytest.raises(ValueError):
        admin.issue("review", "X", days=0)
    # the private key lives only in the admin folder
    assert os.path.isfile(tmp_path / "admin" / "signing_key.json")
