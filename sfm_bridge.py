"""
sfm_bridge.py — PyWebView JS API bridge for StudentFolderMaker.

Every public method on SFMBridge is directly callable from JavaScript as:
    await window.pywebview.api.method_name(arg1, arg2, ...)

Rules:
 - All return values must be JSON-serialisable (dict / list / str / int / float / bool / None).
 - Long-running operations run in a daemon thread and push progress via
   self._emit(event, payload) which calls window.evaluate_js() on the JS side.
 - Never import Tkinter here.
 - Never duplicate backend logic — call file_ops / apostille_matcher / etc. directly.
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

class SFMBridge:
    """
    Singleton exposed to JavaScript as window.pywebview.api.
    The pywebview window reference is injected after creation via set_window().
    """

    def __init__(self):
        self._window = None          # set by main_webview.py after window creation
        self._watch_svc = None       # WatchFolderService instance (lazy)
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

    def open_with_acrobat(self, paths: list) -> dict:
        try:
            _fo.open_paths_with_acrobat(paths)
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

    def recycle_delete(self, paths: list) -> dict:
        results = []
        for p in paths:
            try:
                _fo.delete_path_recycle_or_remove(p)
                results.append({"path": p, "ok": True})
            except Exception as exc:
                results.append({"path": p, "ok": False, "error": str(exc)})
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

    def create_text_file(self, parent: str, name: str) -> dict:
        try:
            p = os.path.join(parent, name)
            if not os.path.exists(p):
                open(p, "w", encoding="utf-8").close()
            return _ok(path=p)
        except Exception as exc:
            return _err(str(exc))

    def set_clipboard(self, text: str) -> dict:
        try:
            import subprocess
            subprocess.run(["clip"], input=text.encode("utf-8"), check=True)
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def plan_similar_moves(self, paths: list) -> dict:
        try:
            plan = _fo.plan_moves_into_similar_folders(paths)
            return _ok(plan=plan)
        except Exception as exc:
            return _err(str(exc))

    def apply_file_moves(self, plan: list) -> dict:
        try:
            _fo.apply_file_moves(plan)
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
    # TEXT / WORD / EXCEL PREVIEW
    # =========================================================================

    def get_text_preview(self, path: str) -> dict:
        try:
            text = _fo.preview_plain_text(path)
            return _ok(text=text)
        except Exception as exc:
            return _err(str(exc))

    def get_docx_preview(self, path: str) -> dict:
        try:
            text = _fo.preview_docx_text(path)
            return _ok(text=text)
        except Exception as exc:
            return _err(str(exc))

    def get_word_html(self, path: str) -> dict:
        """Convert Word doc to plain-text HTML fallback (used when PDF conversion fails)."""
        try:
            result = _fo.preview_word_document(path)
            if isinstance(result, dict):
                html = result.get("html") or result.get("text") or str(result)
            else:
                html = str(result or "")
            return _ok(html=html)
        except Exception as exc:
            return _err(str(exc))

    def get_excel_html(self, path: str, sheet: str = "") -> dict:
        """Render Excel sheet as richly styled HTML table (colors, fonts, merged cells)."""
        try:
            ok, result = _fo.doc_to_html_excel(path, sheet or "")
            if not ok:
                return _err(result)
            sheets_raw = _fo.excel_list_sheets(path) or []
            sheets = [s[0] if isinstance(s, (list, tuple)) else s for s in sheets_raw]
            return _ok(html=result, sheets=sheets)
        except Exception as exc:
            return _err(str(exc))

    def get_doc_as_pdf(self, path: str) -> dict:
        """Convert DOCX/XLSX/HWP/HWPX to a temp PDF for native-quality preview.

        Returns {ok, pdf_path, page_count}.  The temp file lives until the
        next call or until the app exits — no manual cleanup required from JS.
        """
        try:
            ok, result = _fo.doc_to_pdf_preview(path)
            if not ok:
                return _err(result)
            # Count pages
            import fitz  # type: ignore
            try:
                doc = fitz.open(result)
                count = doc.page_count
                doc.close()
            except Exception:
                count = 1
            # Track temp files to avoid leaks
            if not hasattr(self, "_preview_temps"):
                self._preview_temps = []
            self._preview_temps.append(result)
            # Clean up old temps (keep last 5)
            while len(self._preview_temps) > 5:
                old = self._preview_temps.pop(0)
                try:
                    if os.path.exists(old):
                        os.remove(old)
                except Exception:
                    pass
            return _ok(pdf_path=result, page_count=count)
        except Exception as exc:
            return _err(str(exc))

    def get_excel_sheets(self, path: str) -> dict:
        try:
            sheets = _fo.excel_list_sheets(path)
            return _ok(sheets=sheets)
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

    def place_pdf_on_a4(self, path: str, pages_per_sheet: int = 4,
                        out_path: str = "") -> dict:
        """Tile pages of a PDF onto A4 sheets."""
        try:
            import fitz, math
            if not out_path:
                base, ext = os.path.splitext(path)
                out_path = base + f"_a4x{pages_per_sheet}" + ext
            src = fitz.open(path)
            a4_w, a4_h = 595, 842  # A4 portrait in points
            cols = 2 if pages_per_sheet <= 4 else 3
            rows = math.ceil(pages_per_sheet / cols)
            cell_w = a4_w / cols
            cell_h = a4_h / rows
            out = fitz.open()
            page_count = len(src)
            for sheet_start in range(0, page_count, pages_per_sheet):
                new_page = out.new_page(width=a4_w, height=a4_h)
                for slot, src_idx in enumerate(range(sheet_start, min(sheet_start + pages_per_sheet, page_count))):
                    col = slot % cols
                    row = slot // cols
                    x0 = col * cell_w + 2
                    y0 = row * cell_h + 2
                    rect = fitz.Rect(x0, y0, x0 + cell_w - 4, y0 + cell_h - 4)
                    new_page.show_pdf_page(rect, src, src_idx)
            out.save(out_path)
            out.close(); src.close()
            return _ok(out=out_path, sheets=math.ceil(page_count / pages_per_sheet))
        except Exception as exc:
            return _err(str(exc))

    def id_card_on_a4(self, path: str, out_path: str = "") -> dict:
        """Tile 4 copies of page 0 of a PDF onto a single A4 sheet."""
        try:
            import fitz
            if not out_path:
                base, ext = os.path.splitext(path)
                out_path = base + "_id_card_a4" + ext
            src = fitz.open(path)
            a4_w, a4_h = 595, 842
            cell_w = a4_w / 2
            cell_h = a4_h / 2
            out = fitz.open()
            new_page = out.new_page(width=a4_w, height=a4_h)
            for col in range(2):
                for row in range(2):
                    rect = fitz.Rect(col * cell_w + 2, row * cell_h + 2,
                                     col * cell_w + cell_w - 2, row * cell_h + cell_h - 2)
                    new_page.show_pdf_page(rect, src, 0)
            out.save(out_path)
            out.close(); src.close()
            return _ok(out=out_path)
        except Exception as exc:
            return _err(str(exc))

    def print_pdf_range(self, path: str, printer: str = "",
                        page_range: str = "", copies: int = 1) -> dict:
        """Print a PDF to a printer, optionally specifying page range."""
        try:
            import subprocess, shutil, tempfile, fitz
            # If page_range is given, extract those pages first
            if page_range.strip():
                pages = []
                for part in page_range.split(","):
                    part = part.strip()
                    if "-" in part:
                        a, b = part.split("-", 1)
                        pages.extend(range(int(a)-1, int(b)))
                    elif part:
                        pages.append(int(part)-1)
                src = fitz.open(path)
                tmp = tempfile.mktemp(suffix=".pdf")
                out = fitz.open()
                for p in pages:
                    if 0 <= p < len(src):
                        out.insert_pdf(src, from_page=p, to_page=p)
                out.save(tmp); out.close(); src.close()
                print_path = tmp
            else:
                print_path = path

            # Windows: use SumatraPDF or print via shell
            sumatra = shutil.which("SumatraPDF")
            if sumatra:
                cmd = [sumatra, "-print-to", printer or "default",
                       "-silent", print_path]
            else:
                # Fallback: ShellExecute print verb (opens dialog)
                import os
                os.startfile(print_path, "print")
                return _ok(method="shell_print")
            subprocess.run(cmd, check=True, timeout=60)
            return _ok(method="sumatra", printer=printer)
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

    def convert_to_pdf(self, path: str, out_path: str = "") -> dict:
        # Both converters require a log callback; the old code omitted it,
        # so every "Convert to PDF" raised TypeError before doing anything.
        try:
            if out_path:
                _check(_fo.word_to_pdf(path, out_path, _noop_log), "Convert failed")
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

    def find_duplicate_pages(self, path: str) -> dict:
        try:
            dupes = _fo.find_duplicate_pages(path)
            return _ok(duplicates=dupes)
        except Exception as exc:
            return _err(str(exc))

    def crop_image(self, path: str, x: int, y: int, w: int, h: int, out_path: str = "") -> dict:
        try:
            ok, result = _fo.crop_image(path, int(x), int(y), int(w), int(h), out_path)
            return _ok(out=result) if ok else _err(result)
        except Exception as exc:
            return _err(str(exc))

    def rotate_image(self, path: str, degrees: int = 90, out_path: str = "") -> dict:
        try:
            ok, result = _fo.rotate_image(path, int(degrees), out_path)
            return _ok(out=result) if ok else _err(result)
        except Exception as exc:
            return _err(str(exc))

    def flip_image(self, path: str, direction: str = "horizontal", out_path: str = "") -> dict:
        try:
            ok, result = _fo.flip_image(path, direction, out_path)
            return _ok(out=result) if ok else _err(result)
        except Exception as exc:
            return _err(str(exc))

    def adjust_image_save(
        self, path: str,
        brightness: float = 1.0, contrast: float = 1.0,
        out_path: str = "", overwrite: bool = False,
    ) -> dict:
        try:
            ok, result = _fo.adjust_image_save(
                path, float(brightness), float(contrast), out_path, bool(overwrite)
            )
            return _ok(out=result) if ok else _err(result)
        except Exception as exc:
            return _err(str(exc))

    def excel_to_csv(self, path: str, out_dir: str = "") -> dict:
        try:
            if not out_dir:
                out_dir = os.path.dirname(path)
            ok, result = _fo.excel_to_csv_each_sheet(path, out_dir)
            if ok:
                return _ok(files=result)
            return _err(str(result))
        except Exception as exc:
            return _err(str(exc))

    def move_to_root(self, paths: list, root: str) -> dict:
        try:
            ok, moved, errors = _fo.move_to_folder_root(paths, root)
            return _ok(moved=moved, errors=errors)
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


    def resize_photo_to_mm(
        self,
        path: str,
        w_mm: float = 35.0,
        h_mm: float = 45.0,
        dpi: float = 300.0,
        out_path: str = "",
        crop_mode: str = "center",
    ) -> dict:
        """Resize/crop image to exact physical dimensions (mm @ dpi).
        crop_mode: 'center' (crop to ratio, then resize) | 'fit' (letterbox/pad)."""
        try:
            ok, result = _fo.resize_photo_to_mm(
                path, w_mm=float(w_mm), h_mm=float(h_mm), dpi=float(dpi),
                out_path=out_path, crop_mode=crop_mode,
            )
            if ok:
                return _ok(out=result)
            return _err(result)
        except Exception as exc:
            return _err(str(exc))


    def check_passport_photo(self, path: str) -> dict:
        try:
            results = _fo.check_passport_photo_quality(path)
            return _ok(results=results)
        except Exception as exc:
            return _err(str(exc))

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

    def make_ocr_searchable(self, path: str) -> dict:
        def _run():
            try:
                _check(_fo.pdf_make_searchable_ocr_inplace(path), "OCR failed")
                self._emit("ocr_searchable_done", {"ok": True, "path": path})
            except Exception as exc:
                self._emit("ocr_searchable_done", {"ok": False, "error": str(exc)})
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
                    out = _check(_fo.convert_file_to_pdf_replace(p, _noop_log),
                                 "Convert failed")
                    results.append({"path": p, "ok": True, "out": str(out)})
                except Exception as exc:
                    results.append({"path": p, "ok": False, "error": str(exc)})
                self._emit("convert_pdf_progress", {"results": results})
            self._emit("convert_pdf_done", {"ok": True, "results": results})
        self._thread(_run)
        return _ok(started=True)

    def generate_report(self, folder: str) -> dict:
        def _run():
            try:
                log_cb = self._log_cb("report_log")
                out = _fo.generate_checklist_report(folder, log=log_cb)
                self._emit("report_done", {"ok": True, "path": str(out)})
            except Exception as exc:
                self._emit("report_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    # =========================================================================
    # ZIP
    # =========================================================================

    def zip_paths(self, paths: list, out_path: str = "") -> dict:
        # Zips the SELECTED paths. (Previously, with no out_path, this zipped
        # every immediate subfolder of the parent folder — ignoring the
        # actual selection entirely.)
        def _run():
            try:
                import shutil
                import zipfile as _zf
                made = []
                if out_path:
                    # zip a single selection to out_path
                    base = os.path.splitext(out_path)[0]
                    shutil.make_archive(base, "zip", root_dir=os.path.dirname(paths[0]),
                                        base_dir=os.path.basename(paths[0]))
                    made.append(base + ".zip")
                else:
                    # one sibling <name>.zip per selected path (never overwrite)
                    for p in paths:
                        name = os.path.basename(p.rstrip("\\/"))
                        stem = name if os.path.isdir(p) else os.path.splitext(name)[0]
                        base = os.path.join(os.path.dirname(p), stem)
                        tgt, i = base + ".zip", 2
                        while os.path.exists(tgt):
                            tgt = f"{base}-{i}.zip"
                            i += 1
                        if os.path.isdir(p):
                            shutil.make_archive(tgt[:-4], "zip",
                                                root_dir=os.path.dirname(p),
                                                base_dir=name)
                        else:
                            with _zf.ZipFile(tgt, "w", _zf.ZIP_DEFLATED) as zf:
                                zf.write(p, name)
                        made.append(tgt)
                self._emit("zip_done", {"ok": True, "files": made})
            except Exception as exc:
                self._emit("zip_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def unzip(self, path: str) -> dict:
        def _run():
            try:
                out = _fo.extract_zip_to_sibling_folder(path)
                self._emit("unzip_done", {"ok": True, "dir": str(out)})
            except Exception as exc:
                self._emit("unzip_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def unzip_all(self, paths: list) -> dict:
        # NOTE: paths is a list of ZIP file paths (multi-select), so extract
        # each one individually. (Previously this passed the list straight to
        # extract_all_zips_to_sibling_folders(), which expects a single folder
        # path — every "Extract All…" call failed silently.)
        def _run():
            try:
                stats = {"extracted": 0, "skipped": 0, "failed": 0}
                errors: list = []
                for p in paths:
                    r = _fo.extract_zip_to_sibling_folder(p)
                    for k in stats:
                        stats[k] += int(r.get(k, 0) or 0)
                    errors.extend(r.get("errors", []))
                ok = stats["failed"] == 0
                payload = {"ok": ok, **stats}
                if errors:
                    payload["error"] = "; ".join(str(e) for e in errors)
                # UI listens on 'unzip_done' (toast + refresh); emit both.
                self._emit("unzip_done", payload)
                self._emit("unzip_all_done", payload)
            except Exception as exc:
                self._emit("unzip_done", {"ok": False, "error": str(exc)})
                self._emit("unzip_all_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def list_zip(self, path: str) -> dict:
        try:
            children = _fo.zip_list_immediate_children(path)
            return _ok(children=children)
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # TRANSLATE / QR
    # =========================================================================

    def translate_to_korean(self, text: str) -> dict:
        try:
            result = _fo.translate_to_korean_openai(text)
            return _ok(text=result)
        except Exception as exc:
            return _err(str(exc))

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

    # =========================================================================
    # EXPIRY HEATMAP + CHECKLIST
    # =========================================================================

    def get_expiry_heatmap(self, folder: str) -> dict:
        try:
            data = _fo.get_expiry_heatmap_data(folder)
            return _ok(data=data)
        except Exception as exc:
            return _err(str(exc))

    def get_checklist_labels(self) -> dict:
        try:
            labels = _fo.get_checklist_document_labels()
            return _ok(labels=labels)
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # APOSTILLE MATCHER
    # =========================================================================

    def process_apostille(
        self,
        urls: list,
        local_folder: str,
        output_folder: str,
        api_key: str = "",
    ) -> dict:
        """
        Async: runs apostille_matcher.process_apostille_batch() in a thread.
        Events:  apostille_log, apostille_result, apostille_done
        """
        def _run():
            try:
                import apostille_matcher as am
                if api_key:
                    os.environ.setdefault("OPENAI_API_KEY", api_key)
                def _progress(i, total, r):
                    self._emit("apostille_result", {"index": i, "total": total, "result": r})
                results = am.process_apostille_batch(
                    urls, local_folder, output_folder,
                    api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
                    log=self._log_cb("apostille_log"),
                    progress=_progress,
                )
                self._emit("apostille_done", {"ok": True, "results": results})
            except Exception as exc:
                self._emit("apostille_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def scan_apostille_refs(self, folder: str) -> dict:
        try:
            import apostille_matcher as am
            refs = am.scan_folder_for_apostille_refs(folder)
            # refs is {pdf_path: [url, ...]}
            flat = []
            for pdf_path, urls in refs.items():
                for url in urls:
 
                    flat.append({"pdf": pdf_path, "url": url})
            return _ok(refs=flat)
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # WATCH FOLDER SERVICE
    # =========================================================================

    def watch_start(self, folder: str, output_folder: str = "") -> dict:
        try:
            from watch_folder_service import WatchFolderService
            if self._watch_svc is None:
                self._watch_svc = WatchFolderService(
                    folder,
                    output_folder or folder,
                    log=self._log_cb("watch_log"),
                )
                self._watch_svc.start()
            return _ok(folder=folder)
        except Exception as exc:
            return _err(str(exc))

    def watch_stop(self) -> dict:
        try:
            if self._watch_svc:
                self._watch_svc.stop()
                self._watch_svc = None
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def watch_get_entries(self) -> dict:
        try:
            if not self._watch_svc:
                return _ok(entries=[])
            return _ok(entries=self._watch_svc.get_entries())
        except Exception as exc:
            return _err(str(exc))

    def watch_clear_completed(self) -> dict:
        try:
            if self._watch_svc:
                self._watch_svc.clear_completed()
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    # =========================================================================
    # STUDENT FOLDER MAKER
    # =========================================================================

    def create_student_folders(self, names: list, dest: str) -> dict:
        """Create one subfolder per name inside dest. Emits folder_create_done."""
        def _run():
            try:
                created, skipped = [], []
                for name in names:
                    name = str(name).strip()
                    if not name:
                        continue
                    folder = os.path.join(dest, name)
                    if os.path.exists(folder):
                        skipped.append(name)
                    else:
                        os.makedirs(folder, exist_ok=True)
                        created.append(name)
                self._emit("folder_create_done",
                           {"ok": True, "created": created, "skipped": skipped})
            except Exception as exc:
                self._emit("folder_create_done", {"ok": False, "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def list_printers(self) -> dict:
        try:
            import subprocess
            out = subprocess.check_output(
                ["powershell", "-Command",
                 "Get-Printer | Select-Object -ExpandProperty Name"],
                text=True, timeout=5
            )
            printers = [p.strip() for p in out.strip().splitlines() if p.strip()]
            return _ok(printers=printers)
        except Exception as exc:
            return _err(str(exc))

    def print_pdf(self, path: str, printer: str = "") -> dict:
        try:
            import subprocess
            if printer:
                cmd = ["powershell", "-Command",
                       f'$printer="{printer}"; $pdf="{path}"; '
                       r'$shell=New-Object -ComObject Shell.Application; '
                       r'$item=$shell.Namespace((Split-Path $pdf)).ParseName((Split-Path $pdf -Leaf)); '
                       r'$item.InvokeVerb("Print")']
            else:
                cmd = ["powershell", "-Command",
                       f'Start-Process -FilePath "{path}" -Verb Print']
            subprocess.Popen(cmd)
            return _ok(started=True)
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
            safe = {k: v for k, v in s.items() if k not in ("openai_api_key", "cloud_token")}
            if "openai_api_key" in s and s["openai_api_key"]:
                safe["has_api_key"] = True
            else:
                safe["has_api_key"] = False
            return _ok(settings=safe)
        except Exception as exc:
            return _err(str(exc))

    def save_settings(self, settings: dict) -> dict:
        try:
            # theme and similar UI settings — store directly
            allowed = {"theme", "last_folder", "zoom", "panel_layout"}
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

    def run_ai_photo_action(self, path: str, action: str, opts: dict = None) -> dict:
        opts = opts or {}
        def _run():
            try:
                from ai_photo_editor import AIPhotoEditor
                editor = AIPhotoEditor(path)
                result = editor.run_action(action, **opts)
                if result and result.get("ok"):
                    self._emit("ai_photo_result", result)
                else:
                    self._emit("ai_photo_result", {"ok": False, "error": result.get("error", "Unknown error") if result else "No result"})
            except ImportError:
                self._emit("ai_photo_result", {"ok": False, "error": "ai_photo_editor module not found"})
            except Exception as exc:
                self._emit("ai_photo_result", {"ok": False, "error": str(exc)})
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
    # ACCOUNT / CLOUD
    # Sign-in, monthly subscription and cloud storage via the PDF Axe account
    # server (server/app.py), using cloud_client.py. The session token lives in
    # sfm_settings.json ("cloud_token") and is never returned to JS.
    #
    # Events pushed to JS:
    #   account_changed        {reason}             sign-in state / plan changed
    #   cloud_event            {event, data}        every live event from /events
    #   cloud_live             {connected}          live stream connected or not
    #   cloud_upload_progress  {done, total, name}
    #   cloud_upload_done      {ok, uploaded, failed, errors}
    #   cloud_download_done    {ok, downloaded, failed, errors, paths, dest_dir}
    #   cloud_sync_done        {folder, uploaded, downloaded, conflicts, errors, time}
    #   cloud_sync_log         {log}
    # =========================================================================

    _cloud_lock = threading.RLock()
    _cloud_stream = None          # cloud_client.EventStream
    _cloud_stream_up = False
    _cloud_sync_worker = None     # cloud_client.SyncWorker
    _cloud_sync_folder = ""
    _cloud_sync_last = None
    _cloud_user_cache = None      # last user dict seen from the server

    def _cloud_server_url(self) -> str:
        url = self._load_settings().get("server_url") or os.environ.get("SFM_SERVER_URL", "")
        return str(url or "").strip().rstrip("/")

    def _cloud_token(self) -> str:
        return str(self._load_settings().get("cloud_token") or "")

    def _cloud_client(self, timeout: float = 20.0):
        from cloud_client import CloudClient
        return CloudClient(self._cloud_server_url(), self._cloud_token(), timeout=timeout)

    @staticmethod
    def _cloud_err(exc, **kw) -> dict:
        status = getattr(exc, "status", 0) or 0
        extra = {"status": status}
        if status == 402:
            extra["need_subscription"] = True
        if status == 401:
            extra["need_login"] = True
        if status == 0:
            extra["offline"] = True
        extra.update(kw)
        return _err(str(exc), **extra)

    def _cloud_clear_session(self, reason: str = "signed_out") -> None:
        self._cloud_stop_services()
        self._cloud_user_cache = None
        try:
            self._save_settings({"cloud_token": ""})
        except Exception as exc:
            _log.error("cloud: could not clear token: %s", exc)
        self._emit("account_changed", {"reason": reason})

    @staticmethod
    def _cloud_is_active(user) -> bool:
        try:
            return bool(user and user.get("subscription", {}).get("active"))
        except Exception:
            return False

    # ── live events ──────────────────────────────────────────────────────────

    def _cloud_on_event(self, name: str, data) -> None:
        self._emit("cloud_event", {"event": name, "data": data})
        if name == "subscription_updated":
            if isinstance(self._cloud_user_cache, dict) and isinstance(data, dict):
                self._cloud_user_cache = {**self._cloud_user_cache, "subscription": data}
            self._emit("account_changed", {"reason": "subscription_updated", "subscription": data})
            if isinstance(data, dict) and data.get("active"):
                self._thread(self._cloud_resume_sync)
            else:
                self._cloud_stop_sync_worker()
        elif name == "file_updated":
            w = self._cloud_sync_worker
            if w is not None:
                w.poke()
        elif name == "session_expired":
            self._thread(self._cloud_clear_session, "session_expired")

    def _cloud_on_status(self, connected: bool) -> None:
        if bool(connected) != self._cloud_stream_up:
            self._cloud_stream_up = bool(connected)
            self._emit("cloud_live", {"connected": bool(connected)})

    def _cloud_start_stream(self) -> None:
        from cloud_client import EventStream
        with self._cloud_lock:
            s = self._cloud_stream
            if s is not None and not s.stopped:
                return
            client = self._cloud_client()
            if not client.base_url or not client.token:
                return
            self._cloud_stream = EventStream(client, self._cloud_on_event, self._cloud_on_status).start()
            _log.info("cloud: event stream started")

    def _cloud_stop_services(self) -> None:
        with self._cloud_lock:
            s, self._cloud_stream = self._cloud_stream, None
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass
        self._cloud_on_status(False)
        self._cloud_stop_sync_worker()

    def _cloud_ensure_services(self, user=None) -> None:
        """Start the live stream, and resume folder sync when the plan is active."""
        self._cloud_start_stream()
        if self._cloud_is_active(user if user is not None else self._cloud_user_cache):
            self._cloud_resume_sync()

    # ── folder sync helpers ──────────────────────────────────────────────────

    def _cloud_resume_sync(self) -> None:
        folder = str(self._load_settings().get("cloud_sync_folder") or "")
        if folder and os.path.isdir(folder) and self._cloud_token():
            self._cloud_start_sync_worker(folder)

    def _cloud_start_sync_worker(self, folder: str) -> None:
        from cloud_client import SyncFolder, SyncWorker
        folder = os.path.abspath(folder)
        with self._cloud_lock:
            w = self._cloud_sync_worker
            if w is not None and self._cloud_sync_folder == folder:
                return
            if w is not None:
                w.stop()

            def _done(stats, _folder=folder):
                self._cloud_sync_last = {**stats, "time": time.time()}
                self._emit("cloud_sync_done", {"folder": _folder, **self._cloud_sync_last})

            sync = SyncFolder(self._cloud_client(), folder,
                              log=lambda m: self._emit("cloud_sync_log", {"log": str(m)}))
            self._cloud_sync_folder = folder
            self._cloud_sync_last = None
            self._cloud_sync_worker = SyncWorker(sync, interval=60.0, on_done=_done).start()
            _log.info("cloud: sync worker started for %s", folder)

    def _cloud_stop_sync_worker(self) -> None:
        with self._cloud_lock:
            w, self._cloud_sync_worker = self._cloud_sync_worker, None
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass

    def _require_plan(self):
        """Gate for paid features. Returns None when allowed, otherwise an _err dict.

        With no account server configured the app runs unrestricted. With one
        configured, the signed-in user's subscription must be active.
        """
        if not self._cloud_server_url():
            return None
        if not self._cloud_token():
            return _err("An active subscription is required", need_subscription=True, need_login=True)
        try:
            user = self._cloud_client(timeout=8).me()
            self._cloud_user_cache = user
        except Exception as exc:
            if getattr(exc, "status", None) == 401:
                self._cloud_clear_session("session_expired")
                return _err("An active subscription is required", need_subscription=True, need_login=True)
            user = self._cloud_user_cache   # offline: trust the last known state
        if self._cloud_is_active(user):
            return None
        return _err("An active subscription is required", need_subscription=True)

    # ── account ──────────────────────────────────────────────────────────────

    def account_get_state(self) -> dict:
        try:
            url = self._cloud_server_url()
            st = {"configured": bool(url), "server_url": url, "logged_in": False,
                  "user": None, "offline": False, "live": self._cloud_stream_up}
            if not url or not self._cloud_token():
                return _ok(**st)
            from cloud_client import CloudError
            try:
                user = self._cloud_client(timeout=8).me()
            except CloudError as exc:
                if exc.status == 401:
                    self._cloud_clear_session("session_expired")
                    return _ok(**st)
                if exc.status == 0:
                    st.update(logged_in=True, offline=True, user=self._cloud_user_cache)
                    self._cloud_start_stream()   # reconnects on its own when back
                    return _ok(**st)
                return self._cloud_err(exc, **st)
            self._cloud_user_cache = user
            st.update(logged_in=True, user=user)
            self._cloud_ensure_services(user)
            st["live"] = self._cloud_stream_up
            return _ok(**st)
        except Exception as exc:
            return _err(str(exc))

    def account_set_server(self, url: str) -> dict:
        try:
            url = str(url or "").strip().rstrip("/")
            if url and "://" not in url:
                host = url.split("/")[0].split(":")[0].lower()
                local = host in ("localhost", "127.0.0.1") or host.startswith(("192.168.", "10."))
                url = ("http://" if local else "https://") + url
            if url and not url.lower().startswith(("http://", "https://")):
                return _err("Server address must start with http:// or https://")
            if url != self._cloud_server_url():
                # A token belongs to one server: sign out of the old one locally
                had_token = bool(self._cloud_token())
                self._cloud_stop_services()
                self._cloud_user_cache = None
                self._save_settings({"server_url": url, "cloud_token": ""})
                if had_token:
                    self._emit("account_changed", {"reason": "server_changed"})
            reachable = False
            if url:
                try:
                    self._cloud_client(timeout=5).health()
                    reachable = True
                except Exception:
                    reachable = False
            return _ok(server_url=url, configured=bool(url), reachable=reachable)
        except Exception as exc:
            return _err(str(exc))

    def _account_auth(self, kind: str, email: str, password: str) -> dict:
        from cloud_client import CloudError
        email = str(email or "").strip()
        password = str(password or "")
        if not self._cloud_server_url():
            return _err("Set the server address first")
        if not email or not password:
            return _err("Enter your email and password")
        try:
            import platform
            client = self._cloud_client()
            client.token = ""
            fn = client.register if kind == "register" else client.login
            r = fn(email, password, device=platform.node() or "desktop")
        except CloudError as exc:
            return self._cloud_err(exc)
        self._cloud_stop_services()
        self._save_settings({"cloud_token": client.token})
        user = r.get("user")
        self._cloud_user_cache = user
        self._cloud_ensure_services(user)
        self._emit("account_changed", {"reason": kind})
        return _ok(user=user)

    def account_register(self, email: str, password: str) -> dict:
        try:
            return self._account_auth("register", email, password)
        except Exception as exc:
            return _err(str(exc))

    def account_login(self, email: str, password: str) -> dict:
        try:
            return self._account_auth("login", email, password)
        except Exception as exc:
            return _err(str(exc))

    def account_logout(self) -> dict:
        try:
            self._cloud_stop_services()
            try:
                self._cloud_client(timeout=8).logout()
            except Exception as exc:   # still sign out locally when offline
                _log.info("cloud: server logout failed: %s", exc)
            self._cloud_clear_session("signed_out")
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    # ── billing ──────────────────────────────────────────────────────────────

    def _billing_open(self, which: str) -> dict:
        from cloud_client import CloudError
        try:
            c = self._cloud_client()
            url = c.checkout_url() if which == "checkout" else c.portal_url()
        except CloudError as exc:
            return self._cloud_err(exc)
        import webbrowser
        webbrowser.open_new_tab(url)
        return _ok(url=url)

    def billing_open_checkout(self) -> dict:
        try:
            return self._billing_open("checkout")
        except Exception as exc:
            return _err(str(exc))

    def billing_open_portal(self) -> dict:
        try:
            return self._billing_open("portal")
        except Exception as exc:
            return _err(str(exc))

    # ── cloud files ──────────────────────────────────────────────────────────

    def cloud_list(self, trashed: bool = False) -> dict:
        from cloud_client import CloudError
        try:
            files = self._cloud_client().list_files(trashed=bool(trashed))
            for f in files:
                f["size_str"] = _fmt_size(int(f.get("size") or 0))
            return _ok(files=files, trashed=bool(trashed))
        except CloudError as exc:
            return self._cloud_err(exc)
        except Exception as exc:
            return _err(str(exc))

    def cloud_usage(self) -> dict:
        from cloud_client import CloudError
        try:
            u = self._cloud_client().usage()
            used, quota = int(u.get("used") or 0), int(u.get("quota") or 0)
            return _ok(used=used, quota=quota, used_str=_fmt_size(used), quota_str=_fmt_size(quota))
        except CloudError as exc:
            return self._cloud_err(exc)
        except Exception as exc:
            return _err(str(exc))

    def cloud_pick_files(self) -> dict:
        """Native multi-select open dialog (all file types) for cloud upload."""
        try:
            import webview
            win = self._window
            if win is None:
                return _err("Window not ready")
            result = win.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                            file_types=("All files (*.*)",))
            if result:
                return _ok(paths=[os.path.normpath(p) for p in result])
            return _ok(paths=[], cancelled=True)
        except Exception as exc:
            return _err(str(exc))

    def cloud_upload(self, paths: list, remote_dir: str = "") -> dict:
        from cloud_client import CloudError
        paths = [str(p) for p in (paths or []) if p]
        if not paths:
            return _err("Nothing selected to upload")
        base = "/".join(s for s in str(remote_dir or "").replace("\\", "/").split("/") if s and s != ".")

        def _jobs():
            for p in paths:
                p = os.path.abspath(p)
                if os.path.isfile(p):
                    yield p, os.path.basename(p)
                elif os.path.isdir(p):
                    top = os.path.basename(p.rstrip("\\/"))
                    for root, dirs, files in os.walk(p):
                        dirs[:] = [d for d in dirs
                                   if not d.startswith(".") and d not in ("_to_review", "__pycache__")]
                        for name in files:
                            if name.startswith((".", "~$")) or name.endswith((".part", ".tmp")):
                                continue
                            full = os.path.join(root, name)
                            rel = os.path.relpath(full, p).replace(os.sep, "/")
                            yield full, f"{top}/{rel}"

        def _run():
            uploaded, errors, total = 0, [], 0
            try:
                jobs = list(_jobs())
                total = len(jobs)
                client = self._cloud_client()
                for i, (full, rel) in enumerate(jobs, 1):
                    remote = f"{base}/{rel}" if base else rel
                    self._emit("cloud_upload_progress", {"done": i - 1, "total": total, "name": rel})
                    try:
                        client.upload(full, remote)
                        uploaded += 1
                    except CloudError as exc:
                        errors.append({"path": full, "error": str(exc)})
                        if exc.status in (0, 401, 402, 507):
                            break
                    except OSError as exc:
                        errors.append({"path": full, "error": str(exc)})
                self._emit("cloud_upload_done", {"ok": not errors, "uploaded": uploaded,
                                                 "failed": len(errors), "total": total,
                                                 "errors": errors[:50]})
            except Exception as exc:
                self._emit("cloud_upload_done", {"ok": False, "uploaded": uploaded,
                                                 "failed": len(errors) + 1, "total": total,
                                                 "errors": errors[:50], "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    @staticmethod
    def _cloud_unique_path(path: str) -> str:
        """Never overwrite: 'a.pdf' → 'a (2).pdf', 'a (3).pdf', …"""
        if not os.path.exists(path):
            return path
        stem, ext = os.path.splitext(path)
        n = 2
        while os.path.exists(f"{stem} ({n}){ext}"):
            n += 1
        return f"{stem} ({n}){ext}"

    def cloud_download(self, file_ids: list, dest_dir: str) -> dict:
        from cloud_client import CloudError
        ids = []
        for i in (file_ids or []):
            try:
                ids.append(int(i))
            except (TypeError, ValueError):
                pass
        if not ids:
            return _err("Nothing selected to download")
        if not dest_dir or not os.path.isdir(dest_dir):
            return _err("Choose an existing folder to download into")

        def _run():
            done, errors, saved = 0, [], []
            try:
                client = self._cloud_client()
                by_id = {f["id"]: f for f in client.list_files()}
                for fid in ids:
                    f = by_id.get(fid)
                    if not f:
                        errors.append({"id": fid, "error": "File not found in the cloud"})
                        continue
                    name = f["path"].replace("\\", "/").split("/")[-1] or f"file-{fid}"
                    dest = self._cloud_unique_path(os.path.join(dest_dir, name))
                    try:
                        client.download(fid, dest)
                        done += 1
                        saved.append(dest)
                    except CloudError as exc:
                        errors.append({"id": fid, "path": f["path"], "error": str(exc)})
                        if exc.status in (0, 401, 402):
                            break
                    except OSError as exc:
                        errors.append({"id": fid, "path": f["path"], "error": str(exc)})
                self._emit("cloud_download_done", {"ok": not errors, "downloaded": done,
                                                   "failed": len(errors), "errors": errors[:50],
                                                   "paths": saved, "dest_dir": dest_dir})
            except Exception as exc:
                self._emit("cloud_download_done", {"ok": False, "downloaded": done,
                                                   "failed": len(errors) + 1, "errors": errors[:50],
                                                   "paths": saved, "dest_dir": dest_dir,
                                                   "error": str(exc)})
        self._thread(_run)
        return _ok(started=True)

    def _cloud_each(self, ids: list, op: str) -> dict:
        from cloud_client import CloudError
        try:
            client = self._cloud_client()
            fn = client.trash if op == "trash" else client.restore
            done, errors = 0, []
            for i in (ids or []):
                try:
                    fn(int(i))
                    done += 1
                except CloudError as exc:
                    errors.append({"id": i, "error": str(exc)})
                    if exc.status in (0, 401, 402):
                        break
                except (TypeError, ValueError):
                    errors.append({"id": i, "error": "Invalid file id"})
            if errors and not done:
                return _err(errors[0]["error"], done=0, errors=errors)
            return _ok(done=done, errors=errors)
        except Exception as exc:
            return _err(str(exc))

    def cloud_trash(self, ids: list) -> dict:
        """Move cloud files to the cloud trash (restorable; nothing is erased)."""
        return self._cloud_each(ids, "trash")

    def cloud_restore(self, ids: list) -> dict:
        return self._cloud_each(ids, "restore")

    # ── auto-sync folder ─────────────────────────────────────────────────────

    def cloud_sync_start(self, local_folder: str) -> dict:
        try:
            folder = os.path.abspath(str(local_folder).strip()) if local_folder else ""
            if not folder or not os.path.isdir(folder):
                return _err("Choose an existing folder to sync")
            if not self._cloud_token():
                return _err("Sign in first", need_login=True)
            gate = self._require_plan()
            if gate:
                return gate
            self._save_settings({"cloud_sync_folder": folder})
            self._cloud_start_sync_worker(folder)
            self._cloud_start_stream()
            w = self._cloud_sync_worker
            return _ok(folder=folder, remote_root=w.sync.remote_root if w else "")
        except Exception as exc:
            return _err(str(exc))

    def cloud_sync_stop(self) -> dict:
        try:
            self._cloud_stop_sync_worker()
            self._save_settings({"cloud_sync_folder": ""})
            return _ok()
        except Exception as exc:
            return _err(str(exc))

    def cloud_sync_status(self) -> dict:
        try:
            w = self._cloud_sync_worker
            folder = str(self._load_settings().get("cloud_sync_folder") or "")
            return _ok(running=w is not None,
                       folder=(self._cloud_sync_folder if w else folder),
                       remote_root=(w.sync.remote_root if w else ""),
                       last=self._cloud_sync_last, live=self._cloud_stream_up)
        except Exception as exc:
            return _err(str(exc))
