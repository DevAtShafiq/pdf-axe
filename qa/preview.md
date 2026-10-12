# Preview panel: QA inventory

Scope: `ui/js/preview.js`, `ui/js/pdf-text.js`, `ui/preview-docs.css`, `doc_preview.py`,
`doc_preview_bridge.py`, the preview bridge methods in `sfm_bridge.py`, `excel_grid.py`,
`qr_pick.py`, `qr_scan.py`, `qr_screen_capture.py`, `screen_pick.py`, `media_convert.py`.

How it was tested:
- **py**: pytest (`tests/test_preview_office.py`, `tests/test_preview_qa.py`, the qr/media test files).
- **probe**: direct bridge calls on the fixture set from `qa/e2e/make_preview_fixtures.py`.
- **e2e**: `qa/e2e/preview_e2e.py` against an app started with
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9342`. It uses real mouse and keyboard
  input through Playwright and reads the Windows clipboard with `win32clipboard`. Last run: 66/66 passed.

## Bugs found and fixed

| # | Bug | Fix commit |
|---|-----|-----------|
| 1 | PowerPoint files never previewed: `SaveAs` failed with "can't save ^0 to ^1" when the preview cache path had forward slashes | 68d9471 |
| 2 | A damaged image showed `'NoneType' object has no attribute 'width'` | 68d9471 |
| 3 | A damaged .docx error showed the full temp path ("Package not found at …") | 68d9471 |
| 4 | Workbooks over 15 MB (read-only mode): blank rows were numbered 14, 19, 24… | 8cb0577 |
| 5 | Workbooks over 15 MB stayed locked after preview (rename or move failed with WinError 32) | 8cb0577 |
| 6 | Big workbook without `<dimension>`: the whole sheet was read and "0 rows × 0 columns" was shown | 8cb0577 |
| 7 | A previewed PDF was held open. Other programs could not save over it ("Permission denied"), and after an external change the cached document went stale | 68a570f |
| 8 | Clicking a scrollable PDF page left focus on the file list. Ctrl+A then selected every file and Ctrl+C copied the file path instead of the page text | 0215d59 |
| 9 | Dragging over a word inside an existing selection kept the old selection, so the whole page was copied | 0215d59 |
| 10 | .tiff images ignored zoom, and .tif files had no preview | 0215d59 |
| 11 | After annotate, pdf-edit or pdf-tools saved over the current file, `previewFile(samePath)` did nothing, so the old pages stayed on screen | see git log ("reload") |
| 12 | Rotate and delete page ignored bridge errors. A failed delete still removed a page from the view, and deleting the only page was allowed | see git log |
| 13 | mailto: links in Word previews looked clickable but did nothing. Link-open errors were silent | see git log |

## Inventory

### ui/js/preview.js
| Function | How tested | Result |
|---|---|---|
| `_showOnly` | e2e (every view switch, toolbar and copy-button visibility) | pass |
| `previewFile` (routing by extension, same-path no-op, `{reload}`) | e2e | pass after fix 11 |
| `clear` | e2e (via archive and locked flow) | pass |
| `_resetPdfDom` | e2e (switching files leaves no stale pages) | pass |
| `_openPdf` | e2e sample, broken, empty, locked | pass |
| `_loadVisiblePages` (lazy IntersectionObserver) | e2e | pass |
| `_previewCssWidth`, `_previewDpi` | e2e zoom widens page | pass |
| `_updatePageNav`, `goPrev`, `goNext`, `goToPage` (clamping) | e2e | pass |
| `_pageEl`, `_scrollToPage`, `_initScrollPageTrack` | e2e scroll tracks page | pass |
| `_initTextSelection` (context menus, copy buttons, Ctrl+A per view) | e2e for pdf, text, docx, csv | pass after fix 8 |
| `_selectDoc`, `_docMenu` | e2e docx | pass |
| doc link click handler | code review plus py test | fixed (13) |
| `_openImage` | e2e png, jpg, tiff, tif, webp, gif, broken | pass after fix 2 and 10 |
| `_openText` | e2e UTF-8 and CP949 Korean | pass |
| `_docLoading`, `_notice`, `_officeBtn` | e2e docx and xlsx | pass |
| `_openDocx` | e2e (heading, table, meta bar, Korean) | pass |
| `_wireDocActions` | e2e ("Exact layout via Office" button present) | pass |
| `_openSheet` (tabs, hidden sheet, switch sheet, csv) | e2e | pass |
| `_openOffice`, `_officeOrNoPreview`, `_convertViaOffice` | e2e pptx through real PowerPoint, cached reopen | pass after fix 1 |
| `_appFor` | e2e and py | pass |
| `_showError` | e2e broken.pdf, empty.pdf, broken.png | pass |
| `zoomIn`, `zoomOut`, `zoomReset`, `_setZoom` (min/max), `_applyZoomDoc`, `_updateZoomLabel` | e2e | pass after fix 10 |
| `_initPdfToolbar` (rotate, delete, arrange, split, extract, compress, page input) | code review. Rotate and delete need a writable PDF; result handling fixed | fixed (12) |
| `_initZoomButtons` (fullscreen, Ctrl+wheel) | buttons covered by e2e. Fullscreen opens Dialogs (other area) | pass |
| `_initPdfKeyNav` (arrow keys) | e2e | pass |
| `getCurrentPath`, `getCurrentPage`, `getPdfCount`, `getView` | e2e | pass |

### ui/js/pdf-text.js
| Function | How tested | Result |
|---|---|---|
| `wrapPage`, `attach`, `_syncScale` | e2e (text layer ready, zoom keeps it aligned) | pass |
| `_build`, `_measure` | e2e Korean word spans | pass |
| `_linkLines`, `_linkAtBox`, `_linkAtEvent`, `_bindLinks` | e2e (2 link underlines, `is-link` spans). Ctrl+click not run, to avoid opening a real browser | pass |
| `selectionIn`, `_rangeText`, `_selInLayers`, `selectedText` | e2e | pass |
| document `copy` handler, Windows clipboard via bridge | e2e Ctrl+C, checked with `win32clipboard` (Korean) | pass |
| `selectPage` | e2e Ctrl+A | pass |
| `_clip`, `copyText`, `copyPage`, `copyAll` | e2e buttons, then clipboard read | pass |
| `menu`, `_hideMenu` (Esc, outside click) | e2e | pass |
| `enablePan` | e2e | pass after fixes 8 and 9 |

### doc_preview.py and doc_preview_bridge.py
| Function | How tested | Result |
|---|---|---|
| `_open_pdf`, `pdf_text_layer`, `_page_words`, `_page_links`, `_web_url`, `_rect_link`, `_center_inside` | py and probe (locked, broken, missing, out of range, rotated, scanned) | pass |
| `pdf_page_text`, `pdf_all_text`, `_clean_text` | py, probe, e2e | pass |
| `_DocxRenderer` (lists, runs, links, images, tables, merges) | py and e2e | pass after fix 13 |
| `docx_app_props`, `docx_to_html` | py | pass after fix 3 |
| `col_letter`, `_load_wb`, `_sheet_extent`, `xlsx_to_html`, `_grid_html_ws` | py and e2e | pass after fixes 4–6 |
| `format_cell_value`, `_format_number`, `_format_date`, `_general`, `_split_sections`, `_strip_fmt_section`, `_rgb` | py (parametrised) | pass |
| `_read_text_any`, `csv_to_html` | py and e2e (UTF-8 BOM Korean) | pass |
| `xlsx_sheet_names`, `quick_stats` | py | pass |
| `office_app_for`, `office_available`, `preview_cache_dir`, `cached_pdf_path`, `_valid_pdf` | py and probe | pass after fix 1 |
| `_convert_with_office` (Word, Excel, PowerPoint) | probe with real Office: docx 7 s, xlsx 5 s, pptx 4 s; broken docx gives a clean error | pass after fix 1 |
| `office_to_pdf` (cache, lock, timeout, failure) | py and probe | pass |
| bridge `pdf_text_layer`, `pdf_page_text`, `pdf_all_text`, `docx_preview_html`, `sheet_preview_html`, `office_preview_info`, `office_preview_pdf`, `_guard` | probe and py | pass |

### sfm_bridge.py (preview methods) and file_ops.py
| Function | How tested | Result |
|---|---|---|
| `get_pdf_page_count`, `get_pdf_page_as_png` | probe and e2e | pass |
| `get_image_preview` | probe, py, e2e | pass after fix 2 |
| `get_text_preview` | probe and e2e | pass |
| `set_clipboard` (Korean, CF_UNICODETEXT) | e2e plus win32clipboard read | pass |
| `qr_open_url` | py (`is_safe_to_open`) | pass |
| `rotate_pdf_page`, `delete_pdf_page` | existing tests (pdf tools) | pass |
| `_get_cached_fitz_doc` (file_ops) | py | pass after fix 7 |

### QR, screen pick, media
| Module | How tested | Result |
|---|---|---|
| `qr_pick.py`: `normalize_url`, `parse_result`, `_nearest`, `pick_decode`, `region_decode`, `success_payload`, `helper_command`, `run_helper`, `finalize`, `_emit` | tests/test_qr_pick.py (existing) and an import check | pass |
| `qr_pick.py`: `PickOverlay`, `_Frame`, `grab_virtual_screen`, `_monitors`, `_cursor_pos`, `main` | not driven. It is a full-desktop overlay and would cover the owner's screen. Unit-tested helpers only | not run |
| `qr_scan.py` (all decode, classify, scan functions) | tests/test_qr_scan.py | pass |
| `screen_pick.py`: `normalize_rect`, `save_crop`, `helper_command`, `run_helper`, `_arg` | test_recorder.py and manual call | pass |
| `screen_pick.py`: `RegionOverlay`, `main` | not driven (desktop overlay) | not run |
| `qr_screen_capture.py`, `excel_grid.py` | legacy Tk app only (`student_folder_maker.py`), not used by the webview app. Import check and `backend_status` only | import ok |
| `media_convert.py` (images↔PDF, convert, compress, PDF compress and preview) | tests/test_conversion.py, test_image_compress.py, test_pdf_compress.py | pass |

### ui/preview-docs.css
Checked visually through e2e screenshots: doc sheet, xl-grid, tabs, notices. No defects found.

## Not fixed and open notes
- `office_to_pdf` timeout: the Office process keeps running in the background thread after a timeout (no kill).
- The CSV preview reads at most 8 MB. Beyond that the rows count and the "truncated" flag do not show that the file was cut.
- PDFs over 96 MB are still opened from disk, so they stay locked while shown (memory trade-off).
- Dragging selected text in Word or Excel views follows normal browser behaviour.
