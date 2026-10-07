"""Tests for archive_tools.py (ZIP / unzip / ZIP + PDF passwords).

Run:  PYTHONUTF8=1 python -m pytest -q tests/test_archive.py
"""
from __future__ import annotations

import os
import struct
import sys
import zipfile
import zlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import archive_tools as at  # noqa: E402

KOREAN = "학생_서류"


# ── helpers ──────────────────────────────────────────────────────────────────

def _tree(base):
    """A folder with nested sub-folders, an empty folder and Korean names."""
    root = base / "Docs"
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("bravo" * 1000, encoding="utf-8")
    (root / "sub" / "deeper" / f"{KOREAN}.txt").write_text("한국어 내용", encoding="utf-8")
    (root / f"{KOREAN}.bin").write_bytes(os.urandom(4096))
    return root


def _snapshot(folder):
    out = {}
    for r, ds, fs in os.walk(folder):
        rel = os.path.relpath(r, folder).replace(os.sep, "/")
        for d in ds:
            out[(rel + "/" + d).lstrip("./")] = None
        for f in fs:
            with open(os.path.join(r, f), "rb") as fh:
                out[(rel + "/" + f).lstrip("./")] = fh.read()
    return out


# Minimal ZIP writer (stored entries, optional legacy ZipCrypto, optional raw
# non-UTF-8 names) — the standard library can read but not write these.
_CRC_TABLE = []
for _n in range(256):
    _c = _n
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _CRC_TABLE.append(_c)


class _ZipCrypto:
    def __init__(self, pw: bytes):
        self.k = [0x12345678, 0x23456789, 0x34567890]
        for b in pw:
            self._update(b)

    @staticmethod
    def _crc(crc, b):
        return (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]

    def _update(self, b):
        k = self.k
        k[0] = self._crc(k[0], b)
        k[1] = (k[1] + (k[0] & 0xFF)) & 0xFFFFFFFF
        k[1] = (k[1] * 134775813 + 1) & 0xFFFFFFFF
        k[2] = self._crc(k[2], (k[1] >> 24) & 0xFF)

    def encrypt(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            t = (self.k[2] | 2) & 0xFFFF
            ks = ((t * (t ^ 1)) >> 8) & 0xFF
            self._update(b)
            out.append(b ^ ks)
        return bytes(out)


def _raw_zip(path, entries, password: bytes = b""):
    """entries: [(name_bytes, data, utf8_flag)]"""
    body, central = bytearray(), bytearray()
    dostime, dosdate = (12 << 11) | (30 << 5), ((2024 - 1980) << 9) | (5 << 5) | 17
    for name, data, utf8 in entries:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        flags = (0x800 if utf8 else 0) | (0x1 if password else 0)
        payload = data
        if password:
            zc = _ZipCrypto(password)
            header = os.urandom(11) + bytes([(crc >> 24) & 0xFF])
            payload = zc.encrypt(header + data)
        off = len(body)
        body += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, 0, dostime, dosdate,
                            crc, len(payload), len(data), len(name), 0) + name + payload
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags, 0, dostime,
                               dosdate, crc, len(payload), len(data), len(name), 0, 0, 0, 0,
                               0, off) + name
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries),
                       len(central), len(body), 0)
    with open(path, "wb") as f:
        f.write(bytes(body) + bytes(central) + eocd)
    return path


# ── zip / extract round trip ─────────────────────────────────────────────────

def test_zip_extract_round_trip_folder(tmp_path):
    src = _tree(tmp_path)
    before = _snapshot(src)
    seen = []
    r = at.zip_paths([str(src)], progress=lambda d, t, f: seen.append((d, t, f)))
    assert os.path.basename(r["out_path"]) == "Docs.zip"
    assert r["files"] == 4 and r["dirs"] >= 3 and not r["encrypted"]
    assert seen and seen[-1][0] == seen[-1][1] == r["bytes"]
    with zipfile.ZipFile(r["out_path"]) as zf:
        names = zf.namelist()
    assert f"Docs/sub/deeper/{KOREAN}.txt" in names
    assert "Docs/empty/" in names

    out = tmp_path / "out"
    out.mkdir()
    x = at.extract_zip(r["out_path"], str(out))
    # Single top-level folder named like the ZIP → not nested twice.
    assert x["out_dir"] == str(out / "Docs")
    assert _snapshot(out / "Docs") == before
    assert _snapshot(src) == before      # source untouched


