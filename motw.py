"""Unblock the app's own files when Windows marked them as downloaded.

A folder extracted from a zip that came from the internet, WhatsApp or email
carries a Zone.Identifier stream on every file ("Mark of the Web"). .NET then
refuses to load _internal\\pythonnet\\runtime\\Python.Runtime.dll and the app
dies with "Failed to resolve Python.Runtime.Loader.Initialize".

The stream is rewritten to ZoneId=0 (this computer) rather than removed, so no
file or stream is ever deleted. Errors (read-only install, FAT/USB drive) are
ignored: OfficeAxe.exe.config (loadFromRemoteSources) covers those cases.
"""
from __future__ import annotations

import os

UNBLOCK_EXTS = (".dll", ".pyd", ".exe")
_LOCAL_ZONE = "[ZoneTransfer]\r\nZoneId=0\r\n"


def _zone_id(text: str) -> int | None:
    for line in text.splitlines():
        k, _, v = line.partition("=")
        if k.strip().lower() == "zoneid":
            try:
                return int(v.strip())
            except ValueError:
                return None
    return None


def unblock_file(path: str) -> bool:
    """Mark *path* as local if it carries an internet zone. True when changed."""
    stream = path + ":Zone.Identifier"
    try:
        with open(stream, encoding="utf-8", errors="replace") as f:
            zone = _zone_id(f.read())
    except OSError:
        return False                    # no mark (or no NTFS streams here)
    if zone == 0:
        return False
    try:
        with open(stream, "w", encoding="ascii", newline="") as f:
            f.write(_LOCAL_ZONE)
        return True
    except OSError:
        return False


def unblock_tree(root: str, exts: tuple[str, ...] = UNBLOCK_EXTS) -> int:
    """Unblock every *exts* file under *root*; returns how many were changed."""
    n = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(exts) and unblock_file(os.path.join(dirpath, name)):
                n += 1
    return n
