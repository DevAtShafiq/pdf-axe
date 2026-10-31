# -*- coding: utf-8 -*-
"""Type to select in the file list (ui/js/type-select.js), run with Node."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "ui", "js", "type-select.js")
NODE = shutil.which("node")

NAMES = ["1. KHALAD MAHMUD SAIF", "2. ABIR SYED NAFIJ UDDIN", "5. RAHUL MD SHARIAR HOSSAIN",
         "8. SALMAN SAJIT", "9. SHAKIB MD ARIFUL ISLAM", "15. SHILA MST MAHAJABIN MAHMUD",
         "16. SAMI MD SHAHED", "17. SAFAYET ISLAM SIAD", "20. DURJOY MD NAHID SARKER",
         "(3) রহিম উদ্দিন", "notes.txt"]


def find(cases):
    """cases: [(typed, from, fresh)] → indexes."""
    script = (f"const T = require({json.dumps(JS)}); const n = {json.dumps(NAMES, ensure_ascii=False)};"
              f"console.log(JSON.stringify({json.dumps(cases)}.map(c => T.find(n, c[0], c[1], c[2]))))")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(not NODE, reason="Node.js not installed")


def test_numbers_words_and_anywhere():
    got = find([("15", -1, True), ("shila", -1, True), ("mahmud", -1, True), ("md shahed", -1, True),
                ("hajab", -1, True), ("SHILA MST", -1, True), ("নোট", -1, True), ("রহিম", -1, True),
                ("zzz", -1, True), ("  ", -1, True), ("notes", -1, True), ("2", -1, True)])
    assert got == [5, 5, 0, 6, 5, 5, -1, 9, -1, -1, 10, 1]


def test_best_kind_of_match_wins_over_position():
    # "s" from the top: a word starting with s; a name starting with "s" would win but none does
    assert find([("sa", 4, True)]) == [6]          # after SHAKIB: SAMI (word start) comes first
    assert find([("isl", -1, True)]) == [4]        # SHAKIB MD ARIFUL ISLAM, a word start


def test_same_letter_cycles_and_typing_keeps_the_current_item():
    # fresh search starts after the focused item → cycles
    assert find([("s", 3, True), ("s", 4, True), ("s", 5, True)]) == [4, 5, 6]
    # typing more letters (fresh=False) keeps the current item while it still matches
    assert find([("sh", 4, False), ("shi", 4, False)]) == [4, 5]
