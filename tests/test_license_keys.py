# -*- coding: utf-8 -*-
"""Signed license keys (license_keys.py), their use in SubscriptionManager and
the admin dashboard core. Every key pair is made fresh in a temp folder; no
private key is stored in the repo."""
from __future__ import annotations

import csv
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import ed25519_pure  # noqa: E402
import license_keys as lk  # noqa: E402
import subscription as sub  # noqa: E402

NOW = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
SEED = secrets.token_bytes(32)
PUB = ed25519_pure.public_key(SEED)


def _key(days=30, **kw):
    kw.setdefault("key_type", "review")
    kw.setdefault("holder", "Rahim")
    return lk.make_key(SEED, expires_at=int((NOW + timedelta(days=days)).timestamp()),
                       issued_at=int(NOW.timestamp()), **kw)


def test_ed25519_rfc8032_vector():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk = ed25519_pure.public_key(sk)
    assert pk.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = ed25519_pure.sign(sk, b"")
    assert sig.hex().startswith("e5564300c360ac729086e2cc806e828a")
    assert ed25519_pure.verify(pk, b"", sig) and not ed25519_pure.verify(pk, b"x", sig)


def test_sign_and_verify_round_trip():
    key = _key(key_type="full", holder="Acme Ltd — বাংলা", max_devices=5, plan="yearly")
    assert key.startswith("OAXE-") and lk.looks_signed(key)
    info = lk.parse(key, PUB, now=NOW, revoked=set())
    assert info["type"] == "full" and info["plan"] == "yearly" and info["max_devices"] == 5
    assert info["holder"] == "Acme Ltd — বাংলা"
    assert info["expires_at"] == NOW + timedelta(days=30)
    # pasted in lower case, without dashes, with spaces: still the same key
    assert lk.parse(" " + key.lower().replace("-", "") + " ", PUB, now=NOW, revoked=set())["key_id"] == info["key_id"]
    assert lk.canonical(key.lower()) == key


def test_tampered_or_foreign_keys_are_rejected():
    key = _key()
    n = lk.normalize(key)
    i = 30                                  # flip one character inside the payload
    bad = n[:i] + ("A" if n[i] != "A" else "B") + n[i + 1:]
    with pytest.raises(lk.KeyError_) as e:
        lk.parse(bad, PUB, now=NOW, revoked=set())
    assert e.value.code in ("signature", "format", "version")
    other = lk.make_key(secrets.token_bytes(32), key_type="full", holder="X",
                        expires_at=int((NOW + timedelta(days=999)).timestamp()))
    with pytest.raises(lk.KeyError_) as e:
        lk.parse(other, PUB, now=NOW, revoked=set())
    assert e.value.code == "signature"
    with pytest.raises(lk.KeyError_):
        lk.parse(key[:-12], PUB, now=NOW, revoked=set())     # cut short
    with pytest.raises(lk.KeyError_):
        lk.parse("hello", PUB, now=NOW, revoked=set())


def test_expiry_and_revocation(tmp_path):
    key = _key(days=1)
    lk.parse(key, PUB, now=NOW, revoked=set())
    with pytest.raises(lk.KeyError_) as e:
        lk.parse(key, PUB, now=NOW + timedelta(days=1, seconds=1), revoked=set())
    assert e.value.code == "expired"
    kid = lk.parse(key, PUB, now=NOW, revoked=set())["key_id"]
    path = tmp_path / "revoked_keys.json"
    path.write_text(json.dumps({"revoked": [kid.upper()]}), encoding="utf-8")
    with pytest.raises(lk.KeyError_) as e:
        lk.parse(key, PUB, now=NOW, revoked=lk.revoked_ids(str(path)))
    assert e.value.code == "revoked"
    assert lk.revoked_ids(str(tmp_path / "missing.json")) == set()


def _mgr(tmp_path, revoked=None, now=None):
    clock = {"t": now or NOW}
    backend = sub.LocalLicenseBackend(str(tmp_path / "subscription.json"), secret="t")
    m = sub.SubscriptionManager(backend=backend, gateway=sub.PlaceholderGateway(sandbox=True),
                                device={"id": "dev_a", "name": "PC A", "os": "Windows 11"},
                                now=lambda: clock["t"], public_key=PUB,
                                revoked=revoked if revoked is not None else set())
    return m, clock


def test_manager_activates_a_signed_key_on_a_fresh_pc(tmp_path):
    m, clock = _mgr(tmp_path)
    assert m.state()["access"] is False
    r = m.activate_key(_key(days=30, holder="Employee"))
    assert r["ok"], r
    st = r["state"]
    assert st["access"] and st["status"] == "active" and st["days_left"] == 30
    assert st["key"]["type"] == "review" and st["key"]["holder"] == "Employee"
    assert st["plan"]["name"] == "Review access" and st["max_devices"] == 1
    clock["t"] = NOW + timedelta(days=31)
    st = m.state()
    assert st["status"] == "expired" and not st["access"]


def test_manager_rejects_bad_keys_and_withdrawn_keys(tmp_path):
    m, _ = _mgr(tmp_path)
    assert m.activate_key(_key(days=-1))["code"] == "expired"
    assert not m.activate_key(lk.normalize(_key())[:-3] + "AAA")["ok"]
    key = _key()
    assert m.activate_key(key)["ok"]
    kid = m.state()["key"]["key_id"]
    # the next app update ships the id in revoked_keys.json
    m2, _ = _mgr(tmp_path, revoked={kid})
    st = m2.state()
    assert st["status"] == "revoked" and not st["access"] and st["reason"] == "revoked"
    assert m2.activate_key(key)["code"] == "revoked"


def test_key_does_not_shorten_a_longer_paid_subscription(tmp_path):
    m, _ = _mgr(tmp_path)
    chk = m.start_checkout("yearly", "bkash", {"name": "Owner", "email": "a@b.co", "phone": "01700000000"})
    assert m.confirm_checkout(chk["checkout_id"])["paid"]
    r = m.activate_key(_key(days=30))
    assert not r["ok"] and "lasts longer" in r["error"]
    assert m.state()["plan"]["id"] == "yearly"


def test_old_random_keys_still_take_the_old_path(tmp_path):
    m, _ = _mgr(tmp_path)
    r = m.activate_key("OAXE-ABCD-EFGH-JKLM-NPQR")
    assert not r["ok"] and r.get("server_needed")


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
