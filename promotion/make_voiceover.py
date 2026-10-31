# -*- coding: utf-8 -*-
"""
make_voiceover.py — narration audio from narration.json.

    python promotion/make_voiceover.py [--out promotion/work/voice] [--engine edge-tts|sapi]

Engines
- edge-tts (default): Microsoft's neural voices through the free `edge-tts`
  package (needs internet while generating; no account, no cost).
- sapi: the offline Windows speech engine (pywin32), e.g. "Microsoft Zira
  Desktop" — used automatically if edge-tts fails.

Writes one 48 kHz mono WAV per line plus voice.json (durations). To use a
human recording instead, drop <id>.wav files into a folder and pass it to
edit_demo.py with --voice-dir.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def edge_to_wav(text: str, path: str, voice: str, rate: str = "+0%") -> None:
    import edge_tts
    mp3 = path[:-4] + ".mp3"

    async def go():
        await edge_tts.Communicate(text, voice, rate=rate).save(mp3)
    asyncio.run(go())
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", mp3,
                    "-ar", "48000", "-ac", "1", path], check=True)


def sapi_to_wav(text: str, path: str, voice_name: str = "", rate: int = 0) -> None:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        v = win32com.client.Dispatch("SAPI.SpVoice")
        if voice_name:
            for tok in v.GetVoices():
                if tok.GetDescription() == voice_name:
                    v.Voice = tok
                    break
        v.Rate = rate
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 39                                   # SAFT48kHz16BitMono
        stream.Format = fmt
        stream.Open(path, 3)
        v.AudioOutputStream = stream
        v.Speak(text, 0)
        stream.Close()
        del v, stream, fmt                     # release COM objects before CoUninitialize
    finally:
        pythoncom.CoUninitialize()


def wav_seconds(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=os.path.join(HERE, "narration.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "work", "voice"))
    ap.add_argument("--engine", default="")
    a = ap.parse_args(argv)
    with open(a.script, encoding="utf-8") as fh:
        script = json.load(fh)
    engine = a.engine or script.get("engine", "edge-tts")
    os.makedirs(a.out, exist_ok=True)
    info = {}
    for line in script["lines"]:
        p = os.path.join(a.out, line["id"] + ".wav")
        try:
            if engine == "edge-tts":
                edge_to_wav(line["text"], p, script.get("voice", "en-US-AvaNeural"), script.get("rate", "+0%"))
            else:
                raise RuntimeError("sapi")
        except Exception as exc:
            if engine == "edge-tts":
                print(f"  edge-tts failed for {line['id']} ({exc}); using the offline Windows voice")
            sapi_to_wav(line["text"], p, script.get("fallback_voice", ""), 0)
        info[line["id"]] = round(wav_seconds(p), 3)
        print(f"{line['id']:6s} {info[line['id']]:5.2f}s  {line['text']}")
    with open(os.path.join(a.out, "voice.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    print(f"total {sum(info.values()):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
