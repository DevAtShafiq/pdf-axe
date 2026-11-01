# -*- coding: utf-8 -*-
"""
features.py — switches for features that are built but not offered.

AI_SUIT_ENABLED: the AI "Wear Suit & Tie" photo edit (ai_photo_editor.py).
Off: every entry point is hidden (details panel card, right-click item,
legacy "AI photo…" menu) and the bridge's ai_photo_* calls refuse with a
clear message. The code stays, so turning it back on is one line.
Crop, Photo size and the OpenAI key setting are not affected.
"""
from __future__ import annotations

AI_SUIT_ENABLED = False

AI_SUIT_OFF_MESSAGE = "AI suit & tie is not available in this version of Office Axe."


def flags() -> dict:
    """What the web UI needs to know (read at start-up)."""
    return {"ai_suit": bool(AI_SUIT_ENABLED)}
