"""SFMBridge.smart_split_rename must call file_ops with the real signature."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import file_ops  # noqa: E402
import sfm_bridge  # noqa: E402


def _bridge(monkeypatch, result):
    real_sig = inspect.signature(file_ops.pdf_smart_split_merge_rename)
    calls = []

    def fake(*args, **kwargs):
        bound = real_sig.bind(*args, **kwargs)     # TypeError if the call is wrong
        calls.append(bound.arguments)
        return result

    monkeypatch.setattr(sfm_bridge._fo, "pdf_smart_split_merge_rename", fake)
    b = sfm_bridge.SFMBridge()
    events = []
    b._emit = lambda ev, payload=None: events.append((ev, payload))
    b._thread = lambda fn, *a, **k: fn(*a, **k)   # run synchronously
    return b, calls, events


def test_smart_split_uses_source_folder_by_default(monkeypatch, tmp_path):
    src = str(tmp_path / "scan.pdf")
    b, calls, events = _bridge(monkeypatch, (True, ["x.pdf"]))
    assert b.smart_split_rename(src)["ok"]
    assert calls and calls[0]["out_dir"] == str(tmp_path)
    assert calls[0]["src"] == src
    assert events[-1] == ("smart_rename_done", {"ok": True, "files": ["x.pdf"]})


def test_smart_split_explicit_out_dir_and_failure(monkeypatch, tmp_path):
    b, calls, events = _bridge(monkeypatch, (False, []))
    b.smart_split_rename(str(tmp_path / "a.pdf"), str(tmp_path / "out"))
    assert calls[0]["out_dir"] == str(tmp_path / "out")
    ev, payload = events[-1]
    assert ev == "smart_rename_done" and payload["ok"] is False
