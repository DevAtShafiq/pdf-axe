# File-browser shell QA inventory

How tested: **py** = pytest (`tests/test_shell_bridge.py`, `tests/test_rename_templates.py`);
**e2e1/2/3** = `qa/e2e/shell_e2e.py`, `shell_e2e_2.py`, `shell_e2e_3.py`, run against a live app over CDP (port 9341).
Result: PASS, or FIXED (bug found, fixed, regression test added; commit in brackets).

Commits: A = 9a94043 (bridge), B = 5cb3cf5 (shell JS), C = 8acccef (tests), D = 266b175 (theme save).

## sfm_bridge.py (shell methods)

| Function | How tested | Result |
|---|---|---|
| list_folder | py, e2e1 | FIXED [A]: bare `C:` listed drive C's cwd, now the root |
| list_folder_recursive | py | PASS |
| get_drives / get_home / get_desktop / path_exists | py, e2e1 | PASS |
| get_file_info (pages etc.) | py, e2e1 details | PASS |
| get_file_type_icon(s_batch) / _get_folder_icon / _shell_icon_data_url | not called by the shell UI (Icons.js draws icons) | not exercised |
| browse_for_folder | not automatable (native dialog); wiring checked by code read | n/a |
| open_native | py | FIXED [A]: ignored the (ok, err) tuple, so failures said ok |
| open_folder_in_explorer | code + e2e1 (Show in Explorer wiring) | FIXED [B]: now highlights the item (`select=True`) |
| show_native_context_menu | unused by UI | not exercised |
| run_open_with / get_open_with_commands / open_with_picker | code read only (would launch real apps) | not exercised |
| rename_file | py, e2e1/2 | PASS |
| soft_delete | py, e2e1 | PASS |
| restore_soft_deleted (new) | py, e2e1 | ADDED [A] for undo |
| copy_files | py, e2e1 | FIXED [A]: folder into itself recursed; failures reported as ok |
| move_files | py, e2e1 | FIXED [A]: merged into same-named folder, no failure reporting, folder into itself |
| create_folder | py, e2e1 | PASS |
| set_clipboard | py (opt-in `SFM_TEST_CLIPBOARD=1`), e2e1 | PASS |
| set_clipboard_files / get_clipboard_files (CF_HDROP + Preferred DropEffect) | py (opt-in), e2e1 | PASS |
| get_settings / save_settings / get_api_key / set_api_key | py, e2e3 | PASS |
| get/set_rename_lang, get_rename_templates, filter_rename_suggestions, save_name_template, save_rename_template(s), rename_with_template | py, e2e1/2 | PASS (+ edge tests [C]) |
| log_js | py | PASS |

## rename_templates.py
Covered by the existing 24 tests plus the new edge test; sanitize_stem, strip_ext, unique_name, stem_for, label_for, store rows / suggestions / add_user_row / save_user_rows all PASS.

## ui/js/app.js

