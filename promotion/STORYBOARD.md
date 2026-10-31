# Office Axe — full product demo storyboard

Every feature of the app in 17 chapters, filmed live from the real desktop app
(Python + pywebview / Edge WebView2, entry point `main_webview.py`). Output
2560×1440 (and a 1080p copy), 30 fps, neural-voice narration, captions, UI
sounds and a quiet music bed. Exact timings: `NARRATION.md`.

**Structure:** opening card → (chapter card → chapter) × 17 → closing card.
Chapter cards (number, title, tagline, progress dots, whoosh) sit *between*
features, never over the app. During each chapter a small label (e.g.
"06 Convert to PDF") sits in the bottom-left corner, the camera follows the
action, and callouts point at what matters.

| # | Chapter | What the viewer sees (real app, real input) | Motion graphics |
|---|---------|---------------------------------------------|-----------------|
| — | Opening | — | Logo, "Office Axe", "Every student document task, in one desktop app" |
| 1 | The workspace | Open a folder · thumbnail view · list view · sort by size / name · search "photo" · select a file → details · dark theme → light | Callouts: Thumbnail view, Search as you type, File details |
| 2 | A folder for every student | New folder ▾ → *Folders from a list* · type 6 names · live preview · numbering "01 Name" · Create | Zoom on the dialog; callouts: Live preview, Optional numbering |
| 3 | Instant preview | Word, Excel, photo and PDF previews · zoom in / out | Camera on the preview pane |
| 4 | Smart rename | F2 · "bachelor cert" · pick 학사 학위증명서 (아포스티유 포함) template · More → template manager | Callouts: English + Korean templates, Edit your own templates; templates file path blurred |
| 5 | Organise with undo | Ctrl+click two photos · Ctrl+X · into "Minji Park" · Ctrl+V · Ctrl+Z · Ctrl+Y | Key caps; callout Ctrl+Z undoes · Ctrl+Y redoes |
| 6 | Convert to PDF | 2 Word files → PDF (progress card) · 2 page photos → one PDF · PDF pages → JPG | Word start-up wait at ×N speed with badge; callouts |
| 7 | Compress | Bank statement (5.6 MB) → target "1 MB" with live estimates → compressed copy | Callout: Estimated size for each setting |
| 8 | Merge, split, extract | Merge 2 PDFs · split transcript every 2 pages · extract pages by clicking them | Callouts on the dialogs |
| 9 | Arrange pages | Del blank page · Ctrl+Z · Del · L rotates a sideways scan · drag cover letter to front · Save | Zoom on thumbnails, key caps, drag trail |
| 10 | Fill, sign, add a photo | PDF editor: type into 4 real form fields + checkbox · draw a signature · place it on the line · Photo → Windows file dialog → fit into the photo box · Save | Callouts: Real form fields, Draw with a mouse or pen, Signature and photo placed |
| 11 | Mark up images | Annotate a page photo: highlight, box, arrow · Save | Camera on the page |
| 12 | Passport & visa photos | Crop to photo size: Korea 35×45 mm preset, face guide, zoom, max 100 KB, 4×6 print sheet · Save | Callout: Face guide · exact size |
| 13 | AI suit & tie | Casual portrait (fictional illustration) → suit colour → *Wear suit & tie* (real OpenAI call, gpt-image-1.5) → before/after compare slider | AI wait at ×N speed with badge |
| 14 | Read QR codes | Payment receipt PDF → QR → click the code on screen (× appears at the click) → decoded text | Callout: Read instantly |
| 15 | ZIP & passwords | 3 files → password ZIP · Protect a PDF with an open password | Callouts on the dialogs |
| 16 | Screen recorder | Recorder panel (screen / window / app, audio, quality) · region screenshot | Callout: Screen, window or this app |
| 17 | Subscription & settings | Subscription page (plan, devices, payments) · plans · Settings | OpenAI key field blurred |
| — | Closing | — | "Student documents, sorted." + all 17 feature names |

Filming this chapter found that OpenAI had retired the app's image model
(gpt-image-1); the app was fixed (commit d2e4c2b) before the take.

Not shown: deleting to the Recycle Bin (the project's CLAUDE.md does not
allow the assistant to delete files).

## How it is recorded safely

- A separate Office Axe instance (never the owner's window), demo test-mode
  subscription, private debugging port used only to locate controls and to do
  invisible setup; all visible actions are real mouse / keyboard input.
- Fictional demo data in a new `Documents\Office Axe Demo N` folder.
- The owner's settings are backed up; while filming, saved signatures are
  hidden and recordings go to the demo folder; the backup is restored after.
- Window placed topmost at 2474×1392; a take where the window moved is
  discarded and re-filmed. Sensitive fields are blurred in the edit.
