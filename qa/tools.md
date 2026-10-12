# File tools QA — inventory and results (2026-10-12/13)

Area: pdf_tools.py, pdf_tools_bridge.py, archive_tools.py, archive_bridge.py,
media_convert.py (convert / compress), image_crop.py, ai_photo_editor.py, the
file-tool methods of sfm_bridge.py, and ui/js/pdf-tools.js, archive.js,
convert-tools.js, photo-tools.js (+ the PDF page buttons in preview.js / dialogs.js).

How tested:
- **pytest** = unit/bridge tests in tests/ (`PYTHONUTF8=1 python -m pytest -q -p no:cacheprovider tests server/tests`, 457 pass)
- **probe** = qa/e2e/probe_engines.py / probe_archive.py (corrupt, encrypted, owner-restricted, CMYK, 16-bit, palette, GIF, multi-page TIFF inputs; cancel / damaged ZIP)
- **E2E** = qa/e2e/e2e_file_tools.py driving the real app over CDP (port 9343), outputs checked with PyMuPDF / pyzipper — 13/13 steps pass

## Engines

| Function | How tested | Result |
|---|---|---|
| pdf_tools.unique_path / _write_new / default_output / _safe_stem | pytest | pass |
| pdf_tools.backup_to_review | pytest, E2E (arrange overwrite) | pass |
| parse_page_groups / parse_page_list / pages_label / _page_num | pytest | pass |
| open_pdf (missing / encrypted / owner-only / corrupt) | pytest, probe | pass |
| image_to_pdf_doc (EXIF, alpha, CMYK, 16-bit, TIFF pages) | pytest, probe | pass (docstring fixed: GIF = first frame) |
| open_source / page_count / pdf_info | pytest, probe | pass |
| merge (order, page specs, images, bookmarks, never overwrite, text+fields) | pytest, E2E | pass |
| _fix_toc_levels | pytest (merge bookmarks) | pass |
| split_groups / split (each / every / ranges / extract, text+fields) | pytest, E2E | pass |
| extract | pytest, E2E | pass |
| build_pdf (reorder, rotate, dup, insert, replace + backup) | pytest, E2E | pass |
| thumbnails + cache | pytest | **fixed**: missing file gave raw "[WinError 2]" |
| archive_tools.unique_path / _create_exclusive / _set_aside / _win_safe / safe_member_parts | pytest | pass |
| display_name (cp949) / _pw_candidates / _is_aes / _find_password / _read_error | pytest | pass |
| list_zip | pytest, E2E | pass |
| zip_paths (store/deflate, AES, cancel, self-exclusion) | pytest, E2E | pass |
| extract_zip (folder / here / members / zip-slip / password) | pytest, E2E | **fixed**: cancel or damaged member left a half file under its real name |
| _repack / zip_set_password / zip_remove_password | pytest, probe | pass |
| pdf_is_encrypted / pdf_check_password / pdf_set_password / pdf_remove_password | pytest, probe, E2E | pass (see open questions) |
| media_convert.images_to_pdf / image_to_pdf_each / _frame_stream | pytest, probe, E2E | pass |
| pdf_to_images / parse_pages | pytest, probe, E2E | pass |
| convert_image | pytest, probe | pass |
| compress_image / _fit_to_target / _pick_output | pytest, probe | pass |
| compress_pdf (presets, level-N, KB target, save smallest, text+fields) | pytest, E2E | pass |
| preview_pdf / preview_image (live size preview) | pytest, E2E (level-3 estimate 72 KB vs actual 71 KB) | pass |
| unique_path / unique_dir (media_convert) | pytest | **fixed**: used "name-2", now "name (2)" like all other tools |
| image_crop.crop_source / crop_image / unique_output_path | pytest, probe, E2E | **fixed**: "-2" naming, non-exclusive save |
| ai_photo_editor.normalize_options / build_prompt / masks / cost / run_ai_edit (OpenAI mocked) | pytest | **fixed**: "-2" naming, result save could overwrite a file created during the run |

## Bridge methods

| Method | How tested | Result |
|---|---|---|
| pdf_info / pdf_infos / pdf_thumbnails / pdf_parse_ranges | pytest, E2E | pass |
| pdf_merge / pdf_split / pdf_extract / pdf_build (+ progress events) | pytest, E2E | pass |
| browse_for_pdfs_or_images / browse_for_pdfs | native dialog — not automated | n/t |
| build_pdf_from_pages, merge_pdfs, split_pdf_pages, extract_pdf_pages, reorder_pdf_pages (old API) | pytest (test_file_tools_qa) | **fixed**: overwrote existing outputs / reordered originals in place; now use pdf_tools |
| delete_pdf_page | pytest | **fixed**: now backs the PDF up into _to_review first |
| rotate_pdf_page | existing behaviour, UI error now reported | pass |
| compress_pdf / compress_pdf_quality / compress_pdf_async / compress_preview / file_sizes | pytest, E2E | pass |
| compress_image / compress_images / convert_image | pytest | pass |
| pdf_to_images / pdf_to_images_async | pytest, E2E | pass |
| convert_to_pdf / images_to_pdf / images_to_pdf_async / convert_to_pdf_async | pytest, E2E | pass |
| crop_image / get_crop_source | pytest, E2E | pass |
| ai_photo_actions / ai_photo_estimate / run_ai_photo_action | pytest (mocked), E2E (no-key gate) | pass |
| archive_capabilities / archive_cancel / zip_list / list_zip | pytest, E2E | pass |
| zip_paths / zip_each / zip_folder / zip_extract / unzip | pytest, E2E | pass |
| zip_set_password / zip_remove_password | pytest | pass |
| pdf_is_encrypted / pdf_set_password / pdf_remove_password / pdf_preview_state / pdf_unlock_preview | pytest, E2E | pass |

## UI (E2E, qa/e2e/e2e_file_tools.py)

| Flow | Result |
|---|---|
| Merge dialog (order, name, bookmarks, second run → " (2)") | pass |
| Split every N; bad range disables button with message | pass |
| Extract "3,1" | pass |
| Arrange: rotate, delete, undo/redo, save as new | pass |
| Arrange: overwrite original → backup in _to_review | pass |
| Images → PDF (A4, auto orientation) | pass |
| PDF → images (JPG, page range) | pass |
| Compress: live estimates for every preset, custom level slider, 300 KB target | pass |
| Crop 1:1 + Enter | pass |
| ZIP with AES password; extract prompts, wrong password retried inline | pass |
| PDF protect (no-copy permission) + remove password | pass |
| AI Wear suit with no key → clear "add your key" message, nothing written | pass |
| archive.js password-dialog output name for "x_protected (2)" | **fixed** |
| preview.js rotate/delete ignored failures; could delete the only page | **fixed** |

## Open questions (not changed)

- An owner-restricted PDF (opens without a password, e.g. "no copying") loses its
  restrictions when compressed, merged, split, extracted or re-protected — no owner
  password is asked. Remove password *does* require it, so this is inconsistent.
- Animated GIF → PDF: Images→PDF makes one page per frame; Merge uses the first frame.
