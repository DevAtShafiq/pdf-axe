/**
 * bridge.js — Safe wrapper around window.pywebview.api
 *
 * Provides:
 *   SFM.call(method, ...args)  → Promise<result>
 *   SFM.on(event, handler)     → unsubscribe fn
 *   SFM.emit(event, payload)   → dispatch locally (for testing)
 *
 * window.__sfm_event(event, payload) is called by Python to push events.
 */

const SFM = (() => {
  const _listeners = {};   // event → [fn, ...]

  // ── event bus ──────────────────────────────────────────────────────────────
  function on(event, fn) {
    if (!_listeners[event]) _listeners[event] = [];
    _listeners[event].push(fn);
    return () => off(event, fn);
  }
  function off(event, fn) {
    if (_listeners[event])
      _listeners[event] = _listeners[event].filter(f => f !== fn);
  }
  function emit(event, payload) {
    (_listeners[event] || []).forEach(fn => {
      try { fn(payload); } catch(e) { console.error('[SFM event]', event, e); }
    });
  }

  // Python calls this to push events into JS
  window.__sfm_event = (event, payload) => emit(event, payload);

  // ── JS → Python logger ────────────────────────────────────────────────────
  function _pylog(level, ...parts) {
    const msg = parts.map(p => (typeof p === 'object' ? JSON.stringify(p) : String(p))).join(' ');
    _getApi().then(api => api.log_js && api.log_js(level, msg)).catch(() => {});
  }

  // Global JS error → log file
  window.onerror = (msg, src, line, col, err) => {
    _pylog('error', `UNCAUGHT: ${msg} @ ${src}:${line}:${col}`);
  };
  window.addEventListener('unhandledrejection', e => {
    _pylog('error', `UNHANDLED PROMISE: ${e.reason}`);
  });

  // ── api call wrapper ───────────────────────────────────────────────────────
  async function call(method, ...args) {
    let api = await _getApi();
    // pywebview attaches the API methods in batches after window.pywebview.api
    // appears, so a call made right at start-up can arrive before its method
    // exists (the drive list came up empty on first launch). Wait for it.
    for (let waited = 0; !api[method] && waited < 10000; waited += 50) {
      await new Promise(r => setTimeout(r, 50));
      api = window.pywebview.api || api;
    }
    if (!api[method]) {
      _pylog('error', `Bridge: unknown method "${method}"`);
      throw new Error(`Bridge: unknown method "${method}"`);
    }
    try {
      const result = await api[method](...args);
      if (result && result.ok === false) {
        _pylog('warn', `${method} → error: ${result.error}`);
      }
      return result;
    } catch(e) {
      _pylog('error', `${method} threw: ${e}`);
      throw e;
    }
  }

  // ── wait for pywebview to initialise ──────────────────────────────────────
  let _apiReady = null;
  function _getApi() {
    if (_apiReady) return _apiReady;
    _apiReady = new Promise(resolve => {
      function check() {
        if (window.pywebview && window.pywebview.api) {
          resolve(window.pywebview.api);
        } else {
          setTimeout(check, 50);
        }
      }
      check();
    });
    return _apiReady;
  }

  // ── convenience wrappers ──────────────────────────────────────────────────
  return {
    call, on, off, emit,
    // Folder / file system
    listFolder:          (path)                    => call('list_folder', path),
    listFolderRec:       (path, depth=3)           => call('list_folder_recursive', path, depth),
    getDrives:           ()                        => call('get_drives'),
    getHome:             ()                        => call('get_home'),
    getDesktop:          ()                        => call('get_desktop'),
    pathExists:          (path)                    => call('path_exists', path),
    getFileTypeIcon:     (ext, size=18)            => call('get_file_type_icon', ext, size),
    getFileTypeIconsBatch: (exts, size=18)         => call('get_file_type_icons_batch', exts, size),
    getFileInfo:         (path)                    => call('get_file_info', path),
    openNative:          (path)                    => call('open_native', path),
    openFolder:          (path)                    => call('open_folder_in_explorer', path),
    getOpenWithCommands: (path)                    => call('get_open_with_commands', path),
    runOpenWith:         (argv, path)               => call('_run_open_with', argv, path),
    showContextMenu:     (path, x, y)              => call('show_native_context_menu', path, x, y),
    // File ops
    renameFile:          (old, name)               => call('rename_file', old, name),
    softDelete:          (paths)                   => call('soft_delete', paths),
    copyFiles:           (paths, dest)             => call('copy_files', paths, dest),
    moveFiles:           (paths, dest)             => call('move_files', paths, dest),
    createFolder:        (parent, name)            => call('create_folder', parent, name),
    setClipboard:        (text)                    => call('set_clipboard', text),
    // PDF thumbnails / pages
    getPdfThumb:         (path, page=0, dpi=72)    => call('get_pdf_thumbnail', path, page, dpi),
    getPdfPage:          (path, page, dpi=150)     => call('get_pdf_page_as_png', path, page, dpi),
    getPdfPageCount:     (path)                    => call('get_pdf_page_count', path),
    // Previews
    getImagePreview:     (path, maxDim=1200)       => call('get_image_preview', path, maxDim),
    getTextPreview:      (path)                    => call('get_text_preview', path),
    // PDF ops
    mergePdfs:           (paths, out)              => call('merge_pdfs', paths, out),
    splitPdf:            (path, dir)               => call('split_pdf_pages', path, dir),
    compressPdf:         (path, out)               => call('compress_pdf', path, out),
    compressPdfQuality:  (path, quality='ebook', out='', targetKb=0, saveSmallest=false) => call('compress_pdf_quality', path, quality, out, targetKb, saveSmallest),
    // job='' → compress_done event (legacy); job set → media_progress (per attempt) + media_done
    compressPdfAsync:    (path, out='', preset='ebook', targetKb=0, job='', saveSmallest=false) => call('compress_pdf_async', path, out, preset, targetKb, job, saveSmallest),
    fileSizes:           (paths)                   => call('file_sizes', paths),
    // Predicted sizes, in memory; emits compress_preview events tagged with job
    compressPreview:     (paths, settings={}, job='') => call('compress_preview', paths, settings, job),
    rotatePage:          (path, page, deg)         => call('rotate_pdf_page', path, page, deg),
    deletePage:          (path, page)              => call('delete_pdf_page', path, page),
    reorderPages:        (path, order, out)        => call('reorder_pdf_pages', path, order, out),
    buildPdfFromPages:   (pages, out, replace=false) => call('build_pdf_from_pages', pages, out, replace),
    browseForPdfs:       ()                        => call('browse_for_pdfs'),
    extractPdfPages:     (path, spec, out)         => call('extract_pdf_pages', path, spec, out),
    pdfToImages:         (path, dpi=150, fmt='png', pages='', quality=90) => call('pdf_to_images', path, dpi, fmt, pages, quality),
    pdfToImagesAsync:    (path, opts={}, job='')   => call('pdf_to_images_async', path, opts, job),
    combineToPdf:        (paths, out)              => call('combine_files_to_pdf', paths, out),
    convertToPdf:        (path, out='', opts={})   => call('convert_to_pdf', path, out, opts),
    convertToPdfAsync:   (paths)                   => call('convert_to_pdf_async', paths),
    imagesToPdf:         (paths, out='', opts={})  => call('images_to_pdf', paths, out, opts),
    imagesToPdfAsync:    (paths, opts={}, job='')  => call('images_to_pdf_async', paths, opts, job),
    mediaCapabilities:   ()                        => call('media_capabilities'),
    // PDF tools: merge / split / arrange / extract (pdf_tools_bridge.py)
    pdfInfo:             (path)                    => call('pdf_info', path),
    pdfInfos:            (paths)                   => call('pdf_infos', paths),
    pdfThumbs:           (path, pages, width=160)  => call('pdf_thumbnails', path, pages, width),
    pdfParseRanges:      (spec, count, mode='ranges', every=1) => call('pdf_parse_ranges', spec, count, mode, every),
    pdfMerge:            (inputs, out='', bookmarks=true, job='') => call('pdf_merge', inputs, out, bookmarks, job),
    pdfSplit:            (path, mode='each', every=1, ranges='', outDir='', job='') => call('pdf_split', path, mode, every, ranges, outDir, job),
    pdfExtract:          (path, spec, out='', job='') => call('pdf_extract', path, spec, out, job),
    pdfBuild:            (pages, out, replace=false, job='') => call('pdf_build', pages, out, replace, job),
    browseForPdfsOrImages: ()                      => call('browse_for_pdfs_or_images'),
    // Image
    cropImage:           (path, x, y, w, h, out='', rotate=0) => call('crop_image', path, x, y, w, h, out, rotate),
    getCropSource:       (path, maxDim=1600)               => call('get_crop_source', path, maxDim),
    compressImage:       (path, quality=70, maxEdge=0, fmt='', out='', targetKb=0, saveSmallest=true) => call('compress_image', path, quality, maxEdge, fmt, out, targetKb, saveSmallest),
    compressImages:      (paths, quality=70, maxEdge=0, fmt='', targetKb=0)        => call('compress_images', paths, quality, maxEdge, fmt, targetKb),
    convertImage:        (path, fmt, out='', quality=92)               => call('convert_image', path, fmt, out, quality),
    ocrRenameProgress:   (paths)                           => call('ocr_rename_with_progress', paths),
    // Long async ops
    smartSplit:          (path, outDir='')         => call('smart_split_rename', path, outDir),
    splitAndRenameOcr:   (path)                    => call('split_and_rename_ocr', path),
    ocrRename:           (paths)                   => call('ocr_rename', paths),
    // QR / misc
    qrPickStart:         ()                        => call('qr_pick_start'),
    scanQrFromFile:      (path)                    => call('scan_qr_from_file', path),
    scanQrFiles:         (paths, jobId='')         => call('scan_qr_files', paths, jobId),
    qrScanCancel:        (jobId)                   => call('qr_scan_cancel', jobId),
    qrOpenUrl:           (url)                     => call('qr_open_url', url),
    getCost:             ()                        => call('get_cost'),
    resetCost:           ()                        => call('reset_cost'),
    getSettings:         ()                        => call('get_settings'),
    saveSettings:        (s)                       => call('save_settings', s),
    getApiKey:           ()                        => call('get_api_key'),
    setApiKey:           (k)                       => call('set_api_key', k),
    getRenameTemplates:  (lang='')                 => call('get_rename_templates', lang),
    filterSuggestions:   (q, ext='', lang='')     => call('filter_rename_suggestions', q, ext, lang),
    saveNameTemplate:    (en, local='', lang='')   => call('save_name_template', en, local, lang),
    saveRenameTemplates: (rows, lang='')           => call('save_rename_templates', rows, lang),
    getRenameLang:       ()                        => call('get_rename_lang'),
    setRenameLang:       (lang)                    => call('set_rename_lang', lang),
    renameWithTemplate:  (path, name, save=false, lang='') => call('rename_with_template', path, name, save, lang),
    saveRenameTemplate:  (tpl)                     => call('save_rename_template', tpl),
    // AI photo
    runAiPhoto:          (path, action, opts={})   => call('run_ai_photo_action', path, action, opts),
    aiPhotoActions:      ()                        => call('ai_photo_actions'),
    aiPhotoEstimate:     (path, opts={})           => call('ai_photo_estimate', path, opts),
    // Account / subscription / cloud storage
    accountGetState:     ()                        => call('account_get_state'),
    accountSetServer:    (url)                     => call('account_set_server', url),
    accountRegister:     (email, password, remember=true) => call('account_register', email, password, remember),
    accountLogin:        (email, password, remember=true) => call('account_login', email, password, remember),
    accountLogout:       ()                        => call('account_logout'),
    billingOpenCheckout: ()                        => call('billing_open_checkout'),
    billingOpenPortal:   ()                        => call('billing_open_portal'),
    cloudList:           (trashed=false)           => call('cloud_list', trashed),
    cloudUsage:          ()                        => call('cloud_usage'),
    cloudPickFiles:      ()                        => call('cloud_pick_files'),
    cloudUpload:         (paths, remoteDir='')     => call('cloud_upload', paths, remoteDir),
    cloudDownload:       (ids, destDir, strip='')  => call('cloud_download', ids, destDir, strip),
    cloudTrash:          (ids)                     => call('cloud_trash', ids),
    cloudRestore:        (ids)                     => call('cloud_restore', ids),
    cloudSyncStart:      (folder)                  => call('cloud_sync_start', folder),
    cloudSyncStop:       ()                        => call('cloud_sync_stop'),
    cloudSyncStatus:     ()                        => call('cloud_sync_status'),
    accountListDevices:  ()                        => call('account_list_devices'),
    accountRevokeDevice: (id)                      => call('account_revoke_device', id),
    cloudUploadCancel:   ()                        => call('cloud_upload_cancel'),
    cloudEnableDrop:     ()                        => call('cloud_enable_drop'),
    cloudMove:           (id, newPath)             => call('cloud_move', id, newPath),
    cloudMoveFolder:     (path, newPath)           => call('cloud_move_folder', path, newPath),
    cloudTrashFolder:    (path)                    => call('cloud_trash_folder', path),
    cloudSyncPause:      ()                        => call('cloud_sync_pause'),
    cloudSyncResume:     ()                        => call('cloud_sync_resume'),
    cloudSyncNow:        ()                        => call('cloud_sync_now'),
    // Office (shared Country → Program → Student drive for all staff)
    // events: office_changed {reason}, shared_changed {paths, actor, action},
    //         shared_upload_progress / shared_upload_done, shared_download_progress / shared_download_done
    officeGetState:      ()                        => call('office_get_state'),
    officeCreate:        (name)                    => call('office_create', name),
    officeRename:        (name)                    => call('office_rename', name),
    officeJoin:          (code)                    => call('office_join', code),
    officeAcceptInvite:  (id)                      => call('office_accept_invite', id),
    officeDeclineInvite: (id)                      => call('office_decline_invite', id),
    officeLeave:         ()                        => call('office_leave'),
    officeTransfer:      (userId)                  => call('office_transfer', userId),
    officeMembers:       ()                        => call('office_members'),
    officeInvite:        (email, role='staff')     => call('office_invite', email, role),
    officeInvites:       ()                        => call('office_invites'),
    officeRevokeInvite:  (id)                      => call('office_revoke_invite', id),
    officeSetRole:       (userId, role)            => call('office_set_role', userId, role),
    officeRemoveMember:  (userId)                  => call('office_remove_member', userId),
    officeSettingsGet:   ()                        => call('office_settings_get'),
    officeSettingsSet:   (settings)                => call('office_settings_set', settings),
    sharedList:          (path='')                 => call('shared_list', path),
    sharedMkdir:         (parent, name)            => call('shared_mkdir', parent, name),
    sharedNewStudent:    (country, program, name)  => call('shared_new_student', country, program, name),
    sharedUpload:        (localPaths, remoteDir, jobId) => call('shared_upload', localPaths, remoteDir, jobId),
    sharedDownload:      (remotePaths, localDir, jobId) => call('shared_download', remotePaths, localDir, jobId),
    sharedOpen:          (path)                    => call('shared_open', path),
    sharedRename:        (path, newName)           => call('shared_rename', path, newName),
    sharedMove:          (paths, destDir)          => call('shared_move', paths, destDir),
    sharedTrash:         (paths)                   => call('shared_trash', paths),
    sharedTrashList:     ()                        => call('shared_trash_list'),
    sharedRestore:       (ids)                     => call('shared_restore', ids),
    sharedSearch:        (query)                   => call('shared_search', query),
    sharedActivity:      (limit=50)                => call('shared_activity', limit),
    sharedUsage:         ()                        => call('shared_usage'),
  };
})();
