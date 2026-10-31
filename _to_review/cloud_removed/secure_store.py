"""
secure_store.py — Protect small secrets (the cloud session token) at rest.

On Windows the value is encrypted with DPAPI (CryptProtectData, current-user
scope) through ctypes, so a copied sfm_settings.json is useless on another
machine or Windows account. Elsewhere — or if DPAPI fails — the value is
stored as-is, which is what the app did before.

Stored formats:   "dpapi:<base64>"   or   "plain:<value>"
"""
from __future__ import annotations

import base64
import sys

_ENTROPY = b"PDF Axe cloud session v1"
_PREFIX_DPAPI = "dpapi:"
_PREFIX_PLAIN = "plain:"


def _dpapi():
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return ctypes, DATA_BLOB, crypt32, kernel32
    except Exception:
        return None


def _blob(ctypes, DATA_BLOB, data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _protect_dpapi(data: bytes) -> bytes | None:
    api = _dpapi()
    if api is None:
        return None
    ctypes, DATA_BLOB, crypt32, kernel32 = api
    inp, _k1 = _blob(ctypes, DATA_BLOB, data)
    ent, _k2 = _blob(ctypes, DATA_BLOB, _ENTROPY)
    out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptProtectData(ctypes.byref(inp), "PDF Axe", ctypes.byref(ent),
                                    None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _unprotect_dpapi(data: bytes) -> bytes | None:
    api = _dpapi()
    if api is None:
        return None
    ctypes, DATA_BLOB, crypt32, kernel32 = api
    inp, _k1 = _blob(ctypes, DATA_BLOB, data)
    ent, _k2 = _blob(ctypes, DATA_BLOB, _ENTROPY)
    out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(inp), None, ctypes.byref(ent),
                                      None, None, 0x1, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(value: str) -> str:
    """Encode a secret for storage. Empty stays empty."""
    if not value:
        return ""
    enc = _protect_dpapi(value.encode("utf-8"))
    if enc is not None:
        return _PREFIX_DPAPI + base64.b64encode(enc).decode("ascii")
    return _PREFIX_PLAIN + value


def unprotect(stored: str) -> str:
    """Decode a value written by protect(). Returns "" if it cannot be read
    (for example a DPAPI blob copied from another Windows account)."""
    if not stored:
        return ""
    if stored.startswith(_PREFIX_DPAPI):
        try:
            raw = _unprotect_dpapi(base64.b64decode(stored[len(_PREFIX_DPAPI):]))
        except Exception:
            raw = None
        return raw.decode("utf-8") if raw is not None else ""
    if stored.startswith(_PREFIX_PLAIN):
        return stored[len(_PREFIX_PLAIN):]
    return stored


def is_encrypted(stored: str) -> bool:
    return bool(stored) and stored.startswith(_PREFIX_DPAPI)
