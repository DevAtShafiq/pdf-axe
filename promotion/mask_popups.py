"""mask_popups.py — find Windows taskbar-preview popups in recorded takes.

    python promotion/mask_popups.py promotion/work/<run>/ch03 promotion/work/<run>/ch09 ...

If the mouse ever passes over the taskbar, Windows shows a preview of other
windows (and can "peek" a whole window). record_demo.py now keeps the cursor
inside the app window, so new takes shouldn't have this; this tool checks
older takes. Spans found (raw video seconds) are stored in each take's
events.json as 'mask_raw' events, and edit_demo.py shows the last clean frame
there instead. The popup sits at a fixed spot above the taskbar button (its
dark title strip at x 1400..1608, y 1232..1254 of a 2474×1392 take) — check
a frame by eye if your taskbar layout differs, and add spans by hand the same
way when needed: {"t": -1, "type": "mask_raw", "a": <start s>, "b": <end s>}."""
import json, os, subprocess, sys
import numpy as np
import imageio_ffmpeg
FF = imageio_ffmpeg.get_ffmpeg_exe()
X, Y, CW, CH = 1380, 1226, 250, 34
def frames(raw):
    p = subprocess.Popen([FF, "-v", "error", "-i", raw, "-vf", f"fps=30,format=gray,crop={CW}:{CH}:{X}:{Y}",
                          "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    while True:
        b = p.stdout.read(CW * CH)
        if len(b) < CW * CH:
            return
        yield np.frombuffer(b, np.uint8).reshape(CH, CW)
def is_popup(a):
    box = a[6:28, 22:226]                      # dark title strip of the popup
    left, right = a[:, 0:14], a[:, 236:250]    # just outside it: the app (light)
    return box.mean() < 70 and (left.mean() > 150 or right.mean() > 150)
for d in sys.argv[1:]:
    hits = [i / 30 for i, a in enumerate(frames(os.path.join(d, "raw.mp4"))) if is_popup(a)]
    spans = []
    for t in hits:
        if spans and t - spans[-1][1] <= 0.2:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    spans = [[round(a - 2.0, 2), round(b + 0.8, 2)] for a, b in spans]   # generous: the popup fades in before it is detectable
    ev = os.path.join(d, "events.json")
    log = json.load(open(ev, encoding="utf-8"))
    log["events"] = [e for e in log["events"] if e.get("type") != "mask_raw"]
    log["events"] += [{"t": -1, "type": "mask_raw", "a": a, "b": b, "why": "taskbar preview"} for a, b in spans]
    json.dump(log, open(ev, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(os.path.basename(d), "masked:", spans, flush=True)
