# Office Axe — product demo storyboard (final: 80.7 s, 1920×1080, 30 fps)

Approved plan: opening, four feature steps, closing. The Recycle Bin shot
from the first draft was left out (it would really delete a file, which the
project's CLAUDE.md does not allow the assistant to do).

## What the app is (from inspecting the repo)

- **A Windows desktop app, not a website.** Python + **pywebview** (Microsoft
  Edge WebView2 window); entry point `main_webview.py` (launcher
  `start_office_axe.bat`). The UI is HTML/JS in `ui/`; all file work happens
  in Python behind the `SFMBridge` JS API (`sfm_bridge.py` + `*_bridge.py`).
  Some tools open native helper windows (e.g. the QR picker).
- Audience: an office that prepares students' application documents.

## How it is recorded safely

- `record_demo.py` starts a **separate app instance**, never the owner's
  window, with a demo **test-mode subscription** file inside the take folder
  and Chromium remote debugging on a private port. The debugging port is only
  used to *find* controls and wait for the UI; every click, drag and keystroke
  is real OS input, so the real cursor is filmed.
- **Fictional demo data** only (`make_demo_data.py`): six made-up students,
  a made-up university's certificate and transcript, two synthetic Word files.
  It is written to a new `Documents\Office Axe Demo N` folder so the paths on
  screen stay short and nothing existing is ever touched.
- The app's shared `sfm_settings.json` is backed up and restored afterwards.
- FFmpeg `gdigrab` films the window, which is placed at exactly 1920×1080.
  A 0.4 s magenta marker (before step 1, cut from the video) lets the editor
  line the event log up with the frames.

## Shots as filmed (times in the final video)

| # | Time | Real app on screen | Motion graphics |
|---|------|--------------------|-----------------|
| 0 | 0:00–0:07 | — | Opening card: logo, "Office Axe", "Student documents, done in minutes", accent rule, fade into the app. |
| 1 | 0:07–0:21 | New folder ▾ → *New folders from a list*, six names typed, live preview "6 to create", **Create 6 folders** → folders appear. | "Step 1 of 4 — A folder for every student"; zoom on the dialog; highlight + callout on the preview ("duplicates and invalid names are flagged"); callout "Six folders, one click"; click ripples. |
| 2 | 0:21–0:38 | Open *Documents received*; preview *Study plan.docx* and *scan_0012.pdf*; **F2**, type "bachelor cert", pick **학사 학위증명서 (아포스티유 포함) — bachelor certificate with apostille**. | "Step 2 — Preview and smart rename"; callouts on the Word preview and the name suggestions; zoom on the rename dropdown; key cap F2. |
| 3 | 0:38–0:51 | Ctrl+click two Word files → right-click → **Convert 2 to PDF** → progress card ("File 2 of 2", timer) → "2 PDFs created". | "Step 3 — Word to PDF"; zoom on the progress card with callouts; key cap "Ctrl + click"; the Word start-up wait is shown at **×4 speed** with an on-screen badge. |
| 4 | 0:51–1:14 | *Transcript.pdf* → **Arrange**: select the blank page, **Del**, **Ctrl+Z** brings it back, Del again; select the sideways page, **L** rotates it; drag the cover letter to the front; **Save** as a new file. | "Step 4 — Arrange pages"; zoom on the page thumbnails; key caps Del / Ctrl+Z / L; drag trail; callout "Saved as a new PDF — the original stays as it was". |
| 5 | 1:14–1:21 | — | Closing card: logo, "Student documents, sorted.", four feature bullets. |

Narration and exact line timings: `NARRATION.md` / `narration.srt`.
