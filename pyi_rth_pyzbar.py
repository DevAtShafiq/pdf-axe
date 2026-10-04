# PyInstaller runtime hook — load pyzbar's libzbar DLL from the bundle (onedir _internal).
from __future__ import annotations

import os
import sys


def _add_dll_dir(path: str) -> None:
    if not path or not os.path.isdir(path):
        return
    try:
        os.add_dll_directory(path)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


if getattr(sys, "frozen", False):
    base = getattr(sys, "_MEIPASS", "") or os.path.dirname(os.path.abspath(sys.executable))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for folder in (
        base,
        exe_dir,
        os.path.join(base, "pyzbar"),
        os.path.join(base, "pyzbar", "pyzbar"),
    ):
        _add_dll_dir(folder)
    # Preload zbar deps so pyzbar import succeeds before qr_screen_capture loads.
    for dll_name in ("libiconv.dll", "libiconv-2.dll", "libzbar-64.dll", "libzbar-32.dll"):
        for folder in (os.path.join(base, "pyzbar"), base, exe_dir):
            dll_path = os.path.join(folder, dll_name)
            if os.path.isfile(dll_path):
                try:
                    import ctypes

                    ctypes.CDLL(dll_path)
                except OSError:
                    pass
                break
