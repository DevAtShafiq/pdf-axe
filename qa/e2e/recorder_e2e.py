"""E2E: screen recorder + screenshots (ui/js/recorder.js + recorder_bridge.py).

    python qa/e2e/recorder_e2e.py <scratch dir>

The screen picker is replaced in the page by an animated canvas stream, so no
real screen is recorded; captures are ~2 s long and go to <scratch>/rec (the
recordings folder setting is pointed there and restored afterwards). Nothing is
deleted: a discarded recording ends up in <scratch>/rec/_to_review.
"""
from __future__ import annotations

import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Results, app_page, wait  # noqa: E402

R = Results()

STUB = """() => {
  if (window.__qaStub) return;
  window.__qaStub = true;
  window.__realGDM = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getDisplayMedia = async () => {
    const c = document.createElement('canvas'); c.width = 320; c.height = 240;
    const x = c.getContext('2d'); let t = 0;
    const iv = setInterval(() => { t++; x.fillStyle = `hsl(${t * 7 % 360},70%,50%)`; x.fillRect(0, 0, 320, 240);
                                   x.fillStyle = '#fff'; x.fillText('QA ' + t, 20, 120); }, 50);
    const s = c.captureStream(15);
    s.getVideoTracks()[0].addEventListener('ended', () => clearInterval(iv));
    return s;
  };
}"""


def files(d):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(d, "*")) if os.path.isfile(p))


def run(d):
    rec = os.path.join(d, "rec")
    os.makedirs(rec, exist_ok=True)
    with app_page() as page:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        prev = page.evaluate("SFM.call('rec_default_dir')")
        old = "" if prev.get("is_default") else prev.get("path", "")
        try:
            r = page.evaluate("p => SFM.call('rec_set_dir', p)", rec)
            R.check("rec_set_dir", r.get("ok") and os.path.normcase(r["path"]) == os.path.normcase(rec), r)
            page.evaluate("""() => localStorage.setItem('oxRecPrefs', JSON.stringify({audio: 'none', countdown: false,
                             webcam: false, minimize: false, quality: '720', fps: 15, source: 'screen'}))""")
            page.evaluate(STUB)
            # setup dialog
            page.evaluate("Recorder.open()")
            wait(page, "document.querySelector('.rec-modal') && document.querySelector('[id^=rec-dir-]').value")
            R.check("setup dialog shows folder", os.path.normcase(page.evaluate("document.querySelector('[id^=rec-dir-]').value")) == os.path.normcase(rec))
            wait(page, "!document.querySelector('[id^=rec-list-]').textContent.includes('Loading')")
            R.check("recent list empty state", "No recordings yet" in page.text_content("[id^=rec-list-]"))
            fmt = page.evaluate("Recorder.formats()")
            R.check("a video format is supported", fmt.get("mp4") or fmt.get("webm"), fmt)
            # start → recording
            page.click("[id^=rec-start-]")
            wait(page, "document.querySelector('.rec-bar') && Recorder.isRecording()")
            R.check("setup dialog closed on start", not page.evaluate("!!document.querySelector('.rec-modal')"))
            time.sleep(2.2)
            t1 = page.text_content(".rec-bar .rec-time")
            R.check("timer runs", t1 in ("00:02", "00:03"), t1)
            R.check("window title shows recording", "Recording" in page.title(), page.title())
            # pause / resume
            page.click(".rec-bar [data-act=pause]")
            time.sleep(1.5)
            paused = page.evaluate("document.querySelector('.rec-bar').classList.contains('is-paused')")
            ta = page.text_content(".rec-bar .rec-time")
            time.sleep(1.2)
            tb = page.text_content(".rec-bar .rec-time")
            R.check("pause freezes timer", paused and ta == tb, f"{ta} {tb}")
            page.click(".rec-bar [data-act=pause]")
            time.sleep(1.0)
            R.check("resume", not page.evaluate("document.querySelector('.rec-bar').classList.contains('is-paused')"))
            # parts are written while recording
            parts = glob.glob(os.path.join(rec, ".recording-*.part"))
            R.check("streams to a .part file", parts and os.path.getsize(parts[0]) > 0, parts)
            # stop
            page.click(".rec-bar [data-act=stop]")
            wait(page, "!Recorder.isRecording() && !document.querySelector('.rec-bar')", timeout=20)
            time.sleep(0.5)
            vids = [f for f in files(rec) if f.lower().endswith((".mp4", ".webm"))]
            R.check("stop saves a video", len(vids) == 1 and os.path.getsize(os.path.join(rec, vids[0])) > 1000, vids)
            R.check("no .part left", not glob.glob(os.path.join(rec, ".recording-*.part")))
            R.check("title restored", page.title() == "Office Axe", page.title())
            # Ctrl+Shift+R start / stop (no dialog) then discard via the bar
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.keyboard.press("Control+Shift+R")
            wait(page, "Recorder.isRecording() && document.querySelector('.rec-bar')")
            time.sleep(1.5)
            page.keyboard.press("Control+Shift+R")
            wait(page, "!Recorder.isRecording()", timeout=20)
            time.sleep(0.5)
            vids2 = [f for f in files(rec) if f.lower().endswith((".mp4", ".webm"))]
            R.check("Ctrl+Shift+R start/stop saves second video", len(vids2) == 2, vids2)
            page.evaluate("Recorder.toggle()")
            wait(page, "Recorder.isRecording() && document.querySelector('.rec-bar')")
            time.sleep(1.5)
            page.click(".rec-bar [data-act=discard]")
            wait(page, "document.querySelector('.modal-overlay.dlg-top')")
            page.click(".modal-overlay.dlg-top .btn-danger, .modal-overlay.dlg-top .btn-primary")
            wait(page, "!Recorder.isRecording()", timeout=20)
            time.sleep(0.5)
            moved = glob.glob(os.path.join(rec, "_to_review", "Discarded recording*"))
            vids3 = [f for f in files(rec) if f.lower().endswith((".mp4", ".webm"))]
            R.check("discard moves to _to_review", len(moved) == 1 and len(vids3) == 2, (moved, vids3))
            # recent list shows the videos; rename via bridge
            page.evaluate("Recorder.open()")
            wait(page, "document.querySelectorAll('[id^=rec-list-] .rec-item').length >= 2")
            R.check("recent list shows recordings", page.evaluate("document.querySelectorAll('[id^=rec-list-] .rec-item').length") == 2)
            page.click("[id^=rec-close-]")
            time.sleep(0.3)
            # screenshot (full) into the scratch folder
            page.evaluate("Recorder.screenshot('full')")
            wait(page, "!!document.querySelector('.rec-toast')", timeout=20)
            pngs = [f for f in files(rec) if f.lower().endswith(".png")]
            R.check("full screenshot saved", len(pngs) == 1, pngs)
            if pngs:
                from PIL import Image
                with Image.open(os.path.join(rec, pngs[0])) as im:
                    R.check("screenshot size sane", im.width >= 640 and im.height >= 480, im.size)
            R.check("never left recording", not page.evaluate("Recorder.isRecording()"))
            R.check("no page errors", not errors, errors[:3])
        finally:
            if page.evaluate("Recorder.isRecording()"):
                page.evaluate("Recorder.stop()")
            page.evaluate("p => SFM.call('rec_set_dir', p)", old)
            page.evaluate("() => { if (window.__realGDM) navigator.mediaDevices.getDisplayMedia = window.__realGDM; window.__qaStub = false; }")
    return R.summary()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
