"""Tests for the headless AI photo edit (ai_photo_editor.run_ai_edit)."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import sys
import types

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ai_photo_editor as ape  # noqa: E402

FAKE_KEY = "sk-test-0123456789abcdefghijklmnop"


def _png_b64(size=(512, 512), color=(10, 20, 200)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _FakeImages:
    def __init__(self, recorder):
        self._rec = recorder

    def edit(self, **kwargs):
        self._rec.append(kwargs)
        item = types.SimpleNamespace(b64_json=_png_b64(), url=None)
        return types.SimpleNamespace(data=[item])


def _fake_openai_factory(recorder):
    class FakeOpenAI:
        def __init__(self, **kw):
            self.kw = kw
            self.images = _FakeImages(recorder)

    return FakeOpenAI


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    for k in ("OPENAI_API_KEY", "OPENAI_PROJECT_ID", "OPENAI_ORG_ID", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    # Don't pick up any real .env files.
    monkeypatch.setattr(ape, "merge_dotenv_into_environ", lambda: None)
    return tmp_path


def _make_src(tmp_path, size=(1600, 1200)) -> str:
    p = tmp_path / "student.jpg"
    Image.new("RGB", size, (200, 180, 160)).save(p, format="JPEG")
    return str(p)


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_run_ai_edit_writes_new_file(isolated_env, monkeypatch):
    action = "wear_suit"
    calls: list = []
    monkeypatch.setattr(ape, "_openai_client", lambda: _fake_openai_factory(calls))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    src = _make_src(isolated_env)
    before = _sha(src)

    ok, out = ape.run_ai_edit(src, action)

    assert ok, out
    assert os.path.isfile(out)
    assert os.path.normcase(out) != os.path.normcase(src)
    assert os.path.basename(out).startswith("student_ai")
    assert _sha(src) == before  # original untouched
    with Image.open(out) as im:
        assert im.size == (1600, 1200)
    assert len(calls) == 1
    # upload was downscaled to <= 1024 on the longest edge
    _, data, _ = calls[0]["image"]
    with Image.open(io.BytesIO(data)) as up:
        assert max(up.size) == 1024
    assert "mask" in calls[0]  # wear_suit protects the face with a body-only mask


def test_second_run_gets_unique_name(isolated_env, monkeypatch):
    monkeypatch.setattr(ape, "_openai_client", lambda: _fake_openai_factory([]))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    src = _make_src(isolated_env)
    ok1, out1 = ape.run_ai_edit(src, "wear_suit")
    ok2, out2 = ape.run_ai_edit(src, "wear_suit")
    assert ok1 and ok2 and out1 != out2
    assert os.path.isfile(out1) and os.path.isfile(out2)


def test_explicit_api_key_param(isolated_env, monkeypatch):
    monkeypatch.setattr(ape, "_openai_client", lambda: _fake_openai_factory([]))
    ok, out = ape.run_ai_edit(_make_src(isolated_env), "wear_suit", api_key=FAKE_KEY)
    assert ok, out


def test_missing_key_returns_error(isolated_env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(ape, "_openai_client", lambda: _fake_openai_factory(calls))
    src = _make_src(isolated_env)
    ok, msg = ape.run_ai_edit(src, "wear_suit")
    assert ok is False
    assert "key" in msg.lower()
    assert calls == []
    assert sorted(os.listdir(isolated_env)) == ["student.jpg"]


def test_unknown_action(isolated_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    ok, msg = ape.run_ai_edit(_make_src(isolated_env), "upscale")
    assert ok is False and "Unknown" in msg


def test_actions_list():
    keys = [a["key"] for a in ape.ai_photo_actions()]
    assert keys == ["wear_suit"]


@pytest.mark.parametrize("action", ["white_background", "passport_mode",
                                    "professional_enhance", "remove_grain"])
def test_removed_actions_are_rejected(isolated_env, monkeypatch, action):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    ok, msg = ape.run_ai_edit(_make_src(isolated_env), action)
    assert ok is False and "Unknown" in msg
