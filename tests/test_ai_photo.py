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
    assert os.path.basename(out) == "student_suit.jpg"
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
    assert [os.path.basename(out1), os.path.basename(out2)] == ["student_suit.jpg", "student_suit-2.jpg"]
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


# ── options → prompt, mask, request, cost ────────────────────────────────────

def test_options_map_into_prompt():
    p = ape.build_prompt("wear_suit", {"suit_color": "navy", "tie": True})
    assert "navy" in p and "necktie" in p and "NO necktie" not in p
    assert p.startswith(ape._IDENTITY_PREFIX)
    p = ape.build_prompt("wear_suit", {"suit_color": "gray", "tie": False})
    assert "medium grey" in p and "NO necktie" in p
    p = ape.build_prompt("wear_suit", {"suit_color": "pink", "tie": "false"})
    assert "black" in p and "NO necktie" in p          # unknown colour → default


def test_normalize_options():
    assert ape.normalize_options(None) == ape.DEFAULT_OPTIONS
    o = ape.normalize_options({"suit_color": "Charcoal", "tie": 0, "quality": "medium", "x": 1})
    assert o == {"suit_color": "charcoal", "tie": False, "quality": "medium"}
    assert ape.normalize_options({"quality": "ultra"})["quality"] == "high"


def test_mask_protects_face_and_frees_body():
    m = ape._make_mask_body_only(100, 200)
    a = m.split()[3]
    assert a.getpixel((50, 5)) == 255       # top (face): opaque = kept
    assert a.getpixel((50, 195)) == 0       # bottom (clothes): transparent = edited
    assert m.size == (100, 200)


def test_request_uses_options_size_timeout_and_exif(isolated_env, monkeypatch):
    calls: list = []
    made: list = []
    Fake = _fake_openai_factory(calls)

    class Recorder(Fake):
        def __init__(self, **kw):
            super().__init__(**kw)
            made.append(kw)
    monkeypatch.setattr(ape, "_openai_client", lambda: Recorder)
    # stored landscape with EXIF "rotate 90° CW" → displayed portrait 1200x1600
    exif = Image.Exif()
    exif[0x0112] = 6
    src = isolated_env / "phone.jpg"
    Image.new("RGB", (1600, 1200), (1, 2, 3)).save(src, exif=exif.tobytes())
    ok, out = ape.run_ai_edit(str(src), "wear_suit", api_key=FAKE_KEY,
                              options={"suit_color": "navy", "tie": False, "quality": "medium"})
    assert ok, out
    kw = calls[0]
    assert "navy" in kw["prompt"] and "NO necktie" in kw["prompt"]
    assert kw["quality"] == "medium" and kw["size"] == "1024x1536"
    assert kw["extra_body"] == {"input_fidelity": "high"}
    _, data, _ = kw["image"]
    with Image.open(io.BytesIO(data)) as up:
        assert up.size == (768, 1024)            # upright and downscaled
    _, mdata, _ = kw["mask"]
    with Image.open(io.BytesIO(mdata)) as mk:
        assert mk.size == (768, 1024)
    assert made[0]["timeout"] == ape._REQUEST_TIMEOUT_S
    with Image.open(out) as im:
        assert im.size == (1200, 1600)           # saved upright at full size


def test_input_fidelity_fallback(isolated_env, monkeypatch):
    calls: list = []

    class Images(_FakeImages):
        def edit(self, **kw):
            if "extra_body" in kw:
                calls.append("rejected")
                raise RuntimeError("Unknown parameter: 'input_fidelity'")
            return super().edit(**kw)

    class Fake:
        def __init__(self, **kw):
            self.images = Images(calls)
    monkeypatch.setattr(ape, "_openai_client", lambda: Fake)
    ok, out = ape.run_ai_edit(_make_src(isolated_env), "wear_suit", api_key=FAKE_KEY)
    assert ok, out
    assert calls[0] == "rejected" and len(calls) == 2


