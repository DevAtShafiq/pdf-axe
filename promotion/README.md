# Office Axe — product demo video

Everything for the promotional demo lives in this folder. None of it changes
the app's own code.

| File | What it is |
|---|---|
| `office-axe-demo.mp4` | **The final video** (7 min 59 s, 17 chapters), 1920×1080, compact (53 MB) — the copy kept in git |
| `office-axe-demo-1440p.mp4` | Full-quality master, 2560×1440 (154 MB) — **local only** (too big for GitHub) |
| `office-axe-demo-1080p.mp4` | High-quality 1920×1080 (103 MB) — **local only** |
| `narration.srt` | Subtitles (same timing as the burned-in captions) |
| `NARRATION.md` | Narration script with start/end times, plus plain text for a human voice-over |
| `narration.json` | Script as data: lines, chapter titles and taglines, callout texts, voice |
| `STORYBOARD.md` | The 17 chapters as filmed, and how the recording is kept safe |
| `chapters.py` | **The storyboard as code**: one setup + act function per chapter |
| `record_demo.py` | Films the real app, one take per chapter (separate demo instance, real mouse/keyboard) |
| `desktop.py` | Real mouse / keyboard input and window placement (Win32) |
| `make_demo_data.py` | Creates the fictional demo files (students, certificates, form, photos, statement, QR receipt…) |
| `make_voiceover.py` | Narration: Microsoft neural voice via `edge-tts` (falls back to the offline Windows voice) |
| `sfx.py` | Click, key, whoosh, pop and chime sounds and the music bed — synthesized, no samples |
| `edit_demo.py` | Editing and motion graphics: cards, follow-camera, callouts, key caps, captions, blur, soundtrack, encode |
| `verify.py` | Checks an exported video (size, fps, codecs, duration, clean decode, audio length, speech vs subtitles) |
| `mask_popups.py` | Finds Windows taskbar-preview popups in takes and marks them so the editor freezes over them |
| `work/` | Takes, voice files, previews — not committed, safe to regenerate |

## Requirements

- Windows 10/11, the app's normal Python environment, Microsoft Word (chapter 6
  converts Word files with it).
- Free Python packages: `pip install imageio-ffmpeg numpy edge-tts qrcode`
  (FFmpeg comes with imageio-ffmpeg). `edge-tts` needs internet while making
  the voice; nothing else does.
- A 2560×1440 screen (the app window is filmed at 2474×1392, the full width
  above the taskbar). On another screen size change `W, H` in `record_demo.py`.
- Chapter 13 (AI suit & tie) calls OpenAI with the key saved in the app's
  Settings — about US$0.29 per take. Rehearsals (`--no-capture`) skip that call.

## Make the video (≈ 1 hour, mostly unattended)

From the repository root:

```bat
python promotion\make_voiceover.py --out promotion\work\voice2
python promotion\record_demo.py --run promotion\work\take
python promotion\edit_demo.py promotion\work\take --voice-dir promotion\work\voice2
python promotion\verify.py promotion\office-axe-demo-1440p.mp4 promotion\narration.srt
python promotion\verify.py promotion\office-axe-demo.mp4 promotion\narration.srt --size 1920x1080
```

**While `record_demo.py` runs (≈ 20 min) don't touch the mouse or keyboard** — it
moves the real cursor. It:

- creates fresh fictional files in a new `Documents\Office Axe Demo N` folder,
- backs up `sfm_settings.json`, then for filming hides the owner's saved
  signatures and points recordings at the demo folder; the backup is copied
  back at the end (and at the start of the next run if a run was interrupted),
- starts its own Office Axe with a test-mode subscription and a private
  debugging port (used only to locate controls and do invisible setup),
- films each chapter as its own take (`work/take/chNN/raw.mp4 + events.json`).
  If the window moves during a take, the take is discarded and re-filmed once.

Useful options: `--chapters 6,7` (re-film only some chapters — the editor uses
the newest good take of each), `--no-capture` (rehearse without filming).
A failing step saves `failure.png` in the take folder.

`edit_demo.py` renders the cut (≈ 40 min at full size). `--preview` makes a
fast half-size version, `--chapters 1,2` renders only some chapters.

## Change things

- **Wording, captions, callouts, chapter titles** → `narration.json`, then re-run
  `make_voiceover.py` and `edit_demo.py` (no new recording).
- **Voice** → `voice` / `rate` in `narration.json` (any `edge-tts` voice,
  `python -m edge_tts --list-voices`), or put your own `<id>.wav` files in a
  folder and pass `--voice-dir`.
- **Look** (colours, fonts, card layouts) → top of `edit_demo.py`. Fonts are
  the Helvetica-class fonts bundled with PyMuPDF (no system font files read).
- **Sound** → `sfx.py` (each sound is a small function) and the mix levels in
  `build_audio()` of `edit_demo.py`.
- **Camera** → `Camera` in `edit_demo.py`: every click / focus / drag / typing
  event sets where the view goes and how far it zooms.

## Add a chapter / make a tutorial for one feature

1. In `chapters.py` write `setup_x(rec, data)` (return the folder to show) and
   `act_x(rec, data)`:
   - real input: `rec.click_sel(css)`, `rec.click_text(css, text)`,
     `rec.click_rect(rect)`, `rec.press("z", "ctrl")`, `rec.type("…")`,
     `rec.drag(r0, r1)`, `rec.file_dialog(path)`, `rec.wait(s)`;
   - marks for the editor: `rec.focus("<name>", rect)` (camera + callout if the
     name is in `callouts`), `rec.wide()`, `rec.success()` (chime),
     `rec.ev("redact", rect=…)` / `rec.ev("redact_end")` to blur something.
2. Add it to `CHAPTERS`, add its section (title, tagline) and lines to
   `narration.json`, plus any demo files to `make_demo_data.py`.
3. `record_demo.py --no-capture --chapters N`, then film, then
   `edit_demo.py … --chapters N` for a stand-alone tutorial video.

## Safety

- Only fictional data is filmed. Sensitive things that the app shows are
  blurred in the edit (the saved OpenAI key in Settings, the templates file
  path) or hidden while filming (saved signatures, the recordings list).
- Nothing outside the app may appear: the window is topmost, mouse paths are
  kept inside it (never over the taskbar, whose previews show other windows),
  a watchdog puts the window back if it is moved or minimised and the editor
  freezes the last clean frame over that moment. Before publishing a new cut,
  look at a contact sheet of it and run `mask_popups.py` on new takes.
- The recorder never opens real folders and never deletes anything. Takes and
  demo folders stay until you remove them yourself.
- Ctrl+X / Ctrl+V in chapter 5 use the Windows clipboard.
