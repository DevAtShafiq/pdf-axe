# -*- coding: utf-8 -*-
"""
remove_chapter.py — take one chapter out of the finished tour video without
re-recording: the chapter's footage and voice are cut, and everything that
mentions the chapter count is redrawn with the editor's own drawing code
(edit_demo.py), so it matches the rest of the video:

  * intro line "N features in M minutes"
  * every chapter card (number and progress dots)
  * the chapter badge (bottom left) of every later chapter
  * the outro feature list

    python promotion/remove_chapter.py ai [--src promotion/office-axe-demo.mp4]
        [--chapters webportal/static/video/chapters.json]
        [--captions webportal/static/video/captions.vtt] --out new.mp4

Writes the new video, plus <out>.chapters.json / .chapters.vtt / .captions.vtt
next to it, and narration.json without the section (with --write-script).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edit_demo as ed  # noqa: E402

W, H, FPS = 1920, 1080, 30
CARD = 2.4                      # edit_demo.build_timeline card_d


def fr(t):
    return int(round(t * FPS))


def to_out(img):
    """A 2560×1440 editor frame → this video's 1920×1080."""
    return img.resize((W, H), Image.LANCZOS)


def vtt_time(s):
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def fmt_vtt(t):
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def read_vtt(path):
    cues = []
    blocks = open(path, encoding="utf-8").read().split("\n\n")
    for b in blocks:
        lines = [l for l in b.strip().split("\n") if l.strip()]
        tl = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if tl is None:
            continue
        a, b2 = [x.strip() for x in lines[tl].split("-->")]
        cues.append([vtt_time(a), vtt_time(b2), "\n".join(lines[tl + 1:])])
    return cues


