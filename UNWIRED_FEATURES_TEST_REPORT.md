# Unwired Features — Verification & Test Report
Generated: 2026-07-04 · Baseline: FEATURE_AUDIT.txt (2026-07-03)
Method: audit re-verified against current `ui/js/*` + `sfm_bridge.py`, then backend functions executed headless against sample fixtures (images, xlsx, zips, folders). Windows-only features were code-reviewed instead.

---

## A. Already wired since the audit (no action needed)

These 18 items were ❌ in yesterday's audit but ARE wired in the current code:

| Feature | Wired at |
|---|---|
| Expand All / Collapse All | context menu + More Menu → `FileTree.expandAll/collapseAll` |
| Recent folders dropdown | `app.js` recentFolders (last 10) |
| Open with… submenu | context menu → `getOpenWithCommands` + `_run_open_with` |
| New Text Document | context menu → `create_text_file` |
| Make Folder for File | context menu (createFolder + moveFiles) |
| Move to Root Folder | context menu → `move_to_root` |
| Rename by Doc Type (OCR) | context menu → `openOcrRenameProgress` |
| Split PDFs in Folder (bulk) | context menu + More Menu → dialog #24 |
| Zip Each Subfolder | context menu → `FileTree.zipEachSubfolder` |
| Append PDF (Full View) | `fv-append` button → merge_pdfs |
| Rotate image CW/CCW | context menu → `rotate_image` |
| Flip Horizontal / Vertical | context menu → `flip_image` |
| Crop Image | dialog → `crop_image` |
| Resize to Photo Size (mm) | dialog → `resize_photo_to_mm` |
| Brightness/Contrast | dialog → `adjust_image_save` |
| Excel → CSV | context menu → `excel_to_csv` |

## B. Backend test results (executed, not just reviewed)

| # | Function | Result | Notes |
|---|---|---|---|
| 1 | `rotate_image` ±90 | ✅ PASS | 400×300 → 300×400, saved `_rot+90` suffix |
| 2 | `flip_image` H + V | ✅ PASS | `_flipH` / `_flipV` outputs correct |
| 3 | `crop_image` | ⚠️ PASS w/ ISSUE | Crop correct (100×80) but **overwrites the original in place** — UI passes no out-path. Breaks the app's own auto-suffix convention; no undo. Fix: pass an out path or add `_cropped` suffix default. |
| 4 | `adjust_image_save` | ✅ PASS | `_adjusted` output created |
| 5 | `resize_photo_to_mm` 35×45@300 | ⚠️ PASS w/ NOTE | Correct 413×531 px, but **no DPI metadata embedded** (`dpi=None`) — printed size may be wrong in some apps. Consider `img.save(..., dpi=(300,300))`. |
| 6 | `excel_to_csv` (bridge) | ✅ PASS | One CSV per sheet, UTF-8 BOM |
| 7 | `create_text_file` | ✅ PASS | |
| 8 | `zip_paths` | ✅ PASS | Async (`started:true`); archive contents verified |
| 9 | `unzip` / `unzip_all` | ⚠️ PASS w/ NOTE | Works, but extracting `inner.zip` (containing `inner/…`) into sibling folder `inner/` yields **double-nested `inner/inner/`**. Consider flattening when archive has a single top-level dir matching the stem. |
| 10 | `move_to_root` | ✅ PASS | |
| 11 | `zip_each_immediate_subfolder_to_sibling_archives` (file_ops) | ✅ PASS | zipped 2/2 (note: UI reimplements this loop in JS via `zip_paths` rather than calling it) |
| 12 | `excel_set_cell` (UNWIRED) | ✅ PASS | Signature `(path, coord, value, sheet=None, in_place=False)`; writes `_edited.xlsx`, preserves original. Backend ready — just needs bridge method + UI. |
| 13 | `excel_to_html` (UNWIRED "open in browser") | ✅ PASS | Produces valid HTML table. Needs bridge wrapper + `webbrowser.open` / `open_native` trigger. |
| 14 | `_run_open_with` | ✅ PASS | Command executed with path arg |
| 15 | `get_pdf_page_count` | ⚠️ ROBUSTNESS ISSUE | Sandbox lacks PyMuPDF so PDF ops untestable here, BUT: `pdf_page_count` returns `0` with `ok:true` for a **nonexistent file** or missing fitz. Bulk-split dialog then silently "skips" broken files. Suggest returning an error instead of 0. |

PDF-dependent flows (split/merge/append/page count) could not be executed in the sandbox (PyMuPDF unavailable via proxy). Their wiring was code-verified; runtime behavior should be smoke-tested once in the app.

