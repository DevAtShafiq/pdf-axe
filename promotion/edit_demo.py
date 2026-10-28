# -*- coding: utf-8 -*-
"""
edit_demo.py — turn the chapter takes into the finished product video.

    python promotion/edit_demo.py promotion/work/<run> [--voice-dir promotion/work/voice]
                                  [--out promotion/office-axe-demo.mp4] [--preview] [--chapters 1,2]

Inputs
    <run>/chNN[-k]/raw.mp4, events.json   one take per chapter (record_demo.py);
                                          the newest successful take is used
    narration.json                        script, chapter titles, callout texts
    <voice-dir>/<id>.wav                  narration lines (make_voiceover.py)

Outputs
    office-axe-demo-1440p.mp4   2560×1440, 30 fps, H.264 + AAC (master)
    office-axe-demo-1080p.mp4   the same at 1920×1080
    office-axe-demo.mp4         compact 1080p copy for sharing / the repository
    narration.srt               subtitles (same timing as the burned-in captions)
    <run>/timeline.json         the computed edit

What the editor adds on top of the real footage: opening / chapter / closing
cards, a camera that smoothly follows the action, callouts with highlight
boxes, click ripples, key caps, a chapter label, captions, a blur over
anything sensitive (the API key field), and a soundtrack — narration, UI
sounds and a quiet music bed made by sfx.py. Long waits (Word starting,
the AI call) are sped up with a visible badge.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import math
import os
import re
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sfx  # noqa: E402

OW, OH, FPS = 2560, 1440, 30
SR = sfx.SR
U = OW / 1920.0                    # layout unit: sizes below are designed for 1080p

NAVY = (15, 27, 51)
NAVY2 = (22, 44, 86)
ACCENT = (47, 134, 255)
WHITE = (255, 255, 255)
INK = (24, 31, 46)
MUTED = (110, 120, 138)

_FONTS: dict = {}


def font(size, bold=False):
    key = (int(size * U), bold)
    if key not in _FONTS:
        import pymupdf
        _FONTS[key] = ImageFont.truetype(io.BytesIO(pymupdf.Font("hebo" if bold else "helv").buffer), key[0])
    return _FONTS[key]


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ── video input ───────────────────────────────────────────────────────────────
class RawReader:
    def __init__(self, path, w, h):
        self.w, self.h = w, h
        self.p = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", path,
                                   "-vf", f"fps={FPS}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                  stdout=subprocess.PIPE)
        self.idx, self.frame, self.size = -1, None, w * h * 3

    def get(self, i):
        i = max(0, i)
        while self.idx < i:
            buf = self.p.stdout.read(self.size)
            if len(buf) < self.size:
                break
            self.frame, self.idx = buf, self.idx + 1
        return Image.frombuffer("RGB", (self.w, self.h), self.frame, "raw", "RGB", 0, 1)

    def close(self):
        try:
            self.p.kill()
        except Exception:
            pass


def probe_size(path):
    info = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Video: .*?, (\d{3,5})x(\d{3,5})", info)
    return int(m.group(1)), int(m.group(2))


def find_sync(path, w, h, x, y, t_hint=0.0, span=5.0):
    """Video time of the magenta sync marker. Looks around the time the event
    log says it was shown (it can be minutes into the file)."""
    start = max(0.0, t_hint - 1.0)
    p = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", path,
                          "-t", f"{span:.1f}", "-vf", f"fps={FPS}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        for i in range(int(span * FPS)):
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            o = (y * w + x) * 3
            r, g, b = buf[o], buf[o + 1], buf[o + 2]
            if r > 200 and b > 200 and g < 80:
                return start + i / FPS
    finally:
        p.kill()
    return None


# ── takes ─────────────────────────────────────────────────────────────────────
def load_takes(run, only=None):
    takes = {}
    for d in sorted(glob.glob(os.path.join(run, "ch[0-9][0-9]*"))):
        ev = os.path.join(d, "events.json")
        if not (os.path.isfile(ev) and os.path.isfile(os.path.join(d, "raw.mp4"))):
            continue
        log = json.load(open(ev, encoding="utf-8"))
        if not log.get("ok"):
            continue
        n = log["chapter"]
        if only and n not in only:
            continue
        if n not in takes or os.path.getmtime(ev) > takes[n]["mtime"]:
            takes[n] = {"dir": d, "mtime": os.path.getmtime(ev), **log}
    out = []
    for n in sorted(takes):
        t = takes[n]
        raw = os.path.join(t["dir"], "raw.mp4")
        w, h = probe_size(raw)
        sync = next((e for e in t["events"] if e["type"] == "sync"), None)
        vs = find_sync(raw, w, h, sync["x"], sync["y"], sync["t"] + 0.5) if sync else None
        if vs is None:
            print(f"  ! chapter {n}: sync marker not found, using the typical offset")
        t["offset"] = (vs - sync["t"]) if (sync and vs is not None) else 0.77
        t["raw"], t["w"], t["h"] = raw, w, h
        print(f"chapter {n:2d} {t['key']:10s} {os.path.basename(t['dir'])}  sync {t['offset']:+.3f}s")
        out.append(t)
    return out


# ── timeline ──────────────────────────────────────────────────────────────────
def wav_len(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def speed_pieces(take, r0, r1):
    """Split [r0, r1] (raw seconds) into (a, b, speed): explicit waits and any
    stretch with no logged activity for > 4 s are played faster."""
    vt = lambda e: e["t"] + take["offset"]
    ev = [e for e in take["events"] if r0 <= vt(e) <= r1]
    spans = []
    fc = [e for e in ev if e["type"] == "focus" and e.get("name") == "task card"]
    fd = [e for e in ev if e["type"] == "focus" and e.get("name") == "task done"]
    if fc and fd:
        spans.append((vt(fc[0]) + 1.4, vt(fd[0]) - 0.4))
    ws = [e for e in ev if e["type"] == "wait_start"]
    we = [e for e in ev if e["type"] == "wait_end"]
    for a, b in zip(ws, we):
        spans.append((vt(a) + 1.5, vt(b) - 0.4))
    times = sorted(vt(e) for e in ev if e["type"] not in ("typing",))
    for a, b in zip(times, times[1:]):
        if b - a > 4.2 and not any(sa <= a + 1 <= sb for sa, sb in spans):
            spans.append((a + 1.4, b - 0.9))
    spans = sorted((a, b) for a, b in spans if b - a > 1.5)
    pieces, cur = [], r0
    for a, b in spans:
        if a < cur:
            continue
        pieces.append((cur, a, 1.0))
        sp = max(2.0, min(12.0, (b - a) / 1.6))
        pieces.append((a, b, round(sp, 1)))
        cur = b
    pieces.append((cur, r1, 1.0))
    return [p for p in pieces if p[1] - p[0] > 0.02]


def build_timeline(takes, script, voice_dir):
    durs = {l["id"]: wav_len(os.path.join(voice_dir, l["id"] + ".wav")) for l in script["lines"]}
    lines_for = lambda key: [l for l in script["lines"] if l["section"] == key]
    segs, narr, chapters = [], [], []
    out = 0.0

    def place_lines(key, t):
        nonlocal narr
        for l in lines_for(key):
            narr.append({"id": l["id"], "text": l["text"], "t0": t, "t1": t + durs[l["id"]]})
            t += durs[l["id"]] + 0.35
        return t

    intro = max(7.0, sum(durs[l["id"]] for l in lines_for("intro")) + 2.2)
    segs.append({"kind": "card", "card": "intro", "t0": out, "t1": out + intro})
    place_lines("intro", out + 1.2)
    out += intro

    for ci, tk in enumerate(takes):
        key = tk["key"]
        vt = lambda e, tk=tk: e["t"] + tk["offset"]
        sec = next(e for e in tk["events"] if e["type"] == "section")
        end = next((e for e in tk["events"] if e["type"] == "end"), tk["events"][-1])
        r0, r1 = vt(sec) - 0.1, vt(end) - 0.35
        card_d = 2.4
        segs.append({"kind": "card", "card": "chapter", "key": key, "take": ci, "r": r0 + 0.3,
                     "t0": out, "t1": out + card_d})
        out += card_d
        c0 = out
        for a, b, sp in speed_pieces(tk, r0, r1):
            d = (b - a) / sp
            segs.append({"kind": "video", "take": ci, "r0": a, "r1": b, "speed": sp, "t0": out, "t1": out + d})
            out += d
        # narration: first line shortly after the chapter starts; others at their anchor
        t = c0 + 0.5
        for l in lines_for(key):
            if l.get("anchor"):
                typ, name = l["anchor"]
                e = next((e for e in tk["events"] if e["type"] == typ and
                          (name in (e.get("name") or "") or name == e.get("text"))), None)
                if e:
                    t = max(t, raw_to_out(segs, ci, vt(e)) - 0.3)
            narr.append({"id": l["id"], "text": l["text"], "t0": t, "t1": t + durs[l["id"]]})
            t += durs[l["id"]] + 0.35
        need = t - out + 0.4
        if need > 0:
            segs.append({"kind": "hold", "take": ci, "r": r1, "t0": out, "t1": out + need})
            out += need
        chapters.append({"key": key, "n": tk["chapter"], "t0": c0, "t1": out})

    outro = max(8.0, sum(durs[l["id"]] for l in lines_for("outro")) + 4.0)
    segs.append({"kind": "card", "card": "outro", "t0": out, "t1": out + outro})
    place_lines("outro", out + 1.6)
    out += outro
    return {"segments": segs, "narration": narr, "chapters": chapters, "duration": out}


def raw_to_out(segs, take, r):
    for s in segs:
        if s["kind"] == "video" and s["take"] == take and s["r0"] <= r <= s["r1"]:
            return s["t0"] + (r - s["r0"]) / s["speed"]
    best = None
    for s in segs:                                # inside a gap: nearest segment edge
        if s["kind"] == "video" and s["take"] == take:
            if r < s["r0"]:
                return s["t0"] if best is None else best
            best = s["t1"]
    return best or 0.0


def seg_at(segs, t):
    for s in segs:
        if s["t0"] <= t < s["t1"]:
            return s
    return segs[-1]


# ── camera ────────────────────────────────────────────────────────────────────
class Camera:
    """Follows the action: each logged event sets a target region; a spring
    eases the view there. Coordinates are raw-video pixels of the take."""

    def __init__(self, take):
        self.w, self.h = take["w"], take["h"]
        vt = lambda e: e["t"] + take["offset"]
        tg = []
        for e in take["events"]:
            t, typ = vt(e), e["type"]
            if typ in ("section", "wide", "dialog", "end"):
                tg.append((t - 0.2, None, 1.0))
            elif typ == "click" and "x" in e:
                tg.append((t - 0.45, self._around(e["x"], e["y"], 1.6), 1.6))
            elif typ in ("focus",) and e.get("rect"):
                tg.append((t - 0.2, e["rect"], 2.1))
            elif typ == "typing" and e.get("rect"):
                tg.append((t - 0.2, e["rect"], 2.0))
            elif typ == "drag" and e.get("frm"):
                (x0, y0), (x1, y1) = e["frm"], e["to"]
                tg.append((t - 0.4, [min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1], 1.7))
        self.targets = sorted(tg, key=lambda x: x[0])
        self.state = None
        self.last_r = None

    def _around(self, x, y, s):
        w, h = self.w / s, self.h / s
        return [x - w / 2, y - h / 2, w, h]

    def _goal(self, r):
        cur = (self.w / 2, self.h / 2, 1.0)
        for t, rect, smax in self.targets:
            if t > r:
                break
            if rect is None:
                cur = (self.w / 2, self.h / 2, 1.0)
                continue
            x, y, w, h = rect
            pad = 90
            s = max(1.0, min(smax, self.w / (w + 2 * pad), self.h / (h + 2 * pad)))
            cur = (x + w / 2, y + h / 2, s)
        return cur

    def at(self, r):
        gx, gy, gs = self._goal(r)
        if self.state is None or self.last_r is None or r < self.last_r or r - self.last_r > 1.0:
            self.state = [self.w / 2, self.h / 2, 1.0]
            self.last_r = r
        dt = max(0.0, r - self.last_r)
        self.last_r = r
        k = 1 - math.exp(-dt / 0.42)
        ks = 1 - math.exp(-dt / 0.55)
        x, y, s = self.state
        x += (gx - x) * k
        y += (gy - y) * k
        s = math.exp(math.log(s) + (math.log(gs) - math.log(s)) * ks)
        hw, hh = self.w / s / 2, self.h / s / 2
        x, y = min(max(x, hw), self.w - hw), min(max(y, hh), self.h - hh)
        self.state = [x, y, s]
        return x, y, s

    def reset(self):
        self.state, self.last_r = None, None


def frame_through(img, cam):
    """Crop the camera's view and scale it to the output size → (image, tf)."""
    w, h = img.size
    cx, cy, s = cam
    cw, ch = w / s, h / s
    x0, y0 = cx - cw / 2, cy - ch / 2
    out = img.resize((OW, OH), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch))
    return out, (x0, y0, OW / cw)


