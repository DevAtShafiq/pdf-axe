"""Installed copies keep their files in %LOCALAPPDATA%\\OfficeAxe; portable copies don't move."""
import os
import sys

import app_paths


def _frozen_at(monkeypatch, folder):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", os.path.join(folder, "OfficeAxe.exe"))


def test_dev_run_is_never_installed(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_paths.data_dir(str(tmp_path)) == str(tmp_path)


def test_portable_folder_keeps_its_own_files(monkeypatch, tmp_path):
    app = tmp_path / "OfficeAxe"
    app.mkdir()
    _frozen_at(monkeypatch, str(app))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "pf"))
    assert not app_paths.is_installed()
    assert app_paths.data_dir(str(app)) == str(app)


def test_program_files_uses_localappdata(monkeypatch, tmp_path):
    pf = tmp_path / "Program Files"
    app = pf / "Office Axe"
    app.mkdir(parents=True)
    _frozen_at(monkeypatch, str(app))
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    d = app_paths.data_dir(str(app))
    assert d == str(tmp_path / "local" / "OfficeAxe")
    assert os.path.isdir(d)


def test_per_user_install_is_found_by_its_uninstaller(monkeypatch, tmp_path):
    app = tmp_path / "Programs" / "Office Axe"
    app.mkdir(parents=True)
    (app / "unins000.exe").write_bytes(b"")
    _frozen_at(monkeypatch, str(app))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "pf"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert app_paths.data_dir(str(app)) == str(tmp_path / "local" / "OfficeAxe")


def test_sibling_folder_name_is_not_program_files(monkeypatch, tmp_path):
    pf = tmp_path / "Program Files"
    app = tmp_path / "Program Files Portable" / "OfficeAxe"
    app.mkdir(parents=True)
    _frozen_at(monkeypatch, str(app))
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("ProgramW6432", str(pf))
    assert not app_paths.is_installed()