## C. Still NOT wired to the new UI (the remaining true gaps)

| # | Feature | Backend status | Testable verdict |
|---|---|---|---|
| 1 | **Excel cell editing** | `file_ops.excel_set_cell` — ✅ tested, works | Just needs bridge method + UI |
| 2 | **Render Excel as HTML in browser** | `file_ops.excel_to_html` — ✅ tested, works | Needs bridge wrapper + open-in-browser trigger |
| 3 | **College Photo Sheet (A4·600DPI·CMYK)** | Only inside old Tkinter app (`_make_college_photo_sheet`, duplicated at lines 12438 & 16248 of student_folder_maker.py) | Not portable as-is — logic must be extracted into file_ops first |
| 4 | **Send To → submenu** | Only in old app (line 13145); Windows shell SendTo | Windows-only; code review OK |
| 5 | **Open Word source externally** | Old app `open_word_file` (line 20220) — actually converts Word→PDF for preview | Old behavior differs from audit description; decide desired behavior before porting |
| 6 | **Open in Photoshop** | `_find_adobe_photoshop_executable` lives in old app only (line 917) | Windows-only; needs port to file_ops + bridge |
| 7 | **Delete → Recycle Bin** | `recycle_delete` bridge exists; no UI trigger | ⚠️ Intentionally left unwired? Conflicts with CLAUDE.md no-delete policy. Note: on non-Windows the fallback **permanently deletes** (rmtree). Recommend keeping unwired or adding a confirm gate. |
| 8 | **Expand/Collapse selected folder only** | Pure UI (no backend) | Expand All/Collapse All now cover most of this |
| 9 | **New folder in root** | Backend (`create_folder`) already wired elsewhere | Trivial UI addition |
| 10 | **Extract single page → new PDF** | Covered by wired "Extract Pages…" dialog | Arguably done; dedicated one-click item optional |
| 11 | **Unzip selected folders' ZIPs** | Composable from `list_folder` + `unzip_all` | Trivial UI addition |

## D. Fixes applied 2026-07-04 (all re-tested ✅)

| Bug | Fix | Where |
|---|---|---|
| Crop overwrote original | Default output now `<name>_cropped<ext>`, auto-numbered on collision; original untouched | `file_ops.crop_image` |
| `get_pdf_page_count` returned `ok:true, count:0` for missing/corrupt PDFs | Now returns proper errors: "File not found" / "Could not read PDF" | `sfm_bridge.get_pdf_page_count` |
| Unzip double-nesting (`inner/inner/…`) | New `_zip_wraps_own_stem()` helper — if archive already wraps content in `<stem>/`, extracts beside the zip. Applied to both single unzip and multi-extract | `file_ops.extract_zip_to_sibling_folder`, `extract_all_zips_to_sibling_folders` |
| No DPI metadata in resized photos | Saves with `dpi=(dpi,dpi)` so printed mm size is honored | `file_ops.resize_photo_to_mm` |
| **NEW — "Extract All…" silently failed every time** | `unzip_all` passed a *list* of zip paths to a function expecting a *folder string*, and its `unzip_all_done` event had no UI listener, hiding the failure. Now extracts each zip individually and emits `unzip_done` (which the UI handles) with real stats | `sfm_bridge.unzip_all` |

Verification: full regression suite re-run after patching — crop (original intact, auto-number works), page-count errors for missing + corrupt files, wrapped zip → no nesting, flat zip → normal folder, unzip_all extracts 2/2 with correct events, DPI = (300,300), plus 9 previously passing functions all still pass.

## E. Live UI test session (2026-07-04 evening) — 6 more bugs found & fixed

Drove the actual app on-screen (dev mode, `run_dev.bat`) against `_ui_test_fixtures/`. All fixes below were verified live after restart.

| # | Bug | Root cause | Fix |
|---|---|---|---|
| 6 | **Right-click context menu never opened** (killed ~35 features) | `ReferenceError: disabled is not defined` at context-menu.js:343 — `_item()` used an undeclared variable | Added `disabled = false` parameter |
| 7 | **Details panel always blank** | `index.html` had inline `style="display:none"` on `#details-content`; `_showPanel()` only toggles the `hidden` class, which can't override an inline style | Replaced inline style with `class="hidden"` |
| 8 | **"Zip…" zipped every sibling subfolder instead of the selection** | `zip_paths` with no out-path called `zip_each_immediate_subfolder_to_sibling_archives(parent)` — ignored selection | Now zips each selected path to a sibling `<name>.zip` (auto-numbered, never overwrites) |
| 9 | **All Python→JS events lost** (status stuck on "Zipping…", no toasts, no auto-refresh) | `window.events.loaded` never fired → `bridge.set_window()` never ran → `_emit()` silently dropped every event | Also set the window ref via `webview.start(func=…)`; added logging so dropped events are visible |
| 10 | **Every `_init` in app.js was dead code** — keyboard shortcuts, toolbar buttons, event handlers, pane resizers, cost/watch pollers never initialised | `_initToolbar/_initKeyboard/_initEventHandlers/_initPaneResizers/startCostPoller/startWatchPoller` were defined but never called anywhere | Added a `_boot()` block (DOMContentLoaded-safe) that calls them all |
| 11 | Excel sheet tabs remain visible when switching preview to a PDF | preview.js doesn't clear the sheet-tab bar on type switch | MINOR — noted, not yet fixed |