def to_out(pt, tf):
    x0, y0, k = tf
    return (pt[0] - x0) * k, (pt[1] - y0) * k


def rect_out(r, tf):
    a, b = to_out((r[0], r[1]), tf), to_out((r[0] + r[2], r[1] + r[3]), tf)
    return [a[0], a[1], b[0], b[1]]


# ── drawing helpers (draw on small RGBA patches, paste with alpha) ───────────
def paste_patch(base, patch, x, y, alpha=1.0):
    if alpha <= 0:
        return
    if alpha < 1:
        patch = patch.copy()
        patch.putalpha(patch.getchannel("A").point(lambda v: int(v * alpha)))
    base.paste(patch, (int(round(x)), int(round(y))), patch)


def card_patch(w, h, radius, fill, shadow=True, outline=None, ow=0):
    pad = int(26 * U) if shadow else 0
    im = Image.new("RGBA", (int(w + 2 * pad), int(h + 2 * pad)), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 6 * U, pad + w, pad + h + 8 * U], radius=radius, fill=(0, 0, 0, 80))
        im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(14 * U)))
    ImageDraw.Draw(im).rounded_rectangle([pad, pad, pad + w, pad + h], radius=radius, fill=fill,
                                         outline=outline, width=ow)
    return im, pad


def wrap(text, fnt, max_w):
    out, cur = [], ""
    for wd in text.split():
        test = (cur + " " + wd).strip()
        if fnt.getlength(test) <= max_w or not cur:
            cur = test
        else:
            out.append(cur)
            cur = wd
    if cur:
        out.append(cur)
    return out


