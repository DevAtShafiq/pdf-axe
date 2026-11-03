# QA — editors (Annotate, Edit PDF text, Screen recorder)

Base: aca1864. Method: pytest (`tests/test_annotate.py`, `test_pdf_edit.py`,
`test_recorder.py`, `test_text_pdf_preserved.py`) + own app instance on CDP port
9344 driven by Playwright (`qa/e2e/*.py`, files made in a scratch dir), outputs
checked with PyMuPDF / Pillow.

E2E scripts (run against your own instance, see `qa/e2e/cdp.py`):

| script | checks | result |
|---|---|---|
| `annotate_e2e.py` | every tool, move, resize, undo/redo, zoom, eraser, props, save → real annots, text + form field kept, reload, lock, image + sidecar | 40/40 |
| `annotate_edge_e2e.py` | text-edit undo, empty box, nudge, opacity slider, eraser drag, custom stamp, drawn + remembered signature, close guard, Ctrl+S save-over + backup, rotated page | 18/18 |
| `pdfedit_e2e.py` | Ctrl+Shift+T, context menu, details Tools, preview button, edit block, colour/italic/bold/align/font/size, new text box, undo per box, Del, move, wrap width, save new + save over, re-render | 32/32 |
| `pdfedit_more_e2e.py` | Esc cancels, double-click, empty = delete, image replace/delete, scanned page notice + add text | 13/13 |
| `recorder_e2e.py` | setup dialog, start/pause/resume/stop (~2 s, canvas stream instead of the screen picker), Ctrl+Shift+R, discard → `_to_review`, recent list, full screenshot | 20/20 |
| `entry_points_e2e.py` | Annotate via Ctrl+Shift+A / details / preview / context menu (PDF + PNG), More-actions Capture, Settings recordings folder | 14/14 |

## Inventory

Legend: **P** = pytest, **U** = UI E2E, **R** = code review only. Result ✔ = passes (after fixes).

### annotate.py (38)
| function(s) | how tested | result |
|---|---|---|
| load, _load_pdf, _read_annot, _oadata, _quads_from_vertices, _annot_png, _open_pdf | P round trip of every type, rotated, unknown kinds; U reload | ✔ |
| _load_image, read_sidecar, sidecar_for, _open_upright | P sidecar tests; U image reload | ✔ |
| render_page | P (+ new form-field test); U page images | ✔ fixed (6969dfa) |
| words | P; U markup snaps to words | ✔ |
| save, _pdf_bytes, _write_annot, _derot, _derot_pt, _replace | P every type, rotated, flatten, redact, never overwrite, backup; U | ✔ |
| _save_image, render_image, _encode_image, _write_sidecar, _font, _wrap | P; U image save | ✔ |
| default_output, hex_rgb, rgb_hex, _rgb255, pdf_date, iso_date, _rect4, _norm, _decode_data_url, _data_url, is_image, is_pdf, _fitz | P (indirect) | ✔ |

### annotate_bridge.py (14)
| annot_author, annot_load, annot_page_png, annot_words, annot_save, _annot_run, _ok, _err, _release_handles | P bridge round trip; U | ✔ |
| annot_photo_load | U photo tool (fit into box) | ✔ |
| annot_pick_photo | native dialog — stubbed in U | R |
| annot_signatures, annot_signature_save | P; U remembered signature | ✔ |
| annot_signature_forget | P (+ new index test); U | ✔ fixed (3864a2a) |

### ui/js/annotate.js (72)
Tools: select/move (V), highlight (H), underline (U), strikeout (S), squiggly (Q),
pen (P), rect (R), ellipse (O), arrow (A), line (L), text (T), note (C), stamp menu
+ custom, signature (draw/type/remembered/forget), photo (box fit), cover (X),
redact, eraser (click + drag) — all **U ✔**.
open, buildPages, pump/wantPage, applyZoom/setZoom/zoomStep/fitWidth/fitPage, goPage/setCur,
snapshot/undo/redo, add/removeIds/select, pointer handlers/endDrag/resized/constrain/simplify,
drawLive/ghost/hoverErase/eraseAt, pendingAnn/placePending, stamp menu, placePhotoInBox,
signaturePad/redraw/trimmed/loadSaved, editText/autoGrow/positionEditor/commitEdit, setTool,
renderProps/wireProps/setProp (colour, opacity, width), renderComments, status, onKey (keys,
Del, Enter, arrows, Ctrl+Z/Y/S, Esc), save (new / over / redaction confirm / lock),
tryClose/forceClose, _init/_currentCandidate (Ctrl+Shift+A, preview button) — **U ✔**.
Ctrl+Shift+A was broken by the file list's select-all — fixed (5778568).