def test_zip_multiple_items_and_default_name(tmp_path):
    src = _tree(tmp_path)
    items = [str(src / "a.txt"), str(src / "sub")]
    r = at.zip_paths(items)
    assert os.path.basename(r["out_path"]) == "Docs.zip"         # parent folder name
    assert os.path.dirname(r["out_path"]) == str(src)
    with zipfile.ZipFile(r["out_path"]) as zf:
        assert set(n for n in zf.namelist() if not n.endswith("/")) == {
            "a.txt", "sub/b.txt", f"sub/deeper/{KOREAN}.txt"}
    single = at.zip_paths([str(src / "a.txt")], compression="store")
    assert os.path.basename(single["out_path"]) == "a.zip"
    named = at.zip_paths([str(src / "a.txt")], out_path=f"{KOREAN}.zip", level=9)
    assert os.path.basename(named["out_path"]) == f"{KOREAN}.zip"


def test_never_overwrite_names(tmp_path):
    src = _tree(tmp_path)
    r1 = at.zip_paths([str(src)])
    r2 = at.zip_paths([str(src)])
    assert os.path.basename(r2["out_path"]) == "Docs (2).zip"
    assert os.path.getsize(r1["out_path"]) > 0

    f = tmp_path / "report.txt"
    f.write_text("v1", encoding="utf-8")
    z = at.zip_paths([str(f)])["out_path"]
    x1 = at.extract_zip(z)                       # → report/
    x2 = at.extract_zip(z)                       # → report (2)/
    assert os.path.basename(x1["out_dir"]) == "report"
    assert os.path.basename(x2["out_dir"]) == "report (2)"
    # Extract here: report.txt already exists next to the zip → renamed, original kept.
    x3 = at.extract_zip(z, mode="here")
    assert (tmp_path / "report.txt").read_text(encoding="utf-8") == "v1"
    assert x3["renamed"] and x3["renamed"][0]["to"] == "report (2).txt"
    assert (tmp_path / "report (2).txt").read_text(encoding="utf-8") == "v1"


def test_zip_does_not_include_itself(tmp_path):
    src = _tree(tmp_path)
    r = at.zip_paths([str(src)], out_path=str(src / "inside.zip"))
    with zipfile.ZipFile(r["out_path"]) as zf:
        assert not any(n.endswith("inside.zip") for n in zf.namelist())


def test_list_zip(tmp_path):
    src = _tree(tmp_path)
    z = at.zip_paths([str(src)])["out_path"]
    info = at.list_zip(z)
    assert info["files"] == 4 and info["count"] == len(info["entries"])
    assert not info["encrypted"] and not info["aes"]
    e = next(e for e in info["entries"] if e["name"].endswith("b.txt"))
    assert e["size"] == 5000 and e["compressed"] < e["size"] and not e["is_dir"]
    assert e["date"][:2] in ("19", "20")
    assert any(x["is_dir"] for x in info["entries"])


def test_extract_selected_members(tmp_path):
    src = _tree(tmp_path)
    z = at.zip_paths([str(src)])["out_path"]
    out = tmp_path / "sel"
    r = at.extract_zip(z, str(out), members=["Docs/sub"])
    got = _snapshot(out)
    files = {k for k, v in got.items() if v is not None}
    assert files == {"Docs/sub/b.txt", f"Docs/sub/deeper/{KOREAN}.txt"}
    assert r["files"] == 2
    with pytest.raises(at.ArchiveError):
        at.extract_zip(z, str(out), members=["nope.txt"])


# ── safety ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../evil.txt", "a/../../evil.txt", "/abs.txt", "C:/win.txt", "..\\evil.txt"])
def test_zip_slip_rejected(tmp_path, bad):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("ok.txt", "fine")
        zi = zipfile.ZipInfo("placeholder")
        zi.filename = bad            # bypass zipfile's own normalisation
        zf.writestr(zi, "evil")
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(at.ArchiveError) as ei:
        at.extract_zip(str(z), str(dest), mode="here")
    assert ei.value.code == "unsafe_path"
    assert not (tmp_path / "evil.txt").exists()
    assert os.listdir(dest) == []    # rejected before anything was written


