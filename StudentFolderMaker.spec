# -*- mode: python ; coding: utf-8 -*-
#
# StudentFolderMaker v2 — PyWebView build (onedir)
# Entry point : main_webview.py  (PyWebView + SFMBridge)
# UI assets   : ui/ folder (HTML / CSS / JS) — bundled via glob below
#               (includes ui/js/account.js for the sign-in / subscription screen)
# Cloud       : cloud_client.py talks to the account server in server/
#               (the server itself is deployed separately — see server/README.md)
#
# Run once to test without building:
#   python main_webview.py
# Full rebuild:
#   rebuild.bat  (or close_and_rebuild.bat if the EXE is running)
#
# Distribute the ENTIRE  dist\StudentFolderMaker\  folder — not only the EXE.

from PyInstaller.utils.hooks import collect_all, collect_data_files
import os, glob as _glob

# ── UI assets (HTML / CSS / JS / fonts / icons) ──────────────────────────────
_ui_files = []
for _f in _glob.glob("ui/**/*", recursive=True):
    if os.path.isfile(_f):
        _dest = os.path.dirname(_f).replace("\\", "/")
        _ui_files.append((_f, _dest))

# ── User data files that must live next to the EXE ───────────────────────────
_user_data = []
for _fname in (
    ".env",
    "sfm_settings.json",
    "document_name_templates.txt",
    "sfm_rename_templates.json",
    "sfm_name_templates.json",
):
    if os.path.isfile(_fname):
        _user_data.append((_fname, "."))

datas = [
    ("icon.ico",        "."),
    ("sfm_bridge.py",   "."),
    ("main_webview.py", "."),
    ("file_ops.py",     "."),
    ("apostille_matcher.py",   "."),
    ("watch_folder_service.py","."),
    ("ai_photo_editor.py",     "."),
    ("qr_screen_capture.py",   "."),
    ("student_folder_maker.py","."),   # lazily imported for rename templates
    ("excel_grid.py",          "."),   # imported by student_folder_maker
    ("cloud_client.py",        "."),   # account server client (sign-in, cloud sync, live events)
    ("pyi_rth_sfm_dirs.py",    "."),
    ("pyi_rth_pyzbar.py",      "."),
] + _ui_files + _user_data

binaries: list = []

hiddenimports = [
    # ── App modules ──────────────────────────────────────────────────────────
    "file_ops",
    "apostille_matcher",
    "watch_folder_service",
    "ai_photo_editor",
    "qr_screen_capture",
    "excel_grid",
    "student_folder_maker",
    "sfm_bridge",
    "main_webview",
    "cloud_client",
    # ── PyWebView (EdgeChromium on Windows) ──────────────────────────────────
    "webview",
    "webview.platforms",
    "webview.platforms.winforms",
    "webview.guilib",
    "clr_loader",
    "clr",
    # ── PDF / image ──────────────────────────────────────────────────────────
    "fitz",
    "pymupdf",
    "PIL",
    "PIL.Image",
    "PIL.ImageFilter",
    "PIL.ImageOps",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    # ── Office / Excel ───────────────────────────────────────────────────────
    "docx",
    "docx.shared",
    "docx.enum.text",
    "openpyxl",
    "openpyxl.styles",
    "openpyxl.utils",
    # ── ZIP / network ────────────────────────────────────────────────────────
    "zipfile",
    "urllib.request",
    "urllib.parse",
    "urllib.error",
    "http.client",
    "mimetypes",
    "ssl",
    # ── Screen / QR ─────────────────────────────────────────────────────────
    "mss",
    "mss.tools",
    "mss.windows",
    "pyzbar",
    "pyzbar.pyzbar",
    "pyzbar.pyzbar_error",
    "pyzbar.wrapper",
    "pyzbar.zbar_library",
    # ── Windows / AI ────────────────────────────────────────────────────────
    "win32api",
    "win32con",
    "win32gui",
    "pywintypes",
    "openai",
    # ── Tkinter (pulled in by student_folder_maker / excel_grid) ─────────────
    "tkinter",
    "tkinter.ttk",
    "tkinterdnd2",
    "tksheet",
]

# ── Collect complete packages (data + binaries + hidden imports) ──────────────
for _pkg in ("pyzbar", "mss", "webview"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas    += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        pass

# ── Exclusions (never used, cuts bundle size) ─────────────────────────────────
excludes = [
    "cv2",
    "numpy",
    "matplotlib",
    "scipy",
    "pandas",
    "notebook",
    "IPython",
    "pytest",
    "setuptools",
    "distutils",
    "sphinx",
    "tkinter.test",
    "unittest",
    "xmlrpc",
    "ftplib",
    "imaplib",
    "poplib",
    "smtplib",
    "telnetlib",
    "nntplib",
    "sndhdr",
    "sunau",
    "aifc",
    "cgi",
    "cgitb",
]

a = Analysis(
    ["main_webview.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["pyi_rth_sfm_dirs.py", "pyi_rth_pyzbar.py"],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StudentFolderMaker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["webview*", "clr*", "*.dll"],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icon.ico"],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["webview*", "clr*", "*.dll"],
    name="StudentFolderMaker",
)