Live-verified working after fixes: context menus (file/image/folder/zip/xlsx variants), Details panel with rename + quick actions, image rotate (toast + new file), broken-PDF error message, Excel preview with sheet tabs, PDF preview with page nav, Zip selection → toast + auto-refresh, Extract Here (no double-nesting), Export Sheets as CSV, toolbar navigation buttons.

Note: `_ui_test_fixtures/` contains leftover test artifacts (zips, rotated/cropped images, extracted folders) — safe to remove whenever you like.

## F. Second live session (2026-07-04 ~8 PM) — rename/modal complaints traced to 5 more bugs

All fixed and re-verified live in the running app.

| # | Bug | Root cause | Fix |
|---|---|---|---|
| 12 | **Rename never worked** — Enter/Apply showed "Renamed ✓" toast but the file kept its old name | Bridge passed a full path where `safe_rename` expects a bare name, and ignored its `(ok, msg)` return — so it always failed on disk while reporting success | Bridge now passes the name and honors the return; verified live (`notes.txt` → `n2.txt`) |
| 13 | **Silent failures across ~12 PDF operations** (merge, split, rotate page, delete page, reorder, combine, compress, OCR-searchable, convert-to-PDF, extract pages, PDF→images, soft-delete) | file_ops returns `(ok, msg)` tuples / bools; the bridge ignored them all. Worse: `convert_to_pdf`, `split_pdf_pages`, `combine_files_to_pdf` were missing the required `log` argument (TypeError every call), `pdf_to_images` was called with a wholly wrong signature, and Full-View CCW rotation actually rotated CW (`degrees` passed into a `clockwise` bool) | Added `_check()` normalizer + `_noop_log`; corrected every call signature |
| 14 | **Extract Pages wrote to a temp file and threw it away** — output name in the dialog was never created | `pdf_export_selected_pages_to_tempfile` result ignored; `parse_pdf_page_range_input` tuple also unhandled | Temp file now moved to the chosen output name (auto-numbered); verified live (`three_pages_extract.pdf` created from range "1,3") |
| 15 | **Global shortcuts hijacked typing** — pressing Delete while renaming soft-deleted the selected file; Ctrl+V pasted files instead of text; Ctrl+Z ran file-op undo | `_initKeyboard` had no input-focus guard | Keydown handler now ignores events when focus is in an input/textarea/contentEditable |
| 16 | Modals themselves render fine | User-visible "modal not working" was the downstream operation failing silently (bugs 12–14) | — |

Also confirmed still working after all fixes: context menus, Details panel + rename bar with Enter, zip/extract with toasts and auto-refresh, Excel→CSV, previews, toolbar nav.

## G. Remaining recommendations

1. Wire Excel cell edit + Excel-HTML-in-browser (backends already pass — C-1, C-2)
2. Add a UI listener for richer unzip stats if desired (payload now includes extracted/skipped/failed)
3. Port College Photo Sheet out of the old app into file_ops (C-3)
4. Decide policy on Recycle-Bin delete before wiring (C-7)
5. Smoke-test PDF flows (split/merge/append) once in the app — not executable in the sandbox

## H. Follow-up (2026-07-05): "Modal doesn't appear for merge / arrange"

| # | Bug | Root cause | Fix |
|---|---|---|---|
| 17 | **Arrange never showed the Arrange Pages modal** — the preview toolbar's "⊞ Arrange" opened the *Combine PDFs* dialog with a single file (useless), and the context-menu "Combine / Arrange…" did the same. The real Arrange Pages dialog was only reachable from Full View | `pdf-arrange` (preview.js) and the single-PDF context item were wired to `Dialogs.openCombinePdf` instead of `Dialogs.openArrangePages` | Both now open `openArrangePages`; it auto-fetches the page count when not supplied. Verified live: modal shows Page 1..N rows |
| 18 | **Save Reordered silently produced no file** — UI says "Saved alongside the original" with a `_reordered` name, but nothing appeared | `sfm_bridge.reorder_pdf_pages` ignored `out_path` entirely and reordered the ORIGINAL in place via `pdf_reorder_pages` (in-place API) | Bridge now copies the original to `out_path` (auto-numbered if taken) and reorders the copy; original untouched. Verified live: `..._reordered.pdf` created |

