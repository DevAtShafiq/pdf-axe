"""
sfm_bridge.py — PyWebView JS API bridge for StudentFolderMaker.

Every public method on SFMBridge is directly callable from JavaScript as:
    await window.pywebview.api.method_name(arg1, arg2, ...)

Rules:
 - All return values must be JSON-serialisable (dict / list / str / int / float / bool / None).
 - Long-running operations run in a daemon thread and push progress via
   self._emit(event, payload) which calls window.evaluate_js() on the JS side.
 - Never import Tkinter here.
 - Never duplicate backend logic — call file_ops / ai_photo_editor / etc. directly.
"""
from __future__ import annotations

import os
import sys
import json
import re
import base64
import threading
import traceback
import time
import logging
from typing import Any

# ── file logger (writes to sfm_debug.log next to this script) ────────────────
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sfm_debug.log")
logging.basicConfig(
    filename=_LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    encoding="utf-8",
)
_log = logging.getLogger("sfm")

# ── ensure project root is on sys.path ───────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import file_ops as _fo
import media_convert as _mc
from pdf_tools_bridge import PdfToolsBridgeMixin
from annotate_bridge import AnnotateBridgeMixin
from pdf_edit_bridge import PdfEditBridgeMixin
from studio_bridge import StudioBridgeMixin

# ── helpers ───────────────────────────────────────────────────────────────────

def _ok(**kw) -> dict:
    return {"ok": True, **kw}

def _err(msg: str, **kw) -> dict:
    return {"ok": False, "error": str(msg), **kw}

def _check(res, fail_msg: str = "Operation failed"):
    """Normalize file_ops return conventions and raise on failure.

    Many file_ops functions return (ok, payload) tuples or plain bools
    instead of raising. The bridge used to ignore those values, so failures
    were silently reported to the UI as success. This helper raises
    RuntimeError on failure and returns the useful payload on success.
    """
    if isinstance(res, tuple) and len(res) >= 2 and isinstance(res[0], bool):
        ok, payload = res[0], res[1]
        if not ok:
            raise RuntimeError(str(payload) or fail_msg)
        return payload
    if res is False:
        raise RuntimeError(fail_msg)
    return res

def _noop_log(msg: str) -> None:
    _log.info("[fo] %s", msg)

def _b64_png(pil_img) -> str:
    """Convert a PIL Image to a base64-encoded PNG data-URL."""
    import io
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

def _b64_jpg(pil_img, quality: int = 85) -> str:
    import io
    buf = io.BytesIO()
    pil_img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def _file_entry(path: str) -> dict:
    """Build a JSON-serialisable dict describing one file/folder."""
    try:
        stat = os.stat(path)
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        size, mtime = 0, 0
    name = os.path.basename(path)
    ext  = os.path.splitext(name)[1].lower()
    is_dir = os.path.isdir(path)
    return {
        "path":    path,
        "name":    name,
        "ext":     ext,
        "is_dir":  is_dir,
        "size":    size,
        "mtime":   mtime,
        "size_str": _fmt_size(size) if not is_dir else "",
    }

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── bridge ────────────────────────────────────────────────────────────────────

from cloud_bridge import CloudBridgeMixin  # noqa: E402  (account / subscription / cloud methods)
from office_bridge import OfficeBridgeMixin  # noqa: E402  (office + shared drive methods)
from archive_bridge import ArchiveBridgeMixin  # noqa: E402  (zip / unzip / ZIP + PDF passwords)
from recorder_bridge import RecorderBridgeMixin  # noqa: E402  (screen recording + screenshots)
from doc_preview_bridge import DocPreviewBridgeMixin  # noqa: E402  (PDF text layer, Word/Excel previews)