### pdf_edit.py (≈50)
| extract_blocks, _units, _line_info, _textdict, _detect_align, _public_block, _images, is_scanned, _visible_text_chars, doc_info, page_count, render_page | P; U | ✔ (_images fixed 723f12d) |
| apply_edits, _edit_bytes, _apply_text_redactions, _redact_rect, _layout, _wrap, _write_lines, _fix_tounicode, _set_metadata, default_output | P (all edit kinds, rotated 0/90/180/270, CJK, neighbours, widgets, user redactions); U | ✔ |
| _Fonts (original/windows/helv/_alias), _choose_font, _has_all, family_for, style_from, clean_font_name, font_file, available_fonts | P; U font menu | ✔ (_alias fixed 6f23835) |
| _image_bytes_for, _image_uses, _decode_data_url | P; U replace image | ✔ |
| _open, _r4, _disp_rect, _vec, _int_hex, _hex_rgb, _fitz | P (indirect) | ✔ |

### pdf_edit_bridge.py (7)
pdfedit_open, pdfedit_blocks, pdfedit_page_png, pdfedit_apply, pdfedit_fonts, _pdfedit_run, _release_handles — U (every call) + P engine ✔

### ui/js/pdf-edit.js (52)
open/load, buildPages/pump/loadData/renderPage/textHtml, zoom + page nav, hint (scanned),
status/syncToolbar, setMode (Add text / T / V), select/startEdit/positionEditor/commitEdit/
cancelEdit, applyStyle (font, size, colour, bold, italic, align), deleteSel, imgOp
(replace/delete), pointer move/width drag, dblclick, onKey (Del, Enter, Esc, Ctrl+Z/Y/S,
PageUp/Down), buildEdits, save (new / over, notice, re-render), tryClose/forceClose,
_init (Ctrl+Shift+T, preview button), context menu + details Tools — **U ✔**.
applyStyle/flushEditText fixed (9180868).

### recorder_bridge.py (39)
| clean_stem, unique_path, is_inside, stamp, _fmt_size, known_videos_dir, default_output_dir, _move_no_overwrite | P | ✔ |
| rec_default_dir, rec_set_dir, _rec_resolve_dir, rec_begin, rec_chunk, rec_finish, rec_discard, rec_list, rec_recover, _rec_state/_session/_close/_pop | P; U (real MediaRecorder data) | ✔ |
| rec_set_title | P; U (title while recording / restored) | ✔ |
| rec_hotkey, _Hotkey | P (disable); U registered during recordings; system-wide press not simulated | ✔ / R |
| screenshot_full, _rec_hide_app | P (mocked grab); U real capture into scratch dir | ✔ |
| screenshot_region | P (helper mocked, cancel/Esc); helper overlay needs a human drag | P only |
| rec_window (minimise/restore), rec_reveal (Explorer) | R — not run, would move the user's windows / open Explorer | R |

### ui/js/recorder.js (47)
open/_syncSetup/_showDir/_loadList, start, _write/_blobToB64, _tick/_elapsed, _showBar,
togglePause, stop, discard, toggle (Ctrl+Shift+R), _cleanup/_stopRecorder, screenshot,
_savedToast, _injectMore, _injectSettings, _formats/_pickFormat/_supported — **U ✔**.
_startMicPreview/_stopMicPreview/_startCamPreview/_composite/toggleMute/_fillDevices — R
(needs a real microphone / webcam; prefs set to no audio, no webcam in the E2E).
_placeBar/_focusBar/_countdown — R.

## Totals
~319 functions inventoried; ~300 exercised by pytest and/or UI E2E; all exercised
ones pass after the fixes. Review-only: native pickers, mic/webcam/compositor,
window minimise/restore, Explorer reveal, the region-picker overlay.

## Bugs fixed
| commit | bug |
|---|---|
| 6969dfa | Annotate page image hid filled form fields (annots=False also hides widgets) |
| 6f23835 | Editing an already-edited PDF garbled the new AND earlier inserted text (font alias OAE1 re-used) |
| 9180868 | PDF edit: Undo after a style change emptied the text box being typed; no-op style changes added undo steps |
| 723f12d | PDF edit: a deleted image came back as a ghost 1×1 "image" box |
| 3864a2a | Forget signature removed the wrong one when settings held a stray entry |
| 5778568 | Ctrl+Shift+A selected every file (list's Ctrl+A ignored Shift) so Annotate refused to open |

## Notes / not changed
- Clicking a text block in Edit text selects the whole block's text (by design), so the
  first key replaces the paragraph; Esc restores it.
