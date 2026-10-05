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


class SFMBridge(CloudBridgeMixin):
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
    def get_file_type_icon(self, ext: str, size: int = 18) -> dict:
        """Return a base64-encoded PNG of the real Windows shell icon for *ext*.

        Uses SHGetFileInfoW / SHGFI_USEFILEATTRIBUTES so no real file is needed.
        Returns {ok:true, data: "data:image/png;base64,..."} or {ok:false}.
        """
        import base64, io, sys
        if sys.platform != "win32":
            return _err("not windows")
        try:
            import ctypes
            from ctypes import wintypes
            from PIL import Image
        except ImportError as exc:
            return _err(str(exc))

        if not ext:
            ext = ".file"
        elif not ext.startswith("."):
            ext = "." + ext

        SHGFI_ICON            = 0x100
        SHGFI_USEFILEATTRIBUTES = 0x10
        SHGFI_SMALLICON       = 0x1
        SHGFI_LARGEICON       = 0x0
        FILE_ATTRIBUTE_NORMAL = 0x80

        class SHFILEINFO(ctypes.Structure):
            _fields_ = [
                ("hIcon",         wintypes.HICON),
                ("iIcon",         ctypes.c_int),
                ("dwAttributes",  wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260),
                ("szTypeName",    wintypes.WCHAR * 80),
            ]

        shfi  = SHFILEINFO()
        flags = SHGFI_ICON | SHGFI_USEFILEATTRIBUTES | (
            SHGFI_SMALLICON if size <= 20 else SHGFI_LARGEICON
        )
        try:
            shell32 = ctypes.windll.shell32
            user32  = ctypes.windll.user32
        except (AttributeError, OSError) as exc:
            return _err(str(exc))

        res = shell32.SHGetFileInfoW(
            "x" + ext,
            FILE_ATTRIBUTE_NORMAL,
            ctypes.byref(shfi),
            ctypes.sizeof(shfi),
            flags,
        )
        if not res or not shfi.hIcon:
            return _err("no icon")

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
                # Make white pixels transparent (background bleed)
                px = list(pil_img.getdata())
                px = [(r, g, b, 0) if r >= 250 and g >= 250 and b >= 250 else (r, g, b, a)
                      for r, g, b, a in px]
                pil_img.putdata(px)
                mem_dc.SelectObject(old)
                mem_dc.DeleteDC()
                win32gui.ReleaseDC(0, screen_dc_h)
                win32gui.DeleteObject(hbmp.GetHandle())
            except Exception:
                # ctypes fallback
                hdc = user32.GetDC(0)
                class ICONINFO(ctypes.Structure):
                    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                                 ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                                 ("hbmColor", wintypes.HBITMAP)]
                ii = ICONINFO()
                ctypes.windll.user32.GetIconInfo(shfi.hIcon, ctypes.byref(ii))
                bm = ctypes.create_string_buffer(size * size * 4)
                ctypes.windll.gdi32.GetBitmapBits(ii.hbmColor, len(bm), bm)
                pil_img = Image.frombuffer("RGBA", (size, size), bm.raw, "raw", "BGRA", 0, 1)
                user32.ReleaseDC(0, hdc)
        finally:
            user32.DestroyIcon(shfi.hIcon)

        if pil_img is None:
            return _err("render failed")

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return _ok(data=f"data:image/png;base64,{b64}", ext=ext, size=size)

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
        """
        tmp = None
        try:
            fitz = _fo.get_fitz()
            if not fitz:
                return _err("Install PyMuPDF (pip install pymupdf).")
            if not pages:
                return _err("No pages to save.")
            out_path = os.path.abspath(os.path.normpath(out_path))
            _log.info("build_pdf_from_pages: %d page(s) -> %s (replace=%s)",
                      len(pages), out_path, replace)
            target = out_path
            if replace:
                tmp = out_path + ".sfm_tmp"
                target = tmp
            out_doc = fitz.open()
            for it in pages:
                sp = os.path.abspath(os.path.normpath(str(it.get("path", ""))))
                pi = int(it.get("page", 0))
                rot = int(it.get("rotate", 0)) % 360
                src = fitz.open(sp)
                try:
                    out_doc.insert_pdf(src, from_page=pi, to_page=pi)
                finally:
                    src.close()
                if rot:
                    # Rotate relative to the page's existing /Rotate so the UI's
                    # "turn this page" matches what the user sees in the tile.
                    pg = out_doc[-1]
                    pg.set_rotation((pg.rotation + rot) % 360)
            out_doc.save(target)
            out_doc.close()
            if replace:
                # Release preview/render handles so os.replace can win on Windows.
                _fo.release_pdf_handles_for_paths((out_path,))
                os.replace(tmp, out_path)
                tmp = None
            return _ok(out_path=out_path, page_count=len(pages))
        except Exception as exc:
            _log.error("build_pdf_from_pages failed: %s", exc)
            # Clean up our own half-written temp file (old system did the same).
            try:
                if tmp and os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return _err(str(exc))

    def get_desktop(self) -> dict:
        return _ok(path=os.path.join(os.path.expanduser("~"), "Desktop"))

    def path_exists(self, path: str) -> dict:
        return _ok(exists=os.path.exists(path), is_dir=os.path.isdir(path))

    def get_file_info(self, path: str) -> dict:
        try:
            e = _file_entry(path)
            if e["ext"] == ".pdf":
                e["page_count"] = _fo.pdf_page_count(path)
            return _ok(**e)
        except Exception as exc:
            return _err(str(exc))

    def open_native(self, path: str) -> dict:
        try:
            _fo.open_path_system_default(path)
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def open_folder_in_explorer(self, path: str) -> dict:
        try:
            import subprocess
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

    def _run_open_with(self, argv: list, path: str) -> dict:
        """Launch a file with the given argv (from get_open_with_commands)."""
        try:
            import subprocess
            cmd = list(argv)
            # Substitute placeholder if present; otherwise append path
            substituted = False
            for i, arg in enumerate(cmd):
                if '%1' in arg or '%L' in arg:
                    cmd[i] = arg.replace('%1', path).replace('%L', path)
                    substituted = True
            if not substituted:
                cmd.append(path)
            subprocess.Popen(cmd, shell=False)
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def get_open_with_commands(self, path: str) -> dict:
        try:
            cmds = _fo.windows_open_with_menu_commands(path)
            return _ok(commands=cmds)
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

    def copy_files(self, src_paths: list, dest_dir: str) -> dict:
        import shutil
        results = []
        for src in src_paths:
            try:
                name = os.path.basename(src)
                dst  = os.path.join(dest_dir, name)
                # Auto-number if exists
                base, ext = os.path.splitext(name)
                i = 2
                while os.path.exists(dst):
                    dst = os.path.join(dest_dir, f"{base} ({i}){ext}")
                    i += 1
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                results.append({"src": src, "dst": dst, "ok": True})
            except Exception as exc:
                results.append({"src": src, "ok": False, "error": str(exc)})
        return _ok(results=results)

    def move_files(self, src_paths: list, dest_dir: str) -> dict:
        import shutil
        results = []
        for src in src_paths:
            try:
                name = os.path.basename(src)
                dst  = os.path.join(dest_dir, name)
                shutil.move(src, dst)
                results.append({"src": src, "dst": dst, "ok": True})
            except Exception as exc:
                results.append({"src": src, "ok": False, "error": str(exc)})
        return _ok(results=results)

    def create_folder(self, parent: str, name: str) -> dict:
        try:
            new_path = os.path.join(parent, name)
            os.makedirs(new_path, exist_ok=True)
            return _ok(path=new_path)
        except Exception as exc:
            return _err(str(exc))

    def set_clipboard(self, text: str) -> dict:
        try:
            import subprocess
            subprocess.run(["clip"], input=text.encode("utf-8"), check=True)
            return _ok()
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
        """Render a PDF page for the preview panel."""
        _log.info("get_pdf_page_as_png: %s page=%d dpi=%d", path, page, dpi)
        try:
            # Convert DPI to pixel dimensions (A4 at given DPI)
            max_dim = int(dpi * 8.27)   # A4 width in inches × dpi
            pil = _fo.pil_image_for_pdf_page(path, page, max_dim, max_dim)
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
            pil = _fo.pil_image_load_full_rgb(path)
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

    def merge_pdfs(self, paths: list, out_path: str) -> dict:
        try:
            _check(_fo.pdf_merge_ordered_paths(paths, out_path), "PDF merge failed")
            return _ok(out_path=out_path)
        except Exception as exc:
            return _err(str(exc))

    def split_pdf_pages(self, path: str, out_dir: str) -> dict:
        try:
            # pdf_split_each_page requires a log callback (3rd positional arg)
            _check(_fo.pdf_split_each_page(path, out_dir, _noop_log), "PDF split failed")
            return _ok(files=[])
        except Exception as exc:
            return _err(str(exc))

    def compress_pdf(self, path: str, out_path: str) -> dict:
        try:
            _check(_fo.pdf_compress(path, out_path), "PDF compress failed")
            before = os.path.getsize(path)
            after  = os.path.getsize(out_path)
            saved  = before - after
            return _ok(out_path=out_path, before_bytes=before,
                       after_bytes=after, saved_bytes=saved,
                       saved_str=_fmt_size(max(0, saved)))
        except Exception as exc:
            return _err(str(exc))

    def compress_pdf_quality(self, path: str, quality: str = "ebook",
                             out_path: str = "") -> dict:
        """Compress PDF using Ghostscript quality preset."""
        try:
            if not out_path:
                base, ext = os.path.splitext(path)
                out_path = base + "_compressed" + ext
            # Try Ghostscript first; fall back to pymupdf deflate
            import subprocess, shutil
            gs = shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")
            if gs:
                cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
                       f"-dPDFSETTINGS=/{quality}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                       f"-sOutputFile={out_path}", path]
                subprocess.run(cmd, check=True, timeout=120)
            else:
                # pymupdf fallback
                import fitz
                doc = fitz.open(path)
                doc.save(out_path, garbage=4, deflate=True, clean=True)
                doc.close()
            before = os.path.getsize(path)
            after  = os.path.getsize(out_path)
            reduction = max(0, round((before - after) / before * 100)) if before else 0
            return _ok(out=out_path, before_bytes=before, after_bytes=after, reduction=reduction)
        except Exception as exc:
            return _err(str(exc))

    def rotate_pdf_page(self, path: str, page: int, degrees: int) -> dict:
        try:
            # pdf_rotate_page takes clockwise: bool — passing degrees directly
            # meant -90 (truthy) also rotated clockwise.
            _check(_fo.pdf_rotate_page(path, page, degrees >= 0), "Rotate failed")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def delete_pdf_page(self, path: str, page: int) -> dict:
        try:
            _check(_fo.pdf_delete_page(path, page), "Delete page failed")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def reorder_pdf_pages(self, path: str, new_order: list, out_path: str) -> dict:
        # pdf_reorder_pages works IN PLACE. If out_path is given, copy the
        # original there first and reorder the copy, so the original is kept
        # (the UI promises "saved alongside the original").
        _log.info("reorder_pdf_pages: %s order=%s out=%s", path, new_order, out_path)
        try:
            target = path
            if out_path and os.path.abspath(out_path) != os.path.abspath(path):
                import shutil
                base, ext = os.path.splitext(out_path)
                final, i = out_path, 2
                while os.path.exists(final):
                    final = f"{base}-{i}{ext}"
                    i += 1
                shutil.copy2(path, final)
                target = final
            _check(_fo.pdf_reorder_pages(target, new_order), "Reorder failed")
            return _ok(out_path=target)
        except Exception as exc:
            _log.error("reorder_pdf_pages failed: %s", exc)
            return _err(str(exc))

    def extract_pdf_pages(self, path: str, page_spec: str, out_path: str) -> dict:
        try:
            # parse_pdf_page_range_input also returns (ok, pages_or_error)
            pages = _check(_fo.parse_pdf_page_range_input(
                page_spec, _fo.pdf_page_count(path)), "Invalid page range")
            # Returns (ok, temp_pdf_path); previously the temp file was created
            # and abandoned — nothing was ever written to out_path.
            tmp = _check(_fo.pdf_export_selected_pages_to_tempfile(path, pages),
                         "Page extraction failed")
            if out_path:
                import shutil
                base, ext = os.path.splitext(out_path)
                final, i = out_path, 2
                while os.path.exists(final):
                    final = f"{base}-{i}{ext}"
                    i += 1
                shutil.move(tmp, final)
            else:
                final = tmp
            return _ok(pages=pages, out_path=final)
        except Exception as exc:
            return _err(str(exc))

    def pdf_to_images(self, path: str, dpi: int = 150, fmt: str = "png") -> dict:
        # The old call didn't match file_ops.pdf_to_images(src, out_dir, log, dpi)
        # at all (TypeError every time) and that helper has no jpg support or
        # file list return, so render directly here.
        try:
            fitz = _fo.get_fitz()
            Image = _fo.get_pillow()
            if not fitz or not Image:
                return _err("PyMuPDF and Pillow are required")
            fmt = (fmt or "png").lower().lstrip(".")
            if fmt == "jpeg":
                fmt = "jpg"
            doc = fitz.open(path)
            base = os.path.splitext(path)[0]
            files = []
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=int(dpi))
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                outp, n = f"{base}_p{i + 1}.{fmt}", 2
                while os.path.exists(outp):
                    outp = f"{base}_p{i + 1}-{n}.{fmt}"
                    n += 1
                img.save(outp, quality=92)
                files.append(outp)
            doc.close()
            return _ok(files=files)
        except Exception as exc:
            _log.error("pdf_to_images error: %s", exc, exc_info=True)
            return _err(str(exc))

    @staticmethod
    def _require_image(path: str) -> None:
        """Convert-to-PDF is image -> PDF only (Office conversion was removed)."""
        ext = os.path.splitext(path or "")[1].lower()
        if ext not in _fo.IMAGE_EXT:
            raise RuntimeError(
                f"Only images can be converted to PDF (got '{ext or 'no extension'}')")

    def convert_to_pdf(self, path: str, out_path: str = "") -> dict:
        """Convert one image to PDF (beside the original, or to out_path)."""
        try:
            self._require_image(path)
            if out_path:
                _check(_fo.image_to_pdf([path], out_path, _noop_log), "Convert failed")
                result = out_path
            else:
                result = _check(_fo.convert_file_to_pdf_replace(path, _noop_log),
                                "Convert failed")
            return _ok(out_path=str(result or path))
        except Exception as exc:
            return _err(str(exc))

    def combine_files_to_pdf(self, paths: list, out_path: str) -> dict:
        try:
            _check(_fo.combine_files_to_pdf(paths, out_path, _noop_log),
                   "Combine failed")
            return _ok(out_path=out_path)
        except Exception as exc:
            return _err(str(exc))

    def crop_image(self, path: str, x: int, y: int, w: int, h: int, out_path: str = "") -> dict:
        try:
            ok, result = _fo.crop_image(path, int(x), int(y), int(w), int(h), out_path)
            return _ok(out=result) if ok else _err(result)
        except Exception as exc:
            return _err(str(exc))

    # ── Image compression / format conversion ─────────────────────────────

    def compress_image(self, path: str, quality: int = 70, max_edge: int = 0,
                       fmt: str = "", out_path: str = "") -> dict:
        try:
            ok, res = _fo.compress_image(path, int(quality or 70), int(max_edge or 0),
                                         fmt or "", out_path or "")
            if not ok:
                return _err(res)
            before, after = res["before"], res["after"]
            pct = round((before - after) * 100.0 / before, 1) if before else 0.0
            return _ok(out=res["out"], before=before, after=after,
                       saved=before - after, reduction=pct)
        except Exception as exc:
            return _err(str(exc))

    def compress_images(self, paths: list, quality: int = 70, max_edge: int = 0,
                        fmt: str = "") -> dict:
        try:
            if isinstance(paths, str):
                paths = [paths]
            r = _fo.compress_images(list(paths or []), int(quality or 70),
                                    int(max_edge or 0), fmt or "")
            before = r["before"]
            pct = round(r["saved"] * 100.0 / before, 1) if before else 0.0
            failed = sum(1 for x in r["results"] if not x.get("ok"))
            return _ok(results=r["results"], before=before, after=r["after"],
                       saved=r["saved"], reduction=pct, failed=failed)
        except Exception as exc:
            return _err(str(exc))

    def convert_image(self, path: str, fmt: str, out_path: str = "") -> dict:
        try:
            ok, result = _fo.convert_image(path, fmt, out_path or "")
            return _ok(out=result) if ok else _err(result)
        except Exception as exc:
            return _err(str(exc))

    def ocr_rename_with_progress(self, paths: list, api_key: str = "") -> dict:
        """Same as ocr_rename but emits ocr_rename_log + ocr_rename_done events."""
        def _run():
            try:
                if api_key:
                    os.environ.setdefault("OPENAI_API_KEY", api_key)
                log_cb = self._log_cb("ocr_rename_log")
                _fo.pdf_rename_files_by_ocr_smart(paths, log=log_cb)
                self._emit("ocr_rename_done", {"ok": True})
            except Exception as exc:
                self._emit("ocr_rename_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)


    # ── long ops (threaded, push events) ─────────────────────────────────────

    def smart_split_rename(self, path: str, out_dir: str = "", api_key: str = "") -> dict:
        """
        Async: runs pdf_smart_split_merge_rename() in a thread.
        Progress emitted as:  __sfm_event("smart_rename_log", {log: "..."})
        Done emitted as:      __sfm_event("smart_rename_done", {ok, error?})
        """
        def _run():
            try:
                if api_key:
                    os.environ.setdefault("OPENAI_API_KEY", api_key)
                log_cb = self._log_cb("smart_rename_log")
                _fo.pdf_smart_split_merge_rename(
                    path,
                    output_folder=out_dir or None,
                    log=log_cb,
                )
                self._emit("smart_rename_done", {"ok": True})
            except Exception as exc:
                self._emit("smart_rename_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def ocr_rename(self, paths: list, api_key: str = "") -> dict:
        def _run():
            try:
                if api_key:
                    os.environ.setdefault("OPENAI_API_KEY", api_key)
                log_cb = self._log_cb("ocr_rename_log")
                _fo.pdf_rename_files_by_ocr_smart(paths, log=log_cb)
                self._emit("ocr_rename_done", {"ok": True})
            except Exception as exc:
                self._emit("ocr_rename_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def split_and_rename_ocr(self, path: str, api_key: str = "") -> dict:
        def _run():
            try:
                if api_key:
                    os.environ.setdefault("OPENAI_API_KEY", api_key)
                log_cb = self._log_cb("split_ocr_log")
                _fo.pdf_split_and_rename_by_ocr(path, log=log_cb)
                self._emit("split_ocr_done", {"ok": True})
            except Exception as exc:
                self._emit("split_ocr_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def compress_pdf_async(self, path: str, out_path: str) -> dict:
        def _run():
            try:
                r = self.compress_pdf(path, out_path)
                self._emit("compress_done", r)
            except Exception as exc:
                self._emit("compress_done", _err(str(exc)))
        self._thread(_run)
        return _ok(started=True)

    def convert_to_pdf_async(self, paths: list) -> dict:
        def _run():
            results = []
            for p in paths:
                try:
                    self._require_image(p)
                    out = _check(_fo.convert_file_to_pdf_replace(p, _noop_log),
                                 "Convert failed")
                    results.append({"path": p, "ok": True, "out": str(out)})
                except Exception as exc:
                    results.append({"path": p, "ok": False, "error": str(exc)})
                self._emit("convert_pdf_progress", {"results": results})
            self._emit("convert_pdf_done", {"ok": True, "results": results})
        self._thread(_run)
        return _ok(started=True)

    # =========================================================================
    # QR
    # =========================================================================

    def scan_qr_from_screen(self) -> dict:
        """Minimise the app window, wait 1.5 s, grab the whole screen,
        decode any QR code, restore the window, emit qr_result.

        This is the fallback path (toolbar button). The preferred path is
        scan_qr_from_file() which reads QR directly from a selected file.
        """
        def _run():
            try:
                import mss
                from PIL import Image as _Img
                import qr_screen_capture as qr

                # Hide our window so the QR code behind it is visible
                if self._window:
                    try:
                        self._window.minimize()
                    except Exception:
                        pass
                import time
                time.sleep(1.5)   # let OS animation finish

                decoder = qr.QRDecoder()
                with mss.mss() as sct:
                    monitor = sct.monitors[0]  # full virtual desktop
                    shot = sct.grab(monitor)
                    img = _Img.frombytes("RGB", (shot.width, shot.height), shot.rgb)

                result = decoder.try_decode_qr(img)

                # Restore window
                if self._window:
                    try:
                        self._window.restore()
                    except Exception:
                        pass

                if result:
                    self._emit("qr_result", {"ok": True, "url": result.text})
                else:
                    self._emit("qr_result", {"ok": False,
                                             "error": "No QR code found on screen"})
            except Exception as exc:
                if self._window:
                    try:
                        self._window.restore()
                    except Exception:
                        pass
                self._emit("qr_result", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def scan_qr_from_file(self, path: str) -> dict:
        """Decode QR codes directly from an image or PDF file.

        For images  → pyzbar on the PIL image directly.
        For PDFs    → scan each page (up to 10) converted at 200 dpi.
        Returns {ok, url, text, page} on success or {ok:false, error} on failure.
        This is the primary QR scan path in the UI (right-click a file).
        """
        def _run():
            try:
                import qr_screen_capture as qr
                from PIL import Image as _Img
                decoder = qr.QRDecoder()

                ext = os.path.splitext(path)[1].lower()

                # ── Image file ───────────────────────────────────────────────
                if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp",
                           ".gif", ".tiff", ".tif"):
                    img = _Img.open(path).convert("RGB")
                    result = decoder.try_decode_qr(img)
                    if result:
                        self._emit("qr_result", {
                            "ok": True, "url": result.text, "text": result.text})
                    else:
                        self._emit("qr_result", {
                            "ok": False, "error": "No QR code found in image"})
                    return

                # ── PDF file ─────────────────────────────────────────────────
                if ext == ".pdf":
                    try:
                        import fitz  # type: ignore  (PyMuPDF)
                    except ImportError:
                        self._emit("qr_result", {
                            "ok": False,
                            "error": "PyMuPDF (fitz) not installed — cannot scan PDF"})
                        return
                    doc = fitz.open(path)
                    for page_num in range(min(doc.page_count, 10)):
                        page = doc.load_page(page_num)
                        mat = fitz.Matrix(200 / 72, 200 / 72)  # 200 dpi
                        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                        img = _Img.frombytes("RGB",
                                             (pix.width, pix.height), pix.samples)
                        result = decoder.try_decode_qr(img)
                        if result:
                            self._emit("qr_result", {
                                "ok": True,
                                "url": result.text,
                                "text": result.text,
                                "page": page_num + 1,
                            })
                            doc.close()
                            return
                    doc.close()
                    self._emit("qr_result", {
                        "ok": False,
                        "error": f"No QR code found in first {min(doc.page_count,10)} PDF pages"})
                    return

                self._emit("qr_result", {
                    "ok": False,
                    "error": f"Unsupported file type for QR scan: {ext}"})
            except Exception as exc:
                self._emit("qr_result", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    # =========================================================================
    # DOCUMENT RENAME TEMPLATES
    # =========================================================================

    def get_rename_templates(self) -> dict:
        try:
            # Import from student_folder_maker helpers
            import student_folder_maker as sfm
            pairs = sfm.document_rename_merged_pairs()
            return _ok(pairs=[{"key": k, "label": v} for k, v in pairs])
        except Exception as exc:
            return _err(str(exc))

    def filter_rename_suggestions(self, query: str, ext: str = "") -> dict:
        try:
            import student_folder_maker as sfm
            suggestions = sfm._filter_document_renames(query)
            return _ok(suggestions=suggestions)
        except Exception as exc:
            return _err(str(exc))

    def save_name_template(self, key: str, label: str) -> dict:
        try:
            import student_folder_maker as sfm
            sfm.document_rename_save_user_templates({key: label})
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def save_rename_template(self, template: dict) -> dict:
        """Save a rename template dict with {name, pattern} keys."""
        try:
            name    = template.get("name", "")
            pattern = template.get("pattern", name)
            if not name:
                return _err("Template name is required")
            import student_folder_maker as sfm
            sfm.document_rename_save_user_templates({name: pattern})
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # GPT-4o COST TRACKER
    # =========================================================================

    def get_cost(self) -> dict:
        try:
            _tin, _tout, cost_usd = _fo.get_gpt4o_session_cost()
            return _ok(cost_usd=round(cost_usd, 4))
        except Exception as exc:
            return _err(str(exc))

    def reset_cost(self) -> dict:
        try:
            _fo.reset_gpt4o_session_cost()
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

    def _save_settings(self, data: dict) -> None:
        import json as _json
        existing = self._load_settings()
        existing.update(data)
        open(self._SETTINGS_FILE, "w", encoding="utf-8").write(
            _json.dumps(existing, indent=2, ensure_ascii=False)
        )

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
                       "last_folder", "zoom", "panel_layout"}
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
        """List the AI photo actions as [{key, label}, ...]."""
        try:
            from ai_photo_editor import ai_photo_actions as _actions
            return _ok(actions=_actions())
        except Exception as exc:
            return _err(str(exc))

    def run_ai_photo_action(self, path: str, action: str, opts: dict = None) -> dict:
        """Start an AI photo edit in the background.

        Returns {ok, started:true} immediately; the result arrives as the
        ``ai_photo_result`` event: {ok:true, out, action} or {ok:false, error, action}.
        The original file is never overwritten (a new *_ai file is written).
        """
        opts = opts or {}
        gate = self._require_plan()
        if gate:
            return gate
        if not path or not os.path.isfile(path):
            return _err(f"File not found: {path}")

        api_key = ""
        try:
            api_key = (self._load_settings().get("openai_api_key") or "").strip()
        except Exception:
            pass

        def _run():
            try:
                from ai_photo_editor import run_ai_edit
                ok, msg = run_ai_edit(
                    path, action,
                    out_path=str(opts.get("out_path") or ""),
                    api_key=api_key,
                )
                if ok:
                    self._emit("ai_photo_result", {"ok": True, "out": msg, "action": action})
                else:
                    self._emit("ai_photo_result", {"ok": False, "error": msg, "action": action})
            except Exception as exc:
                _log.exception("run_ai_photo_action failed")
                self._emit("ai_photo_result", {"ok": False, "error": str(exc), "action": action})
        self._thread(_run)
        return _ok(started=True)

    # ── Screen capture / QR overlay ───────────────────────────────────────────

    def get_screen_capture(self) -> dict:
        """Minimize window, screenshot full screen, restore, return base64 data-URL."""
        def _run():
            try:
                import mss, time, base64, io
                from PIL import Image as _Img
                if self._window:
                    try: self._window.minimize()
                    except Exception: pass
                time.sleep(1.2)
                with mss.mss() as sct:
                    monitor = sct.monitors[0]
                    shot = sct.grab(monitor)
                    img = _Img.frombytes("RGB", (shot.width, shot.height), shot.rgb)
                if self._window:
                    try: self._window.restore()
                    except Exception: pass
                # Store for decode_qr_at_point
                self._last_screenshot = img
                # Encode as JPEG data-URL (smaller than PNG)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                b64 = base64.b64encode(buf.getvalue()).decode()
                data_url = f"data:image/jpeg;base64,{b64}"
                self._emit("screen_capture_ready", {
                    "ok": True,
                    "data_url": data_url,
                    "width": img.width,
                    "height": img.height,
                })
            except Exception as exc:
                if self._window:
                    try: self._window.restore()
                    except Exception: pass
                self._emit("screen_capture_ready", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def decode_qr_at_point(self, cx: float, cy: float) -> dict:
        """Crop last screenshot around (cx,cy) at multiple sizes, decode QR, open URL."""
        try:
            import qr_screen_capture as qr
            img = getattr(self, "_last_screenshot", None)
            if img is None:
                return _err("No screenshot available — call get_screen_capture first")
            decoder = qr.QRDecoder()
            icx, icy = int(cx), int(cy)
            # Try multiple crop radii around click point
            for half in (125, 175, 250, 350, 500):
                x1 = max(0, icx - half)
                y1 = max(0, icy - half)
                x2 = min(img.width, icx + half)
                y2 = min(img.height, icy + half)
                crop = img.crop((x1, y1, x2, y2))
                result = decoder.try_decode_qr(crop)
                if result:
                    text = result.text
                    is_url = text.startswith("http://") or text.startswith("https://")
                    if is_url:
                        import webbrowser
                        webbrowser.open_new_tab(text)
                    return _ok(text=text, is_url=is_url)
            # Full image fallback
            result = decoder.try_decode_qr(img)
            if result:
                text = result.text
                is_url = text.startswith("http://") or text.startswith("https://")
                if is_url:
                    import webbrowser
                    webbrowser.open_new_tab(text)
                return _ok(text=text, is_url=is_url)
            return _err("No QR code found near that point")
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # ACCOUNT / CLOUD — sign-in, monthly subscription and cloud storage live in
    # cloud_bridge.CloudBridgeMixin (inherited above).
    # =========================================================================