def test_failure_is_readable_and_writes_nothing(isolated_env, monkeypatch):
    class Images:
        def edit(self, **kw):
            raise RuntimeError("Request timed out.")

    class Fake:
        def __init__(self, **kw):
            self.images = Images()
    monkeypatch.setattr(ape, "_openai_client", lambda: Fake)
    ok, msg = ape.run_ai_edit(_make_src(isolated_env), "wear_suit", api_key=FAKE_KEY)
    assert not ok and "timed out" in msg
    assert sorted(os.listdir(isolated_env)) == ["student.jpg"]


def test_cost_tracking(isolated_env, monkeypatch):
    ape.reset_session_cost()
    monkeypatch.setattr(ape, "_openai_client", lambda: _fake_openai_factory([]))
    ape.run_ai_edit(_make_src(isolated_env), "wear_suit", api_key=FAKE_KEY)
    assert ape.session_cost_usd() == pytest.approx(ape.estimate_cost_usd(1024, 768, "high"))
    usage = {"output_tokens": 1000, "input_tokens_details": {"text_tokens": 100, "image_tokens": 200}}
    assert ape._cost_from_usage(usage) == pytest.approx(1000 * 40e-6 + 100 * 5e-6 + 200 * 10e-6)
    assert ape.estimate_cost_usd(600, 800, "medium") < ape.estimate_cost_usd(600, 800, "high")
    ape.reset_session_cost()
    assert ape.session_cost_usd() == 0


# ── bridge gate behaviour ────────────────────────────────────────────────────

def _bridge(monkeypatch):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    events, threads = [], []
    monkeypatch.setattr(b, "_emit", lambda ev, payload=None: events.append((ev, payload)))
    monkeypatch.setattr(b, "_thread", lambda fn, *a, **k: threads.append(fn))
    return b, events, threads


def test_gate_rejects_before_anything_starts(isolated_env, monkeypatch):
    b, events, threads = _bridge(monkeypatch)
    monkeypatch.setattr(b, "_require_plan",
                        lambda: {"ok": False, "error": "An active subscription is required",
                                 "need_subscription": True})
    r = b.run_ai_photo_action(_make_src(isolated_env), "wear_suit", {})
    assert r["ok"] is False and r["need_subscription"]
    assert threads == [] and events == []


def test_missing_api_key_reported_synchronously(isolated_env, monkeypatch):
    b, events, threads = _bridge(monkeypatch)
    monkeypatch.setattr(b, "_require_plan", lambda: None)
    monkeypatch.setattr(b, "_openai_key", lambda: "")
    r = b.run_ai_photo_action(_make_src(isolated_env), "wear_suit", {})
    assert r["ok"] is False and r["need_api_key"] and threads == []


def test_bridge_runs_with_options(isolated_env, monkeypatch):
    b, events, threads = _bridge(monkeypatch)
    calls: list = []
    monkeypatch.setattr(b, "_require_plan", lambda: None)
    monkeypatch.setattr(b, "_openai_key", lambda: FAKE_KEY)
    monkeypatch.setattr(ape, "_openai_client", lambda: _fake_openai_factory(calls))
    src = _make_src(isolated_env)
    r = b.run_ai_photo_action(src, "wear_suit", {"suit_color": "grey", "tie": False, "job_id": "j1"})
    assert r["ok"] and r["started"] and r["job_id"] == "j1" and r["cost_est_usd"] > 0
    assert events == []                      # nothing emitted before the job runs
    threads[0]()
    ev, payload = events[-1]
    assert ev == "ai_photo_result" and payload["ok"] and payload["job_id"] == "j1"
    assert payload["out"].endswith("student_suit.jpg") and payload["src"] == src
    assert "medium grey" in calls[0]["prompt"]
    est = b.ai_photo_estimate(src, {"quality": "medium"})
    assert est["ok"] and est["out_name"] == "student_suit-2.jpg"
