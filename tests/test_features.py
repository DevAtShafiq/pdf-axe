# -*- coding: utf-8 -*-
"""features.py switches: AI suit & tie is off, hidden in the UI and refused by the bridge."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import features  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_ai_suit_is_switched_off():
    assert features.AI_SUIT_ENABLED is False
    assert features.flags() == {"ai_suit": False}


def test_bridge_refuses_ai_suit_calls(tmp_path):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"x")
    assert b.features() == {"ok": True, "ai_suit": False}
    for r in (b.ai_photo_actions(), b.ai_photo_estimate(str(img)),
              b.run_ai_photo_action(str(img), "wear_suit", {})):
        assert r["ok"] is False and r.get("disabled") is True
        assert "not available" in r["error"]


def test_ui_hides_ai_suit_until_switched_on():
    html = _read("ui/index.html")
    assert '<body class="no-ai-suit">' in html                       # hidden from the first paint
    assert '<div class="ai-card ai-suit-only">' in html
    assert 'class="qa-btn hidden ai-suit-only" id="ai-compare"' in html
    assert "body.no-ai-suit .ai-suit-only { display: none !important; }" in _read("ui/media-tools.css")
    assert "if (App.aiSuitOn()) _item(menu, 'shirt'" in _read("ui/js/context-menu.js")
    assert "App.aiSuitOn()" in _read("ui/js/photo-tools.js")
    assert "App.aiSuitOn()" in _read("ui/js/details.js")
    # Crop and Photo size stay
    assert 'id="ai-crop"' in html and 'id="ai-photo-size"' in html