def test_windows_unsafe_names_are_cleaned(tmp_path):
    z = tmp_path / "w.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("ab:c?.txt", "1")
        zf.writestr("CON.txt", "2")
        zf.writestr("trail. ", "3")
    r = at.extract_zip(str(z))
    assert sorted(os.listdir(r["out_dir"])) == ["_CON.txt", "ab_c_.txt", "trail"]


def test_not_a_zip(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"not a zip at all")
    with pytest.raises(at.ArchiveError) as ei:
        at.list_zip(str(p))
    assert ei.value.code == "not_zip"


def test_non_utf8_korean_names(tmp_path):
    z = _raw_zip(tmp_path / "kr.zip", [(f"{KOREAN}.txt".encode("cp949"), b"hello", False)])
    info = at.list_zip(str(z))
    assert info["entries"][0]["name"] == f"{KOREAN}.txt"
    r = at.extract_zip(str(z))
    assert os.listdir(r["out_dir"]) == [f"{KOREAN}.txt"]


# ── ZipCrypto (readable with the standard library) ───────────────────────────

def test_zipcrypto_extract_with_password(tmp_path):
    z = _raw_zip(tmp_path / "legacy.zip",
                 [(b"secret.txt", b"top secret data", True),
                  (f"{KOREAN}.txt".encode("utf-8"), "비밀".encode("utf-8"), True)],
                 password=b"pass123")
    info = at.list_zip(str(z))
    assert info["encrypted"] and not info["aes"]
    assert all(e["encrypted"] for e in info["entries"])
    assert at.list_zip(str(z), "pass123")["password_ok"] is True
    assert at.list_zip(str(z), "nope")["password_ok"] is False

    with pytest.raises(at.ArchiveError) as ei:
        at.extract_zip(str(z))
    assert ei.value.code == "need_password" and str(ei.value) == "This ZIP is password-protected"
    with pytest.raises(at.ArchiveError) as ei:
        at.extract_zip(str(z), password="wrong")
    assert ei.value.code == "wrong_password" and str(ei.value) == "Wrong password"

    r = at.extract_zip(str(z), password="pass123")
    with open(os.path.join(r["out_dir"], "secret.txt"), "rb") as f:
        assert f.read() == b"top secret data"
    with open(os.path.join(r["out_dir"], f"{KOREAN}.txt"), encoding="utf-8") as f:
        assert f.read() == "비밀"


def test_zipcrypto_remove_password_without_pyzipper(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "HAVE_PYZIPPER", False)
    z = _raw_zip(tmp_path / "legacy.zip", [(b"s.txt", b"data!", True)], password=b"pw")
    before = z.read_bytes()
    with pytest.raises(at.ArchiveError) as ei:
        at.zip_remove_password(str(z), "bad")
    assert ei.value.code == "wrong_password"
    pending = [n for n in os.listdir(tmp_path) if n.startswith(at.PENDING_PREFIX)]
    assert pending == []            # password checked before any file is created
    r = at.zip_remove_password(str(z), "pw")
    assert os.path.basename(r["out_path"]) == "legacy_unlocked.zip"
    assert z.read_bytes() == before                     # original untouched
    with zipfile.ZipFile(r["out_path"]) as zf:
        assert zf.read("s.txt") == b"data!"
    with pytest.raises(at.ArchiveError) as ei:
        at.zip_remove_password(r["out_path"], "pw")
    assert ei.value.code == "not_encrypted"


# ── pyzipper (AES) ───────────────────────────────────────────────────────────

def test_need_pyzipper_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "HAVE_PYZIPPER", False)
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(at.ArchiveError) as ei:
        at.zip_paths([str(f)], password="secret")
    assert ei.value.code == "need_pyzipper" and "pyzipper" in str(ei.value)
    assert not (tmp_path / "a.zip").exists()
    z = at.zip_paths([str(f)])["out_path"]          # plain zip still works
    with pytest.raises(at.ArchiveError) as ei:
        at.zip_set_password(z, "secret")
    assert ei.value.code == "need_pyzipper"
    assert at.capabilities()["zip_password"] is False


needs_pyzipper = pytest.mark.skipif(not at.HAVE_PYZIPPER, reason="pyzipper not installed")


