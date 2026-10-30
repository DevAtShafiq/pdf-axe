# -*- mode: python ; coding: utf-8 -*-
#
# StudentFolderMaker v2 — PyWebView build (onedir)
# Entry point : main_webview.py  (PyWebView + SFMBridge)
# UI assets   : ui/ folder (HTML / CSS / JS) — bundled via glob below
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
# Never bundle .env (OpenAI key, server secrets) or sfm_settings.json
# (personal settings / API key): the EXE is shared with staff.
for _fname in (
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
    ("ai_photo_editor.py",     "."),
    ("qr_screen_capture.py",   "."),
    ("rename_templates.py",    "."),   # document-name templates (F2 rename suggestions)
    ("student_folder_maker.py","."),   # lazily imported for rename templates
    ("excel_grid.py",          "."),   # imported by student_folder_maker
    ("media_convert.py",       "."),   # image<->PDF conversion + PDF/image compression
    ("pdf_tools.py",         "."),
    ("pdf_tools_bridge.py",  "."),
    ("annotate.py",          "."),   # annotation engine (PDF + image)
    ("annotate_bridge.py",   "."),
    ("pdf_edit.py",          "."),   # PDF text editing engine (Edit text)
    ("pdf_edit_bridge.py",   "."),
    ("pdf_objects.py",       "."),   # Edit: images / vector objects engine
    ("pdf_studio.py",        "."),
    ("studio_bridge.py",     "."),
    ("pdf_form.py",          "."),   # Fill form (AcroForm fields)
    ("qr_scan.py",           "."),
    ("qr_pick.py",           "."),   # QR pick overlay (helper process: EXE --qr-pick)
    ("image_crop.py",        "."),
    ("photo_size.py",        "."),   # Crop to photo size (passport / visa presets)
    ("subscription.py",      "."),   # plans, devices, payments (gateway seam)
    ("subscription_bridge.py", "."), # subscription JS API + access gate
    ("recycle_bin.py",       "."),   # Delete → Recycle Bin, Ctrl+Z restore
    ("shell_menu.py",        "."),   # File Explorer right-click menu (HKCU)
    ("shell_ipc.py",         "."),   # hand files from Explorer to the running app
    ("shell_bridge.py",      "."),
    ("license_keys.py",      "."),   # signed offline license keys
    ("ed25519_pure.py",      "."),
    ("license_client.py",    "."),   # online key checks against the portal
    ("path_input.py",        "."),   # typed / pasted paths (breadcrumb box)
    ("revoked_keys.json",    "."),   # withdrawn key ids (dashboard writes it)
    ("archive_tools.py",     "."),
    ("archive_bridge.py",    "."),
    ("recorder_bridge.py",   "."),   # screen recording + screenshots (JS bridge)
    ("doc_preview.py",       "."),   # PDF text layer + Word/Excel/Office previews
    ("doc_preview_bridge.py", "."),
    ("docx_edit.py",         "."),   # Word quick edits
    ("doc_convert.py",       "."),   # Word / HWP(X) → PDF
    ("screen_pick.py",       "."),   # screenshot region overlay (EXE --screen-pick)
    ("pyi_rth_sfm_dirs.py",    "."),
    ("pyi_rth_pyzbar.py",      "."),
] + _ui_files + _user_data

binaries: list = []

hiddenimports = [
    # ── App modules ──────────────────────────────────────────────────────────
    "file_ops",
    "ai_photo_editor",
    "qr_screen_capture",
    "rename_templates",
    "excel_grid",
    "student_folder_maker",
    "sfm_bridge",
    "main_webview",
    "media_convert",
    "pdf_tools",
    "pdf_tools_bridge",
    "annotate",
    "annotate_bridge",
    "pdf_edit",
    "pdf_edit_bridge",
    "pdf_objects",
    "pdf_studio",
    "studio_bridge",
    "pdf_form",
    "qr_scan",
    "qr_pick",
    "image_crop",
    "photo_size",
    "archive_tools",
    "archive_bridge",
    "recorder_bridge",
    "doc_preview",
    "doc_preview_bridge",
    "docx_edit",
    "doc_convert",
    "screen_pick",
    "subscription",
    "subscription_bridge",
    "recycle_bin",
    "shell_menu",
    "shell_ipc",
    "shell_bridge",
    "license_keys",
    "ed25519_pure",
    "license_client",
    "path_input",
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
    "pyzipper",
    "Cryptodome",
    "Cryptodome.Cipher.AES",
    "Cryptodome.Protocol.KDF",
    "Cryptodome.Hash.HMAC",
    "Cryptodome.Hash.SHA1",
    "Cryptodome.Util.Counter",
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
    name="OfficeAxe",
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
    name="OfficeAxe",
)