def caption_chunks(text, max_chars=84):
    parts = re.split(r"(?<=[.?!—])\s+|(?<=,)\s+", text)
    chunks, cur = [], ""
    for p in parts:
        if len(cur) + len(p) + 1 <= max_chars or not cur:
            cur = (cur + " " + p).strip()
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


# ── overlays ──────────────────────────────────────────────────────────────────
class Overlays:
    def __init__(self, takes, tl, script):
        self.takes, self.tl, self.script = takes, tl, script
        self.callouts = script.get("callouts", {})
        self.captions = []
        for n in tl["narration"]:
            ch = caption_chunks(n["text"])
            tot = sum(len(c) for c in ch)
            t = n["t0"]
            for c in ch:
                d = (n["t1"] - n["t0"]) * len(c) / tot
                self.captions.append({"t0": t, "t1": t + d, "text": c})
                t += d
        self.focus_windows = {}
        for ci, tk in enumerate(takes):
            vt = lambda e, tk=tk: e["t"] + tk["offset"]
            ev = tk["events"]
            wins = []
            for i, e in enumerate(ev):
                if e["type"] != "focus" or e.get("name") not in self.callouts or not e.get("rect"):
                    continue
                t0 = vt(e)
                nxt = next((vt(x) for x in ev[i + 1:] if x["type"] in ("focus", "click", "section", "key")
                            and vt(x) > t0 + 0.4), t0 + 3.0)
                wins.append((t0, min(t0 + 3.4, max(t0 + 1.6, nxt + 0.2)), e))
            self.focus_windows[ci] = wins

    def chapter_label(self, img, t, key):
        meta = self.script["sections"].get(key, {})
        txt = f"{meta.get('n', 0):02d}   {meta.get('title', key)}"
        f = font(20, True)
        w = f.getlength(txt) + 40 * U
        patch, pad = card_patch(w, 42 * U, int(21 * U), (15, 27, 51, 225), shadow=False)
        d = ImageDraw.Draw(patch)
        d.text((pad + 20 * U, pad + 21 * U), txt, font=f, fill=WHITE, anchor="lm")
        paste_patch(img, patch, 26 * U, OH - 70 * U)

    def ripples(self, img, ci, r, tf):
        if r is None:
            return
        d = None
        for e in self.takes[ci]["events"]:
            if e["type"] != "click" or "x" not in e:
                continue
            dt = r - (e["t"] + self.takes[ci]["offset"]) - 0.03
            if 0 <= dt <= 0.6:
                x, y = to_out((e["x"], e["y"]), tf)
                k = ease_out(dt / 0.6)
                rad = (12 + 40 * k) * U
                s = int(rad * 2 + 20)
                patch = Image.new("RGBA", (s, s), (0, 0, 0, 0))
                pd = ImageDraw.Draw(patch)
                c = s / 2
                pd.ellipse([c - rad, c - rad, c + rad, c + rad], outline=ACCENT + (int(230 * (1 - k)),), width=int(5 * U))
                if dt < 0.25:
                    r2 = 16 * U
                    pd.ellipse([c - r2, c - r2, c + r2, c + r2], fill=(255, 214, 10, int(150 * (1 - dt / 0.25))))
                paste_patch(img, patch, x - c, y - c)

    def drags(self, img, ci, r, tf):
        if r is None:
            return
        for e in self.takes[ci]["events"]:
            if e["type"] != "drag" or not e.get("frm") or e.get("name") == "signature":
                continue
            dt = r - (e["t"] + self.takes[ci]["offset"])
            if 0 <= dt <= 1.9:
                x0, y0 = to_out(e["frm"], tf)
                x1, y1 = to_out(e["to"], tf)
                k = ease((dt - 0.3) / 1.2)
                a = int(190 * min(1.0, dt / 0.3) * (1 - max(0.0, (dt - 1.5) / 0.4)))
                xm, ym = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
                l, tp = min(x0, xm) - 12, min(y0, ym) - 12
                patch = Image.new("RGBA", (int(abs(xm - x0) + 24), int(abs(ym - y0) + 24)), (0, 0, 0, 0))
                pd = ImageDraw.Draw(patch)
                pd.line([(x0 - l, y0 - tp), (xm - l, ym - tp)], fill=ACCENT + (a,), width=int(6 * U))
                pd.ellipse([xm - l - 9, ym - tp - 9, xm - l + 9, ym - tp + 9], fill=ACCENT + (a,))
                paste_patch(img, patch, l, tp)

    def highlight(self, img, ci, r, tf):
        if r is None:
            return
        for t0, t1, e in self.focus_windows.get(ci, []):
            if not (t0 <= r <= t1):
                continue
            k = min(ease_out((r - t0) / 0.35), ease((t1 - r) / 0.3))
            box = rect_out(e["rect"], tf)
            pad = 10 * U
            box = [max(8, box[0] - pad), max(8, box[1] - pad), min(OW - 8, box[2] + pad), min(OH - 8, box[3] + pad)]
            bw, bh = box[2] - box[0], box[3] - box[1]
            if bw < 20 or bh < 20:
                continue
            ring = Image.new("RGBA", (int(bw + 8), int(bh + 8)), (0, 0, 0, 0))
            ImageDraw.Draw(ring).rounded_rectangle([4, 4, bw + 4, bh + 4], radius=int(16 * U),
                                                   outline=ACCENT + (255,), width=int(4 * U))
            paste_patch(img, ring, box[0] - 4, box[1] - 4, k)
            text = self.callouts[e["name"]]
            f = font(28, True)
            lines = wrap(text, f, 440 * U)
            cw = max(f.getlength(l) for l in lines) + 60 * U
            chh = 34 * U + 40 * U * len(lines)
            patch, ppad = card_patch(cw, chh, int(16 * U), (15, 27, 51, 242))
            d = ImageDraw.Draw(patch)
            d.rectangle([ppad, ppad + 14 * U, ppad + 6 * U, ppad + chh - 14 * U], fill=ACCENT)
            for i, l in enumerate(lines):
                d.text((ppad + 30 * U, ppad + 17 * U + 40 * U * i), l, font=f, fill=WHITE)
            gap = 34 * U
            if OW - box[2] > cw + 2 * gap:
                cx = box[2] + gap
            elif box[0] > cw + 2 * gap:
                cx = box[0] - gap - cw
            else:
                cx = min(max(gap, box[0]), OW - cw - gap)
            cy = min(max(gap, (box[1] + box[3]) / 2 - chh / 2), OH - chh - 190 * U)
            if box[0] - gap < cx + cw and cx < box[2] + gap and box[1] - gap < cy + chh and cy < box[3] + gap:
                cy = box[1] - chh - gap if box[1] > chh + 2 * gap else min(box[3] + gap, OH - chh - 190 * U)
            paste_patch(img, patch, cx - ppad + (1 - k) * 18 * (1 if cx > box[2] else -1), cy - ppad, k)

    def keycaps(self, img, ci, r):
        if r is None:
            return
        for e in self.takes[ci]["events"]:
            if e["type"] != "key" or not e.get("show", True):
                continue
            dt = r - (e["t"] + self.takes[ci]["offset"])
            if not (0 <= dt <= 1.5):
                continue
            k = min(ease_out(dt / 0.25), ease((1.5 - dt) / 0.3))
            label = e["text"]
            parts = [label] if "click" in label or "rotate" in label else [p.strip() for p in label.split("+")]
            f = font(40, True)
            widths = [max(70 * U, f.getlength(p) + 48 * U) for p in parts]
            total = sum(widths) + 44 * U * (len(parts) - 1)
            x = OW / 2 - total / 2
            y = OH - 270 * U + (1 - k) * 20 * U
            for i, p in enumerate(parts):
                patch, pad = card_patch(widths[i], 80 * U, int(14 * U), (255, 255, 255, 250),
                                        outline=(205, 212, 224, 255), ow=2)
                d = ImageDraw.Draw(patch)
                d.line([(pad + 10, pad + 74 * U), (pad + widths[i] - 10, pad + 74 * U)], fill=(205, 212, 224, 255), width=3)
                d.text((pad + widths[i] / 2, pad + 38 * U), p, font=f, fill=INK, anchor="mm")
                paste_patch(img, patch, x - pad, y - pad, k)
                x += widths[i]
                if i < len(parts) - 1:
                    plus = Image.new("RGBA", (int(44 * U), int(80 * U)), (0, 0, 0, 0))
                    ImageDraw.Draw(plus).text((22 * U, 40 * U), "+", font=font(36, True), fill=WHITE, anchor="mm")
                    paste_patch(img, plus, x, y, k)
                    x += 44 * U

    def redact(self, img, ci, r, tf):
        if r is None:
            return
        ev = self.takes[ci]["events"]
        off = self.takes[ci]["offset"]
        for i, e in enumerate(ev):
            if e["type"] != "redact" or not e.get("rect"):
                continue
            end = next((x for x in ev[i + 1:] if x["type"] == "redact_end"), None)
            t0, t1 = e["t"] + off - 0.4, (end["t"] + off + 0.4) if end else 1e9
            if t0 <= r <= t1:
                b = [int(v) for v in rect_out(e["rect"], tf)]
                b = [max(0, b[0]), max(0, b[1]), min(OW, b[2]), min(OH, b[3])]
                if b[2] > b[0] and b[3] > b[1]:
                    region = img.crop(b).filter(ImageFilter.GaussianBlur(18 * U))
                    region = region.resize((max(1, (b[2] - b[0]) // 24), max(1, (b[3] - b[1]) // 24))).resize((b[2] - b[0], b[3] - b[1]))
                    img.paste(region, b[:2])

    def caption(self, img, t):
        for c in self.captions:
            if c["t0"] <= t < c["t1"]:
                f = font(34)
                lines = wrap(c["text"], f, 1400 * U)
                lw = max(f.getlength(l) for l in lines)
                bw, bh = lw + 64 * U, 24 * U + 48 * U * len(lines)
                patch, pad = card_patch(bw, bh, int(16 * U), (12, 18, 30, 210), shadow=False)
                d = ImageDraw.Draw(patch)
                for i, l in enumerate(lines):
                    d.text((pad + bw / 2, pad + 12 * U + 48 * U * i + 24 * U), l, font=f, fill=WHITE, anchor="mm")
                paste_patch(img, patch, OW / 2 - bw / 2 - pad, OH - 80 * U - bh - pad)
                return

    def speed_badge(self, img, seg):
        if seg.get("kind") == "video" and seg.get("speed", 1) > 1:
            txt = f"▶▶  ×{seg['speed']:g}"
            f = font(26, True)
            w = f.getlength(txt) + 44 * U
            patch, pad = card_patch(w, 54 * U, int(27 * U), (15, 27, 51, 225), shadow=False)
            ImageDraw.Draw(patch).text((pad + w / 2, pad + 27 * U), txt, font=f, fill=WHITE, anchor="mm")
            paste_patch(img, patch, OW - w - 40 * U, 36 * U)


# ── title cards ───────────────────────────────────────────────────────────────
_BG = None
_LOGO = None


def background():
    global _BG
    if _BG is None:
        g = np.linspace(0, 1, OH)[:, None]
        gx = np.linspace(0, 1, OW)[None, :]
        k = np.clip(0.6 * g + 0.4 * gx, 0, 1)[..., None]
        img = (np.array(NAVY, float) * (1 - k) + np.array(NAVY2, float) * k).astype(np.uint8)
        im = Image.fromarray(img, "RGB").convert("RGBA")
        glow = Image.new("RGBA", (OW, OH), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([OW * 0.55, -OH * 0.4, OW * 1.3, OH * 0.7], fill=(47, 134, 255, 70))
        im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(160)))
        _BG = im.convert("RGB")
    return _BG.copy()


def logo(size):
    global _LOGO
    if _LOGO is None:
        _LOGO = Image.open(os.path.join(ROOT, "ui", "assets", "brand", "office-axe-mark.png")).convert("RGBA")
    im = _LOGO.copy()
    im.thumbnail((int(size), int(size)), Image.LANCZOS)
    return im


def intro_card(dt, dur):
    img = background()
    d = ImageDraw.Draw(img, "RGBA")
    k1, k2, k3 = ease_out(dt / 0.8), ease_out((dt - 0.35) / 0.8), ease_out((dt - 0.75) / 0.8)
    lg = logo((150 + 20 * k1) * U)
    img.paste(lg, (int(OW / 2 - lg.width / 2), int(300 * U - lg.height / 2 + (1 - k1) * 30)), lg)
    d.text((OW / 2, 470 * U + (1 - k2) * 24), "Office Axe", font=font(124, True), fill=WHITE + (int(255 * k2),), anchor="mm")
    d.text((OW / 2, 575 * U + (1 - k3) * 18), "Every student document task, in one desktop app", font=font(46),
           fill=(205, 220, 245, int(255 * k3)), anchor="mm")
    lw = 300 * U * ease_out((dt - 1.0) / 0.7)
    d.rounded_rectangle([OW / 2 - lw / 2, 640 * U, OW / 2 + lw / 2, 646 * U], radius=3, fill=ACCENT)
    d.text((OW / 2, 722 * U), "17 features in 6 minutes  ·  Windows", font=font(30), fill=(160, 180, 210, int(255 * k3)), anchor="mm")
    return img


def chapter_card(dt, dur, key, script, bg_frame, total):
    meta = script["sections"].get(key, {})
    bw, bh = bg_frame.size
    top = int(bh * 0.05)                              # leave out the dark window title bar
    bg = bg_frame.crop((0, top, bw, bh)).resize((OW, OH), Image.BILINEAR).filter(ImageFilter.GaussianBlur(26 * U))
    dark = Image.new("RGB", (OW, OH), NAVY)
    img = Image.blend(bg, dark, 0.72)
    d = ImageDraw.Draw(img, "RGBA")
    k1, k2, k3 = ease_out(dt / 0.55), ease_out((dt - 0.15) / 0.55), ease_out((dt - 0.3) / 0.55)
    n = meta.get("n", 0)
    d.text((OW / 2, 520 * U + (1 - k1) * 30), f"{n:02d}", font=font(150, True), fill=ACCENT + (int(255 * k1),), anchor="mm")
    d.text((OW / 2, 660 * U + (1 - k2) * 26), meta.get("title", key), font=font(84, True), fill=WHITE + (int(255 * k2),), anchor="mm")
    d.text((OW / 2, 750 * U + (1 - k3) * 20), meta.get("tagline", ""), font=font(40), fill=(190, 205, 230, int(255 * k3)), anchor="mm")
    # progress dots
    gap = 26 * U
    x0 = OW / 2 - gap * (total - 1) / 2
    for i in range(total):
        r = (7 if i + 1 == n else 5) * U
        col = ACCENT + (255,) if i + 1 == n else ((255, 255, 255, 170) if i + 1 < n else (255, 255, 255, 60))
        d.ellipse([x0 + gap * i - r, 880 * U - r, x0 + gap * i + r, 880 * U + r], fill=col)
    return img


def outro_card(dt, dur, script):
    img = background()
    d = ImageDraw.Draw(img, "RGBA")
    k1, k2 = ease_out(dt / 0.7), ease_out((dt - 0.3) / 0.7)
    lg = logo(120 * U)
    img.paste(lg, (int(OW / 2 - lg.width / 2), int(150 * U - lg.height / 2 + (1 - k1) * 20)), lg)
    d.text((OW / 2, 285 * U), "Office Axe", font=font(100, True), fill=WHITE + (int(255 * k1),), anchor="mm")
    d.text((OW / 2, 365 * U), "Student documents, sorted.", font=font(40), fill=(205, 220, 245, int(255 * k2)), anchor="mm")
    items = [v["title"] for v in sorted(script["sections"].values(), key=lambda v: v["n"])]
    cols = 2
    per = math.ceil(len(items) / cols)
    for i, it in enumerate(items):
        k = ease_out((dt - 0.6 - 0.07 * i) / 0.5)
        col, row = divmod(i, per)
        x = OW / 2 - 640 * U + col * 680 * U + (1 - k) * 24
        y = 470 * U + row * 56 * U
        d.ellipse([x, y - 15 * U, x + 30 * U, y + 15 * U], fill=ACCENT + (int(255 * k),))
        d.line([(x + 9 * U, y), (x + 14 * U, y + 6 * U), (x + 22 * U, y - 7 * U)], fill=WHITE + (int(255 * k),), width=int(4 * U))
        d.text((x + 48 * U, y), it, font=font(32), fill=WHITE + (int(255 * k),), anchor="lm")
    return img


# ── audio ─────────────────────────────────────────────────────────────────────
def load_wav(path):
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        xs = np.linspace(0, len(data) - 1, int(len(data) * SR / sr))
        data = np.interp(xs, np.arange(len(data)), data).astype(np.float32)
    return data


def build_audio(tl, takes, ov, voice_dir, path):
    n = int(math.ceil(tl["duration"] * SR)) + SR
    voice = np.zeros(n, np.float32)
    fx = np.zeros(n, np.float32)

    def add(track, clip, t, gain=1.0):
        i = int(t * SR)
        if 0 <= i < n:
            seg = clip[: n - i]
            track[i:i + len(seg)] += seg * gain

    for line in tl["narration"]:
        add(voice, load_wav(os.path.join(voice_dir, line["id"] + ".wav")), line["t0"])
    segs = tl["segments"]
    S_CLICK, S_POP, S_CHIME, S_WHOOSH = sfx.click(), sfx.pop(), sfx.chime(), sfx.whoosh()
    for s in segs:
        if s["kind"] == "card" and s["card"] == "chapter":
            add(fx, S_WHOOSH, s["t0"] - 0.15, 0.9)
        if s["kind"] == "card" and s["card"] in ("intro", "outro"):
            add(fx, S_CHIME, s["t0"] + 0.7, 0.55)

    def at_normal_speed(ci, r):
        for s in segs:
            if s["kind"] == "video" and s["take"] == ci and s["r0"] <= r <= s["r1"]:
                return s["speed"] == 1.0
        return False

    for ci, tk in enumerate(takes):
        off = tk["offset"]
        for e in tk["events"]:
            r = e["t"] + off
            if not at_normal_speed(ci, r):
                continue
            t = raw_to_out(segs, ci, r)
            if e["type"] == "click" and e.get("sound", True):
                add(fx, S_CLICK, t + 0.02, 0.9)
                if e.get("double"):
                    add(fx, S_CLICK, t + 0.15, 0.7)
            elif e["type"] == "key":
                add(fx, sfx.key(), t, 1.6)
            elif e["type"] == "typing":
                chars, dur = max(1, e.get("chars", 1)), e.get("dur", 0.5)
                step = max(0.06, dur / chars)
                k = 0.0
                while k < dur:
                    add(fx, sfx.key(), t + k, 1.0)
                    k += step
            elif e["type"] == "success":
                add(fx, S_CHIME, t, 0.8)
        for t0, t1, e in ov.focus_windows.get(ci, []):
            if at_normal_speed(ci, t0):
                add(fx, S_POP, raw_to_out(segs, ci, t0) + 0.05, 0.8)

    bed = sfx.music(n / SR)
    bed = sfx.duck(bed, voice, amount=0.55)
    mix = voice * 1.0 + fx * 0.55 + bed * 0.16
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = np.clip(mix / max(peak, 0.97) * 0.95, -1, 1)
    stereo = np.repeat(mix[:, None], 2, axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((stereo * 32767).astype(np.int16).tobytes())


def write_srt(captions, path):
    def ts(s):
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    with open(path, "w", encoding="utf-8") as fh:
        for i, c in enumerate(captions, 1):
            fh.write(f"{i}\n{ts(c['t0'])} --> {ts(c['t1'])}\n{c['text']}\n\n")


# ── render ────────────────────────────────────────────────────────────────────
def render(run, script, voice_dir, out_path, srt_path, preview=False, only=None):
    takes = load_takes(run, only)
    if not takes:
        raise SystemExit("no successful takes found in " + run)
    tl = build_timeline(takes, script, voice_dir)
    ov = Overlays(takes, tl, script)
    with open(os.path.join(run, "timeline.json"), "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in tl.items()}, fh, ensure_ascii=False, indent=1)
    write_srt(ov.captions, srt_path)
    wav = os.path.join(run, "soundtrack.wav")
    build_audio(tl, takes, ov, voice_dir, wav)
    print(f"timeline {tl['duration']:.1f}s, {len(takes)} chapters")

    total = int(round(tl["duration"] * FPS))
    scale = 0.5 if preview else 1.0
    ow, oh = int(OW * scale), int(OH * scale)
    enc = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ow}x{oh}", "-r", str(FPS), "-i", "-",
                            "-i", wav, "-c:v", "libx264", "-preset", "veryfast" if preview else "medium",
                            "-crf", "22" if preview else "18", "-pix_fmt", "yuv420p", "-profile:v", "high",
                            "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-shortest", out_path],
                           stdin=subprocess.PIPE)
    readers = {}
    cams = {ci: Camera(tk) for ci, tk in enumerate(takes)}
    segs = tl["segments"]

    def reader(ci):
        if ci not in readers:
            for k in list(readers):                 # one open decoder at a time
                readers.pop(k).close()
            tk = takes[ci]
            readers[ci] = RawReader(tk["raw"], tk["w"], tk["h"])
        return readers[ci]

    # Gaps where the app window was minimised / moved for a moment (logged by
    # the recorder): show the last good frame instead, never what was behind.
    lost = {}
    for ci, tk in enumerate(takes):
        sp, cur = [], None
        for e in tk["events"]:
            if e["type"] == "window_lost" and cur is None:
                cur = e["t"] + tk["offset"] - 0.3
            elif e["type"] == "window_back" and cur is not None:
                sp.append((cur, e["t"] + tk["offset"] + 0.5))
                cur = None
        if cur is not None:
            sp.append((cur, 1e9))
        # spans found afterwards in the footage (e.g. a taskbar preview popup)
        sp += [(e["a"], e["b"]) for e in tk["events"] if e["type"] == "mask_raw"]
        lost[ci] = sp
    last_good = {}

    def masked(ci, r):
        return next(((a, b) for a, b in lost[ci] if a <= r <= b), None)

    def video_frame(ci, r):
        span = masked(ci, r)
        if span and ci in last_good:
            img = last_good[ci]
        elif span:                                   # masked from the start: use the next clean frame
            img = reader(ci).get(int(round((span[1] + 0.05) * FPS)))
            last_good[ci] = img.copy()
        else:
            img = reader(ci).get(int(round(r * FPS)))
            last_good[ci] = img.copy()
        cam = cams[ci].at(r)
        return frame_through(img, cam)

    last_video = {}
    try:
        for fi in range(total):
            t = fi / FPS
            s = seg_at(segs, t)
            if s["kind"] == "card":
                dt, dur = t - s["t0"], s["t1"] - s["t0"]
                if s["card"] == "intro":
                    img = intro_card(dt, dur)
                elif s["card"] == "outro":
                    img = outro_card(dt, dur, script)
                    if dt < 0.6 and last_video.get("img") is not None:
                        img = Image.blend(last_video["img"], img, ease(dt / 0.6))
                else:
                    ci = s["take"]
                    if "bg" not in s:
                        cams[ci].reset()
                        rbg = s["r"]
                        span = masked(ci, rbg)
                        if span:
                            rbg = span[1] + 0.05
                        s["bg"] = reader(ci).get(int(round(rbg * FPS))).copy()
                    img = chapter_card(dt, dur, s["key"], script, s["bg"], len(script["sections"]))
                    if dt < 0.35 and last_video.get("img") is not None:
                        img = Image.blend(last_video["img"], img, ease(dt / 0.35))
                    if dur - dt < 0.4:
                        nxt = next(x for x in segs if x["kind"] == "video" and x["take"] == ci)
                        vid, _ = video_frame(ci, nxt["r0"])
                        img = Image.blend(img, vid, ease(1 - (dur - dt) / 0.4))
                ov.caption(img, t)
            else:
                ci = s["take"]
                r = s["r0"] + (t - s["t0"]) * s["speed"] if s["kind"] == "video" else s["r"]
                img, tf = video_frame(ci, r)
                live = r if s["kind"] == "video" else None
                ov.redact(img, ci, r, tf)
                ov.highlight(img, ci, r, tf)
                ov.ripples(img, ci, live, tf)
                ov.drags(img, ci, live, tf)
                ov.keycaps(img, ci, live)
                ov.speed_badge(img, s)
                ov.chapter_label(img, t, takes[ci]["key"])
                ov.caption(img, t)
                last_video["img"] = img
            if scale != 1.0:
                img = img.resize((ow, oh), Image.BILINEAR)
            enc.stdin.write(img.tobytes())
            if fi % (FPS * 10) == 0:
                print(f"  {t:6.1f}s / {tl['duration']:.1f}s", flush=True)
    finally:
        for r in readers.values():
            r.close()
        enc.stdin.close()
        enc.wait()
    return tl


def make_1080p(src, dst):
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", src,
                    "-vf", "scale=1920:1080:flags=lanczos", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-c:a", "copy", dst], check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--script", default=os.path.join(HERE, "narration.json"))
    ap.add_argument("--voice-dir", default=os.path.join(HERE, "work", "voice2"))
    ap.add_argument("--out", default=os.path.join(HERE, "office-axe-demo-1440p.mp4"))
    ap.add_argument("--srt", default=os.path.join(HERE, "narration.srt"))
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--chapters", default="")
    ap.add_argument("--no-1080p", action="store_true")
    a = ap.parse_args(argv)
    script = json.load(open(a.script, encoding="utf-8"))
    only = [int(x) for x in a.chapters.split(",") if x.strip()] or None
    tl = render(os.path.abspath(a.run), script, a.voice_dir, a.out, a.srt, a.preview, only)
    print(f"wrote {a.out} ({tl['duration']:.1f}s) and {a.srt}")
    if not a.preview and not a.no_1080p:
        dst = a.out.replace("-1440p", "") [:-4] + "-1080p.mp4"
        make_1080p(a.out, dst)
        print(f"wrote {dst}")
        # a compact copy for sharing / the repository (GitHub refuses files > 100 MB)
        web = os.path.join(os.path.dirname(a.out), "office-axe-demo.mp4")
        subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", dst, "-c:v", "libx264",
                        "-preset", "slow", "-crf", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        "-c:a", "aac", "-b:a", "128k", web], check=True)
        print(f"wrote {web}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