@needs_pyzipper
def test_aes_zip_round_trip(tmp_path):
    src = _tree(tmp_path)
    before = _snapshot(src)
    z = at.zip_paths([str(src)], password="s3cret!")["out_path"]
    info = at.list_zip(z)
    assert info["encrypted"] and info["aes"]
    with pytest.raises(at.ArchiveError) as ei:
        at.extract_zip(z, str(tmp_path / "o1"))
    assert ei.value.code == "need_password"
    with pytest.raises(at.ArchiveError) as ei:
        at.extract_zip(z, str(tmp_path / "o2"), password="bad")
    assert ei.value.code == "wrong_password"
    r = at.extract_zip(z, str(tmp_path / "o3"), password="s3cret!")
    assert _snapshot(r["out_dir"]) == before


@needs_pyzipper
def test_aes_set_change_remove_password(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello", encoding="utf-8")
    plain = at.zip_paths([str(f)])["out_path"]
    p = at.zip_set_password(plain, "one")
    assert os.path.basename(p["out_path"]) == "doc_protected.zip"
    assert not at.list_zip(plain)["encrypted"]                 # original untouched
    with pytest.raises(at.ArchiveError) as ei:
        at.zip_set_password(p["out_path"], "two")
    assert ei.value.code == "need_password"
    c = at.zip_set_password(p["out_path"], "two", old_password="one")
    assert os.path.basename(c["out_path"]) == "doc_protected (2).zip"
    u = at.zip_remove_password(c["out_path"], "two")
    assert os.path.basename(u["out_path"]) == "doc_unlocked.zip"
    with zipfile.ZipFile(u["out_path"]) as zf:
        assert zf.read("doc.txt") == b"hello"


# ── PDF passwords ────────────────────────────────────────────────────────────

def _pdf(path, text="hello pdf"):
    fitz = at._fitz()
    d = fitz.open()
    d.new_page().insert_text((72, 72), text)
    d.new_page()
    d.save(str(path))
    d.close()
    return path


def test_pdf_password_round_trip(tmp_path):
    fitz = at._fitz()
    src = _pdf(tmp_path / "report.pdf")
    before = src.read_bytes()
    assert at.pdf_is_encrypted(str(src))["encrypted"] is False
    with pytest.raises(at.ArchiveError) as ei:
        at.pdf_remove_password(str(src), "x")
    assert ei.value.code == "not_encrypted"

    r = at.pdf_set_password(str(src), "user1", "owner1", {"print": False, "copy": False})
    assert os.path.basename(r["out_path"]) == "report_protected.pdf"
    assert src.read_bytes() == before
    st = at.pdf_is_encrypted(r["out_path"])
    assert st["encrypted"] and st["needs_password"]
    d = fitz.open(r["out_path"])
    assert d.needs_pass and d.authenticate("user1") and not d.permissions & fitz.PDF_PERM_PRINT
    d.close()

    with pytest.raises(at.ArchiveError) as ei:
        at.pdf_remove_password(r["out_path"], "wrong")
    assert ei.value.code == "wrong_password" and str(ei.value) == "Wrong password"
    with pytest.raises(at.ArchiveError) as ei:
        at.pdf_remove_password(r["out_path"], "")
    assert ei.value.code == "need_password"

    u = at.pdf_remove_password(r["out_path"], "user1")
    assert os.path.basename(u["out_path"]) == "report_unlocked.pdf"
    d = fitz.open(u["out_path"])
    assert not d.needs_pass and len(d) == 2 and "hello pdf" in d[0].get_text()
    d.close()
    # owner password works too, and never overwrites
    u2 = at.pdf_remove_password(r["out_path"], "owner1")
    assert os.path.basename(u2["out_path"]) == "report_unlocked (2).pdf"


def test_pdf_set_password_on_protected_needs_current(tmp_path):
    src = _pdf(tmp_path / "a.pdf")
    r = at.pdf_set_password(str(src), "u")
    with pytest.raises(at.ArchiveError) as ei:
        at.pdf_set_password(r["out_path"], "v")
    assert ei.value.code == "need_password"
    with pytest.raises(at.ArchiveError) as ei:
        at.pdf_set_password(r["out_path"], "v", current_password="bad")
    assert ei.value.code == "wrong_password"
    r2 = at.pdf_set_password(r["out_path"], "v", current_password="u")
    assert at.pdf_check_password(r2["out_path"], "v")
    assert not at.pdf_check_password(r2["out_path"], "u")
    with pytest.raises(at.ArchiveError):
        at.pdf_set_password(str(src), "")


def test_pdf_owner_only_restrictions(tmp_path):
    fitz = at._fitz()
    src = _pdf(tmp_path / "b.pdf")
    r = at.pdf_set_password(str(src), "", "boss", {"edit": False})
    st = at.pdf_is_encrypted(r["out_path"])
    assert st["encrypted"] and not st["needs_password"]
    d = fitz.open(r["out_path"])
    assert not d.permissions & fitz.PDF_PERM_MODIFY
    d.close()
    with pytest.raises(at.ArchiveError):
        at.pdf_remove_password(r["out_path"], "nope")
    u = at.pdf_remove_password(r["out_path"], "boss")
    d = fitz.open(u["out_path"])
    assert "hello pdf" in d[0].get_text() and not (d.metadata or {}).get("encryption")
    d.close()


# ── bridge (archive_bridge.ArchiveBridgeMixin) ───────────────────────────────

class _Bridge:
    def __init__(self):
        import threading
        from archive_bridge import ArchiveBridgeMixin

        events, done = [], threading.Event()

        class B(ArchiveBridgeMixin):
            def _emit(self, ev, payload=None):
                events.append((ev, payload))
                if ev == "archive_done":
                    done.set()

        self.b, self.events, self.done = B(), events, done

    def wait(self):
        assert self.done.wait(10)
        self.done.clear()
        return [p for e, p in self.events if e == "archive_done"][-1]


def test_bridge_zip_and_extract_events(tmp_path):
    br = _Bridge()
    src = _tree(tmp_path)
    r = br.b.zip_paths([str(src)], "", "", "job1")
    assert r["ok"] and r["job_id"] == "job1"
    d = br.wait()
    assert d["ok"] and d["job_id"] == "job1" and d["out_path"].endswith("Docs.zip")
    assert any(e == "archive_progress" and p["job_id"] == "job1" for e, p in br.events)
    r = br.b.zip_extract(d["out_path"], str(tmp_path / "x"), "", None, "job2")
    assert r["ok"]
    d2 = br.wait()
    assert d2["ok"] and os.path.isdir(d2["out_path"])
    caps = br.b.archive_capabilities()
    assert caps["ok"] and caps["zip_password"] == at.HAVE_PYZIPPER


def test_bridge_extract_password_codes(tmp_path):
    br = _Bridge()
    z = _raw_zip(tmp_path / "p.zip", [(b"s.txt", b"x", True)], password=b"pw")
    assert br.b.zip_extract(str(z))["code"] == "need_password"
    assert br.b.zip_extract(str(z), "", "bad")["code"] == "wrong_password"
    assert br.b.zip_extract(str(z), "", "pw", None, "j")["ok"]
    assert br.wait()["ok"]
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"nope")
    assert br.b.zip_list(str(bad))["code"] == "not_zip"


