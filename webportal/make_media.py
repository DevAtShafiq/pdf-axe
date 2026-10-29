# -*- coding: utf-8 -*-
"""
make_media.py — landing-page media from the demo video in promotion/.

    python webportal/make_media.py

Writes static/video/poster.jpg, captions.vtt (from promotion/narration.srt),
chapters.vtt and chapters.json, and static/slides/*.jpg (one frame per
chosen chapter, cropped above the burned-in captions). Re-run after
re-rendering the video. The video itself is served from
promotion/office-axe-demo.mp4 (see web.py).
"""
from __future__ import annotations

import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROMO = os.path.join(ROOT, "promotion")
VIDEO = os.path.join(PROMO, "office-axe-demo.mp4")
OUT_V = os.path.join(HERE, "static", "video")
OUT_S = os.path.join(HERE, "static", "slides")

# Chapter card start times in office-axe-demo.mp4 (from the editor's
# timeline: chapter t0 minus the 2.4 s title card).
CHAPTERS = [
    (10.6, "The workspace"), (43.1, "A folder for every student"), (64.0, "Instant preview"),
    (88.5, "Smart rename"), (109.5, "Organise with undo"), (126.7, "Convert anything to PDF"),
    (159.2, "Compress to a target size"), (179.9, "Merge, split and extract"), (219.0, "Arrange pages"),
    (243.8, "Fill, sign and add a photo"), (301.9, "Mark up images"), (327.9, "Passport and visa photos"),
    (347.6, "AI suit and tie"), (376.4, "Read QR codes"), (393.6, "ZIP and passwords"),
    (423.6, "Screen recorder"), (445.1, "Subscription and settings"),
]
DURATION = 478.77

# slide name, time, caption
SLIDES = [
    ("folders", 52.0, "Make a folder for every student from a list of names"),
    ("preview", 76.0, "Preview documents, PDFs and photos instantly"),
    ("convert", 143.0, "Turn Word, Excel, PowerPoint and photos into PDFs"),
    ("compress", 169.5, "Compress a PDF to the size a portal accepts"),
    ("split", 199.5, "Merge, split and extract pages"),
    ("fill-sign", 272.9, "Fill forms, sign and add a passport photo"),
    ("markup", 314.9, "Highlight and mark up scans"),
    ("passport", 337.8, "Crop passport and visa photos to the exact size"),
    ("ai-suit", 362.0, "Put a suit and tie on a photo with AI"),
    ("qr", 385.0, "Read QR codes on certificates and receipts"),
    ("zip", 408.6, "Zip folders and protect them with a password"),
]


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def srt_to_vtt(src: str, dst: str) -> None:
    with open(src, encoding="utf-8") as fh:
        text = fh.read().replace("\r\n", "\n")
    text = re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", text)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n" + text.strip() + "\n")


def main() -> int:
    os.makedirs(OUT_V, exist_ok=True)
    os.makedirs(OUT_S, exist_ok=True)
    ff = ffmpeg()
    srt_to_vtt(os.path.join(PROMO, "narration.srt"), os.path.join(OUT_V, "captions.vtt"))

    ends = [t for t, _ in CHAPTERS[1:]] + [DURATION]
    with open(os.path.join(OUT_V, "chapters.vtt"), "w", encoding="utf-8") as fh:
        fh.write("WEBVTT\n\n")
        for i, ((t, title), end) in enumerate(zip(CHAPTERS, ends), 1):
            fh.write(f"{i}\n{ts(t)} --> {ts(end)}\n{title}\n\n")
    with open(os.path.join(OUT_V, "chapters.json"), "w", encoding="utf-8") as fh:
        json.dump([{"n": i, "t": t, "title": title} for i, (t, title) in enumerate(CHAPTERS, 1)], fh, indent=1)

    def frame(t, path, vf):
        subprocess.run([ff, "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", VIDEO, "-frames:v", "1",
                        "-vf", vf, "-q:v", "4", path], check=True)
    frame(3.5, os.path.join(OUT_V, "poster.jpg"), "scale=1280:-2")
    for name, t, _ in SLIDES:
        frame(t, os.path.join(OUT_S, name + ".jpg"), "crop=iw:ih*0.84:0:0,scale=1280:-2")
    with open(os.path.join(OUT_S, "slides.json"), "w", encoding="utf-8") as fh:
        json.dump([{"name": n, "caption": c} for n, _, c in SLIDES], fh, indent=1, ensure_ascii=False)
    print("media written to", OUT_V, "and", OUT_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
