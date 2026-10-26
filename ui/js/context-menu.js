/**
 * context-menu.js — Custom HTML right-click menu
 *
 * Usage:
 *   ContextMenu.show(x, y, entries, selectedPaths)
 *   ContextMenu.hide()
 */

const ContextMenu = (() => {

  // ── Build DOM ─────────────────────────────────────────────────────────────
  let _menu = null;
  const DANGER = new Set(['Move to Review']);

  function _ensureMenu() {
    if (_menu) return _menu;
    _menu = document.createElement('div');
    _menu.id = 'ctx-menu';
    _menu.className = 'context-menu hidden';
    document.body.appendChild(_menu);

    document.addEventListener('mousedown', e => {
      if (!_menu.contains(e.target)) hide();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') hide();
    });
    return _menu;
  }

  // ── Show ──────────────────────────────────────────────────────────────────
  function show(x, y, entries, selectedPaths) {
    const menu = _ensureMenu();
    menu.innerHTML = '';
    menu.dataset.kind = 'items';

    const single  = entries.length === 1;
    const multi    = entries.length > 1;
    const entry    = single ? entries[0] : null;
    const isPdf    = single && (entry?.ext || '').toLowerCase() === '.pdf';
    const isImg    = single && ['.jpg','.jpeg','.png','.bmp','.webp','.gif'].includes((entry?.ext||'').toLowerCase());
    const isDir    = single && entry?.is_dir;
    const paths    = selectedPaths || entries.map(e => e.path);
    const mainPath = entry?.path || paths[0];

    // Section: open
    // Enter opens (Ctrl+Enter is Merge/Arrange). A folder opens in the list.
    if (single) _item(menu, isDir ? 'folder-open' : 'external-link', 'Open', 'Enter', async () => {
      hide();
      if (isDir) { App.navigate(entry.path); return; }
      const r = await SFM.openNative(entry.path);
      if (r && r.ok === false) App.toast('Could not open: ' + r.error, 'error');
    });
    if (single) _item(menu, 'folder-open', 'Show in Explorer', '', () => { hide(); SFM.openFolder(entry.path, true); });
    if (single && entry && !isDir) {
      _item(menu, 'app-window', 'Open with\u2026', '', async () => {
        hide();
        App.setStatus('Getting apps\u2026', true);
        const r = await SFM.getOpenWithCommands(mainPath);
        App.setStatus('Ready');
        const apps = (r.apps && r.apps.length) ? r.apps : (r.commands || []).map(c => ({ name: c[0], argv: c[1], icon: '' }));
        if (!r.ok || !apps.length) { App.toast('No apps found for this file type', 'info'); return; }
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.zIndex = 99999;
        const listHtml = apps.map((c, i) => {
          const icon = c.icon
            ? '<img src="' + c.icon + '" alt="" width="16" height="16">'
            : Icons.svg('app-window', 16);
          return '<button class="qa-btn" data-idx="' + i + '"><span class="icon">' + icon + '</span>' + _esc(c.name) + '</button>';
        }).join('');
        overlay.innerHTML = '<div class="modal modal-sm" role="dialog">'
          + '<div class="modal-header"><h2 class="modal-title">Open with\u2026</h2></div>'
          + '<div class="modal-body" style="gap:2px;padding:8px 12px">' + listHtml + '</div>'
          + '<div class="modal-footer"><button class="btn" id="ow-cancel">Cancel</button></div></div>';
        document.body.appendChild(overlay);
        overlay.querySelectorAll('[data-idx]').forEach(btn => {
          btn.addEventListener('click', async () => {
            overlay.remove();
            const cmd = apps[parseInt(btn.dataset.idx)];
            await SFM.runOpenWith(cmd.argv, mainPath);
          });
        });
        overlay.querySelector('#ow-cancel').addEventListener('click', () => overlay.remove());
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
      });
    }
    _sep(menu);

    // Section: edit
    _item(menu, 'scissors', 'Cut',  'Ctrl+X', () => { hide(); FileTree.cutSelection(); });
    _item(menu, 'copy', 'Copy', 'Ctrl+C', () => { hide(); FileTree.copySelection(); });
    _item(menu, 'paste', 'Paste','Ctrl+V', () => { hide(); FileTree.pasteSelection(); });
    _sep(menu);

    // Section: file ops
    _item(menu, 'text-cursor', 'Rename',         'F2',  () => { hide(); setTimeout(() => FileTree.startRename(), 50); });
    // Same flow as the Del key: names the file (not its full path), undoable.
    _item(menu, 'archive', 'Move to Review', 'Del', () => { hide(); FileTree.softDeletePaths(paths); });
    _sep(menu);

    // Section: PDF ops
    const allPdf = entries.length > 0 && entries.every(e => (e.ext||'').toLowerCase() === '.pdf');
    // Mixed PDFs + images → merge (images become pages).
    const mixedPdfImg = !allPdf && entries.length > 1
      && entries.every(e => /^\.(pdf|jpe?g|png|bmp|gif|tiff?|webp)$/i.test(e.ext || ''))
      && entries.some(e => (e.ext||'').toLowerCase() === '.pdf');
    // One Compress dialog for PDFs, images or a mix (target size or quality).
    const cmpLabel = n => n > 1 ? 'Compress ' + n + ' files…' : 'Compress…';
    const openCmp = () => { hide(); ConvertTools.openCompress(entries.map(e => e.path)); };
    if (mixedPdfImg) {
      _item(menu, 'merge', 'Merge into One PDF…', '', () => { hide(); PdfTools.openMerge(paths, FileTree.getCurrentFolder()); });
      _item(menu, 'compress', cmpLabel(entries.length), '', openCmp);
      _sep(menu);
    }
    if (!multi && /\.docx$/i.test(mainPath || '') && window.DocxEdit) {
      _item(menu, 'file-pen', 'Edit…', '', () => { hide(); DocxEdit.open(mainPath); });
      _sep(menu);
    }
    if (allPdf) {
      if (multi) {
        _item(menu, 'merge', 'Merge PDFs…', '', () => { hide(); PdfTools.openMerge(paths, FileTree.getCurrentFolder()); });
        _item(menu, 'compress', cmpLabel(entries.length), '', openCmp);
        _sep(menu);
      }
      if (isPdf) {
        if (window.Studio) _item(menu, 'file-pen', 'Edit…', 'Ctrl+Shift+P', () => { hide(); Studio.open(mainPath); });
        if (window.Studio && !multi) {
          _item(menu, 'signature', 'Sign…', 'Ctrl+Shift+S', () => { hide(); Details.sign(mainPath); });
          _item(menu, 'image', 'Add Photo…', '', () => { hide(); Details.addPhoto(mainPath); });
        }
        _item(menu, 'layers', 'Arrange Pages…', '', () => { hide(); PdfTools.openArrange([mainPath]); });
        _item(menu, 'scissors', 'Split PDF…', '', () => { hide(); PdfTools.openSplit(mainPath); });
        _item(menu, 'extract', 'Extract Pages…', '', () => { hide(); PdfTools.openExtract(mainPath); });
        // "Current page" = the page shown in the preview for this file.
        const curPage = PdfTools.previewPageFor(mainPath);
        if (curPage !== null) {
          _item(menu, 'file-pdf', 'Extract Current Page (' + (curPage + 1) + ') → New PDF', '', () => {
            hide(); PdfTools.extractPage(mainPath, curPage);
          });
        }
        if (!multi) _item(menu, 'compress', 'Compress…', '', () => { hide(); ConvertTools.openCompress([mainPath]); });
        _item(menu, 'images', 'Convert to Images…',        '', () => { hide(); Dialogs.openPdfToImages(mainPath); });
        _sep(menu);
      }
    }

    // Section: image ops
    if (isImg) {
      if (window.Annotate && Annotate.canAnnotate(mainPath)) _item(menu, 'annotate', 'Annotate…', 'Ctrl+Shift+A', () => { hide(); Annotate.open(mainPath); });
      if (window.Annotate && Annotate.canAnnotate(mainPath) && !multi) _item(menu, 'signature', 'Sign…', 'Ctrl+Shift+S', () => { hide(); Details.sign(mainPath); });
      _item(menu, 'crop', 'Crop Image…', '', () => { hide(); Dialogs.openCropImage(mainPath); });
      if (typeof PhotoSize !== 'undefined') _item(menu, 'user', 'Crop to photo size…', '', () => { hide(); PhotoSize.open(mainPath); });
      _item(menu, 'shirt', 'Wear Suit & Tie (AI)', '', () => { hide(); Details.runAiPhoto(mainPath, 'wear_suit'); });
    }

    // Section: Word / 한글 documents → PDF (single or multi-select)
    const allDoc = entries.length > 0 && entries.every(e => !e.is_dir && ConvertTools.isDoc(e.path));
    if (allDoc) {
      const docPaths = entries.map(e => e.path);
      _item(menu, 'file-pdf', docPaths.length > 1 ? `Convert ${docPaths.length} to PDF` : 'Convert to PDF', '', () => { hide(); ConvertTools.docsToPdf(docPaths); });
      _sep(menu);
    }

    // Section: image compress / convert (single or multi-select)
    const IMG_EXTS = ConvertTools.IMAGE_EXTS;
    const allImg = entries.length > 0 && entries.every(e => !e.is_dir && IMG_EXTS.includes((e.ext||'').toLowerCase()));
    if (allImg) {
      const imgPaths = entries.map(e => e.path);
      _item(menu, 'compress', cmpLabel(imgPaths.length), '', () => { hide(); ConvertTools.openCompress(imgPaths); });
      _item(menu, 'convert', 'Convert Image Format…', '', () => { hide(); ConvertTools.openConvertImage(imgPaths); });
      if (imgPaths.length > 1) {
        // Several images → one multi-page PDF (reorderable) or one PDF each
        _item(menu, 'combine', 'Combine Images into One PDF…', '', () => { hide(); ConvertTools.openImagesToPdf(imgPaths, { mode: 'combine' }); });
        _item(menu, 'file-pdf', 'Convert Each to PDF…', '', () => { hide(); ConvertTools.openImagesToPdf(imgPaths, { mode: 'separate' }); });
      } else {
        _item(menu, 'file-pdf', 'Convert to PDF', '', () => { hide(); ConvertTools.quickImageToPdf(imgPaths[0]); });
        _item(menu, 'file-pdf', 'Convert to PDF (options)…', '', () => { hide(); ConvertTools.openImagesToPdf(imgPaths); });
      }
      _sep(menu);
    } else if (isImg) {
      _sep(menu);
    }

    // Section: ZIP / unzip / ZIP + PDF passwords (archive.js)
    if (typeof Archive !== 'undefined') Archive.menuItems(menu, { entries, paths, item: _item, sep: _sep, hide });

    // Section: copy / move (always visible)
    _item(menu, 'copy', 'Copy To…', '', () => { hide(); Dialogs.openCopyTo(paths); });
    _item(menu, 'folder-input', 'Move To…', '', () => { hide(); Dialogs.openMoveTo(paths); });
    _sep(menu);

    // Section: folder ops (directory only)
    if (isDir) {
      _item(menu, 'chevrons-up-down', 'Expand All', '', () => { hide(); FileTree.expandAll(); });
      _item(menu, 'chevrons-down-up', 'Collapse All', '', () => { hide(); FileTree.collapseAll(); });
      _sep(menu);
    }

    // Section: QR
    // QR from file (image / PDF) — primary path
    const _qrExts = new Set(['.jpg','.jpeg','.jfif','.png','.bmp','.webp','.gif',
                              '.tiff','.tif','.pdf']);
    const _qrPaths = entries.filter(e => !e.is_dir && _qrExts.has((e.ext || '').toLowerCase())).map(e => e.path);
    if (!isDir && _qrPaths.length) {
      _item(menu, 'qr-code', _qrPaths.length > 1 ? `Check QR Codes (${_qrPaths.length} files)` : 'Scan QR from File', '', () => {
        hide();
        QrScan.scanPaths(_qrPaths);
      });
      _item(menu, 'scan', 'Scan QR from Screen', '', () => { hide(); QrScan.pick(); });
    } else {
      // Screen scan — open click-overlay
      _item(menu, 'scan', 'Scan QR from Screen', '', () => { hide(); QrScan.pick(); });
    }

    // Section: copy path
    _sep(menu);
    _item(menu, 'link', 'Copy Path', '', () => {
      hide();
      const text = paths.join('\n');
      SFM.copyText(text).then(ok => App.toast(ok ? (paths.length > 1 ? `${paths.length} paths copied` : 'Path copied') : 'Could not copy to the clipboard', ok ? 'success' : 'error'));
    });
    _item(menu, 'clipboard', 'Copy Name', '', () => {
      hide();
      const names = entries.map(e => e.name).join('\n');
      SFM.copyText(names).then(ok => App.toast(ok ? 'Name copied' : 'Could not copy to the clipboard', ok ? 'success' : 'error'));
    });

    _place(menu, x, y);
  }

  // Position & show
  function _place(menu, x, y) {
    menu.classList.remove('hidden');
    const vw = window.innerWidth, vh = window.innerHeight;
    const mw = Math.max(220, menu.offsetWidth || 0), mh = menu.scrollHeight || 400;
    const left = x + mw > vw ? vw - mw - 4 : x;
    const top  = y + mh > vh ? vh - mh - 4 : y;
    menu.style.left = left + 'px';
    menu.style.top  = top  + 'px';
  }

  // A plain menu of actions (empty-space menu of the file list, toolbar
  // drop-downs): items = [{ icon, label, shortcut?, run, disabled? } | 'sep']
  function showItems(x, y, items, opts) {
    const menu = _ensureMenu();
    menu.innerHTML = '';
    menu.dataset.kind = (opts && opts.kind) || '';
    (items || []).forEach(it => {
      if (it === 'sep') { _sep(menu); return; }
      _item(menu, it.icon, it.label, it.shortcut || '', () => { hide(); it.run && it.run(); }, !!it.disabled);
    });
    _place(menu, x, y);
  }

  function hide() {
    if (_menu) _menu.classList.add('hidden');
  }

  // ── Builders ──────────────────────────────────────────────────────────────
  // icon: an Icons name (js/icons.js), e.g. 'copy', 'scissors', 'file-pdf'
  function _item(menu, icon, label, shortcut, fn, disabled = false) {
    const div = document.createElement('div');
    div.className = 'ctx-item' + (DANGER.has(label) ? ' danger' : '');
    div.setAttribute('role', 'menuitem');
    div.innerHTML = '<span class="ctx-icon">' + (icon ? Icons.svg(icon, 16) : '') + '</span>'
      + '<span class="ctx-label">' + _esc(label) + '</span>'
      + (shortcut ? '<span class="ctx-shortcut">' + shortcut + '</span>' : '');
    if (disabled) div.classList.add('disabled');
    if (!disabled) div.addEventListener('click', fn);
    menu.appendChild(div);
  }

  function _sep(menu) {
    const d = document.createElement('div');
    d.className = 'ctx-sep';
    menu.appendChild(d);
  }

  function _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  return { show, showItems, hide };
})();