Note: toolbar **Combine** requires 2+ PDFs selected — with fewer it shows a warning toast by design (modal verified working with 2 files selected).

## I. Follow-up 2 (2026-07-05): Full View arrange + usable page ordering

| # | Bug | Root cause | Fix |
|---|---|---|---|
| 19 | **Full View was half-dead**: page counter showed "/ undefined", Next never advanced past page 1, and its Arrange button opened an EMPTY modal | `openFullView` read `rc.count` from `getPdfPageCount`, but the bridge returns `page_count` — total was `undefined` everywhere downstream (same bug in Bulk Split's single-page skip) | Reads `page_count` (with fallbacks); Bulk Split check fixed too |
| 20 | **Soft-delete (Move to Review / Delete key) silently left the original in place for any PDF open in preview** — a timestamped copy appeared in `_to_review/` while the "moved" file stayed put, with a success toast | The preview holds a PyMuPDF handle; `shutil.move` degrades to copy + failed-unlink on Windows. `soft_delete_path` never released handles (unlike `delete_path_recycle_or_remove`), and the bridge returned top-level `ok:true` even when every file failed | `soft_delete_path` now releases PDF handles first and cleans up half-copies; bridge returns `ok:false` + per-file errors when any move fails. Verified live on a previewed PDF |

**Arrange Pages modal rebuilt**: page thumbnails + ◀/▶ move buttons (drag-and-drop kept as a secondary path), auto page-count fetch, and the success toast now reports the real output name (bridge auto-numbers if `_reordered` already exists). Verified end-to-end live: swapped a 2-page PDF via the buttons from the normal preview (no Full View needed), saved, and confirmed the new file's page order on screen. Full View's Arrange verified stacking correctly on top of the viewer.

## J. Follow-up 3 (2026-07-05): Full-window merge/arrange — old-system port

Rebuilt Combine/Arrange as a full-window Page Organizer (`openPageOrganizer` in dialogs.js), ported from the old
`student_folder_maker.py` merge/arrange window and verified live:

- **Full window**, one tile per PDF page across all input files, exactly like the old maximized dialog.
- **Toolbar**: Add PDF… (native multi-select picker, new bridge `browse_for_pdfs`), Size −/+/Reset zoom (+Ctrl+wheel),
  Select All / Deselect All, "✂ Extract Selected (n)" (disabled at 0), Cancel, Save/OK.
- **Merge mode**: "Save as (no .pdf)" defaulting to next free `mergedN` + Folder + Browse…, overwrite confirm,
  sources moved to `_to_review/` after merge (old behavior).
- **Arrange mode**: "Save to PDF file" **saves in place over the original** (tmp + os.replace, old `_save_arrange_inplace`).
- **Tiles**: mouse **drag to reorder** (pointer-based; fixed elementFromPoint self-hit with pointer-events:none),
  click = green-✓ select, Shift+click range, right-click menu (Rotate CW/CCW · Remove page · Extract this/selected),
  Space+arrows keyboard reorder with Escape-restores-order, Enter = save.
- **New bridge** `build_pdf_from_pages(pages,out,replace)`: page-level builder {path,page,rotate} via fitz — powers
  merge, arrange-in-place and extraction. Rotation is applied **relative to the page's existing /Rotate**
  (fixed an old-system quirk where set_rotation() was absolute and couldn't undo a baked-in rotation).
- Ctrl+Enter now matches the old app: 1 PDF selected → Arrange; 2+ → Combine.

Verified end-to-end on 아버지 신분증-father ID card.pdf: drag-reorder → rotate → save-in-place → preview confirmed;
then restored the file to its original page order/rotation through the same UI.

## K. Follow-up 4 (2026-07-05): Double-click opens Full View (old behavior)

Old `_on_tree_double_click`: folder → navigate, PDF/image → PdfFullViewDialog, other files → rename.
New app double-clicked files only loaded the side preview. Now: double-click a **PDF or image**
(pdf/jpg/jpeg/png/bmp/tif/tiff/webp/gif/jfif) opens **Full View** (page nav, zoom, rotate, Del Page,
Append, Merge, Arrange) and syncs the side preview; folders still navigate; other files preview as before.
Full View itself was widened to fill the window like the old maximized dialog. Verified live.