def write_vtt(cues, path, numbered=True):
    out = ["WEBVTT", ""]
    for i, (a, b, txt) in enumerate(cues, 1):
        if numbered:
            out.append(str(i))
        out += [f"{fmt_vtt(a)} --> {fmt_vtt(b)}", txt, ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("key", help="section key to remove, e.g. ai")
    ap.add_argument("--src", default=os.path.join(HERE, "office-axe-demo.mp4"))
    ap.add_argument("--script", default=os.path.join(HERE, "narration.json"))
    ap.add_argument("--chapters", default=os.path.join(ed.ROOT, "webportal", "static", "video", "chapters.json"))
    ap.add_argument("--captions", default=os.path.join(ed.ROOT, "webportal", "static", "video", "captions.vtt"))
    ap.add_argument("--chapter-times", default="",
                    help="JSON list of the source's chapter card start times, in order (default: --chapters)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crf", default="19")
    ap.add_argument("--write-script", action="store_true")
    a = ap.parse_args(argv)

    script = json.load(open(a.script, encoding="utf-8"))
    old_secs = sorted(script["sections"].items(), key=lambda kv: kv[1]["n"])
    rm_n = script["sections"][a.key]["n"]
    old_total = len(old_secs)
    times = json.loads(a.chapter_times) if a.chapter_times else \
        [c["t"] for c in sorted(json.load(open(a.chapters, encoding="utf-8")), key=lambda c: c["n"])]
    assert len(times) == old_total, "need one card time per section"
    cues = read_vtt(a.captions)
    # outro: its first narration line starts 1.6 s in (edit_demo.build_timeline)
    outro_t0 = cues[-1][0] - 1.6
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "csv=p=0", a.src]).decode().strip())

    cut0, cut1 = times[rm_n - 1], (times[rm_n] if rm_n < old_total else outro_t0)
    cut_f0, cut_f1 = fr(cut0), fr(cut1)
    shift = (cut_f1 - cut_f0) / FPS

    new = copy.deepcopy(script)
    del new["sections"][a.key]
    new["lines"] = [l for l in new["lines"] if l.get("section") != a.key]
    for k, v in new["sections"].items():
        if v["n"] > rm_n:
            v["n"] -= 1
    new_total = len(new["sections"])
    by_old_n = {v["n"]: k for k, v in script["sections"].items()}

    # source frame ranges
    cards = []                                  # (f0, f1, key) in source frames
    for i, t in enumerate(times):
        if i + 1 == rm_n:
            continue
        cards.append((fr(t), fr(t + CARD), by_old_n[i + 1]))
    sections = []                               # (f0, f1, key): video part of each later chapter
    for i, t in enumerate(times):
        n = i + 1
        if n <= rm_n:
            continue
        end = times[i + 1] if i + 1 < old_total else outro_t0
        sections.append((fr(t + CARD), fr(end), by_old_n[n]))
    intro_f1 = fr(times[0])
    outro_f0 = fr(outro_t0)
    new_minutes = round((dur - shift) / 60)
    intro_line = f"{new_total} features in {new_minutes} minutes  ·  Windows"

    def intro_card(dt, d):                      # the editor's intro with the new count
        img = ed.intro_card(dt, d)
        k3 = ed.ease_out((dt - 0.75) / 0.8)
        dr = ImageDraw.Draw(img, "RGBA")
        # repaint the line's band from a clean background, then the new text
        band = (0, int(690 * ed.U), ed.OW, int(755 * ed.U))
        img.paste(ed.background().crop(band), band[:2])
        dr.text((ed.OW / 2, 722 * ed.U), intro_line, font=ed.font(30),
                fill=(160, 180, 210, int(255 * k3)), anchor="mm")
        return img

    # chapter badge digits (bottom left): repaint only the number
    bfont = ed.ImageFont.truetype(__import__("io").BytesIO(__import__("pymupdf").Font("hebo").buffer), 20)

    def fix_badge(img, old_n, new_n):
        d = ImageDraw.Draw(img)
        x0, cy = 26 + 20, 1080 - 70 + 21
        wold = bfont.getlength(f"{old_n:02d}")
        bg = img.getpixel((36, cy))
        d.rectangle([x0 - 2, cy - 13, x0 + wold + 2, cy + 13], fill=bg)
        d.text((x0, cy), f"{new_n:02d}", font=bfont, fill=(255, 255, 255), anchor="lm")

    proc_in = subprocess.Popen(["ffmpeg", "-v", "error", "-i", a.src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               stdout=subprocess.PIPE)
    tmp_v = a.out + ".video.mp4"
    proc_out = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                                 "-crf", a.crf, "-pix_fmt", "yuv420p", "-profile:v", "high", tmp_v],
                                stdin=subprocess.PIPE)
    size = W * H * 3
    card_at = {f: (c0, c1, k) for c0, c1, k in cards for f in range(c0, c1)}
    sec_at = {f: k for s0, s1, k in sections for f in range(s0, s1)}
    # frames we need before drawing a card: the next video frame (end blend)
    need_next = {c1: (c0, c1, k) for c0, c1, k in cards}
    pending = []                               # card frames waiting for the frame after the card
    last_video = None
    bg_for = {}
    fi = 0
    while True:
        buf = proc_in.stdout.read(size)
        if len(buf) < size:
            break
        f = fi
        fi += 1
        if cut_f0 <= f < cut_f1:
            continue
        img = Image.frombuffer("RGB", (W, H), buf, "raw", "RGB", 0, 1).copy()
        if f in sec_at:
            k = sec_at[f]
            fix_badge(img, script["sections"][k]["n"], new["sections"][k]["n"])
        if f < intro_f1:
            dt = f / FPS
            fresh = to_out(intro_card(dt, times[0]))
            band = (0, 680, W, 740)
            img.paste(fresh.crop(band), band[:2])
        elif f in card_at:
            pending.append((f, img))
            continue
        elif f >= outro_f0:
            dt = (f - outro_f0) / FPS
            o = ed.outro_card(dt, dur - outro_t0, new)
            o = to_out(o)
            if dt < 0.6 and last_video is not None:
                o = Image.blend(last_video, o, ed.ease(dt / 0.6))
            o.paste(img.crop((0, 940, W, H)), (0, 940))       # keep the caption
            img = o
        if pending:                             # this is the first frame after a card
            c0, c1, k = card_at[pending[0][0]]
            bgf = img.resize((ed.OW, ed.OH), Image.BILINEAR)
            d = (c1 - c0) / FPS
            for pf, orig in pending:
                dt = (pf - c0) / FPS
                cimg = to_out(ed.chapter_card(dt, d, k, new, bgf, new_total))
                if dt < 0.35 and last_video is not None:
                    cimg = Image.blend(last_video, cimg, ed.ease(dt / 0.35))
                if d - dt < 0.4:
                    cimg = Image.blend(cimg, img, ed.ease(1 - (d - dt) / 0.4))
                proc_out.stdin.write(cimg.tobytes())
            pending = []
        if intro_f1 <= f < outro_f0:
            last_video = img
        proc_out.stdin.write(img.tobytes())
    proc_out.stdin.close()
    proc_out.wait()
    proc_in.wait()

    # audio without the chapter
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.src, "-i", tmp_v, "-filter_complex",
                    f"[0:a]atrim=0:{cut0:.4f},asetpts=PTS-STARTPTS,afade=t=out:st={cut0 - 0.02:.4f}:d=0.02[a1];"
                    f"[0:a]atrim=start={cut1:.4f},asetpts=PTS-STARTPTS,afade=t=in:d=0.02[a2];"
                    "[a1][a2]concat=n=2:v=0:a=1[a]",
                    "-map", "1:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", a.out], check=True)
    os.remove(tmp_v)

    # chapters and captions
    chs = []
    for i, t in enumerate(times):
        n = i + 1
        if n == rm_n:
            continue
        k = by_old_n[n]
        chs.append({"n": new["sections"][k]["n"], "t": round(t - (shift if t >= cut1 - 0.01 else 0), 1),
                    "title": new["sections"][k]["title"]})
    base = os.path.splitext(a.out)[0]
    old_ch = {c["n"]: c for c in json.load(open(a.chapters, encoding="utf-8"))} if os.path.isfile(a.chapters) else {}
    for c in chs:                                # keep the website's own wording of titles
        o = next((v for v in old_ch.values() if abs(v["t"] - (c["t"] + (shift if c["t"] >= cut0 else 0))) < 0.2), None)
        if o:
            c["title"] = o["title"]
    json.dump(chs, open(base + ".chapters.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ends = [c["t"] for c in chs[1:]] + [round(outro_t0 - shift, 3)]
    write_vtt([[c["t"], e, c["title"]] for c, e in zip(chs, ends)], base + ".chapters.vtt")
    kept = []
    for s, e, txt in cues:
        if cut0 <= s < cut1:
            continue
        if s >= cut1:
            s, e = s - shift, e - shift
        kept.append([s, e, txt])
    write_vtt(kept, base + ".captions.vtt")
    if a.write_script:
        json.dump(new, open(a.script, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"cut": [cut0, cut1], "shift": shift, "intro": intro_line, "chapters": len(chs)}))


if __name__ == "__main__":
    sys.exit(main())
