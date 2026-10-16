"""
main_webview.py — PyWebView entry point for StudentFolderMaker v2.

Run:  python main_webview.py
Build into EXE via StudentFolderMaker.spec (see Phase 11).

This file ONLY handles:
 - Creating the SFMBridge instance
 - Opening the pywebview window
 - Wiring bridge.set_window(window) after creation
 - Maximising the window on start

It does NOT touch any Tkinter code, backend logic, or file_ops.
"""
from __future__ import annotations

import os
import sys
import threading

# ── ensure project root is importable ────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── load .env early so OPENAI_API_KEY is available ───────────────────────────
def _load_dotenv() -> None:
    env_path = os.path.join(_HERE, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key  = key.strip()
            val  = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

_load_dotenv()

# ── resolve ui/index.html path ───────────────────────────────────────────────
_UI_DIR   = os.path.join(_HERE, "ui")
_INDEX    = os.path.join(_UI_DIR, "index.html")

def _ui_url() -> str:
    """Return file:// URL to index.html, works both in dev and PyInstaller bundle."""
    # When frozen by PyInstaller, _MEIPASS holds the extracted bundle dir
    base = getattr(sys, "_MEIPASS", _HERE)
    index = os.path.join(base, "ui", "index.html")
    if not os.path.isfile(index):
        # fallback: look next to this script
        index = _INDEX
    return index  # pywebview accepts a filesystem path


def _is_app_uri(uri: str) -> bool:
    """True for the app's own pages (file:// or pywebview's local server)."""
    u = (uri or "").lower()
    return (u.startswith("file:") or u.startswith("http://127.0.0.1")
            or u.startswith("http://localhost") or u.startswith("http://[::1]"))


def _allow_media_permissions(window) -> None:
    """Screen recorder: let the app's own page use the microphone / camera.

    pywebview's EdgeChromium backend does not handle WebView2's
    PermissionRequested event, so getUserMedia() would show a browser-style
    "Allow / Block" prompt (or be denied). Granted only for Microphone and
    Camera, and only for the app's own pages. Screen capture
    (getDisplayMedia) always shows Windows/WebView2's own source picker.
    Best effort — the app must start even if this fails.
    """
    if getattr(window, "_oa_media_hooked", False):
        return
    import logging
    try:
        form = window.native
        wv = getattr(getattr(form, "browser", None), "webview", None)
        if form is None or wv is None:
            return
        from System import Func, Type  # type: ignore
        from Microsoft.Web.WebView2.Core import (  # type: ignore
            CoreWebView2PermissionKind as Kind,
            CoreWebView2PermissionState as State,
        )
        kinds = (Kind.Microphone, Kind.Camera)

        def _on_permission(sender, args):
            try:
                if args.PermissionKind in kinds and _is_app_uri(str(args.Uri)):
                    args.State = State.Allow
                    logging.getLogger("sfm").info("media permission granted: %s", args.PermissionKind)
            except Exception:
                pass

        def _hook():
            core = wv.CoreWebView2
            if core is not None and not getattr(window, "_oa_media_hooked", False):
                core.PermissionRequested += _on_permission
                window._oa_media_hooked = True
                logging.getLogger("sfm").info("media permission handler installed")

        form.Invoke(Func[Type](_hook))
    except Exception as exc:  # pragma: no cover - platform specific
        logging.getLogger("sfm").warning("media permission hook failed: %s", exc)


def main() -> None:
    # Helper-process mode: the QR button re-launches this EXE with --qr-pick to
    # show the native "click a QR code" overlay (qr_pick.py). No webview here.
    if "--qr-pick" in sys.argv[1:]:
        import qr_pick
        sys.exit(qr_pick.main())
    # Screenshot region picker (screen_pick.py), same helper-process pattern.
    if "--screen-pick" in sys.argv[1:]:
        import screen_pick
        sys.exit(screen_pick.main(sys.argv[1:]))

    try:
        import webview  # type: ignore
    except ImportError:
        print(
            "ERROR: pywebview is not installed.\n"
            "Run:  pip install pywebview\n"
        )
        sys.exit(1)

    from sfm_bridge import SFMBridge

    bridge = SFMBridge()

    # ── create window ─────────────────────────────────────────────────────────
    window = webview.create_window(
        title      = "Office Axe",
        url        = _ui_url(),
        js_api     = bridge,
        width      = 1400,
        height     = 900,
        min_size   = (900, 600),
        resizable  = True,
        # text_select keeps default Chromium behaviour
        text_select = True,
        # background colour while page loads (matches our dark chrome)
        background_color = "#0e1014",
    )

    # ── inject window reference into bridge after DOM is ready ────────────────
    def _on_loaded():
        bridge.set_window(window)
        _allow_media_permissions(window)
        # Maximise — pywebview exposes this on the window object
        try:
            window.maximize()
        except Exception:
            pass

    window.events.loaded += _on_loaded

    # Belt and braces: also set the window ref when the GUI loop starts.
    # If the `loaded` event doesn't fire (pywebview version differences),
    # Python→JS events (zip_done, unzip_done, report_done, …) silently
    # stop working because bridge._window stays None.
    def _on_start():
        bridge.set_window(window)

    # ── start ─────────────────────────────────────────────────────────────────
    webview.start(
        _on_start,
        debug       = "--debug" in sys.argv,
        # Use EdgeChromium on Windows (best Fluent rendering)
        # Falls back to CEF / GTK on other platforms automatically
        gui         = "edgechromium" if sys.platform == "win32" else None,
        http_server = False,   # serve files directly, no embedded server needed
    )


if __name__ == "__main__":
    main()
