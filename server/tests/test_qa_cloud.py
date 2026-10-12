"""
Regression tests for bugs found in the accounts / cloud / office QA pass (qa/cloud.md).

Run:  python -m pytest server/tests/test_qa_cloud.py -q
"""
from __future__ import annotations

import os

os.environ["SFM_SERVER_NO_AUTOAPP"] = "1"

from server.tests.test_hardening import _sub_client, _up  # noqa: E402


def _paths(c, h, trashed=False):
    return sorted(f["path"] for f in c.get("/files", headers=h,
                                            params={"trashed": trashed}).json()["files"])


# ── cloud folders are case-sensitive (SQLite LIKE is not) ────────────────────

def test_folder_trash_does_not_touch_other_case_folder(tmp_path):
    c, h = _sub_client(tmp_path)
    _up(c, h, "Work/a.txt")
    _up(c, h, "work/b.txt")
    assert c.post("/folders/trash", headers=h, json={"path": "Work"}).json()["trashed"] == 1
    assert _paths(c, h) == ["work/b.txt"]


def test_folder_move_does_not_take_other_case_folder(tmp_path):
    c, h = _sub_client(tmp_path)
    _up(c, h, "Work/a.txt")
    _up(c, h, "work/b.txt")
    r = c.post("/folders/move", headers=h, json={"path": "Work", "new_path": "Archive"})
    assert r.status_code == 200 and r.json()["moved"] == 1
    assert _paths(c, h) == ["Archive/a.txt", "work/b.txt"]


def test_folder_move_up_into_parent_with_same_names(tmp_path):
    # "a/a" → "a": "a/a/a/x" becomes "a/a/x", a name "a/a/x" is leaving at the same time
    c, h = _sub_client(tmp_path)
    _up(c, h, "a/a/x")
    _up(c, h, "a/a/a/x")
    r = c.post("/folders/move", headers=h, json={"path": "a/a", "new_path": "a"})
    assert r.status_code == 200, r.text
    assert _paths(c, h) == ["a/a/x", "a/x"]