def test_bridge_pdf_password_and_preview_unlock(tmp_path):
    import file_ops
    br = _Bridge()
    src = _pdf(tmp_path / "c.pdf")
    r = br.b.pdf_set_password(str(src), "pw", "", {"print": True})
    assert r["ok"] and r["out_path"].endswith("c_protected.pdf")
    assert br.b.pdf_is_encrypted(r["out_path"])["needs_password"] is True
    st = br.b.pdf_preview_state(r["out_path"])
    assert st["ok"] and st["locked"] and st["needs_password"] and st["encrypted"]
    assert br.b.pdf_unlock_preview(r["out_path"], "bad")["code"] == "wrong_password"
    u = br.b.pdf_unlock_preview(r["out_path"], "pw")
    assert u["ok"] and u["pages"] == 2
    st = br.b.pdf_preview_state(r["out_path"])
    assert st["ok"] and not st["locked"] and st["encrypted"]
    plain = br.b.pdf_preview_state(str(src))
    assert plain["ok"] and not plain["encrypted"] and not plain["locked"]
    assert file_ops.pil_image_for_pdf_page(r["out_path"], 0, 200, 200) is not None
    file_ops.release_pdf_handles_for_paths((r["out_path"],))
    rm = br.b.pdf_remove_password(r["out_path"], "pw")
    assert rm["ok"] and rm["out_path"].endswith("c_unlocked.pdf")
    assert br.b.pdf_remove_password(r["out_path"], "x")["code"] == "wrong_password"
