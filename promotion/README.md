# Office Axe — product demo video

Everything for the promotional demo video lives in this folder. The app's
source code is not changed by any of it.

| File | What it is |
|---|---|
| `office-axe-demo.mp4` | **The final video**: 80.7 s, 1920×1080, 30 fps, H.264 + AAC, captions burned in |
| `narration.srt` | Subtitles (same timing as the burned-in captions) |
| `NARRATION.md` | Narration script with start/end times, plus plain text for a human voice-over |
| `narration.json` | The script as data: lines, section titles, callout texts (edit this to change wording) |
| `STORYBOARD.md` | Shot list as filmed, and how the recording is kept safe |
| `make_demo_data.py` | Creates the fictional demo files (students, certificate, transcript, Word files) |
| `desktop.py` | Real mouse / keyboard input and window placement (Win32) |
| `record_demo.py` | Films the real app: launches a separate demo instance, performs the shots, logs events |
| `make_voiceover.py` | Offline narration with the Windows speech engine → one WAV per line |
| `edit_demo.py` | The editing / motion-graphics layer: title cards, zooms, highlights, callouts, key caps, captions, audio mix, encode |
| `verify.py` | Checks the exported video (resolution, fps, codecs, duration, clean decode, audio/subtitle sync) |
| `work/` | Takes, voice files and scratch output (not committed; safe to regenerate) |

## What you need

- Windows 10/11 with Office Axe's normal requirements (see the main repo), and
  Microsoft Word installed (step 3 converts Word files with it).
- Python packages, all free: `pip install imageio-ffmpeg numpy` (bundles FFmpeg)
  plus `playwright`, `pywin32`, `pymupdf`, `python-docx`, `Pillow` (already
  used by the app / its tests). No paid service and no network are needed.
- A screen of at least 1920×1080 (the app window is filmed at exactly that size).

## Make the video again (≈ 10 minutes)

Run from the repository root:

```bat
python promotion\make_voiceover.py
python promotion\record_demo.py --run promotion\work\take3
python promotion\edit_demo.py promotion\work\take3
python promotion\verify.py
```

1. **Voice** — `make_voiceover.py` speaks every line of `narration.json` with
   the installed voice ("Microsoft Zira Desktop") into `work/voice/`.
2. **Record** — `record_demo.py` takes about 90 s. **Don't touch the mouse or
   keyboard while it runs**: it moves the real cursor. It:
   - writes fresh fictional files to a new `Documents\Office Axe Demo N` folder,
   - backs up `sfm_settings.json` (restored at the end, also on errors),
   - starts its own Office Axe with a test-mode subscription (no payment data)
     and a private debugging port used only to locate buttons,
   - places the window at 1920×1080, starts FFmpeg, and performs the shots.
   Output: `raw.mp4`, `events.json`, `app.log` in the take folder.
   If something goes wrong it saves `failure.png` and stops.
   Rehearse without filming: add `--no-capture`; only some shots: `--shots 1,2`.
3. **Edit** — `edit_demo.py` renders the final cut (≈ 4 min). Add `--preview`
   for a fast half-size version to check timing first.
4. **Check** — `verify.py` must print `11/11 checks passed`.

## Change things

- **Wording / captions / callouts**: edit `narration.json`, then re-run
  `make_voiceover.py` and `edit_demo.py` (no new recording needed).
- **A different voice**: put your own `<id>.wav` files (e.g. a recorded human
  voice, any sample rate) in a folder and run
  `edit_demo.py <take> --voice-dir <folder>`. Line ids are in `narration.json`.
- **Zooms**: the `zoom_windows()` table in `edit_demo.py` says which logged
  moment zooms in, until which event, and how far.
- **Look**: colours and fonts are at the top of `edit_demo.py` (fonts are the
  Helvetica-class fonts bundled with PyMuPDF, so no system font files are read).

## Make a tutorial for another feature

1. In `record_demo.py`, add a method `shotN_<name>(self, data)` and register
   it in `main()`. Inside it:
   - `self.ev("section", n=N, name="<id>")` at the start,
   - drive the app with `self.click_sel(css)`, `self.click_text(css, text)`,
     `self.click_rect(rect)`, `self.press("z", "ctrl")`, `D.type_text("...")`,
     `D.drag(...)`, and `self.wait(seconds)` for readable pauses,
   - log what to point at with `self.ev("focus", name="<callout id>", rect=self.rect(css))`.
2. If the feature needs files, add them in `make_demo_data.py` (fictional only).
3. In `narration.json`: add the section title under `sections`, its lines
   under `lines` (with `"section": "<id>"`), and callout texts keyed by the
   focus names.
4. Optional zoom: add a rule for the focus name in `zoom_windows()`.
5. Rehearse with `--no-capture --shots N`, then record, edit, verify.

## Safety notes

- Only fictional data appears in the video; no passwords, API keys, real
  people or customer records. The test-mode subscription and the
  "Demo Office / demo@example.com" customer are placeholders.
- The recorder never opens real folders and never deletes anything. Each take
  writes to new folders; old takes stay in `work/` and `Documents` until you
  remove them yourself.
