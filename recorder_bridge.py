"""
recorder_bridge.py — screen recording + screenshots (JS-facing bridge methods).

Mixed into SFMBridge, so every public method is callable from JS as
window.pywebview.api.<name>(...). All return {ok: true, ...} or
{ok: false, error: "readable message"}.

Recording happens in the page (getDisplayMedia + MediaRecorder, see
ui/js/recorder.js). The page streams the encoded data here about once a
second, so long recordings never sit in memory:

    rec_begin(ext, out_dir)  → {id, dir, final_name}     (nothing on disk yet)
    rec_chunk(id, b64)       → appends to  <dir>/.recording-<id>.<ext>.part
    rec_finish(id, name)     → renames the .part to a unique "<name>.<ext>"
    rec_discard(id)          → moves the app's own .part to <dir>/_to_review/

Existing files are never overwritten (" (2)", " (3)" … names) and nothing is
deleted: a discarded recording is moved to ``_to_review`` like the app's
soft-delete. Screenshots: screenshot_full() (mss, every monitor) and
screenshot_region() (the screen_pick.py helper process, frozen-screen
"drag a box" overlay).
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Optional

_log = logging.getLogger("sfm")
_HERE = os.path.dirname(os.path.abspath(__file__))

VIDEO_EXTS = ("mp4", "webm")
LIST_EXTS = (".mp4", ".webm", ".png")
MAX_CHUNK_B64 = 64 * 1024 * 1024          # one ~1 s chunk is far smaller
MAX_RECORDING_BYTES = 200 * 1024 ** 3     # sanity cap (200 GB)
MIN_FREE_BYTES = 200 * 1024 ** 2          # stop before the disk is full
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_PART_RE = re.compile(r"^\.recording-([0-9a-f]{16})\.(mp4|webm)\.part$")
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(msg: str, **kw) -> dict:
    return {"ok": False, "error": str(msg), **kw}


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── pure helpers (unit-tested) ───────────────────────────────────────────────

def known_videos_dir() -> str:
    """The user's Videos folder (honours OneDrive / redirected folders)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                            ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

            # FOLDERID_Videos {18989B1D-99B5-455B-841C-AB7C74E4DDFC}
            g = GUID(0x18989B1D, 0x99B5, 0x455B,
                     (ctypes.c_ubyte * 8)(0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                                          ctypes.byref(p)) == 0:
                path = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                if path:
                    return path
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Videos")


def default_output_dir() -> str:
    return os.path.join(known_videos_dir(), "Office Axe")


def clean_stem(name: str, fallback: str = "Recording") -> str:
    """A safe Windows file-name stem (no folders, no reserved names)."""
    s = os.path.basename(str(name or "").replace("\\", "/"))
    s = _BAD_NAME_CHARS.sub("_", s).strip().rstrip(". ")
    if s.lower() in _RESERVED or not s.strip("._ "):
        s = fallback
    return s[:150]


def unique_path(folder: str, stem: str, ext: str) -> str:
    """<folder>/<stem>.<ext>, or "<stem> (2).<ext>" … when that exists."""
    ext = ext.lstrip(".")
    cand = os.path.join(folder, f"{stem}.{ext}")
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(folder, f"{stem} ({n}).{ext}")
        n += 1
    return cand


def is_inside(folder: str, path: str) -> bool:
    """True when *path* is *folder* itself's direct/indirect child."""
    try:
        f = os.path.normcase(os.path.realpath(folder))
        p = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([f, p]) == f and p != f
    except (ValueError, OSError):
        return False


def stamp(t: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t).strftime("%Y-%m-%d %H-%M-%S")


def _move_no_overwrite(src: str, dst_folder: str, stem: str, ext: str) -> str:
    """Rename *src* into *dst_folder* under a unique name; returns the new path."""
    for _ in range(50):
        dst = unique_path(dst_folder, stem, ext)
        try:
            os.rename(src, dst)          # Windows: fails instead of replacing
            return dst
        except FileExistsError:
            continue
    raise OSError("Could not find a free file name")


# ── global hotkey (Ctrl+Shift+R while recording, even when minimised) ───────

class _Hotkey:
    MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x2, 0x4, 0x4000
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

    def __init__(self, on_press):
        self._on_press = on_press
        self._tid = None
        self._thread = None
        self.registered = False

    def start(self) -> bool:
        if sys.platform != "win32" or self._thread:
            return self.registered
        ready = threading.Event()

        def run():
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
            self.registered = bool(u.RegisterHotKey(
                None, 0xB0A1, self.MOD_CONTROL | self.MOD_SHIFT | self.MOD_NOREPEAT, ord("R")))
            ready.set()
            if not self.registered:
                return
            msg = wintypes.MSG()
            try:
                while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == self.WM_HOTKEY:
                        try:
                            self._on_press()
                        except Exception:
                            pass
            finally:
                u.UnregisterHotKey(None, 0xB0A1)

        self._thread = threading.Thread(target=run, name="rec-hotkey", daemon=True)
        self._thread.start()
        ready.wait(2)
        return self.registered

    def stop(self) -> None:
        if self._thread and self._tid:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)
            except Exception:
                pass
            self._thread.join(2)
        self._thread = None
        self._tid = None
        self.registered = False


