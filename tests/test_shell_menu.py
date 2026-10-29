# -*- coding: utf-8 -*-
"""File Explorer right-click menu (shell_menu.py) and the hand-over of files
from Explorer to the running app (shell_ipc.py). The registry is faked."""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shell_ipc  # noqa: E402
import shell_menu  # noqa: E402


class FakeReg:
    """Just enough of winreg: keys are paths, values are dicts."""
    HKEY_CURRENT_USER = "HKCU"
    KEY_WRITE = KEY_READ = 0
    REG_SZ = 1

    def __init__(self):
        self.keys = {}

    class _K:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def CreateKeyEx(self, root, path, res=0, access=0):
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]), {})
        return self._K(self, path)

    def OpenKey(self, root, path, res=0, access=0):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self._K(self, path)

    def SetValueEx(self, k, name, res, typ, val):
        self.keys[k.path][name] = val

    def QueryValueEx(self, k, name):
        if name not in self.keys[k.path]:
            raise FileNotFoundError(name)
        return self.keys[k.path][name], self.REG_SZ

    def DeleteKey(self, root, path):
        if any(p.startswith(path + "\\") for p in self.keys):
            raise PermissionError("has subkeys")
        if path not in self.keys:
            raise FileNotFoundError(path)
        del self.keys[path]


PREFIX = [r"C:\Apps\OfficeAxe\OfficeAxe.exe"]


def test_register_writes_the_four_verbs_only_under_hkcu_classes():
    reg = FakeReg()
    n = shell_menu.register(PREFIX, icon="", reg=reg)
    assert n == len(shell_menu.entries(PREFIX, ""))
    assert all(k == "Software" or k.startswith(r"Software\Classes") for k in reg.keys)   # never HKLM / elsewhere
    labels = {v[""] for k, v in reg.keys.items() if not k.endswith("command") and "OfficeAxe." in k}
    assert labels == {"Open in Office Axe", "Convert to PDF with Office Axe", "Compress with Office Axe",
                      "Merge into one PDF (Office Axe)"}
    cmd = reg.keys[r"Software\Classes\SystemFileAssociations\.pdf\shell\OfficeAxe.Merge\command"][""]
    assert cmd == r'"C:\Apps\OfficeAxe\OfficeAxe.exe" --merge "%1"'
    bg = reg.keys[r"Software\Classes\Directory\Background\shell\OfficeAxe.Open\command"][""]
    assert bg.endswith('--open "%V"')
    for ext in (".docx", ".hwp", ".xlsx", ".pptx", ".jpg"):
        assert rf"Software\Classes\SystemFileAssociations\{ext}\shell\OfficeAxe.ToPdf" in reg.keys


def test_status_and_unregister_remove_exactly_ours():
    reg = FakeReg()
    reg.CreateKeyEx(reg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\SomeoneElse\command")
    shell_menu.register(PREFIX, icon="", reg=reg)
    # status() compares commands with this process's own app command
    st = shell_menu.status(reg=reg)
    assert st["installed"] and st["count"] == st["total"]
    removed = shell_menu.unregister(reg=reg)
    assert removed == st["total"]
    assert not [k for k in reg.keys if "OfficeAxe." in k]
    assert r"Software\Classes\*\shell\SomeoneElse\command" in reg.keys      # untouched
    assert shell_menu.status(reg=reg)["count"] == 0


def test_unregister_skips_a_same_named_key_without_our_mark():
    reg = FakeReg()
    reg.CreateKeyEx(reg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\OfficeAxe.Open")
    assert shell_menu.unregister(reg=reg) == 0
    assert r"Software\Classes\*\shell\OfficeAxe.Open" in reg.keys


def test_parse_and_group_args():
    a = shell_ipc.parse_args(["--merge", r"C:\x\a.pdf", "--junk", "--merge", r"C:\x\b.pdf", "--open"])
    assert [x["action"] for x in a] == ["merge", "merge"]
    g = shell_ipc.group(a + [{"action": "merge", "path": r"C:\x\a.pdf"}, {"action": "open", "path": r"C:\y"}])
    assert g == [{"action": "merge", "paths": [os.path.abspath(r"C:\x\a.pdf"), os.path.abspath(r"C:\x\b.pdf")]},
                 {"action": "open", "paths": [os.path.abspath(r"C:\y")]}]


def test_second_launches_hand_files_to_the_first_and_are_grouped():
    got, done = [], threading.Event()
    port = 48700 + os.getpid() % 200

    def deliver(groups):
        got.extend(groups)
        done.set()
    server, fwd = shell_ipc.start_or_forward(["--merge", r"C:\d\one.pdf"], deliver, port=port)
    assert server is not None and fwd is False
    # Explorer starts one process per selected file: they forward and exit
    for name in ("two.pdf", "three.pdf"):
        s2, fwd2 = shell_ipc.start_or_forward(["--merge", rf"C:\d\{name}"], lambda g: None, port=port)
        assert s2 is None and fwd2 is True
    assert done.wait(5)
    assert got[0]["action"] == "merge" and len(got[0]["paths"]) == 3
