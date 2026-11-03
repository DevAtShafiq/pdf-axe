"""Files Windows marked as downloaded get unblocked (ZoneId=0), never deleted."""
import os
import sys

import pytest

import motw

BLOCKED = "[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://example.com/a.zip\r\n"


def test_zone_id_parsing():
    assert motw._zone_id(BLOCKED) == 3
    assert motw._zone_id("[ZoneTransfer]\nZoneId=0\n") == 0
    assert motw._zone_id("[ZoneTransfer]\n") is None


def test_unblock_tree_with_fake_streams(monkeypatch, tmp_path):
    # Stand-in for NTFS streams so the logic runs on any OS/file system.
    streams = {}
    real_open = open

    def fake_open(path, mode="r", *a, **k):
        if path.endswith(":Zone.Identifier"):
            import io
            if "w" in mode:
                buf = io.StringIO()
                buf.close = lambda: streams.__setitem__(path, buf.getvalue())
                return buf
            if path not in streams:
                raise FileNotFoundError(path)
            return io.StringIO(streams[path])
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    sub = tmp_path / "pythonnet" / "runtime"
    sub.mkdir(parents=True)
    dll, txt, ok = sub / "Python.Runtime.dll", tmp_path / "notes.txt", tmp_path / "local.pyd"
    for p in (dll, txt, ok):
        p.write_bytes(b"x")
    streams[str(dll) + ":Zone.Identifier"] = BLOCKED
    streams[str(txt) + ":Zone.Identifier"] = BLOCKED
    streams[str(ok) + ":Zone.Identifier"] = "[ZoneTransfer]\r\nZoneId=0\r\n"

    assert motw.unblock_tree(str(tmp_path)) == 1
    assert motw._zone_id(streams[str(dll) + ":Zone.Identifier"]) == 0
    assert motw._zone_id(streams[str(txt) + ":Zone.Identifier"]) == 3   # not a DLL: left alone
    assert dll.exists() and txt.exists()
    assert motw.unblock_tree(str(tmp_path)) == 0                         # second start: nothing to do


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS alternate data streams")
def test_unblock_real_stream_on_ntfs(tmp_path):
    dll = tmp_path / "Python.Runtime.dll"
    dll.write_bytes(b"MZ")
    try:
        with open(str(dll) + ":Zone.Identifier", "w") as f:
            f.write(BLOCKED)
    except OSError:
        pytest.skip("temp folder has no NTFS streams")
    assert motw.unblock_file(str(dll)) is True
    with open(str(dll) + ":Zone.Identifier") as f:
        assert motw._zone_id(f.read()) == 0
    assert dll.read_bytes() == b"MZ"


def test_unmarked_file_is_untouched(tmp_path):
    p = tmp_path / "a.dll"
    p.write_bytes(b"x")
    assert motw.unblock_file(str(p)) is False
    assert not os.path.exists(str(p) + ":Zone.Identifier") or sys.platform != "win32"
