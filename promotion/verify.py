# -*- coding: utf-8 -*-
"""
verify.py — check the exported product video.

    python promotion/verify.py [promotion/office-axe-demo.mp4] [promotion/narration.srt]

Checks: the file exists; FFmpeg decodes every frame without errors; video is
1920×1080 at 30 fps, H.264; there is an AAC audio stream as long as the video;
duration is 60–90 s; and the narration is in sync with the subtitles (each
subtitle's time span has speech energy, the gaps between lines are quieter).
Prints PASS/FAIL per check and exits 1 on any failure.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path):
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace")
    return p.stderr


def srt_spans(path):
    spans = []
    for m in re.finditer(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", open(path, encoding="utf-8").read()):
        g = [int(x) for x in m.groups()]
        spans.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000))
    return spans


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    mp4 = argv[0] if argv else os.path.join(HERE, "office-axe-demo.mp4")
    srt = argv[1] if len(argv) > 1 else os.path.join(HERE, "narration.srt")
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    check("video file exists", os.path.isfile(mp4), f"{os.path.getsize(mp4) / 1e6:.1f} MB" if os.path.isfile(mp4) else mp4)
    if not os.path.isfile(mp4):
        return 1
    info = probe(mp4)
    dur_m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    dur = int(dur_m.group(1)) * 3600 + int(dur_m.group(2)) * 60 + float(dur_m.group(3)) if dur_m else 0
    v = re.search(r"Stream #\S+: Video: (\w+).*?, (\d+)x(\d+)[ ,].*?([\d.]+) fps", info)
    a = re.search(r"Stream #\S+: Audio: (\w+).*?(\d+) Hz", info)
    check("duration 60–90 s", 60 <= dur <= 90, f"{dur:.2f}s")
    check("video is H.264", bool(v) and v.group(1) == "h264", v.group(1) if v else "no video stream")
    check("resolution 1920×1080", bool(v) and (v.group(2), v.group(3)) == ("1920", "1080"),
          f"{v.group(2)}x{v.group(3)}" if v else "")
    check("30 fps", bool(v) and abs(float(v.group(4)) - 30) < 0.01, v.group(4) if v else "")
    check("AAC audio stream", bool(a) and a.group(1) == "aac", f"{a.group(1)} {a.group(2)} Hz" if a else "no audio")

    # full decode: every frame and the audio, errors reported by FFmpeg fail the check
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-v", "error", "-i", mp4, "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    check("decodes cleanly end to end", p.returncode == 0 and not p.stderr.strip(), p.stderr.strip()[:200])

    # audio length and sync with subtitles
    sr = 16000
    pcm = subprocess.run([ffmpeg_exe(), "-hide_banner", "-v", "error", "-i", mp4, "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True).stdout
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    adur = len(audio) / sr
    check("audio as long as the video (±0.3 s)", abs(adur - dur) <= 0.3, f"audio {adur:.2f}s, video {dur:.2f}s")
    if os.path.isfile(srt):
        spans = srt_spans(srt)
        rms = lambda t0, t1: float(np.sqrt(np.mean(audio[int(t0 * sr):int(t1 * sr)] ** 2) + 1e-12))
        voiced = [rms(a_, b_) for a_, b_ in spans]
        gaps = [(spans[i][1] + 0.15, spans[i + 1][0] - 0.15) for i in range(len(spans) - 1)
                if spans[i + 1][0] - spans[i][1] > 0.6]
        quiet = [rms(a_, b_) for a_, b_ in gaps]
        ok_spans = sum(1 for x in voiced if x > 0.02)
        check("speech during every subtitle", ok_spans == len(spans), f"{ok_spans}/{len(spans)} subtitles voiced")
        if quiet:
            check("quiet between narration lines", np.median(quiet) < 0.3 * np.median(voiced),
                  f"gap RMS {np.median(quiet):.4f} vs speech {np.median(voiced):.4f}")
        check("subtitles inside the video", spans and spans[-1][1] <= dur + 0.05, f"last ends {spans[-1][1]:.2f}s")
    else:
        check("subtitle file exists", False, srt)
    bad = results.count(False)
    print(f"\n{len(results) - bad}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