| Function | How tested | Result |
|---|---|---|
| switchPanel | e2e1 | PASS |
| toast / setStatus / setStatusFolder / setStatusSelection | e2e1/2 | PASS |
| pushUndo / undo / redo | e2e1 (rename, Move to review) | PASS |
| navigate | e2e1 | FIXED [B]: paths not normalised (breadcrumb gave `C:/x/`) |
| normPath / parentPath (new) | e2e1 | ADDED [B] |
| navBack / navForward / navUp | e2e1 | FIXED [B]: Up did nothing after a breadcrumb click; went to `C:` |
| _addRecent / _showRecentDropdown | e2e1/2 | FIXED [B]: first folder never recorded |
| applyTheme / theme button | e2e1/3 | FIXED [D]: saves serialised |
| _initPaneResizers | e2e2 | PASS |
| keyboard: Ctrl+Z/Y, Ctrl+C/X/V (Caps Lock), Ctrl+Shift+N, Ctrl+Enter, F2, F5, Del, Alt+arrows, Ctrl +/-/0, Esc | e2e1/2/3 | FIXED [B]: worked behind open dialogs / other pages; Ctrl+X cut files while text was selected |
| blocked (new) | e2e1 | ADDED [B] |
| search box (debounced filter, Esc) | e2e1 | PASS |
| syncFileTools / _gateTool | e2e1 | PASS |
| event handlers (ocr/compress/convert/qr/ai done) | code read | PASS (others' areas) |

## ui/js/file-tree.js

| Function | How tested | Result |
|---|---|---|
| loadFolder / refresh | e2e1/2 | FIXED [B]: stale Shift anchor kept; slower old listing could overwrite newer one; refresh() now returns a promise |
| _sortEntries / setSort / header + folders-first | e2e1 | FIXED [B]: focus stayed on the row number (F2 renamed the wrong file) |
| applySearch / _applySearchFilter | e2e1/2 | FIXED [B]: hidden items stayed selected; focus index stale |
| refreshAndSelect / revealPath | e2e1 (new folder) | PASS |
| _renderList / _renderThumbs / lazy thumbnails / empty states | e2e1 | PASS |
| _renderBreadcrumb | e2e1 | FIXED [B]: UNC paths lost the leading `\\` |
| _handleRowClick (click / Ctrl / Shift) | e2e1 | FIXED [B]: Ctrl+click did not set the Shift anchor |
| _handleRowDblClick / Enter | e2e1/3 | PASS |
| _handleRowCtx | e2e1 | PASS |
| _selectionChanged | e2e1 | PASS |
| keyboard nav (arrows, Home/End, PgUp/Dn, Space, Ctrl+A, Backspace, type-ahead) | e2e1 | FIXED [B]: Backspace went to `C:`; keys acted behind dialogs |
| setViewMode / thumb zoom (buttons, slider, Ctrl+wheel) | e2e1/2 | PASS |
| copySelection / cutSelection / pasteSelection | e2e1 | FIXED [B]: errors shown as success; pasted items now selected |
| newFolder | e2e1 | PASS |
| deleteSelection / softDeletePaths (new) | e2e1 | FIXED [B]: undo did nothing but said "Undone" |
| combineSelected | e2e3 | PASS |
| browseFolder | wiring only (native dialog) | n/a |
| startRename / editor / _commit / _applyRename / Tab / Shift+Tab / Esc / blur / clash / extension prompt / reserved names / balloon tip / thumb textarea / initialText | e2e1/2 | PASS |
| _uniqueName / _ask | e2e1 | PASS |
| expandAll / collapseAll | code + More menu e2e1 | PASS |
| tree: _initTree / _renderTreeRoot / drive + folder nodes / toggle / _updateTreeHighlight | e2e1 | FIXED [B]: nested expanded nodes lost children and the highlight was wiped by the re-render |
| getEntry (new) | e2e1 | ADDED [B] |

## ui/js/details.js

| Function | How tested | Result |
|---|---|---|
| showFile | e2e1 | FIXED [B]: crashed on a null entry |
| showMultiple / clear | e2e1 | PASS |
| qa-open | e2e1 | FIXED [B]: folders did nothing; errors now shown |
| qa-opendir | wiring | FIXED [B]: highlights the item |
| qa-rename / qa-copy-path | e2e1 | PASS |
| qa-delete | e2e1 | FIXED [B]: now undoable (shared flow) |
| Open with popover | code read (launches apps) | not exercised |

## ui/js/context-menu.js
Show/position/hide, Rename, Copy Path, Copy Name, Cut/Copy/Paste, Copy To/Move To: e2e1 PASS.
FIXED [B]: "Open" did nothing for folders and showed Ctrl+Enter; Move to Review showed the full path (regex only split on `/`) and was not undoable.

## ui/js/dialogs.js (shell parts)
ask / confirm / openModal stack + Esc: e2e1 PASS. openUppercase: FIXED [B] (changed the extension's case, reported failures as success). openSettings: FIXED [B] (`rk.key` never existed; placeholder now shows the masked key) + e2e3 persistence PASS. openMoreMenu, openCopyTo, openMoveTo, openFullView (page nav, Esc): e2e1/3 PASS.

## ui/js/rename-templates.js
attach (filter, arrows, Enter, Ctrl+Enter save, Esc, language select), openManager (search, Add, Apply, Remove, Save to file, unsaved-changes prompt): e2e1/2 PASS.

## ui/js/bridge.js
call / on / off / emit / copyText: exercised by every e2e check. Added openFolder(select) and restoreSoftDeleted wrappers.

## student_folder_maker.py / doc_renamer.py
The bulk folder maker exists only in the legacy Tk app (`StudentFoldersFrame`); the webview UI has no folder-maker page and imports neither module, so there is no shell function to test. Not exercised.

## Totals
Python: 26 new bridge tests + 1 new rename test (473 passed, 2 clipboard tests opt-in).
E2E: 131 + 38 + 16 = 185 checks, all passing.