# ── bridge mixin ─────────────────────────────────────────────────────────────

class RecorderBridgeMixin:
    """Screen recording / screenshot API (see ui/js/recorder.js)."""

    # SFMBridge provides these; harmless defaults keep the mixin testable.
    _window = None

    def _emit(self, event, payload=None):  # pragma: no cover - overridden
        return None

    def _load_settings(self) -> dict:  # pragma: no cover - overridden
        return {}

    def _save_settings(self, data: dict) -> None:  # pragma: no cover - overridden
        return None

    # -- state ---------------------------------------------------------------
    def _rec_state(self) -> dict:
        st = self.__dict__.get("_rec")
        if st is None:
            st = {"lock": threading.Lock(), "sessions": {}, "hotkey": None}
            self.__dict__["_rec"] = st
        return st

    # -- folders -------------------------------------------------------------
    def _rec_resolve_dir(self, out_dir: str = "") -> str:
        d = str(out_dir or "").strip()
        if not d:
            d = str((self._load_settings() or {}).get("rec_folder") or "").strip()
        if not d:
            d = default_output_dir()
        d = os.path.abspath(os.path.expanduser(os.path.expandvars(d)))
        if not os.path.isabs(d):
            raise ValueError("The output folder must be a full path")
        os.makedirs(d, exist_ok=True)
        if not os.path.isdir(d):
            raise ValueError(f"Not a folder: {d}")
        return d

    def rec_default_dir(self) -> dict:
        """The folder recordings and screenshots are saved to (created if missing)."""
        try:
            saved = str((self._load_settings() or {}).get("rec_folder") or "").strip()
            path = self._rec_resolve_dir("")
            return _ok(path=path, is_default=not saved, default=default_output_dir())
        except Exception as exc:
            return _err(f"Cannot use the recordings folder: {exc}")

    def rec_set_dir(self, path: str = "") -> dict:
        """Remember the recordings folder ("" = back to Videos\\Office Axe)."""
        try:
            p = str(path or "").strip()
            if p:
                p = self._rec_resolve_dir(p)
            self._save_settings({"rec_folder": p})
            return self.rec_default_dir()
        except Exception as exc:
            return _err(f"Cannot use that folder: {exc}")

    # -- recording -----------------------------------------------------------
    def rec_begin(self, ext: str = "webm", out_dir: str = "") -> dict:
        try:
            ext = str(ext or "").lower().lstrip(".")
            if ext not in VIDEO_EXTS:
                return _err(f"Unsupported format: {ext}")
            folder = self._rec_resolve_dir(out_dir)
            rid = secrets.token_hex(8)
            part = os.path.join(folder, f".recording-{rid}.{ext}.part")
            st = self._rec_state()
            with st["lock"]:
                st["sessions"][rid] = {
                    "id": rid, "dir": folder, "ext": ext, "part": part, "fh": None,
                    "bytes": 0, "started": time.time(), "lock": threading.Lock(),
                }
            _log.info("rec_begin %s → %s", rid, part)
            return _ok(id=rid, dir=folder, part_path=part,
                       final_name=f"Recording {stamp()}.{ext}")
        except Exception as exc:
            return _err(f"Cannot start the recording: {exc}")

    def _rec_session(self, rid) -> Optional[dict]:
        rid = str(rid or "")
        if not _ID_RE.match(rid):
            return None
        st = self._rec_state()
        with st["lock"]:
            return st["sessions"].get(rid)

    def rec_chunk(self, rid: str, b64: str) -> dict:
        s = self._rec_session(rid)
        if not s:
            return _err("Unknown recording")
        if not isinstance(b64, str) or len(b64) > MAX_CHUNK_B64:
            return _err("Chunk too large")
        try:
            data = base64.b64decode(b64, validate=True) if b64 else b""
        except (binascii.Error, ValueError):
            return _err("Invalid chunk data")
        with s["lock"]:
            if s.get("closed"):
                return _err("Recording already finished")
            if s["bytes"] + len(data) > MAX_RECORDING_BYTES:
                return _err("Recording is too large", code="too_large")
            try:
                if shutil.disk_usage(s["dir"]).free < MIN_FREE_BYTES + len(data):
                    return _err("The disk is almost full", code="disk_full")
            except OSError:
                pass
            try:
                if s["fh"] is None:
                    if not is_inside(s["dir"], s["part"]):
                        return _err("Invalid output path")
                    s["fh"] = open(s["part"], "xb")
                s["fh"].write(data)
                s["bytes"] += len(data)
            except OSError as exc:
                return _err(f"Cannot write the recording: {exc}", code="write_failed")
            return _ok(bytes=s["bytes"])

    def _rec_close(self, s: dict) -> None:
        s["closed"] = True
        fh = s.get("fh")
        if fh is not None:
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                pass
            fh.close()
            s["fh"] = None

    def _rec_pop(self, rid) -> Optional[dict]:
        s = self._rec_session(rid)
        if s:
            st = self._rec_state()
            with st["lock"]:
                st["sessions"].pop(s["id"], None)
        return s

    def rec_finish(self, rid: str, name: str = "") -> dict:
        s = self._rec_pop(rid)
        if not s:
            return _err("Unknown recording")
        with s["lock"]:
            self._rec_close(s)
            if s["bytes"] <= 0 or not os.path.isfile(s["part"]):
                return _err("Nothing was recorded", code="empty")
            ext = s["ext"]
            stem = clean_stem(os.path.splitext(name)[0] if str(name).lower().endswith("." + ext)
                              else name, f"Recording {stamp(s['started'])}")
            try:
                final = _move_no_overwrite(s["part"], s["dir"], stem, ext)
            except OSError as exc:
                return _err(f"Saved as {os.path.basename(s['part'])} — could not rename: {exc}",
                            path=s["part"])
        size = os.path.getsize(final)
        _log.info("rec_finish %s → %s (%d bytes)", s["id"], final, size)
        return _ok(path=final, name=os.path.basename(final), dir=s["dir"],
                   size=size, size_str=_fmt_size(size),
                   duration=round(time.time() - s["started"], 1))

    def rec_discard(self, rid: str) -> dict:
        """Throw away a recording: only this recording's own .part file is
        touched, and it is moved to _to_review (never deleted)."""
        s = self._rec_pop(rid)
        if not s:
            return _err("Unknown recording")
        with s["lock"]:
            self._rec_close(s)
            if s["bytes"] <= 0 or not os.path.isfile(s["part"]):
                return _ok(moved_to="")
            review = os.path.join(s["dir"], "_to_review")
            try:
                os.makedirs(review, exist_ok=True)
                dst = _move_no_overwrite(s["part"], review,
                                         f"Discarded recording {stamp(s['started'])}", s["ext"])
            except OSError as exc:
                return _err(f"Could not move the discarded recording: {exc}")
        return _ok(moved_to=dst)

    def rec_list(self, limit: int = 20, out_dir: str = "") -> dict:
        """Newest recordings / screenshots in the folder, plus unfinished
        .part files left by a crash (which can be recovered)."""
        try:
            folder = self._rec_resolve_dir(out_dir)
            limit = max(1, min(int(limit or 20), 200))
            active = {s["part"] for s in self._rec_state()["sessions"].values()}
            items, parts = [], []
            with os.scandir(folder) as it:
                for e in it:
                    if not e.is_file():
                        continue
                    low = e.name.lower()
                    try:
                        stt = e.stat()
                    except OSError:
                        continue
                    row = {"path": e.path, "name": e.name, "size": stt.st_size,
                           "size_str": _fmt_size(stt.st_size), "mtime": stt.st_mtime}
                    if _PART_RE.match(low):
                        if e.path not in active and stt.st_size > 0:
                            parts.append(row)
                    elif low.endswith(LIST_EXTS):
                        row["kind"] = "image" if low.endswith(".png") else "video"
                        items.append(row)
            items.sort(key=lambda r: r["mtime"], reverse=True)
            parts.sort(key=lambda r: r["mtime"], reverse=True)
            return _ok(dir=folder, items=items[:limit], unfinished=parts[:10])
        except Exception as exc:
            return _err(str(exc))

    def rec_recover(self, part_path: str) -> dict:
        """Turn an unfinished .part (app crashed mid-recording) into a video."""
        try:
            p = os.path.abspath(str(part_path or ""))
            m = _PART_RE.match(os.path.basename(p).lower())
            folder = self._rec_resolve_dir("")
            if not m or not os.path.isfile(p) or os.path.dirname(os.path.normcase(p)) != \
                    os.path.normcase(folder):
                return _err("Not an unfinished recording")
            if p in {s["part"] for s in self._rec_state()["sessions"].values()}:
                return _err("That recording is still running")
            dst = _move_no_overwrite(p, folder, f"Recovered recording {stamp(os.path.getmtime(p))}",
                                     m.group(2))
            return _ok(path=dst, name=os.path.basename(dst))
        except Exception as exc:
            return _err(str(exc))

    def rec_reveal(self, path: str) -> dict:
        """Open Explorer with the file selected."""
        try:
            p = os.path.abspath(str(path or ""))
            if not os.path.exists(p):
                return _err("File not found")
            subprocess.Popen(["explorer", "/select,", p])
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def rec_set_title(self, text: str = "") -> dict:
        """Window (taskbar) title, e.g. "● Recording 00:12 — Office Axe"."""
        try:
            t = str(text or "").strip()[:120] or "Office Axe"
            if self._window is not None:
                self._window.set_title(t)
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def rec_hotkey(self, enable: bool = True) -> dict:
        """Ctrl+Shift+R as a system-wide hotkey while recording (event rec_hotkey)."""
        st = self._rec_state()
        try:
            if enable:
                if st["hotkey"] is None:
                    st["hotkey"] = _Hotkey(lambda: self._emit("rec_hotkey", {}))
                return _ok(registered=st["hotkey"].start())
            if st["hotkey"] is not None:
                st["hotkey"].stop()
                st["hotkey"] = None
            return _ok(registered=False)
        except Exception as exc:
            return _err(str(exc))

    def rec_window(self, action: str = "") -> dict:
        """'minimize' the app while recording, 'restore' it afterwards
        (back to maximised if it was maximised)."""
        try:
            if action not in ("minimize", "restore"):
                return _err("Unknown action")
            form = getattr(self._window, "native", None)
            if form is None:
                return _err("No window")
            from System import Func, Type  # type: ignore

            def _apply():
                if action == "minimize":
                    import System.Windows.Forms as WinForms  # type: ignore
                    form.WindowState = WinForms.FormWindowState.Minimized
                else:
                    import ctypes
                    hwnd = int(form.Handle.ToInt64())
                    ctypes.windll.user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)

            form.Invoke(Func[Type](_apply))
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    # -- screenshots ---------------------------------------------------------
    def _rec_hide_app(self, hide: bool) -> bool:
        """Make the app window invisible (opacity 0) for a capture. Best effort."""
        try:
            form = getattr(self._window, "native", None)
            if form is None:
                return False
            from System import Func, Type  # type: ignore

            def _apply():
                form.Opacity = 0.0 if hide else 1.0

            form.Invoke(Func[Type](_apply))
            if hide:
                time.sleep(0.25)      # let DWM present the change
            return True
        except Exception:
            return False

    def screenshot_full(self, hide_app: bool = True, out_dir: str = "") -> dict:
        """PNG of every monitor (the whole virtual desktop)."""
        hidden = False
        try:
            folder = self._rec_resolve_dir(out_dir)
            if hide_app:
                hidden = self._rec_hide_app(True)
            import qr_pick
            img, _origin = qr_pick.grab_virtual_screen()
        except Exception as exc:
            return _err(f"Screen capture failed: {exc}")
        finally:
            if hidden:
                self._rec_hide_app(False)
        try:
            path = unique_path(folder, f"Screenshot {stamp()}", "png")
            with open(path, "xb") as fh:
                img.save(fh, format="PNG", optimize=False)
            size = os.path.getsize(path)
            return _ok(path=path, name=os.path.basename(path), dir=folder, size=size,
                       size_str=_fmt_size(size), width=img.width, height=img.height)
        except Exception as exc:
            return _err(f"Cannot save the screenshot: {exc}")

    def screenshot_region(self, hide_app: bool = True, out_dir: str = "") -> dict:
        """Freeze the screen, let the user drag a box (screen_pick.py helper),
        save that part as PNG. {ok:false, reason:'cancelled'} on Esc."""
        st = self._rec_state()
        if st.get("picking"):
            return _err("A screenshot is already in progress", reason="busy")
        st["picking"] = True
        hidden = False
        try:
            folder = self._rec_resolve_dir(out_dir)
            path = unique_path(folder, f"Screenshot {stamp()}", "png")
            if hide_app:
                hidden = self._rec_hide_app(True)
            import screen_pick
            res = screen_pick.run_helper(path)
        except Exception as exc:
            res = {"ok": False, "reason": "error", "detail": str(exc)}
        finally:
            if hidden:
                self._rec_hide_app(False)
            st["picking"] = False
        if not res.get("ok"):
            reason = res.get("reason") or "error"
            return _err("Cancelled" if reason == "cancelled" else (res.get("detail") or "Screenshot failed"),
                        reason=reason)
        p = res.get("path") or ""
        if not p or not os.path.isfile(p) or not is_inside(folder, p):
            return _err("The screenshot was not saved", reason="error")
        size = os.path.getsize(p)
        return _ok(path=p, name=os.path.basename(p), dir=folder, size=size,
                   size_str=_fmt_size(size), width=res.get("width"), height=res.get("height"))
