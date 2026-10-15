"""New folders from a pasted list + the Copy / Move folder picker (file_ops + SFMBridge)."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import file_ops as fo  # noqa: E402
import sfm_bridge  # noqa: E402


@pytest.fixture
def b(monkeypatch, tmp_path):
    monkeypatch.setattr(sfm_bridge.SFMBridge, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    return sfm_bridge.SFMBridge()


EXCEL_PASTE = (
    "Alice Kim\tBob Lee\t\r\n"       # two cells on one row, trailing empty cell
    "\r\n"                            # blank line
    "  Carol Park  \r\n"              # spaces around
    " Dan \r\n"             # non-breaking spaces (copied from the web / Excel)
    "alice kim\r\n"                   # duplicate (case-insensitive)
    "Bad:Name\r\n"                    # invalid character
    "CON\r\n"                         # reserved
    "Trailing.\r\n"                   # trailing dot
    "\"Quoted, Cell\"\r\n"            # Excel-quoted cell
    "Existing\r\n"                    # already in the folder (different case below)
)


# ── parsing ──────────────────────────────────────────────────────────────────

def test_split_pasted_names_excel_text():
    assert fo.split_pasted_names(EXCEL_PASTE) == [
        "Alice Kim", "Bob Lee", "Carol Park", "Dan", "alice kim", "Bad:Name",
        "CON", "Trailing.", "Quoted, Cell", "Existing"]
    assert fo.split_pasted_names("") == []
    assert fo.split_pasted_names("\n\t \n") == []
    assert fo.split_pasted_names("a\rb\nc") == ["a", "b", "c"]
    assert fo.split_pasted_names("﻿First​") == ["First"]


@pytest.mark.parametrize("name,ok", [
    ("Report 2024", True), ("한국어 폴더", True), (".hidden", True),
    ("a<b", False), ("a>b", False), ('a"b', False), ("a/b", False), ("a\\b", False),
    ("a|b", False), ("a?b", False), ("a*b", False), ("a:b", False), ("a\x01b", False),
    ("CON", False), ("con", False), ("Lpt9", False), ("COM1.txt", False), ("NUL ", False),
    ("CONSOLE", True), ("name.", False), ("name ", False), (".", False), ("..", False),
    ("", False), ("x" * 256, False), ("x" * 255, True),
])
def test_folder_name_problem(name, ok):
    assert (fo.folder_name_problem(name) == "") is ok


# ── plan ─────────────────────────────────────────────────────────────────────

def test_plan_flags_invalid_duplicates_and_existing(tmp_path):
    (tmp_path / "existing").mkdir()
    plan = fo.plan_folders_from_list(str(tmp_path), EXCEL_PASTE)
    st = {r["name"]: r["status"] for r in plan["rows"]}
    assert st["Alice Kim"] == "ok" and st["Bob Lee"] == "ok" and st["Carol Park"] == "ok"
    assert st["Dan"] == "ok" and st["Quoted, Cell"] == "ok"
    assert st["alice kim"] == "duplicate"
    assert st["Bad:Name"] == "invalid" and st["CON"] == "invalid" and st["Trailing."] == "invalid"
    assert st["Existing"] == "exists"
    dup = next(r for r in plan["rows"] if r["name"] == "alice kim")
    assert dup["reason"] == "Same as row 1"
    assert plan["counts"] == {"total": 10, "ok": 5, "invalid": 3, "exists": 1, "duplicate": 1}
    assert not any(p.name != "existing" for p in tmp_path.iterdir())     # preview creates nothing


def test_plan_prefix_suffix_and_numbering(tmp_path):
    names = "\n".join(f"N{i}" for i in range(1, 13))
    plan = fo.plan_folders_from_list(str(tmp_path), names, prefix="P-", suffix="_S", numbering="before", sep=" - ")
    assert [r["name"] for r in plan["rows"]][:2] == ["01 - P-N1_S", "02 - P-N2_S"]
    assert plan["rows"][-1]["name"] == "12 - P-N12_S"
    plan = fo.plan_folders_from_list(str(tmp_path), "a\nb\nc", numbering="after", start=9, sep="_")
    assert [r["name"] for r in plan["rows"]] == ["a_09", "b_10", "c_11"]
    plan = fo.plan_folders_from_list(str(tmp_path), "a\nb", numbering="before", pad=3)
    assert [r["name"] for r in plan["rows"]] == ["001 a", "002 b"]
    # numbering makes repeated names distinct; a suffix can make a name invalid
    plan = fo.plan_folders_from_list(str(tmp_path), "x\nx", numbering="after")
    assert [r["status"] for r in plan["rows"]] == ["ok", "ok"]
    plan = fo.plan_folders_from_list(str(tmp_path), "x", suffix=".")
    assert plan["rows"][0]["status"] == "invalid"


def test_plan_too_long_path(tmp_path):
    plan = fo.plan_folders_from_list(str(tmp_path), "y" * 250)
    assert plan["rows"][0]["status"] == "invalid" and "too long" in plan["rows"][0]["reason"]


# ── create ───────────────────────────────────────────────────────────────────

def test_create_folders_from_list_creates_valid_and_skips_problems(tmp_path):
    keep = tmp_path / "Existing"
    keep.mkdir()
    (keep / "inside.txt").write_text("keep me", encoding="utf-8")
    r = fo.create_folders_from_list(str(tmp_path), EXCEL_PASTE)
    made = sorted(os.path.basename(p) for p in r["created"])
    assert made == ["Alice Kim", "Bob Lee", "Carol Park", "Dan", "Quoted, Cell"]
    assert all(os.path.isdir(p) for p in r["created"])
    assert {s["name"] for s in r["skipped"]} == {"alice kim", "Bad:Name", "CON", "Trailing.", "Existing"}
    # the existing folder is untouched (never merged / overwritten)
    assert (keep / "inside.txt").read_text(encoding="utf-8") == "keep me"
    assert sorted(os.listdir(keep)) == ["inside.txt"]


def test_create_folders_never_merges_a_folder_that_appears_after_the_preview(tmp_path, monkeypatch):
    real_plan = fo.plan_folders_from_list

    def plan_then_race(parent, text="", **kw):
        plan = real_plan(parent, text, **kw)
        os.mkdir(os.path.join(parent, "Race"))           # created by someone else meanwhile
        return plan
    monkeypatch.setattr(fo, "plan_folders_from_list", plan_then_race)
    r = fo.create_folders_from_list(str(tmp_path), "Race\nFine")
    assert [os.path.basename(p) for p in r["created"]] == ["Fine"]
    assert r["skipped"] == [{"name": "Race", "reason": "Already exists in this folder"}]


def test_create_folders_missing_parent(tmp_path):
    with pytest.raises(FileNotFoundError):
        fo.create_folders_from_list(str(tmp_path / "missing"), "a")


def test_bridge_plan_and_create(b, tmp_path):
    p = b.plan_folders_from_list(str(tmp_path), "One\tTwo\n\nOne", {"prefix": "X ", "numbering": "none"})
    assert p["ok"] and p["counts"]["ok"] == 2 and p["counts"]["duplicate"] == 1
    assert [r["name"] for r in p["rows"]] == ["X One", "X Two", "X One"]
    c = b.create_folders_from_list(str(tmp_path), "One\tTwo\n\nOne", {"prefix": "X "})
    assert c["ok"] and len(c["created"]) == 2 and len(c["skipped"]) == 1
    assert sorted(os.listdir(tmp_path)) == ["X One", "X Two"]
    again = b.create_folders_from_list(str(tmp_path), "One", {"prefix": "X "})
    assert again["ok"] and again["created"] == [] and again["skipped"][0]["reason"].startswith("Already")
    assert not b.create_folders_from_list(str(tmp_path / "nope"), "a", {})["ok"]
    assert b.plan_folders_from_list(str(tmp_path), "a", None)["ok"]


def test_bridge_create_folder_rejects_reserved_names(b, tmp_path):
    for bad in ("CON", "nul", "COM3.txt"):
        r = b.create_folder(str(tmp_path), bad)
        assert not r["ok"] and "reserved" in r["error"], bad
    assert b.create_folder(str(tmp_path), "Console")["ok"]


# ── folder picker tree ───────────────────────────────────────────────────────

def test_list_subfolders_lazy_tree(b, tmp_path):
    (tmp_path / "b10").mkdir()
    (tmp_path / "b2" / "child").mkdir(parents=True)
    (tmp_path / "a").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "$Recycle.Bin").mkdir()
    r = b.list_subfolders(str(tmp_path))
    assert r["ok"]
    assert [f["name"] for f in r["folders"]] == ["a", "b2", "b10"]
    kids = {f["name"]: f["has_children"] for f in r["folders"]}
    assert kids == {"a": False, "b2": True, "b10": False}
    assert not b.list_subfolders(str(tmp_path / "missing"))["ok"]


@pytest.mark.skipif(os.name != "nt", reason="Windows hidden attribute / junctions")
def test_list_subfolders_skips_hidden_and_junctions(b, tmp_path):
    (tmp_path / "shown").mkdir()
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    subprocess.run(["attrib", "+h", str(hidden)], check=True, capture_output=True)
    target = tmp_path / "shown"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp_path / "junction"), str(target)],
                   check=True, capture_output=True)
    names = [f["name"] for f in b.list_subfolders(str(tmp_path))["folders"]]
    assert names == ["shown"]


def test_find_folders_breadth_first(b, tmp_path):
    (tmp_path / "Clients" / "Kim Invoices").mkdir(parents=True)
    (tmp_path / "Invoices 2024").mkdir()
    (tmp_path / "Other" / "deep" / "old invoices").mkdir(parents=True)
    r = b.find_folders(str(tmp_path), "invoices")
    assert r["ok"] and not r["truncated"]
    assert [x["name"] for x in r["results"]] == ["Invoices 2024", "Kim Invoices", "old invoices"]
    assert [x["name"] for x in b.find_folders(str(tmp_path), "kim inv")["results"]] == ["Kim Invoices"]
    assert b.find_folders(str(tmp_path), "  ")["results"] == []
    lim = b.find_folders(str(tmp_path), "i", limit=2)
    assert len(lim["results"]) == 2 and lim["truncated"]


def test_find_folders_searches_the_preferred_subtree_first(b, tmp_path):
    (tmp_path / "Invoices top").mkdir()
    (tmp_path / "Other" / "deep" / "old invoices").mkdir(parents=True)
    r = b.find_folders(str(tmp_path), "invoices", 200, str(tmp_path / "Other"))
    assert [x["name"] for x in r["results"]] == ["old invoices", "Invoices top"]   # no repeats
    # a prefer folder outside the root is ignored
    r = b.find_folders(str(tmp_path / "Other"), "invoices", 200, str(tmp_path))
    assert [x["name"] for x in r["results"]] == ["old invoices"]
