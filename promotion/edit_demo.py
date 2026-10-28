# -*- coding: utf-8 -*-
"""
edit_demo.py — turn a raw take into the finished product video.

    python promotion/edit_demo.py promotion/work/<take> [--voice-dir promotion/work/voice]
                                  [--out promotion/office-axe-demo.mp4] [--preview]

Inputs
    <take>/raw.mp4       real screen recording (record_demo.py)
    <take>/events.json   what happened when, with on-screen rectangles
    narration.json       script, section titles and callout texts
    <voice-dir>/*.wav    one WAV per narration line (make_voiceover.py)

Output
    office-axe-demo.mp4  1920×1080, 30 fps, H.264 + AAC
    narration.srt        subtitles with the same timing as the burned-in captions
    <take>/timeline.json the computed edit (for debugging / re-use)

The motion graphics (title cards, section titles, zooms, highlight boxes,
callouts, click ripples, key caps, captions) are drawn here with Pillow on
top of the real frames; the app itself is never altered.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
W, H, FPS = 1920, 1080, 30
SR = 48000

# ── look ──────────────────────────────────────────────────────────────────────
NAVY = (15, 27, 51)
NAVY2 = (22, 44, 86)
ACCENT = (47, 134, 255)
ACCENT_SOFT = (47, 134, 255, 60)
WHITE = (255, 255, 255)
INK = (24, 31, 46)
MUTED = (110, 120, 138)
CARD = (255, 255, 255, 245)
CAPTION_BG = (12, 18, 30, 205)

_FONT_CACHE: dict = {}


def font(size: int, bold: bool = False):
    """Helvetica-class fonts bundled with PyMuPDF (no system font folder needed)."""
    key = (size, bold)
    if key not in _FONT_CACHE:
        import pymupdf
        data = pymupdf.Font("hebo" if bold else "helv").buffer
        _FONT_CACHE[key] = ImageFont.truetype(io.BytesIO(data), size)
    return _FONT_CACHE[key]


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


# ── video I/O ─────────────────────────────────────────────────────────────────
class RawReader:
    """Sequential frames of raw.mp4 at a constant 30 fps (frame i = i / 30 s)."""

    def __init__(self, path: str):
        self.p = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", path,
                                   "-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                  stdout=subprocess.PIPE)
        self.idx = -1
        self.frame = None
        self.size = W * H * 3

    def get(self, i: int):
        i = max(0, i)
        while self.idx < i:
            buf = self.p.stdout.read(self.size)
            if len(buf) < self.size:
                break                       # past the end: keep the last frame
            self.frame = buf
            self.idx += 1
        return Image.frombuffer("RGB", (W, H), self.frame, "raw", "RGB", 0, 1)

    def close(self):
        try:
            self.p.kill()
        except Exception:
            pass


def find_sync(raw_path: str, x: int, y: int, max_s: float = 10.0) -> float | None:
    """Video time of the first frame whose pixel (x, y) is magenta."""
    r = RawReader(raw_path)
    try:
        for i in range(int(max_s * FPS)):
            img = r.get(i)
            px = img.getpixel((x, y))
            if px[0] > 200 and px[2] > 200 and px[1] < 80:
                return i / FPS
    finally:
        r.close()
    return None


# ── timeline ──────────────────────────────────────────────────────────────────
def wav_info(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def build_timeline(events: list, offset: float, script: dict, voice_dir: str) -> dict:
    """Segments (cards, video, holds), narration placement and overlays — all
    in output seconds."""
    vt = lambda e: e["t"] + offset                      # event → raw video time
    by_type = lambda t: [e for e in events if e["type"] == t]
    secs = by_type("section")
    end_t = vt(by_type("end")[0]) if by_type("end") else vt(events[-1])
    durs = {ln["id"]: wav_info(os.path.join(voice_dir, ln["id"] + ".wav")) for ln in script["lines"]}
    lines_for = lambda sec: [ln for ln in script["lines"] if ln["section"] == sec]

    segs, narr = [], []
    out = 0.0

    # opening card
    intro_d = max(6.5, sum(durs[l["id"]] for l in lines_for("intro")) + 1.6)
    segs.append({"kind": "card", "card": "intro", "t0": out, "t1": out + intro_d})
    t = out + 0.8
    for ln in lines_for("intro"):
        narr.append({"id": ln["id"], "text": ln["text"], "t0": t, "t1": t + durs[ln["id"]]})
        t += durs[ln["id"]] + 0.3
    out += intro_d

    sections = []
    for k, s in enumerate(secs):
        r0 = vt(s) - 0.2
        r1 = vt(secs[k + 1]) - 0.2 if k + 1 < len(secs) else end_t
        # speed up long waits (Word starting up): logged by the recorder as task card → task done
        pieces = [(r0, r1, 1.0)]
        if s["name"] == "convert":
            fc = [e for e in events if e["type"] == "focus" and e.get("name") == "task card"]
            fd = [e for e in events if e["type"] == "focus" and e.get("name") == "task done"]
            if fc and fd and vt(fd[0]) - vt(fc[0]) > 4:
                a, b = vt(fc[0]) + 1.2, vt(fd[0]) - 0.4
                pieces = [(r0, a, 1.0), (a, b, 4.0), (b, r1, 1.0)]
        sec_out0 = out
        for a, b, sp in pieces:
            d = (b - a) / sp
            segs.append({"kind": "video", "r0": a, "r1": b, "speed": sp, "t0": out, "t1": out + d,
                         "section": s["name"]})
            out += d
        # narration for this section
        t = sec_out0 + 0.9
        lines = lines_for(s["name"])
        # second line of a section starts at a meaningful moment when we can
        anchors = {"s2b": ("key", "F2"), "s4b": ("click", "sideways page")}
        for ln in lines:
            if ln["id"] in anchors:
                typ, name = anchors[ln["id"]]
                ev = next((e for e in events if e["type"] == typ and (e.get("name") == name or e.get("text") == name)
                           and vt(e) >= r0), None)
                if ev:
                    t = max(t, map_raw_to_out(segs, vt(ev)) - 0.2)
            narr.append({"id": ln["id"], "text": ln["text"], "t0": t, "t1": t + durs[ln["id"]]})
            t += durs[ln["id"]] + 0.35
        need = t - out + 0.2
        if need > 0:                                    # narration longer than the footage: hold the last frame
            segs.append({"kind": "hold", "r": r1, "t0": out, "t1": out + need, "section": s["name"]})
            out += need
        sections.append({"name": s["name"], "t0": sec_out0, "t1": out})

    outro_d = max(6.0, sum(durs[l["id"]] for l in lines_for("outro")) + 2.2)
    segs.append({"kind": "card", "card": "outro", "t0": out, "t1": out + outro_d})
    t = out + 0.9
    for ln in lines_for("outro"):
        narr.append({"id": ln["id"], "text": ln["text"], "t0": t, "t1": t + durs[ln["id"]]})
    out += outro_d
    return {"segments": segs, "narration": narr, "sections": sections, "duration": out, "offset": offset}


def map_raw_to_out(segs, r: float) -> float:
    for s in segs:
        if s["kind"] == "video" and s["r0"] <= r <= s["r1"]:
            return s["t0"] + (r - s["r0"]) / s["speed"]
    return 0.0


def out_to_raw(segs, t: float):
    for s in segs:
        if s["t0"] <= t < s["t1"]:
            if s["kind"] == "video":
                return s, s["r0"] + (t - s["t0"]) * s["speed"]
            if s["kind"] == "hold":
                return s, s["r"]
            return s, None
    return segs[-1], None


# ── camera (zoom) ─────────────────────────────────────────────────────────────
def zoom_windows(events, offset):
    """[(r_start, r_end, rect, scale)] in raw time: where the camera zooms in."""
    vt = lambda e: e["t"] + offset
    ev = events

    def after(name_or_text, t, typ=None):
        for e in ev:
            if vt(e) > t and (typ is None or e["type"] == typ) and \
                    (e.get("name") == name_or_text or e.get("text") == name_or_text):
                return vt(e)
        return None

    out = []
    for e in ev:
        if e["type"] not in ("focus", "typing") or not e.get("rect"):
            continue
        n, t = e.get("name"), vt(e)
        rule = {
            "names":       ("click", "Create", -0.15, 1.75),
            "suggestions": ("click", "학사 학위증명서 (아포스티유 포함)", 0.9, 1.9),
            "task card":   ("focus", "task done", 1.8, 1.9),
            "page grid":   ("click", "Save", -0.25, 1.35),
        }.get(n)
        if not rule:
            continue
        typ, until, extra, scale = rule
        t_end = after(until, t, typ)
        if t_end is None:
            continue
        rect = e["rect"]
        if n == "names":                       # the whole dialog: names on the left, preview on the right
            dlg = next((x for x in ev if x["type"] == "focus" and x.get("name") == "dialog"), None)
            rect = dlg["rect"] if dlg else rect
        if n == "suggestions":                 # start a little earlier: when F2 is pressed
            f2 = next((vt(x) for x in reversed(ev) if x["type"] == "key" and x.get("text") == "F2" and vt(x) < t), t)
            t = f2 + 0.2
        out.append((t - 0.1, t_end + extra, rect, scale))
    return out


def camera_at(zooms, r: float):
    """(cx, cy, scale) of the virtual camera at raw time r (eased in/out)."""
    full = (W / 2, H / 2, 1.0)
    for a, b, rect, smax in zooms:
        if a - 0.6 <= r <= b + 0.6:
            x, y, w, h = rect
            pad = 70
            sc = min(smax, W / (w + 2 * pad), H / (h + 2 * pad))
            sc = max(1.0, sc)
            cx, cy = x + w / 2, y + h / 2
            hw, hh = W / sc / 2, H / sc / 2
            cx, cy = min(max(cx, hw), W - hw), min(max(cy, hh), H - hh)
            k = ease((r - (a - 0.6)) / 0.6) if r < a else ease(((b + 0.6) - r) / 0.6) if r > b else 1.0
            return (full[0] + (cx - full[0]) * k, full[1] + (cy - full[1]) * k, 1.0 + (sc - 1.0) * k)
    return full


def apply_camera(img: Image.Image, cam):
    cx, cy, sc = cam
    if sc <= 1.001:
        return img, (0.0, 0.0, 1.0)
    cw, ch = W / sc, H / sc
    x0, y0 = cx - cw / 2, cy - ch / 2
    box = (x0, y0, x0 + cw, y0 + ch)
    return img.resize((W, H), Image.BICUBIC, box=box), (x0, y0, sc)


def to_out(pt, tf):
    x0, y0, sc = tf
    return (pt[0] - x0) * sc, (pt[1] - y0) * sc


def rect_out(r, tf):
    a = to_out((r[0], r[1]), tf)
    b = to_out((r[0] + r[2], r[1] + r[3]), tf)
    return [a[0], a[1], b[0], b[1]]


# ── drawing helpers ───────────────────────────────────────────────────────────
def rounded(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle([round(v) for v in box], radius=r, fill=fill, outline=outline, width=width)


def shadow_card(layer, box, radius=18, fill=CARD, blur=18, alpha=70):
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([box[0], box[1] + 6, box[2], box[3] + 10], radius=radius,
                                         fill=(0, 0, 0, alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    layer.alpha_composite(sh)
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=fill)


def wrap(text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if fnt.getlength(test) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def caption_chunks(text, max_chars=78):
    """Split a narration line into caption-sized pieces at sentence/comma breaks."""
    import re
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
    def __init__(self, events, offset, tl, script, segs):
        self.ev = events
        self.off = offset
        self.tl = tl
        self.script = script
        self.segs = segs
        self.vt = lambda e: e["t"] + offset
        self.captions = []
        for n in tl["narration"]:
            chunks = caption_chunks(n["text"])
            total = sum(len(c) for c in chunks)
            t = n["t0"]
            for c in chunks:
                d = (n["t1"] - n["t0"]) * len(c) / total
                self.captions.append({"t0": t, "t1": t + d, "text": c})
                t += d

    def section_title(self, layer, t):
        for s in self.tl["sections"]:
            dt = t - s["t0"]
            if 0 <= dt <= 3.2:
                meta = self.script["sections"].get(s["name"], {})
                k_in, k_out = ease_out(dt / 0.5), ease(min(1.0, (3.2 - dt) / 0.45))
                a = min(k_in, k_out)
                f1, f2 = font(40, True), font(22)
                title = meta.get("title", s["name"])
                n = meta.get("n", "")
                tw = f1.getlength(title)
                w_ = int(tw + 150)
                x = int(48 - (1 - k_in) * 60)
                y = 54
                card = Image.new("RGBA", layer.size, (0, 0, 0, 0))
                shadow_card(card, [x, y, x + w_, y + 104], radius=22, fill=(255, 255, 255, 250), alpha=60)
                d = ImageDraw.Draw(card)
                d.ellipse([x + 22, y + 24, x + 78, y + 80], fill=ACCENT)
                d.text((x + 50, y + 52), str(n), font=font(30, True), fill=WHITE, anchor="mm")
                d.text((x + 98, y + 22), f"STEP {n} OF 4", font=f2, fill=MUTED)
                d.text((x + 98, y + 48), title, font=f1, fill=INK)
                card.putalpha(card.getchannel("A").point(lambda v: int(v * a)))
                layer.alpha_composite(card)

    def highlight(self, layer, t, raw, tf):
        """Box + callout for focus events that have a callout text."""
        callouts = self.script.get("callouts", {})
        for e in self.ev:
            if e["type"] != "focus" or e.get("name") not in callouts or not e.get("rect"):
                continue
            rt = self.vt(e)
            nxt = next((self.vt(x) for x in self.ev if self.vt(x) > rt + 0.3 and x["type"] in ("focus", "section", "click")), rt + 3.0)
            r_end = min(rt + 3.2, nxt + 0.2) if e.get("name") != "task card" else nxt
            if raw is None or not (rt <= raw <= r_end):
                continue
            k = min(ease_out((raw - rt) / 0.35), ease((r_end - raw) / 0.3))
            box = rect_out(e["rect"], tf)
            pad = 8
            box = [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad]
            box = [max(6, box[0]), max(6, box[1]), min(W - 6, box[2]), min(H - 6, box[3])]
            lay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            grow = (1 - k) * 14
            rounded(d, [box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow], 14,
                    outline=ACCENT + (int(255 * k),), width=4)
            # callout card beside the box, with a short connector
            text = callouts[e["name"]]
            f = font(28, True)
            lines = wrap(text, f, 420)
            cw = int(max(f.getlength(l_) for l_ in lines) + 56)
            ch = 30 + 38 * len(lines)
            right_space = W - box[2]
            if right_space > cw + 80:
                cx0 = box[2] + 40
            elif box[0] > cw + 80:
                cx0 = box[0] - 40 - cw
            else:
                cx0 = min(max(40, box[0]), W - cw - 40)
            cy0 = min(max(40, (box[1] + box[3]) / 2 - ch / 2), H - ch - 150)
            if box[1] - 20 < cy0 + ch and box[3] + 20 > cy0 and box[0] - 20 < cx0 + cw and box[2] + 20 > cx0:
                cy0 = min(box[3] + 24, H - ch - 150)          # would cover the box: put it below
            cx0 += (1 - k) * (16 if cx0 > box[2] else -16)
            shadow_card(lay, [cx0, cy0, cx0 + cw, cy0 + ch], radius=16, fill=(15, 27, 51, 240), alpha=80)
            d = ImageDraw.Draw(lay)
            d.rectangle([cx0, cy0 + 14, cx0 + 5, cy0 + ch - 14], fill=ACCENT)
            for i, l_ in enumerate(lines):
                d.text((cx0 + 28, cy0 + 16 + 38 * i), l_, font=f, fill=WHITE)
            # connector
            if cx0 > box[2]:
                p0, p1 = (box[2] + 4, (box[1] + box[3]) / 2), (cx0, cy0 + ch / 2)
            elif cx0 + cw < box[0]:
                p0, p1 = (box[0] - 4, (box[1] + box[3]) / 2), (cx0 + cw, cy0 + ch / 2)
            else:
                p0, p1 = ((box[0] + box[2]) / 2, box[3] + 4), (cx0 + cw / 2, cy0)
            d.line([p0, p1], fill=ACCENT + (int(230 * k),), width=3)
            d.ellipse([p0[0] - 6, p0[1] - 6, p0[0] + 6, p0[1] + 6], fill=ACCENT + (int(255 * k),))
            lay.putalpha(lay.getchannel("A").point(lambda v: int(v * k)))
            layer.alpha_composite(lay)

    def ripples(self, layer, raw, tf):
        if raw is None:
            return
        d = ImageDraw.Draw(layer)
        for e in self.ev:
            if e["type"] != "click" or "x" not in e:
                continue
            dt = raw - self.vt(e) - 0.05
            if 0 <= dt <= 0.6:
                x, y = to_out((e["x"], e["y"]), tf)
                k = ease_out(dt / 0.6)
                r = 10 + 34 * k
                a = int(220 * (1 - k))
                d.ellipse([x - r, y - r, x + r, y + r], outline=ACCENT + (a,), width=5)
                if dt < 0.25:
                    r2 = 14
                    d.ellipse([x - r2, y - r2, x + r2, y + r2], fill=(255, 214, 10, int(140 * (1 - dt / 0.25))))
        # drag: a soft trail along the drag path
        for e in self.ev:
            if e["type"] == "drag" and e.get("rect"):
                dt = raw - self.vt(e)
                if 0 <= dt <= 1.9:
                    x0, y0 = to_out((e["rect"][0] + e["rect"][2] / 2, e["rect"][1] + e["rect"][3] / 2), tf)
                    x1, y1 = to_out(tuple(e["to"]), tf)
                    k = ease((dt - 0.3) / 1.2)
                    a = int(200 * min(1.0, dt / 0.3) * (1 - max(0.0, (dt - 1.5) / 0.4)))
                    xm, ym = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
                    d.line([(x0, y0), (xm, ym)], fill=ACCENT + (a,), width=6)
                    d.ellipse([xm - 9, ym - 9, xm + 9, ym + 9], fill=ACCENT + (a,))

    def keycaps(self, layer, raw):
        if raw is None:
            return
        for e in self.ev:
            if e["type"] != "key" or e.get("text") in ("Ctrl+A",):
                continue
            dt = raw - self.vt(e)
            if 0 <= dt <= 1.5:
                k = min(ease_out(dt / 0.25), ease((1.5 - dt) / 0.3))
                label = e["text"].replace("Ctrl+Z", "Ctrl + Z")
                f = font(40, True)
                parts = [p.strip() for p in label.split("+")] if "+" in label and "click" not in label else [label]
                widths = [max(64, f.getlength(p) + 44) for p in parts]
                total = sum(widths) + 40 * (len(parts) - 1)
                x = W / 2 - total / 2
                y = H - 250 + (1 - k) * 20
                lay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
                for i, p in enumerate(parts):
                    shadow_card(lay, [x, y, x + widths[i], y + 76], radius=14, fill=(255, 255, 255, 250), alpha=90)
                    d = ImageDraw.Draw(lay)
                    rounded(d, [x, y, x + widths[i], y + 76], 14, outline=(205, 212, 224, 255), width=2)
                    d.line([(x + 10, y + 70), (x + widths[i] - 10, y + 70)], fill=(205, 212, 224, 255), width=3)
                    d.text((x + widths[i] / 2, y + 36), p, font=f, fill=INK, anchor="mm")
                    x += widths[i]
                    if i < len(parts) - 1:
                        d.text((x + 20, y + 36), "+", font=font(36, True), fill=WHITE, anchor="mm")
                        x += 40
                lay.putalpha(lay.getchannel("A").point(lambda v: int(v * k)))
                layer.alpha_composite(lay)

    def caption(self, layer, t):
        for c in self.captions:
            if c["t0"] <= t < c["t1"]:
                f = font(34)
                lines = wrap(c["text"], f, 1360)
                lw = max(f.getlength(l_) for l_ in lines)
                bw, bh = lw + 64, 22 + 46 * len(lines)
                x0, y0 = W / 2 - bw / 2, H - 70 - bh
                d = ImageDraw.Draw(layer)
                rounded(d, [x0, y0, x0 + bw, y0 + bh], 14, fill=CAPTION_BG)
                for i, l_ in enumerate(lines):
                    d.text((W / 2, y0 + 11 + 46 * i + 23), l_, font=f, fill=WHITE, anchor="mm")
                return

    def speed_badge(self, layer, seg):
        if seg and seg.get("kind") == "video" and seg.get("speed", 1) > 1:
            d = ImageDraw.Draw(layer)
            rounded(d, [W - 230, 40, W - 40, 100], 30, fill=(15, 27, 51, 220))
            d.text((W - 135, 70), f"▶▶ ×{int(seg['speed'])} speed", font=font(26, True), fill=WHITE, anchor="mm")


# ── title cards ───────────────────────────────────────────────────────────────
def background():
    g = np.linspace(0, 1, H)[:, None]
    gx = np.linspace(0, 1, W)[None, :]
    k = np.clip(0.6 * g + 0.4 * gx, 0, 1)[..., None]
    a, b = np.array(NAVY, float), np.array(NAVY2, float)
    img = (a * (1 - k) + b * k).astype(np.uint8)
    im = Image.fromarray(img, "RGB").convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.55, -H * 0.4, W * 1.3, H * 0.7], fill=(47, 134, 255, 70))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    return im


_BG = None
_LOGO = None


def logo(size):
    global _LOGO
    if _LOGO is None:
        p = os.path.join(ROOT, "ui", "assets", "brand", "office-axe-mark.png")
        _LOGO = Image.open(p).convert("RGBA")
    im = _LOGO.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    return im


def card_frame(kind, dt, dur):
    global _BG
    if _BG is None:
        _BG = background()
    img = _BG.copy()
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    fade_out = ease((dur - dt) / 0.5)
    if kind == "intro":
        k1, k2, k3 = ease_out(dt / 0.8), ease_out((dt - 0.35) / 0.8), ease_out((dt - 0.7) / 0.8)
        lg = logo(int(150 + 20 * k1))
        lay.alpha_composite(lg, (int(W / 2 - lg.width / 2), int(300 - lg.height / 2 + (1 - k1) * 30)))
        d.text((W / 2, 470 + (1 - k2) * 24), "Office Axe", font=font(124, True), fill=WHITE + (int(255 * k2),), anchor="mm")
        d.text((W / 2, 575 + (1 - k3) * 18), "Student documents, done in minutes", font=font(46),
               fill=(205, 220, 245, int(255 * k3)), anchor="mm")
        lw = 260 * ease_out((dt - 1.0) / 0.7)
        d.rounded_rectangle([W / 2 - lw / 2, 640, W / 2 + lw / 2, 646], radius=3, fill=ACCENT)
        d.text((W / 2, 720), "A desktop app for Windows", font=font(30), fill=(160, 180, 210, int(255 * k3)), anchor="mm")
    else:
        k1, k2 = ease_out(dt / 0.7), ease_out((dt - 0.3) / 0.7)
        lg = logo(120)
        lay.alpha_composite(lg, (int(W / 2 - lg.width / 2), int(200 - lg.height / 2 + (1 - k1) * 20)))
        d.text((W / 2, 340), "Office Axe", font=font(100, True), fill=WHITE + (int(255 * k1),), anchor="mm")
        d.text((W / 2, 425), "Student documents, sorted.", font=font(40), fill=(205, 220, 245, int(255 * k2)), anchor="mm")
        items = ["A folder for every student, from a list", "Instant preview and English–Korean renaming",
                 "Word to PDF in the background", "Arrange pages, with Ctrl+Z for every step"]
        for i, it in enumerate(items):
            k = ease_out((dt - 0.7 - 0.25 * i) / 0.6)
            y = 540 + 72 * i
            x = W / 2 - 400 + (1 - k) * 30
            d.ellipse([x, y - 18, x + 36, y + 18], fill=ACCENT + (int(255 * k),))
            d.line([(x + 10, y), (x + 16, y + 7), (x + 27, y - 8)], fill=WHITE + (int(255 * k),), width=4)
            d.text((x + 58, y), it, font=font(36), fill=WHITE + (int(255 * k),), anchor="lm")
    img.alpha_composite(lay)
    if kind == "intro":
        return img.convert("RGB"), fade_out
    fade_in = ease(dt / 0.5)
    return img.convert("RGB"), fade_in


# ── audio ─────────────────────────────────────────────────────────────────────
def build_audio(tl, voice_dir, path):
    n = int(math.ceil(tl["duration"] * SR))
    track = np.zeros(n, dtype=np.float32)
    for line in tl["narration"]:
        with wave.open(os.path.join(voice_dir, line["id"] + ".wav"), "rb") as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)
        if sr != SR:                                     # simple linear resample
            xs = np.linspace(0, len(data) - 1, int(len(data) * SR / sr))
            data = np.interp(xs, np.arange(len(data)), data).astype(np.float32)
        i0 = int(line["t0"] * SR)
        seg = data[: max(0, n - i0)]
        track[i0:i0 + len(seg)] += seg * 0.95
    peak = float(np.max(np.abs(track))) or 1.0
    track = np.clip(track / max(peak, 0.89) * 0.89, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((track * 32767).astype(np.int16).tobytes())


def write_srt(captions, path):
    def ts(s):
        ms = int(round(s * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    with open(path, "w", encoding="utf-8") as fh:
        for i, c in enumerate(captions, 1):
            fh.write(f"{i}\n{ts(c['t0'])} --> {ts(c['t1'])}\n{c['text']}\n\n")


# ── render ────────────────────────────────────────────────────────────────────
def render(take, script, voice_dir, out_path, srt_path, preview=False):
    with open(os.path.join(take, "events.json"), encoding="utf-8") as fh:
        log = json.load(fh)
    events = log["events"]
    raw = os.path.join(take, "raw.mp4")
    sync = next((e for e in events if e["type"] == "sync"), None)
    vsync = find_sync(raw, sync["x"], sync["y"]) if sync else None
    offset = (vsync - sync["t"]) if (sync and vsync is not None) else 0.6
    print(f"sync offset: {offset:+.3f}s (video time = event time {'+' if offset >= 0 else ''}{offset:.3f})")

    tl = build_timeline(events, offset, script, voice_dir)
    segs = tl["segments"]
    zooms = zoom_windows(events, offset)
    ov = Overlays(events, offset, tl, script, segs)
    with open(os.path.join(take, "timeline.json"), "w", encoding="utf-8") as fh:
        json.dump({**tl, "zooms": zooms, "captions": ov.captions}, fh, ensure_ascii=False, indent=1)
    write_srt(ov.captions, srt_path)
    wav = os.path.join(take, "narration_mix.wav")
    build_audio(tl, voice_dir, wav)

    total = int(round(tl["duration"] * FPS))
    scale = 0.5 if preview else 1.0
    ow, oh = int(W * scale), int(H * scale)
    enc = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ow}x{oh}", "-r", str(FPS), "-i", "-",
                            "-i", wav,
                            "-c:v", "libx264", "-preset", "veryfast" if preview else "slow", "-crf", "20" if preview else "17",
                            "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart",
                            "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2", "-shortest", out_path],
                           stdin=subprocess.PIPE)
    reader = RawReader(raw)
    first_video = next(s for s in segs if s["kind"] == "video")
    last_video = [s for s in segs if s["kind"] in ("video", "hold")][-1]
    try:
        for fi in range(total):
            t = fi / FPS
            seg, r = out_to_raw(segs, t)
            if seg["kind"] == "card":
                img, k = card_frame(seg["card"], t - seg["t0"], seg["t1"] - seg["t0"])
                # cross-fade into / out of the footage
                if seg["card"] == "intro" and k < 1:
                    vid = reader.get(int(round(first_video["r0"] * FPS)))
                    img = Image.blend(vid, img, k)
                elif seg["card"] == "outro" and k < 1:
                    rr = last_video["r1"] if last_video["kind"] == "video" else last_video["r"]
                    vid = reader.get(int(round(rr * FPS)))
                    img = Image.blend(vid, img, k)
            else:
                frame = reader.get(int(round(r * FPS)))
                cam = camera_at(zooms, r)
                img, tf = apply_camera(frame, cam)
                layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ov.ripples(layer, r if seg["kind"] == "video" else None, tf)
                ov.highlight(layer, t, r, tf)
                ov.keycaps(layer, r if seg["kind"] == "video" else None)
                ov.section_title(layer, t)
                ov.speed_badge(layer, seg)
                ov.caption(layer, t)
                img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
            if seg["kind"] == "card":
                ImageDraw.Draw(img)
                lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ov.caption(lay, t)
                img = Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB")
            if scale != 1.0:
                img = img.resize((ow, oh), Image.BILINEAR)
            enc.stdin.write(img.tobytes())
            if fi % (FPS * 5) == 0:
                print(f"  {t:5.1f}s / {tl['duration']:.1f}s", flush=True)
    finally:
        reader.close()
        enc.stdin.close()
        enc.wait()
    return tl


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("take")
    ap.add_argument("--script", default=os.path.join(HERE, "narration.json"))
    ap.add_argument("--voice-dir", default=os.path.join(HERE, "work", "voice"))
    ap.add_argument("--out", default=os.path.join(HERE, "office-axe-demo.mp4"))
    ap.add_argument("--srt", default=os.path.join(HERE, "narration.srt"))
    ap.add_argument("--preview", action="store_true", help="half size, fast encode")
    a = ap.parse_args(argv)
    with open(a.script, encoding="utf-8") as fh:
        script = json.load(fh)
    tl = render(os.path.abspath(a.take), script, a.voice_dir, a.out, a.srt, a.preview)
    print(f"wrote {a.out} ({tl['duration']:.1f}s) and {a.srt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