class SFMBridge(PdfToolsBridgeMixin, AnnotateBridgeMixin, PdfEditBridgeMixin, StudioBridgeMixin, CloudBridgeMixin, OfficeBridgeMixin, ArchiveBridgeMixin, RecorderBridgeMixin, DocPreviewBridgeMixin):
    """
    Singleton exposed to JavaScript as window.pywebview.api.
    The pywebview window reference is injected after creation via set_window().
    """

    def __init__(self):
        self._window = None          # set by main_webview.py after window creation
        self._lock = threading.Lock()

    def set_window(self, window) -> None:
        self._window = window
        _log.info("set_window: window reference attached (events enabled)")

    # ── internal helpers ──────────────────────────────────────────────────────

    def _emit(self, event: str, payload: Any = None) -> None:
        """Push an event to JS:  window.__sfm_event(event, payload)"""
        if not self._window:
            _log.warning("_emit(%s): dropped — no window reference yet", event)
            return
        try:
            js_payload = json.dumps(payload, ensure_ascii=False)
            res = self._window.evaluate_js(
                f"window.__sfm_event ? (window.__sfm_event({json.dumps(event)}, {js_payload}), 'ok') : 'no-handler'"
            )
            if res != "ok":
                _log.warning("_emit(%s): not delivered (res=%r)", event, res)
        except Exception as exc:
            _log.error("_emit(%s) failed: %s", event, exc)

    def _thread(self, fn, *args, **kwargs) -> None:
        """Run fn(*args, **kwargs) in a daemon thread."""
        t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
        t.start()

    def _log_cb(self, event_name: str):
        """Return a log callable that emits log lines to JS."""
        def _log(msg: str):
            self._emit(event_name, {"log": str(msg)})
        return _log

    # =========================================================================
    # FOLDER / FILE SYSTEM
    # =========================================================================

    def list_folder(self, path: str) -> dict:
        """List immediate children of a folder. Returns sorted entries."""
        _log.info("list_folder: %s", path)
        try:
            # "C:" means the current directory on drive C, not its root.
            if isinstance(path, str) and re.fullmatch(r"[A-Za-z]:", path.strip()):
                path = path.strip() + "\\"
            if not os.path.isdir(path):
                _log.warning("list_folder: not a directory: %s", path)
                return _err(f"Not a directory: {path}")
            names = os.listdir(path)
            entries = []
            for name in names:
                full = os.path.join(path, name)
                entries.append(_file_entry(full))
            entries.sort(key=lambda e: (
                0 if e["is_dir"] else 1,
                _fo.filesystem_natural_sort_key(e["name"])
            ))
            _log.info("list_folder: returned %d entries", len(entries))
            return _ok(entries=entries, path=path)
        except Exception as exc:
            _log.error("list_folder error: %s", exc, exc_info=True)
            return _err(str(exc))

    def list_folder_recursive(self, path: str, max_depth: int = 3) -> dict:
        """Walk folder up to max_depth, return nested structure."""
        def _walk(p, depth):
            if depth > max_depth:
                return []
            try:
                names = os.listdir(p)
            except PermissionError:
                return []
            result = []
            for name in sorted(names, key=lambda n: _fo.filesystem_natural_sort_key(n)):
                full = os.path.join(p, name)
                e = _file_entry(full)
                if e["is_dir"] and depth < max_depth:
                    e["children"] = _walk(full, depth + 1)
                result.append(e)
            return result
        try:
            return _ok(tree=_walk(path, 0), path=path)
        except Exception as exc:
            return _err(str(exc))

    def get_drives(self) -> dict:
        """Return available Windows drives."""
        drives = []
        try:
            import string
            for letter in string.ascii_uppercase:
                p = letter + ":\\"
                if os.path.exists(p):
                    drives.append({"path": p, "name": letter + ":", "is_dir": True})
        except Exception:
            pass
        return _ok(drives=drives)

    def get_home(self) -> dict:
        return _ok(path=os.path.expanduser("~"))

    # ── Windows shell icons ────────────────────────────────────────────────────
    def _shell_icon_data_url(self, spec: str, size: int = 20, *, file_attributes: bool = False) -> str:
        """PNG data-URL of a Windows shell icon, or '' if it cannot be drawn.

        file_attributes=True looks up a fake name such as ``x.pdf`` (no real file).
        Otherwise *spec* is a real path, usually an application's exe.
        """
        import base64, io, sys
        if sys.platform != "win32" or not spec:
            return ""
        try:
            import ctypes
            from ctypes import wintypes
            from PIL import Image
            shell32 = ctypes.windll.shell32
            user32  = ctypes.windll.user32
        except Exception:
            return ""

        SHGFI_ICON = 0x100
        SHGFI_USEFILEATTRIBUTES = 0x10
        SHGFI_SMALLICON = 0x1
        SHGFI_LARGEICON = 0x0
        FILE_ATTRIBUTE_NORMAL = 0x80

        class SHFILEINFO(ctypes.Structure):
            _fields_ = [
                ("hIcon",         wintypes.HICON),
                ("iIcon",         ctypes.c_int),
                ("dwAttributes",  wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260),
                ("szTypeName",    wintypes.WCHAR * 80),
            ]

        shfi = SHFILEINFO()
        flags = SHGFI_ICON | (SHGFI_SMALLICON if size <= 20 else SHGFI_LARGEICON)
        attr = 0
        if file_attributes:
            flags |= SHGFI_USEFILEATTRIBUTES
            attr = FILE_ATTRIBUTE_NORMAL
        if not shell32.SHGetFileInfoW(spec, attr, ctypes.byref(shfi), ctypes.sizeof(shfi), flags):
            return ""
        if not shfi.hIcon:
            return ""
        pil_img = None
        try:
            try:
                import win32gui, win32ui
                screen_dc_h = win32gui.GetDC(0)
                screen_dc   = win32ui.CreateDCFromHandle(screen_dc_h)
                hbmp = win32ui.CreateBitmap()
                hbmp.CreateCompatibleBitmap(screen_dc, size, size)
                mem_dc = screen_dc.CreateCompatibleDC()
                old    = mem_dc.SelectObject(hbmp)
                mem_dc.FillSolidRect((0, 0, size, size), 0xFFFFFF)
                win32gui.DrawIconEx(mem_dc.GetSafeHdc(), 0, 0, shfi.hIcon, size, size, 0, 0, 3)
                bmpinfo = hbmp.GetInfo()
                bmpstr  = hbmp.GetBitmapBits(True)
                pil_img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                                           bmpstr, "raw", "BGRX", 0, 1).convert("RGBA")
                px = list(pil_img.getdata())
                px = [(r, g, b, 0) if r >= 250 and g >= 250 and b >= 250 else (r, g, b, a)
                      for r, g, b, a in px]
                pil_img.putdata(px)
                mem_dc.SelectObject(old)
                mem_dc.DeleteDC()
                win32gui.ReleaseDC(0, screen_dc_h)
                win32gui.DeleteObject(hbmp.GetHandle())
            except Exception:
                pass
        finally:
            user32.DestroyIcon(shfi.hIcon)
        if pil_img is None:
            return ""
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def get_file_type_icon(self, ext: str, size: int = 18) -> dict:
        """Return a base64-encoded PNG of the real Windows shell icon for *ext*.

        Uses SHGetFileInfoW / SHGFI_USEFILEATTRIBUTES so no real file is needed.
        Returns {ok:true, data: "data:image/png;base64,..."} or {ok:false}.
        """
        import sys
        if sys.platform != "win32":
            return _err("not windows")
        if not ext:
            ext = ".file"
        elif not ext.startswith("."):
            ext = "." + ext
        data = self._shell_icon_data_url("x" + ext, size, file_attributes=True)
        if not data:
            return _err("no icon")
        return _ok(data=data, ext=ext, size=size)

    def get_file_type_icons_batch(self, exts: list, size: int = 18) -> dict:
        """Fetch Windows shell icons for multiple extensions in one call.

        Returns {ok:true, icons: {".pdf": "data:...", ".docx": "data:...", ...}}
        """
        icons = {}
        for ext in exts:
            r = self.get_file_type_icon(ext, size)
            if r.get("ok"):
                icons[ext if ext.startswith(".") else "." + ext] = r["data"]
        # Always include folder icon
        r_folder = self._get_folder_icon(size)
        if r_folder.get("ok"):
            icons["__folder__"] = r_folder["data"]
        return _ok(icons=icons)

    def _get_folder_icon(self, size: int = 18) -> dict:
        """Windows shell folder icon as base64 PNG."""
        import base64, io, sys
        if sys.platform != "win32":
            return _err("not windows")
        try:
            import ctypes
            from ctypes import wintypes
            from PIL import Image
        except ImportError as exc:
            return _err(str(exc))

        SHGFI_ICON              = 0x100
        SHGFI_USEFILEATTRIBUTES = 0x10
        SHGFI_SMALLICON         = 0x1
        FILE_ATTRIBUTE_DIRECTORY = 0x10

        class SHFILEINFO(ctypes.Structure):
            _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int),
                        ("dwAttributes", wintypes.DWORD),
                        ("szDisplayName", wintypes.WCHAR * 260),
                        ("szTypeName", wintypes.WCHAR * 80)]

        shfi  = SHFILEINFO()
        flags = SHGFI_ICON | SHGFI_USEFILEATTRIBUTES | (SHGFI_SMALLICON if size <= 20 else 0)
        try:
            shell32 = ctypes.windll.shell32
            user32  = ctypes.windll.user32
        except Exception as exc:
            return _err(str(exc))

        res = shell32.SHGetFileInfoW("C:\\folder", FILE_ATTRIBUTE_DIRECTORY,
                                     ctypes.byref(shfi), ctypes.sizeof(shfi), flags)
        if not res or not shfi.hIcon:
            return _err("no icon")

        try:
            import win32gui, win32ui
            screen_dc_h = win32gui.GetDC(0)
            screen_dc   = win32ui.CreateDCFromHandle(screen_dc_h)
            hbmp = win32ui.CreateBitmap()
            hbmp.CreateCompatibleBitmap(screen_dc, size, size)
            mem_dc = screen_dc.CreateCompatibleDC()
            old    = mem_dc.SelectObject(hbmp)
            mem_dc.FillSolidRect((0, 0, size, size), 0xFFFFFF)
            win32gui.DrawIconEx(mem_dc.GetSafeHdc(), 0, 0, shfi.hIcon, size, size, 0, 0, 3)
            bmpinfo = hbmp.GetInfo()
            bmpstr  = hbmp.GetBitmapBits(True)
            pil_img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                                       bmpstr, "raw", "BGRX", 0, 1).convert("RGBA")
            px = list(pil_img.getdata())
            px = [(r, g, b, 0) if r >= 250 and g >= 250 and b >= 250 else (r, g, b, a)
                  for r, g, b, a in px]
            pil_img.putdata(px)
            mem_dc.SelectObject(old); mem_dc.DeleteDC()
            win32gui.ReleaseDC(0, screen_dc_h)
            win32gui.DeleteObject(hbmp.GetHandle())
        except Exception:
            user32.DestroyIcon(shfi.hIcon)
            return _err("render failed")
        finally:
            user32.DestroyIcon(shfi.hIcon)

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return _ok(data=f"data:image/png;base64,{b64}", size=size)

    def browse_for_folder(self) -> dict:
        """Open a native folder-picker dialog via PyWebView and return the chosen path."""
        try:
            import webview
            win = self._window
            if win is None:
                return _err("Window not ready")
            result = win.create_file_dialog(
                webview.FOLDER_DIALOG,
                allow_multiple=False,
            )
            if result and len(result) > 0:
                return _ok(path=os.path.normpath(result[0]))
            return _ok(path=None, cancelled=True)
        except Exception as exc:
            return _err(str(exc))

    def browse_for_pdfs(self) -> dict:
        """Native open-file dialog (multi-select, PDF filter). Used by 'Add PDF…'."""
        try:
            import webview
            win = self._window
            if win is None:
                return _err("Window not ready")
            result = win.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=("PDF files (*.pdf)", "All files (*.*)"),
            )
            if result:
                return _ok(paths=[os.path.normpath(p) for p in result])
            return _ok(paths=[], cancelled=True)
        except Exception as exc:
            return _err(str(exc))

    def build_pdf_from_pages(self, pages: list, out_path: str, replace: bool = False) -> dict:
        """Build a PDF from an ordered list of {path, page, rotate} slots.

        Mirrors the old system's merge/arrange writer: pages may come from
        multiple source PDFs, per-slot rotation is applied to the OUTPUT only.
        With replace=True the result atomically replaces out_path (in-place
        arrange save, old '_save_arrange_inplace'); otherwise it is written as
        a new file (old '_ok_merge' / extract).

        Same engine as pdf_build (pdf_tools.build_pdf): a new file never
        overwrites an existing one (" (2)" is added), and replace=True first
        copies the old version into _to_review/.
        """
        return self.pdf_build(pages, out_path, bool(replace))

    def get_desktop(self) -> dict:
        return _ok(path=os.path.join(os.path.expanduser("~"), "Desktop"))

    def path_exists(self, path: str) -> dict:
        return _ok(exists=os.path.exists(path), is_dir=os.path.isdir(path))

    def get_file_info(self, path: str) -> dict:
        try:
            e = _file_entry(path)
            if e["ext"] == ".pdf":
                e["page_count"] = _fo.pdf_page_count(path)
                if e["page_count"] > 0:
                    e["pages"] = e["page_count"]
            else:
                try:
                    import doc_preview as _dp
                    e.update(_dp.quick_stats(path))     # pages / words / slides / sheets
                except Exception:
                    pass
            return _ok(**e)
        except Exception as exc:
            return _err(str(exc))

    def open_native(self, path: str) -> dict:
        try:
            # open_path_system_default returns (ok, err) — it never raises, so
            # a failure (e.g. a folder, a missing file) used to report success.
            _check(_fo.open_path_system_default(path), "Could not open the file")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def open_folder_in_explorer(self, path: str, select: bool = False) -> dict:
        """Open a folder in Explorer. With select=True, open the item's parent
        folder with the item highlighted ("Show in Explorer")."""
        try:
            import subprocess
            path = os.path.abspath(os.path.normpath(str(path or "")))
            if not os.path.exists(path):
                return _err(f"Not found: {path}")
            if select:
                subprocess.Popen(f'explorer /select,"{path}"')
                return _ok()
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            subprocess.Popen(["explorer", folder])
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def show_native_context_menu(self, path: str, x: int, y: int) -> dict:
        try:
            _fo.windows_shell_track_context_menu(path, x, y)
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def run_open_with(self, argv: list, path: str) -> dict:
        """Launch a file with the given argv (from get_open_with_commands)."""
        try:
            import ctypes
            from ctypes import wintypes
            def _bare(arg: str) -> str:
                s = str(arg).strip()
                while len(s) >= 2 and s[0] == s[-1] == '"':
                    s = s[1:-1]
                return s

            cmd = [_bare(a) for a in (argv or []) if _bare(a)]
            if not cmd:
                return _err("No application to launch")
            norm = os.path.normpath(path) if path else ""

            def _is_path(arg: str) -> bool:
                if not arg or not norm:
                    return False
                try:
                    return os.path.normcase(os.path.normpath(arg)) == os.path.normcase(norm)
                except OSError:
                    return False

            for i, arg in enumerate(cmd):
                if any(tok in arg for tok in ("%1", "%L", "%V", "%u", "%U", "%*")):
                    cmd[i] = arg.replace("%1", norm).replace("%L", norm).replace("%V", norm).replace("%u", norm).replace("%U", norm).replace("%*", norm)
            if norm and not any(_is_path(a) for a in cmd):
                cmd.append(norm)
            # Chrome and Edge's "--single-argument" form opens a broken tab for a
            # local path. The path itself, as its own argument, opens the file.
            cmd = [a for a in cmd if a != "--single-argument"]

            def _quote(arg: str) -> str:
                if any(c in arg for c in ' \t"'):
                    return '"' + arg.replace('"', '""') + '"'
                return arg

            # Packaged apps (Notepad, Paint) only start through the shell.
            # Desktop apps already running, such as Chrome, Edge and Cursor,
            # ignore a shell "open" and need the file on the real command line.
            if "\\windowsapps\\" in cmd[0].lower():
                params = " ".join(_quote(a) for a in cmd[1:]) or None
                shell32 = ctypes.windll.shell32
                shell32.ShellExecuteW.argtypes = [
                    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
                ]
                shell32.ShellExecuteW.restype = ctypes.c_void_p
                rc = shell32.ShellExecuteW(None, "open", cmd[0], params, None, 1)
                if not rc or int(rc) <= 32:
                    return _err(f"Could not start the application (code {int(rc or 0)})")
                return _ok()
            import subprocess
            subprocess.Popen(cmd, shell=False)
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def _run_open_with(self, argv: list, path: str) -> dict:
        """Back-compat alias. The window calls run_open_with; names starting with _ are hidden from JavaScript."""
        return self.run_open_with(argv, path)

    def get_open_with_commands(self, path: str) -> dict:
        """Apps registered to open this file.

        commands: [[label, argv], ...] (older callers).
        apps: [{name, argv, icon}] where icon is a PNG data-URL of the program.
        """
        try:
            cmds = _fo.windows_open_with_menu_commands(path)
            cache = getattr(self, "_exe_icon_cache", None)
            if cache is None:
                cache = self._exe_icon_cache = {}
            apps = []
            for label, argv in cmds:
                exe = str(argv[0]) if argv else ""
                key = exe.lower()
                icon = cache.get(key, "")
                if exe and key not in cache:
                    icon = self._shell_icon_data_url(exe, 32, file_attributes=False)
                    cache[key] = icon
                apps.append({"name": label, "argv": list(argv), "icon": icon or ""})
            return _ok(commands=cmds, apps=apps)
        except Exception as exc:
            return _err(str(exc))

    def open_with_picker(self, path: str) -> dict:
        """Windows 'Open with' dialog so the user can pick any installed app."""
        try:
            ok, err = _fo.open_with_application_picker(path)
            return _ok() if ok else _err(err or "Could not open the app list")
        except Exception as exc:
            return _err(str(exc))

    # ── file operations ───────────────────────────────────────────────────────

    def rename_file(self, old_path: str, new_name: str) -> dict:
        # NOTE: safe_rename(src, new_basename) wants the bare new NAME and
        # returns an (ok, result) tuple — it does not raise. The old code
        # passed a full path (always rejected) and ignored the tuple, so every
        # rename failed on disk while the UI reported success.
        try:
            ok, res = _fo.safe_rename(old_path, new_name)
            if not ok:
                return _err(res)
            new_path = old_path if res == "Unchanged." else res
            return _ok(new_path=new_path)
        except Exception as exc:
            return _err(str(exc))

    def soft_delete(self, paths: list) -> dict:
        results = []
        for p in paths:
            try:
                # soft_delete_path returns (ok, dst_path, error_msg)
                ok, dst, err = _fo.soft_delete_path(p)
                if ok:
                    results.append({"path": p, "moved_to": dst, "ok": True})
                else:
                    results.append({"path": p, "ok": False, "error": err or "Soft delete failed"})
            except Exception as exc:
                results.append({"path": p, "ok": False, "error": str(exc)})
        failed = [r for r in results if not r.get("ok")]
        if failed:
            msg = "; ".join(
                f'{os.path.basename(f["path"])}: {f.get("error", "failed")}' for f in failed)
            _log.error("soft_delete: %d/%d failed: %s", len(failed), len(results), msg)
            return {"ok": False, "error": msg, "results": results}
        return _ok(results=results)

    @staticmethod
    def _free_dest(dest_dir: str, name: str, is_dir: bool = False) -> str:
        """dest_dir/name, or "name (2).ext", "name (3).ext" … when taken."""
        dst = os.path.join(dest_dir, name)
        base, ext = os.path.splitext(name)
        if is_dir:
            base, ext = name, ""          # folders: "My.Folder (2)", not "My (2).Folder"
        i = 2
        while os.path.exists(dst):
            dst = os.path.join(dest_dir, f"{base} ({i}){ext}")
            i += 1
        return dst

    @staticmethod
    def _inside(child: str, parent: str) -> bool:
        """True when *child* is *parent* or lies somewhere below it."""
        c = os.path.normcase(os.path.abspath(child)).rstrip("\\/")
        p = os.path.normcase(os.path.abspath(parent)).rstrip("\\/")
        return c == p or c.startswith(p + os.sep)

    @staticmethod
    def _file_op_result(results: list, verb: str) -> dict:
        """ok:false (with a readable error) when any item failed — the UI used
        to report "Pasted" / "Moved" even when nothing happened."""
        failed = [r for r in results if not r.get("ok")]
        if failed:
            msg = "; ".join(f'{os.path.basename(str(r["src"]).rstrip(chr(92) + "/"))}: {r.get("error", "failed")}'
                            for r in failed)
            done = len(results) - len(failed)
            return {"ok": False, "error": msg, "results": results,
                    "done": done, "failed": len(failed),
                    "partial": done > 0, "verb": verb}
        return _ok(results=results, done=len(results), failed=0)

    def copy_files(self, src_paths: list, dest_dir: str) -> dict:
        import shutil
        results = []
        if not dest_dir or not os.path.isdir(str(dest_dir)):
            return _err(f"Destination folder not found: {dest_dir}")
        for src in src_paths or []:
            try:
                src = os.path.abspath(str(src))
                if not os.path.exists(src):
                    raise RuntimeError("not found")
                if os.path.isdir(src) and self._inside(dest_dir, src):
                    # copytree into its own subfolder recurses until the path is too long
                    raise RuntimeError("a folder can't be copied into itself")
                dst = self._free_dest(dest_dir, os.path.basename(src), os.path.isdir(src))
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                results.append({"src": src, "dst": dst, "ok": True})
            except Exception as exc:
                results.append({"src": src, "ok": False, "error": str(exc)})
        return self._file_op_result(results, "copy")

    def move_files(self, src_paths: list, dest_dir: str) -> dict:
        """Move items into dest_dir. An existing item is never replaced and a
        folder is never merged into a same-named one: the moved item gets a
        free "name (2)" instead. Moving into the folder it is already in is a
        no-op."""
        import shutil
        results = []
        if not dest_dir or not os.path.isdir(str(dest_dir)):
            return _err(f"Destination folder not found: {dest_dir}")
        for src in src_paths or []:
            try:
                src = os.path.abspath(str(src))
                if not os.path.exists(src):
                    raise RuntimeError("not found")
                if os.path.isdir(src) and self._inside(dest_dir, src):
                    raise RuntimeError("a folder can't be moved into itself")
                if os.path.normcase(os.path.dirname(src)) == os.path.normcase(os.path.abspath(dest_dir)):
                    results.append({"src": src, "dst": src, "ok": True, "unchanged": True})
                    continue
                dst = self._free_dest(dest_dir, os.path.basename(src), os.path.isdir(src))
                try:
                    _fo.release_pdf_handles_for_paths((src,))
                except Exception:
                    pass
                shutil.move(src, dst)
                results.append({"src": src, "dst": dst, "ok": True})
            except Exception as exc:
                results.append({"src": src, "ok": False, "error": str(exc)})
        return self._file_op_result(results, "move")

    def restore_soft_deleted(self, items: list) -> dict:
        """Undo "Move to review": move each {path, moved_to} back to *path*.
        Never overwrites — an item whose original name is taken again is
        restored as "name (2)"."""
        import shutil
        results = []
        for it in items or []:
            orig = str((it or {}).get("path") or "")
            moved = str((it or {}).get("moved_to") or "")
            try:
                if not orig or not moved or not os.path.exists(moved):
                    raise RuntimeError("the item is no longer in _to_review")
                parent = os.path.dirname(os.path.abspath(orig))
                if not os.path.isdir(parent):
                    raise RuntimeError("its folder no longer exists")
                dst = orig if not os.path.exists(orig) else self._free_dest(parent, os.path.basename(orig), os.path.isdir(moved))
                shutil.move(moved, dst)
                results.append({"src": moved, "dst": dst, "ok": True})
            except Exception as exc:
                results.append({"src": moved or orig, "ok": False, "error": str(exc)})
        return self._file_op_result(results, "restore")

    def create_folder(self, parent: str, name: str) -> dict:
        raw = str(name or "")
        cleaned = raw.strip()
        if not cleaned or cleaned in (".", ".."):
            return _err("Enter a folder name.")
        if raw != cleaned or cleaned.endswith("."):
            return _err("A folder name can't end with a dot or a space.")
        if any(c in cleaned for c in '\\/:*?"<>|'):
            return _err("A folder name can't contain any of these characters: \\ / : * ? \" < > |")
        problem = _fo.folder_name_problem(cleaned)   # reserved names (CON, LPT1 …), length
        if problem:
            return _err(problem + ".")
        if not parent or not os.path.isdir(str(parent)):
            return _err("That folder does not exist.")
        new_path = os.path.join(parent, cleaned)
        # Windows names are case-insensitive: "new FOLDER" clashes with "New folder".
        try:
            taken = {n.casefold() for n in os.listdir(parent)}
        except OSError:
            taken = set()
        if os.path.exists(new_path) or cleaned.casefold() in taken:
            return _err("A file or folder with this name already exists here.")
        try:
            os.mkdir(new_path)          # never merges into a folder that appeared meanwhile
            return _ok(path=new_path)
        except Exception as exc:
            return _err(str(exc))

    @staticmethod
    def _folder_list_opts(opts) -> dict:
        o = opts if isinstance(opts, dict) else {}
        return {"prefix": str(o.get("prefix") or ""), "suffix": str(o.get("suffix") or ""),
                "numbering": str(o.get("numbering") or "none"),
                "start": o.get("start", 1), "pad": o.get("pad", 0),
                "sep": str(o.get("sep") if o.get("sep") is not None else " ")}

    def plan_folders_from_list(self, parent: str, text: str, opts: dict = None) -> dict:
        """Preview for "New folders from a list" (nothing is created):
        rows with status ok / invalid / exists / duplicate + counts."""
        try:
            return _ok(**_fo.plan_folders_from_list(parent, text, **self._folder_list_opts(opts)))
        except Exception as exc:
            return _err(str(exc))

    def create_folders_from_list(self, parent: str, text: str, opts: dict = None) -> dict:
        """Create all valid names in *parent* at once; problem rows are skipped,
        existing folders are never overwritten or merged."""
        try:
            r = _fo.create_folders_from_list(parent, text, **self._folder_list_opts(opts))
            return _ok(**r)
        except Exception as exc:
            return _err(str(exc))

    def list_subfolders(self, path: str) -> dict:
        """Subfolders of *path* for the Copy / Move folder tree (lazy-loaded)."""
        try:
            return _ok(path=path, folders=_fo.list_subfolders(path))
        except Exception as exc:
            return _err(str(exc), path=path, folders=[])

    def find_folders(self, root: str, query: str, limit: int = 200, prefer: str = "") -> dict:
        """Folders under *root* whose name contains every word of *query*
        (the *prefer* folder's subtree is searched first)."""
        try:
            return _ok(**_fo.find_folders(root, query, limit=int(limit or 200), prefer=str(prefer or "")))
        except Exception as exc:
            return _err(str(exc), results=[])

    def set_clipboard(self, text: str) -> dict:
        """Put Unicode text on the Windows clipboard (Korean etc. safe)."""
        text = str(text or "")
        try:
            import win32clipboard, win32con
            for _ in range(5):                      # another app may hold the clipboard briefly
                try:
                    win32clipboard.OpenClipboard()
                    break
                except Exception:
                    import time; time.sleep(0.05)
            else:
                return _err("The clipboard is busy — try again")
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
            return _ok(chars=len(text))
        except ImportError:
            try:   # fallback without pywin32: clip.exe reads UTF-16LE with a BOM correctly
                import subprocess
                subprocess.run(["clip"], input=b"\xff\xfe" + text.encode("utf-16-le"), check=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return _ok(chars=len(text))
            except Exception as exc:
                return _err(str(exc))
        except Exception as exc:
            return _err(str(exc))

    def set_clipboard_files(self, paths: list, cut: bool = False) -> dict:
        """Put files on the Windows clipboard (like Ctrl+C / Ctrl+X in Explorer) so
        they can be pasted into Explorer, WhatsApp, Outlook… Nothing is moved here:
        a 'cut' only marks them; the app that pastes decides."""
        try:
            import struct
            import win32clipboard, win32con
            files = [os.path.abspath(str(p)) for p in (paths or []) if p and os.path.exists(str(p))]
            if not files:
                return _err("Nothing to copy")
            body = ("\0".join(files) + "\0\0").encode("utf-16-le")
            dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1) + body   # DROPFILES: offset, pt, fNC, fWide
            effect_fmt = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")
            for _ in range(5):
                try:
                    win32clipboard.OpenClipboard()
                    break
                except Exception:
                    import time; time.sleep(0.05)
            else:
                return _err("The clipboard is busy — try again")
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, dropfiles)
                win32clipboard.SetClipboardData(effect_fmt, struct.pack("<I", 2 if cut else 1))
                # plain-text fallback for apps that only accept text
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, "\r\n".join(files))
            finally:
                win32clipboard.CloseClipboard()
            return _ok(count=len(files), cut=bool(cut))
        except Exception as exc:
            return _err(str(exc))

    def get_clipboard_files(self) -> dict:
        """Files currently on the Windows clipboard (copied/cut in Explorer or here)."""
        try:
            import struct
            import win32clipboard, win32con
            for _ in range(5):
                try:
                    win32clipboard.OpenClipboard()
                    break
                except Exception:
                    import time; time.sleep(0.05)
            else:
                return _ok(paths=[], cut=False)
            try:
                paths, cut = [], False
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                    paths = [str(p) for p in win32clipboard.GetClipboardData(win32con.CF_HDROP)]
                    fmt = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")
                    if win32clipboard.IsClipboardFormatAvailable(fmt):
                        raw = win32clipboard.GetClipboardData(fmt)
                        if isinstance(raw, (bytes, bytearray)) and len(raw) >= 4:
                            cut = bool(struct.unpack("<I", bytes(raw[:4]))[0] & 2)
            finally:
                win32clipboard.CloseClipboard()
            return _ok(paths=[p for p in paths if os.path.exists(p)], cut=cut)
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # PDF THUMBNAILS & PREVIEW
    # =========================================================================

    def get_pdf_thumbnail(self, path: str, page: int = 0, dpi: int = 72) -> dict:
        try:
            max_dim = int(dpi * 8.27)
            pil = _fo.pil_image_for_pdf_page(path, page, max_dim, max_dim)
            if pil is None:
                return _err("Could not render page")
            return _ok(data_url=_b64_jpg(pil, quality=80))
        except Exception as exc:
            return _err(str(exc))

    def get_pdf_page_count(self, path: str) -> dict:
        _log.info("get_pdf_page_count: %s", path)
        try:
            if not os.path.isfile(path):
                _log.error("get_pdf_page_count: file not found: %s", path)
                return _err(f"File not found: {os.path.basename(path)}")
            n = _fo.pdf_page_count(path)
            if n <= 0:
                # pdf_page_count returns 0 for unreadable/corrupt PDFs (or a
                # missing PDF engine) — surface that as an error, not "0 pages".
                _log.error("get_pdf_page_count: unreadable PDF: %s", path)
                return _err(f"Could not read PDF: {os.path.basename(path)}")
            _log.info("get_pdf_page_count: %d pages", n)
            return _ok(count=n)
        except Exception as exc:
            _log.error("get_pdf_page_count error: %s", exc, exc_info=True)
            return _err(str(exc))

    def get_pdf_page_as_png(self, path: str, page: int, dpi: int = 150) -> dict:
        """Render a PDF page for the preview panel.

        The preview used to cap a portrait page to a square of A4's width and
        then stretch that bitmap, which made the type look soft next to Acrobat.
        The requested dpi is kept on both axes.
        """
        _log.info("get_pdf_page_as_png: %s page=%d dpi=%d", path, page, dpi)
        try:
            dpi = max(72, min(int(dpi or 150), 320))
            # Long side: tabloid (17in) at this dpi, so height does not
            # shrink the zoom the way an A4-width square did.
            cap = int(dpi * 17)
            pil = _fo.pil_image_for_pdf_page(path, page, cap, cap, max_zoom=dpi / 72.0)
            if pil is None:
                _log.error("get_pdf_page_as_png: returned None")
                return _err("Could not render page")
            _log.info("get_pdf_page_as_png: rendered %dx%d", pil.width, pil.height)
            return _ok(data_url=_b64_png(pil), width=pil.width, height=pil.height)
        except Exception as exc:
            _log.error("get_pdf_page_as_png error: %s", exc, exc_info=True)
            return _err(str(exc))

    def get_image_preview(self, path: str, max_dim: int = 1200) -> dict:
        """Load an image file and return as base64 for preview."""
        try:
            if not os.path.isfile(path):
                return _err(f"File not found: {os.path.basename(path)}")
            pil = _fo.pil_image_load_full_rgb(path)
            if pil is None:
                return _err("Could not read this image — the file may be damaged "
                            "or not a supported image format")
            if max(pil.width, pil.height) > max_dim:
                ratio = max_dim / max(pil.width, pil.height)
                pil = pil.resize((int(pil.width * ratio), int(pil.height * ratio)))
            return _ok(data_url=_b64_png(pil), width=pil.width, height=pil.height)
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # TEXT PREVIEW
    # =========================================================================

    def get_text_preview(self, path: str) -> dict:
        try:
            text = _fo.preview_plain_text(path)
            return _ok(text=text)
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # PDF OPERATIONS
    # =========================================================================

    # Old entry points, kept for any leftover caller. They use the pdf_tools
    # engine, so nothing is ever overwritten (the old writers replaced an
    # existing output file).
    def merge_pdfs(self, paths: list, out_path: str) -> dict:
        return self.pdf_merge(list(paths or []), out_path or "", False)

    def split_pdf_pages(self, path: str, out_dir: str) -> dict:
        return self.pdf_split(path, "each", 1, "", out_dir or "")

    def compress_pdf(self, path: str, out_path: str = "", preset: str = "ebook",
                     target_kb: float = 0, save_smallest: bool = False,
                     _progress=None) -> dict:
        """Compress a PDF (see media_convert.compress_pdf). Never overwrites;
        when the result would not be smaller no file is written and
        kept_original is True.

        target_kb > 0 searches for the best quality at or under that size
        (preset is ignored). If the target cannot be reached nothing is
        written (target_met False, ``smallest`` = best possible size) unless
        save_smallest is True."""
        try:
            r = _mc.compress_pdf(path, preset or "ebook", out_path or "",
                                 target_kb=float(target_kb or 0),
                                 save_smallest=bool(save_smallest),
                                 progress=_progress)
            return _ok(**r, path=path, out_path=r["out"], before_bytes=r["before"],
                       after_bytes=r["after"], saved_bytes=r["saved"],
                       saved_str=_fmt_size(r["saved"]),
                       before_str=_fmt_size(r["before"]),
                       after_str=_fmt_size(r["after"]))
        except Exception as exc:
            _log.error("compress_pdf failed: %s", exc)
            return _err(str(exc), path=path)

    def compress_pdf_quality(self, path: str, quality: str = "ebook",
                             out_path: str = "", target_kb: float = 0,
                             save_smallest: bool = False) -> dict:
        """Compress a PDF with a preset: screen / ebook / printer / lossless
        (aliases low/medium/high/prepress accepted), or to a target size."""
        return self.compress_pdf(path, out_path, quality, target_kb, save_smallest)

    def compress_preview(self, paths: list, settings: dict = None, job: str = "") -> dict:
        """Predict compressed sizes in a background thread; nothing is written.

        settings: {"channel": "pdf"|"img"|..., "pdf_keys": ["screen", "level-7", ...],
                   "img_quality": 70, "img_edge": 0, "img_fmt": "", "smallest": bool}
        Emits ``compress_preview`` {job, path, ok, kind, before, sizes|after,
        smallest, estimate} per file, then {job, done: True}. A newer job on the
        same channel cancels this one (it stops between files / presets)."""
        o = dict(settings or {})
        channel = str(o.get("channel") or "default")
        paths = list(paths or [])
        with self._lock:
            gens = getattr(self, "_preview_gens", None)
            if gens is None:
                gens = self._preview_gens = {}
            gen = gens[channel] = gens.get(channel, 0) + 1

        def stale() -> bool:
            return self._preview_gens.get(channel) != gen

        def _run():
            for p in paths:
                if stale():
                    self._emit("compress_preview", {"job": job, "done": True, "cancelled": True})
                    return
                try:
                    if _mc.is_image(p):
                        r = _mc.preview_image(p, int(o.get("img_quality") or 70),
                                              int(o.get("img_edge") or 0),
                                              o.get("img_fmt") or "", bool(o.get("smallest")))
                        r["kind"] = "image"
                    else:
                        r = _mc.preview_pdf(p, o.get("pdf_keys") or [],
                                            bool(o.get("smallest")), cancelled=stale)
                        r["kind"] = "pdf"
                    payload = _ok(job=job, path=p, **r)
                except Exception as exc:
                    payload = _err(str(exc), job=job, path=p)
                if stale():
                    self._emit("compress_preview", {"job": job, "done": True, "cancelled": True})
                    return
                self._emit("compress_preview", payload)
            self._emit("compress_preview", {"job": job, "done": True})
        self._thread(_run)
        return _ok(started=True, job=job)

    def file_sizes(self, paths: list) -> dict:
        """{path: size in bytes} for existing files (missing ones are left out)."""
        sizes = {}
        for p in list(paths or []):
            try:
                if os.path.isfile(p):
                    sizes[p] = os.path.getsize(p)
            except OSError:
                pass
        return _ok(sizes=sizes)

    def rotate_pdf_page(self, path: str, page: int, degrees: int) -> dict:
        try:
            # pdf_rotate_page takes clockwise: bool — passing degrees directly
            # meant -90 (truthy) also rotated clockwise.
            _check(_fo.pdf_rotate_page(path, page, degrees >= 0), "Rotate failed")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def delete_pdf_page(self, path: str, page: int) -> dict:
        """Delete one page from the PDF itself. The previous version is first
        copied into _to_review/ next to it (nothing is ever lost)."""
        try:
            import pdf_tools as _pt
            backup = _pt.backup_to_review(path)
            _check(_fo.pdf_delete_page(path, page), "Delete page failed")
            return _ok(backup=backup)
        except Exception as exc:
            return _err(str(exc))

    def reorder_pdf_pages(self, path: str, new_order: list, out_path: str) -> dict:
        """Old entry point: pages of *path* in *new_order* (0-based) → a NEW
        file (out_path, or '<name>_arranged.pdf'); the original is kept."""
        _log.info("reorder_pdf_pages: %s order=%s out=%s", path, new_order, out_path)
        out = out_path or ""
        if not out or os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(path)):
            stem = os.path.splitext(os.path.abspath(path))[0]
            out = stem + "_arranged.pdf"
        pages = [{"path": path, "page": int(p), "rotate": 0} for p in (new_order or [])]
        return self.pdf_build(pages, out, False)

    def extract_pdf_pages(self, path: str, page_spec: str, out_path: str) -> dict:
        """Old entry point → pdf_extract (new file, never overwrites)."""
        return self.pdf_extract(path, page_spec, out_path or "")

    def pdf_to_images(self, path: str, dpi: int = 150, fmt: str = "png",
                      pages: str = "", quality: int = 90) -> dict:
        """Render PDF pages (all, or a range like "1-3,5") into a new
        <name>_images folder. Never overwrites."""
        try:
            r = _mc.pdf_to_images(path, fmt or "png", int(dpi or 150),
                                  pages or "", "", int(quality or 90))
            return _ok(**r)
        except Exception as exc:
            _log.error("pdf_to_images error: %s", exc, exc_info=True)
            return _err(str(exc))

    def pdf_to_images_async(self, path: str, opts: dict = None, job: str = "") -> dict:
        """Like pdf_to_images, in a thread. Emits media_progress / media_done
        events tagged with ``job``."""
        o = dict(opts or {})

        def _run():
            try:
                r = _mc.pdf_to_images(
                    path, o.get("fmt") or "png", int(o.get("dpi") or 150),
                    o.get("pages") or "", "", int(o.get("quality") or 90),
                    progress=self._media_progress(job))
                self._emit("media_done", _ok(job=job, **r))
            except Exception as exc:
                _log.error("pdf_to_images_async error: %s", exc)
                self._emit("media_done", _err(str(exc), job=job))
        self._thread(_run)
        return _ok(started=True, job=job)

    def _media_progress(self, job: str):
        last = [0.0]

        def _cb(done: int, total: int, label: str = "") -> None:
            now = time.time()
            if done < total and now - last[0] < 0.15:   # throttle UI updates
                return
            last[0] = now
            self._emit("media_progress", {"job": job, "done": done,
                                          "total": total, "label": label})
        return _cb

    def media_capabilities(self) -> dict:
        """Formats / presets the conversion + compression tools support."""
        try:
            return _ok(**_mc.capabilities())
        except Exception as exc:
            return _err(str(exc))

    @staticmethod
    def _require_image(path: str) -> None:
        """Convert-to-PDF is image -> PDF only (Office conversion was removed)."""
        ext = os.path.splitext(path or "")[1].lower()
        if ext not in _mc.IMAGE_EXT:
            raise RuntimeError(
                f"Only images can be converted to PDF (got '{ext or 'no extension'}')")

    @staticmethod
    def _img_pdf_opts(opts) -> dict:
        o = dict(opts or {})
        return {
            "page_size":   o.get("page_size") or "fit",
            "orientation": o.get("orientation") or "auto",
            "margin_mm":   float(o.get("margin_mm") or 0),
            "quality":     int(o.get("quality") or 85),
        }

    def convert_to_pdf(self, path: str, out_path: str = "", opts: dict = None) -> dict:
        """Convert one image to PDF beside the original (<name>.pdf, or
        <name>-2.pdf … when taken). Never overwrites."""
        try:
            self._require_image(path)
            r = _mc.images_to_pdf([path], out_path or "", **self._img_pdf_opts(opts))
            return _ok(out_path=r["out"], **r)
        except Exception as exc:
            return _err(str(exc))

    def images_to_pdf(self, paths: list, out_path: str = "", opts: dict = None) -> dict:
        """Several images (in the given order, or by name with
        opts.order="name") → one PDF. Never overwrites."""
        try:
            o = dict(opts or {})
            r = _mc.images_to_pdf(list(paths or []), out_path or "",
                                  order=o.get("order") or "selection",
                                  **self._img_pdf_opts(o))
            return _ok(out_path=r["out"], **r)
        except Exception as exc:
            return _err(str(exc))

    def images_to_pdf_async(self, paths: list, opts: dict = None, job: str = "") -> dict:
        """Threaded image → PDF. opts.mode = "combine" (one PDF, default) or
        "separate" (one PDF per image). Emits media_progress / media_done."""
        o = dict(opts or {})
        paths = list(paths or [])

        def _run():
            try:
                prog = self._media_progress(job)
                if o.get("mode") == "separate":
                    if o.get("order") == "name":
                        paths.sort(key=_mc._natural_key)
                    results = []
                    for i, p in enumerate(paths):
                        prog(i, len(paths), os.path.basename(p))
                        results.append(self.convert_to_pdf(p, "", o))
                        results[-1]["path"] = p
                    prog(len(paths), len(paths), "Done")
                    good = [r for r in results if r.get("ok")]
                    self._emit("media_done", {
                        "ok": bool(good), "job": job, "mode": "separate",
                        "results": results,
                        "files": [r["out"] for r in good],
                        "error": "" if good else (results[0].get("error") if results else "Nothing to convert"),
                    })
                else:
                    r = _mc.images_to_pdf(paths, o.get("out_path") or "",
                                          order=o.get("order") or "selection",
                                          progress=prog, **self._img_pdf_opts(o))
                    self._emit("media_done", _ok(job=job, mode="combine",
                                                 out_path=r["out"], **r))
            except Exception as exc:
                _log.error("images_to_pdf_async error: %s", exc)
                self._emit("media_done", _err(str(exc), job=job))
        self._thread(_run)
        return _ok(started=True, job=job)

    def combine_files_to_pdf(self, paths: list, out_path: str) -> dict:
        try:
            _check(_fo.combine_files_to_pdf(paths, out_path, _noop_log),
                   "Combine failed")
            return _ok(out_path=out_path)
        except Exception as exc:
            return _err(str(exc))

    def crop_image(self, path: str, x: int, y: int, w: int, h: int, out_path: str = "",
                   rotate: int = 0, out_w: int = 0, out_h: int = 0, dpi: int = 0,
                   max_kb: int = 0) -> dict:
        """Crop at full resolution (EXIF-upright, then *rotate*° clockwise) into a
        NEW file <name>_cropped<ext>; (x, y, w, h) are full-size pixels."""
        try:
            import image_crop
            ok, result = image_crop.crop_image(path, int(x), int(y), int(w), int(h),
                                               int(rotate or 0), out_path or "",
                                               int(out_w or 0), int(out_h or 0),
                                               int(dpi or 0), int(max_kb or 0))
            return _ok(out=result) if ok else _err(result)
        except Exception as exc:
            return _err(str(exc))

    def get_crop_source(self, path: str, max_dim: int = 1600) -> dict:
        """Scaled preview + full (EXIF-upright) size for the Crop dialog."""
        try:
            import image_crop
            return _ok(**image_crop.crop_source(path, int(max_dim or 1600)))
        except Exception as exc:
            return _err(str(exc))

    # ── Image compression / format conversion ─────────────────────────────

    def compress_image(self, path: str, quality: int = 70, max_edge: int = 0,
                       fmt: str = "", out_path: str = "", target_kb: float = 0,
                       save_smallest: bool = True) -> dict:
        """Compress one image to <name>_compressed.<ext>. target_kb > 0 finds
        the best quality under that size. If plain re-encoding would not make
        it smaller, nothing is written and kept_original is True. With a
        target that cannot be reached, save_smallest=False writes nothing
        (target_met False, ``smallest`` = best possible size)."""
        try:
            res = _mc.compress_image(path, int(quality or 70), int(max_edge or 0),
                                     fmt or "", out_path or "",
                                     int(float(target_kb or 0) * 1024),
                                     bool(save_smallest))
            before, after = res["before"], res["after"]
            pct = round((before - after) * 100.0 / before, 1) if before else 0.0
            return _ok(**res, saved=before - after, reduction=pct, path=path)
        except Exception as exc:
            return _err(str(exc), path=path)

    def compress_images(self, paths: list, quality: int = 70, max_edge: int = 0,
                        fmt: str = "", target_kb: int = 0) -> dict:
        try:
            if isinstance(paths, str):
                paths = [paths]
            results, before, after = [], 0, 0
            for p in list(paths or []):
                r = self.compress_image(p, quality, max_edge, fmt, "", target_kb)
                if r.get("ok"):
                    before += r["before"]
                    after += r["after"]
                results.append(r)
            saved = before - after
            pct = round(saved * 100.0 / before, 1) if before else 0.0
            failed = sum(1 for x in results if not x.get("ok"))
            kept = sum(1 for x in results if x.get("kept_original"))
            return _ok(results=results, before=before, after=after,
                       saved=saved, reduction=pct, failed=failed, kept=kept)
        except Exception as exc:
            return _err(str(exc))

    def convert_image(self, path: str, fmt: str, out_path: str = "",
                      quality: int = 92) -> dict:
        """Convert an image to jpg/png/webp/bmp/tiff beside the original
        (never overwrites; transparency → white for JPG/BMP)."""
        try:
            r = _mc.convert_image(path, fmt, int(quality or 92), out_path or "")
            return _ok(**r, path=path)
        except Exception as exc:
            return _err(str(exc), path=path)

    # ── long ops (threaded, push events) ─────────────────────────────────────

    def compress_pdf_async(self, path: str, out_path: str = "", preset: str = "ebook",
                           target_kb: float = 0, job: str = "",
                           save_smallest: bool = False) -> dict:
        """Threaded compress_pdf. Without ``job`` the result is emitted as
        compress_done (legacy). With ``job`` every target-size attempt is
        emitted as media_progress and the result as media_done, both tagged
        with ``job``."""
        def _run():
            event = "media_done" if job else "compress_done"
            try:
                prog = self._media_progress(job) if job else None
                r = self.compress_pdf(path, out_path, preset, target_kb,
                                      save_smallest, _progress=prog)
                if job:
                    r["job"] = job
                self._emit(event, r)
            except Exception as exc:
                self._emit(event, _err(str(exc), job=job) if job else _err(str(exc)))
        self._thread(_run)
        return _ok(started=True, job=job)

    def convert_to_pdf_async(self, paths: list) -> dict:
        def _run():
            results = []
            for p in paths:
                r = self.convert_to_pdf(p)
                results.append({"path": p, "ok": r["ok"], "out": r.get("out_path", ""),
                                "error": r.get("error", "")})
                self._emit("convert_pdf_progress", {"results": results})
            self._emit("convert_pdf_done", {"ok": any(x["ok"] for x in results),
                                            "results": results})
        self._thread(_run)
        return _ok(started=True)

    # =========================================================================
    # QR
    # =========================================================================

    # File decoding lives in qr_scan.py; the on-screen picker is qr_pick.py,
    # run as a helper process (Tk must not run inside the pywebview process).

    def qr_pick_start(self) -> dict:
        """Show the native "click a QR code" overlay over the whole desktop.

        Runs the qr_pick helper in the background and emits ``qr_pick_result``
        {ok, text, url, is_url, opened, rect, mode, host, domain, ...} or
        {ok:false, reason:'cancelled'|'not_found'|'unavailable'|'error', detail}.
        http(s) links are opened in the default browser right away (as the old
        app did); nothing else is ever opened.
        """
        if not hasattr(self, "_qr_pick_lock"):
            self._qr_pick_lock = threading.Lock()
            self._qr_pick_busy = False
        with self._qr_pick_lock:
            if self._qr_pick_busy:
                return _err("The QR picker is already open", busy=True)
            self._qr_pick_busy = True

        def _run():
            try:
                import qr_pick
                try:
                    res = qr_pick.finalize(qr_pick.run_helper())
                except Exception as exc:
                    _log.exception("qr_pick failed")
                    res = {"ok": False, "reason": "error", "detail": str(exc)}
                with self._qr_pick_lock:
                    self._qr_pick_busy = False
                self._emit("qr_pick_result", res)
            finally:
                with self._qr_pick_lock:
                    self._qr_pick_busy = False
        try:
            self._thread(_run)
        except Exception as exc:
            with self._qr_pick_lock:
                self._qr_pick_busy = False
            return _err(str(exc))
        return _ok(started=True)

    def scan_qr_from_file(self, path: str) -> dict:
        """Decode every QR code in one image or PDF (all pages) in the background.

        Emits ``qr_result`` {ok, path, results:[...], page_count, pages_scanned,
        url, text, page} — url/text/page describe the first code (older callers).
        """
        def _run():
            try:
                import qr_scan
                res = qr_scan.scan_file(path)
                if not res["ok"]:
                    self._emit("qr_result", {"ok": False, "path": path, "error": res.get("error")})
                    return
                if not res["results"]:
                    where = (f"{res['pages_scanned']} PDF page(s)" if res["kind"] == "pdf"
                             else "the image")
                    self._emit("qr_result", {"ok": False, "path": path, "results": [],
                                             "error": f"No QR code found in {where}"})
                    return
                first = res["results"][0]
                self._emit("qr_result", dict(res, url=first["text"], text=first["text"],
                                             page=first.get("page")))
            except Exception as exc:
                self._emit("qr_result", {"ok": False, "path": path, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def scan_qr_files(self, paths: list, job_id: str = "") -> dict:
        """Batch QR check of images / PDFs (one results table in the UI).

        Returns {ok, started, job_id, total} immediately, then emits
        ``qr_scan_progress`` {job_id, index, total, path, name, file} after each
        file and ``qr_scan_done`` {job_id, ok, files:[...], cancelled}.
        """
        import qr_scan
        paths = [p for p in (paths or []) if isinstance(p, str) and p]
        if not paths:
            return _err("No files selected")
        if not qr_scan.decoder_available():
            return _err(qr_scan.decoder_error())
        job_id = str(job_id or f"qr{int(time.time() * 1000)}")
        if not hasattr(self, "_qr_cancel"):
            self._qr_cancel = set()
        self._qr_cancel.discard(job_id)

        def _run():
            files = []
            cancelled = False
            try:
                for i, p in enumerate(paths):
                    if job_id in self._qr_cancel:
                        cancelled = True
                        break
                    res = qr_scan.scan_file(p, cancelled=lambda: job_id in self._qr_cancel)
                    files.append(res)
                    self._emit("qr_scan_progress", {"job_id": job_id, "index": i + 1,
                                                    "total": len(paths), "path": p,
                                                    "name": os.path.basename(p), "file": res})
                self._emit("qr_scan_done", {"job_id": job_id, "ok": True, "files": files,
                                            "cancelled": cancelled})
            except Exception as exc:
                _log.exception("scan_qr_files failed")
                self._emit("qr_scan_done", {"job_id": job_id, "ok": False, "files": files,
                                            "error": str(exc)})
            finally:
                self._qr_cancel.discard(job_id)
        self._thread(_run)
        return _ok(started=True, job_id=job_id, total=len(paths))

    def qr_scan_cancel(self, job_id: str) -> dict:
        if not hasattr(self, "_qr_cancel"):
            self._qr_cancel = set()
        self._qr_cancel.add(str(job_id))
        return _ok()

    def qr_open_url(self, url: str) -> dict:
        """Open a decoded http(s) link in the default browser (user clicked 'Open')."""
        try:
            import qr_scan
            url = (url or "").strip()
            if not qr_scan.is_safe_to_open(url):
                return _err("Only http:// and https:// links can be opened")
            import webbrowser
            webbrowser.open_new_tab(url)
            return _ok(url=url)
        except Exception as exc:
            return _err(str(exc))

    def qr_classify(self, text: str) -> dict:
        """Type + warnings for a QR payload (URL checks etc.)."""
        try:
            import qr_scan
            return _ok(**qr_scan.classify_payload(text or ""))
        except Exception as exc:
            return _err(str(exc))
    # =========================================================================
    # DOCUMENT RENAME TEMPLATES
    # =========================================================================

    # Logic lives in rename_templates.py (language registry, user file,
    # scoring); the old tkinter module is no longer imported here.

    def _rename_lang(self, lang: str = "") -> str:
        import rename_templates as rt
        return rt.normalize_lang(lang or self._load_settings().get("rename_lang") or rt.DEFAULT_LANG)

    def get_rename_lang(self) -> dict:
        try:
            import rename_templates as rt
            return _ok(lang=self._rename_lang(), languages=rt.languages())
        except Exception as exc:
            return _err(str(exc))

    def set_rename_lang(self, lang: str) -> dict:
        try:
            import rename_templates as rt
            code = str(lang or "").strip().lower()
            if code not in rt.LANGUAGES:
                return _err(f"Unknown template language: {lang}")
            self._save_settings({"rename_lang": code})
            return _ok(lang=code)
        except Exception as exc:
            return _err(str(exc))

    def get_rename_templates(self, lang: str = "") -> dict:
        try:
            import rename_templates as rt
            code = self._rename_lang(lang)
            st = rt.store()
            rows = [{"en": r.en, "local": r.local, "builtin": r.builtin,
                     "source": r.source, "stem": rt.stem_for(r.en, r.local, code),
                     "label": rt.label_for(r.en, r.local, code)}
                    for r in st.rows(code)]
            return _ok(lang=code, languages=rt.languages(), rows=rows,
                       user_file=st.user_file_path(code),
                       pairs=[{"key": r["en"], "label": r["local"]} for r in rows])
        except Exception as exc:
            return _err(str(exc))

    def filter_rename_suggestions(self, query: str, ext: str = "", lang: str = "") -> dict:
        try:
            import rename_templates as rt
            code = self._rename_lang(lang)
            return _ok(lang=code, suggestions=rt.store().suggestions(query, code, ext, limit=60))
        except Exception as exc:
            return _err(str(exc))

    def save_name_template(self, en: str, local: str = "", lang: str = "") -> dict:
        """Add one custom template row (English + optional local name)."""
        try:
            import rename_templates as rt
            code = self._rename_lang(lang)
            ok, res = rt.store().add_user_row(code, en, local)
            return _ok(path=res, lang=code) if ok else _err(res)
        except Exception as exc:
            return _err(str(exc))

    def save_rename_template(self, template: dict) -> dict:
        """Add a template from {en, local, lang} (legacy {name, pattern} accepted)."""
        try:
            t = template or {}
            en = (t.get("en") or t.get("name") or "").strip()
            local = (t.get("local") or "").strip()
            if not en and not local:
                return _err("Template name is required")
            return self.save_name_template(en, local, t.get("lang", ""))
        except Exception as exc:
            return _err(str(exc))

    def save_rename_templates(self, rows: list, lang: str = "") -> dict:
        """Replace the custom rows of a language ([{en, local}, ...]) — 'Save to file'."""
        try:
            import rename_templates as rt
            code = self._rename_lang(lang)
            ok, res = rt.store().save_user_rows(code, rows or [])
            return _ok(path=res, lang=code) if ok else _err(res)
        except Exception as exc:
            return _err(str(exc))

    def rename_with_template(self, path: str, name: str, save: bool = False,
                             lang: str = "") -> dict:
        """Rename *path* to ``<name><original ext>`` without overwriting.

        A taken name gets `` (2)``, `` (3)`` … appended.  With *save*, the typed
        name is also stored as a custom template for the current language.
        """
        try:
            import rename_templates as rt
            code = self._rename_lang(lang)
            if not path or not os.path.exists(path):
                return _err("File not found")
            folder = os.path.dirname(os.path.abspath(path))
            base = os.path.basename(path)
            ext = "" if os.path.isdir(path) else os.path.splitext(base)[1]
            stem = rt.sanitize_stem(rt.strip_ext(name, ext))
            if not stem:
                return _err("Name is empty")
            new_name = rt.unique_name(folder, stem, ext, src_path=path)
            ok, res = _fo.safe_rename(path, new_name)
            if not ok:
                return _err(res)
            new_path = path if res == "Unchanged." else res
            saved = False
            if save:
                pair = rt.pair_from_name_text(stem, code)
                if pair:
                    saved, _r = rt.store().add_user_row(code, *pair)
            return _ok(new_path=new_path, new_name=os.path.basename(new_path),
                       saved=bool(saved), lang=code)
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # GPT-4o COST TRACKER
    # =========================================================================

    def get_cost(self) -> dict:
        try:
            _tin, _tout, cost_usd = _fo.get_gpt4o_session_cost()
            try:  # + AI photo edits (gpt-image-1) run this session
                import ai_photo_editor as _ape
                cost_usd += _ape.session_cost_usd()
            except Exception:
                pass
            return _ok(cost_usd=round(cost_usd, 4))
        except Exception as exc:
            return _err(str(exc))

    def reset_cost(self) -> dict:
        try:
            _fo.reset_gpt4o_session_cost()
            try:
                import ai_photo_editor as _ape
                _ape.reset_session_cost()
            except Exception:
                pass
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def log_js(self, level: str = "info", msg: str = "") -> dict:
        try:
            import logging
            getattr(logging, level.lower(), logging.info)(f"[JS] {msg}")
            return _ok()
        except Exception:
            return _ok()

    # ── Settings / API key ───────────────────────────────────────────────────

    _SETTINGS_FILE = os.path.join(_HERE, "sfm_settings.json")

    def _load_settings(self) -> dict:
        try:
            if os.path.exists(self._SETTINGS_FILE):
                import json as _json
                return _json.loads(open(self._SETTINGS_FILE, encoding="utf-8").read())
        except Exception:
            pass
        return {}

    _SETTINGS_LOCK = threading.Lock()

    def _save_settings(self, data: dict) -> None:
        import json as _json
        # Background threads (live events, sync) save too: serialise the
        # read-modify-write, and write a temp file then swap it in so a reader
        # never sees a half-written (empty) settings file.
        with self._SETTINGS_LOCK:
            existing = self._load_settings()
            existing.update(data)
            tmp = f"{self._SETTINGS_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(_json.dumps(existing, indent=2, ensure_ascii=False))
            os.replace(tmp, self._SETTINGS_FILE)

    def get_settings(self) -> dict:
        try:
            s = self._load_settings()
            # Don't expose raw API key — return masked version
            safe = {k: v for k, v in s.items()
                    if k not in ("openai_api_key", "cloud_token", "cloud_token_enc", "cloud_last_user")}
            if "openai_api_key" in s and s["openai_api_key"]:
                safe["has_api_key"] = True
            else:
                safe["has_api_key"] = False
            return _ok(settings=safe)
        except Exception as exc:
            return _err(str(exc))

    def save_settings(self, settings: dict) -> dict:
        try:
            # UI settings from the Settings dialog / toolbar — store directly.
            # (The API key is saved separately via set_api_key.)
            allowed = {"theme", "output_folder", "ocr_lang",
                       "last_folder", "zoom", "panel_layout", "rename_lang"}
            to_save = {k: v for k, v in settings.items() if k in allowed}
            if to_save:
                self._save_settings(to_save)
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def get_api_key(self) -> dict:
        try:
            s = self._load_settings()
            key = s.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
            masked = (key[:8] + "…" + key[-4:]) if len(key) > 12 else ("*" * len(key))
            return _ok(masked=masked, has_key=bool(key))
        except Exception as exc:
            return _err(str(exc))

    def set_api_key(self, key: str) -> dict:
        try:
            self._save_settings({"openai_api_key": key.strip()})
            os.environ["OPENAI_API_KEY"] = key.strip()
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    # ── AI Photo Editor ───────────────────────────────────────────────────────

    def ai_photo_actions(self) -> dict:
        """List the AI photo actions as [{key, label}, ...] plus option choices."""
        try:
            import ai_photo_editor as _ape
            return _ok(actions=_ape.ai_photo_actions(), options=_ape.ai_photo_options(),
                       cost_est_usd=_ape.estimate_cost_usd())
        except Exception as exc:
            return _err(str(exc))

    def _openai_key(self) -> str:
        key = ""
        try:
            key = (self._load_settings().get("openai_api_key") or "").strip()
        except Exception:
            pass
        if not key:
            try:
                from ai_photo_editor import merge_dotenv_into_environ
                merge_dotenv_into_environ()
            except Exception:
                pass
            key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        return key

    def ai_photo_estimate(self, path: str, opts: dict = None) -> dict:
        """Estimated USD + output file name for running Wear Suit on *path*."""
        try:
            import ai_photo_editor as _ape
            o = _ape.normalize_options(opts or {})
            w = h = 0
            try:
                from PIL import Image as _Img, ImageOps as _IO
                with _Img.open(path) as im:
                    w, h = _IO.exif_transpose(im).size
            except Exception:
                pass
            return _ok(cost_est_usd=_ape.estimate_cost_usd(w, h, o["quality"]),
                       out_name=os.path.basename(_ape.ai_output_path(path, "wear_suit")),
                       options=o)
        except Exception as exc:
            return _err(str(exc))

    def run_ai_photo_action(self, path: str, action: str, opts: dict = None) -> dict:
        """Start an AI photo edit in the background.

        The subscription gate, file and API-key checks happen synchronously, so a
        rejected call returns {ok:false, error, need_subscription|need_login|
        need_api_key} and nothing starts. Otherwise returns {ok, started:true,
        job_id, cost_est_usd}; the result arrives as the ``ai_photo_result``
        event: {ok:true, out, src, action, job_id} or {ok:false, error, action, job_id}.
        The original file is never overwritten (a new <name>_suit<ext> is written).
        opts: {suit_color, tie, quality, job_id, out_path}.
        """
        opts = opts or {}
        gate = self._require_plan()
        if gate:
            return gate
        if not path or not os.path.isfile(path):
            return _err(f"File not found: {path}")
        import ai_photo_editor as _ape
        if action not in {a["key"] for a in _ape.ai_photo_actions()}:
            return _err(f"Unknown AI photo action: {action}")
        api_key = self._openai_key()
        if not api_key:
            return _err("Add your OpenAI API key in Settings to use AI photo editing",
                        need_api_key=True)
        options = _ape.normalize_options(opts)
        job_id = str(opts.get("job_id") or f"ai{int(time.time() * 1000)}")
        est = None
        try:
            est = self.ai_photo_estimate(path, options).get("cost_est_usd")
        except Exception:
            pass

        def _run():
            base = {"action": action, "job_id": job_id, "src": path}
            try:
                ok, msg = _ape.run_ai_edit(
                    path, action,
                    out_path=str(opts.get("out_path") or ""),
                    api_key=api_key,
                    options=options,
                )
                if ok:
                    self._emit("ai_photo_result", dict(base, ok=True, out=msg))
                else:
                    self._emit("ai_photo_result", dict(base, ok=False, error=msg))
            except Exception as exc:
                _log.exception("run_ai_photo_action failed")
                self._emit("ai_photo_result", dict(base, ok=False, error=str(exc)))
        self._thread(_run)
        return _ok(started=True, job_id=job_id, cost_est_usd=est, options=options)

    # =========================================================================
    # ACCOUNT / CLOUD — sign-in, monthly subscription and cloud storage live in
    # cloud_bridge.CloudBridgeMixin (inherited above).
    # =========================================================================
