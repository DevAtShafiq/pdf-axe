# PyInstaller runtime hook — runs before the main script (frozen builds only).
"""Keep temp files off C:\\Users\\...\\AppData\\Local\\Temp when the app lives on another drive."""
from __future__ import annotations

import os
import sys


def _frozen_app_dir() -> str:
    return os.path.dirname(os.path.abspath(sys.executable))


def _data_root() -> str:
    # Installed under Program Files: temp goes to %LOCALAPPDATA%\OfficeAxe.
    try:
        from app_paths import data_dir
        return data_dir(_frozen_app_dir())
    except Exception:
        return _frozen_app_dir()


if getattr(sys, "frozen", False):
    _temp_root = os.path.join(_data_root(), "_sfm_temp")
    try:
        os.makedirs(_temp_root, exist_ok=True)
        os.environ["TEMP"] = _temp_root
        os.environ["TMP"] = _temp_root
        os.environ["SFM_TEMP"] = _temp_root
    except OSError:
        pass
