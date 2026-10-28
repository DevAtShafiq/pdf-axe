# -*- coding: utf-8 -*-
"""
make_voiceover.py — narration audio from narration.json, fully offline.

    python promotion/make_voiceover.py [--out promotion/work/voice]

Uses the Windows speech engine (SAPI, through pywin32) with an installed
voice — no network, no paid service. One 48 kHz mono WAV per line plus
voice.json with each line's duration, which edit_demo.py uses for timing.

To use another voice later (a recorded human voice, another TTS), drop
WAV files with the same names (<id>.wav) into the folder and re-run
edit_demo.py with --voice-dir.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))


def speak_to_wav(text: str, path: str, voice_name: str = "", rate: int = 0) -> None:
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
        stream.Open(path, 3)                            # SSFMCreateForWrite
        v.AudioOutputStream = stream
        # "Control Z" reads better than "Ctrl+Z"; the text file keeps the plain wording.
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
    a = ap.parse_args(argv)
    with open(a.script, encoding="utf-8") as fh:
        script = json.load(fh)
    os.makedirs(a.out, exist_ok=True)
    info = {}
    for line in script["lines"]:
        p = os.path.join(a.out, line["id"] + ".wav")
        speak_to_wav(line["text"], p, script.get("voice", ""), int(script.get("rate", 0)))
        info[line["id"]] = round(wav_seconds(p), 3)
        print(f"{line['id']:6s} {info[line['id']]:5.2f}s  {line['text']}")
    with open(os.path.join(a.out, "voice.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
