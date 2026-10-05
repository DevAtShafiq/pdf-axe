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

    const single   = entries.length === 1;
    const multi    = entries.length > 1;
    const entry    = single ? entries[0] : null;
    const isPdf    = single && (entry?.ext || '').toLowerCase() === '.pdf';
    const isImg    = single && ['.jpg','.jpeg','.png','.bmp','.webp','.gif'].includes((entry?.ext||'').toLowerCase());
    const isDir    = single && entry?.is_dir;
    const paths    = selectedPaths || entries.map(e => e.path);
    const mainPath = entry?.path || paths[0];

    // Section: open
    _item(menu, '\u{1F5C2}️', 'Open',           'Ctrl+Enter', () => { hide(); if(entry) SFM.openNative(entry.path); });
    _item(menu, '\u{1F4C2}', 'Show in Explorer', '',              () => { hide(); if(entry) SFM.openFolder(entry.path.replace(/[\\\/][^\\\/]+$/, '')); });
    if (single && entry && !isDir) {
      _item(menu, '\u{1F4E4}', 'Open with\u2026', '', async () => {
        hide();
        App.setStatus('Getting apps\u2026', true);
        const r = await SFM.getOpenWithCommands(mainPath);
        App.setStatus('Ready');
        if (!r.ok || !r.commands?.length) { App.toast('No apps found for this file type', 'info'); return; }
        // Mini floating picker
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;background:#0004;';
        const listHtml = r.commands.map((c, i) =>
          '<button class="btn" style="width:100%;text-align:left;margin:2px 0;padding:7px 12px" data-idx="' + i + '">' + _esc(c[0]) + '</button>'
        ).join('');
        overlay.innerHTML = '<div style="background:var(--bg-surface);border:1px solid var(--border);border-radius:10px;padding:16px;min-width:260px;max-width:420px;max-height:80vh;overflow-y:auto;box-shadow:0 8px 40px #0008"><div style="font-weight:600;margin-bottom:10px;font-size:14px">Open with\u2026</div>' + listHtml + '<button class="btn" id="ow-cancel" style="width:100%;margin-top:10px">Cancel</button></div>';
        document.body.appendChild(overlay);
        overlay.querySelectorAll('[data-idx]').forEach(btn => {
          btn.addEventListener('click', async () => {
            overlay.remove();
            const cmd = r.commands[parseInt(btn.dataset.idx)];
            await SFM.runOpenWith(cmd[1], mainPath);
          });
        });
        overlay.querySelector('#ow-cancel').addEventListener('click', () => overlay.remove());
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
      });
    }
    _sep(menu);

    // Section: edit
    _item(menu, '✂️', 'Cut',  'Ctrl+X', () => { hide(); FileTree.cutSelection(); });
    _item(menu, '\u{1F4CB}', 'Copy', 'Ctrl+C', () => { hide(); FileTree.copySelection(); });
    _item(menu, '\u{1F4CC}', 'Paste','Ctrl+V', () => { hide(); FileTree.pasteSelection(); });
    _sep(menu);

    // Section: file ops
    _item(menu, '✏️', 'Rename',         'F2',  () => { hide(); setTimeout(() => Details.beginRename(), 100); });
    _item(menu, '\u{1F5D1}️', 'Move to Review', 'Del', async () => {
      hide();
      if (!confirm('Move ' + paths.length + ' item(s) to _to_review/?')) return;
      const r = await SFM.softDelete(paths);
      if (r.ok) { App.toast('Moved to _to_review/', 'success'); FileTree.refresh(); }
      else       { App.toast('Failed: ' + r.error, 'error'); }
    });
    _sep(menu);

    // Section: PDF ops
    const allPdf = entries.length > 0 && entries.every(e => (e.ext||'').toLowerCase() === '.pdf');
    // Mixed PDFs + images → merge (images become pages).
    const mixedPdfImg = !allPdf && entries.length > 1
      && entries.every(e => /^\.(pdf|jpe?g|png|bmp|gif|tiff?|webp)$/i.test(e.ext || ''))
      && entries.some(e => (e.ext||'').toLowerCase() === '.pdf');
    if (mixedPdfImg) {
      _item(menu, '\u{1F4CE}', 'Merge into One PDF…', '', () => { hide(); PdfTools.openMerge(paths, FileTree.getCurrentFolder()); });
      _sep(menu);
    }
    if (allPdf) {
      if (multi) {
        _item(menu, '\u{1F4CE}', 'Merge PDFs…', '', () => { hide(); PdfTools.openMerge(paths, FileTree.getCurrentFolder()); });
        _item(menu, '\u{1F5DC}️', 'Compress ' + entries.length + ' PDFs…', '', () => { hide(); Dialogs.openCompressPdf(entries.map(e => e.path)); });
        _sep(menu);
      }
      if (isPdf) {
        _item(menu, '\u{2B0D}', 'Arrange Pages…', '', () => { hide(); PdfTools.openArrange([mainPath]); });
        _item(menu, '✂️', 'Split PDF…', '', () => { hide(); PdfTools.openSplit(mainPath); });
        _item(menu, '\u{1F4C4}', 'Extract Pages…', '', () => { hide(); PdfTools.openExtract(mainPath); });
        // "Current page" = the page shown in the preview for this file.
        const curPage = PdfTools.previewPageFor(mainPath);
        if (curPage !== null) {
          _item(menu, '\u{1F4C4}→', 'Extract Current Page (' + (curPage + 1) + ') → New PDF', '', () => {
            hide(); PdfTools.extractPage(mainPath, curPage);
          });
        }
        _item(menu, '\u{1F520}', 'Smart Split & Rename…','', () => {
          hide(); Dialogs.openSmartSplitProgress(mainPath);
        });
        _item(menu, '\u{1F50D}', 'Rename by Doc Type (OCR)\u2026', '', () => { hide(); Dialogs.openOcrRenameProgress([mainPath]); });
        _item(menu, '\u{1F5DC}️', 'Compress PDF…',  '', () => { hide(); Dialogs.openCompressPdf(mainPath); });
        _item(menu, '\u{1F4F7}', 'Convert to Images…',        '', () => { hide(); Dialogs.openPdfToImages(mainPath); });
        _item(menu, '\u{1F50D}', 'Split & Rename by OCR…',   '', () => { hide(); Dialogs.openSplitRenameOcrProgress(mainPath); });
        _sep(menu);
      }
    }

    // Section: image ops
    if (isImg) {
      _item(menu, '✂️', 'Crop Image…', '', () => { hide(); Dialogs.openCropImage(mainPath); });
      _item(menu, '\u{1F454}', 'Wear Suit & Tie (AI)', '', () => { hide(); Details.runAiPhoto(mainPath, 'wear_suit'); });
    }

    // Section: image compress / convert (single or multi-select)
    const IMG_EXTS = ConvertTools.IMAGE_EXTS;
    const allImg = entries.length > 0 && entries.every(e => !e.is_dir && IMG_EXTS.includes((e.ext||'').toLowerCase()));
    if (allImg) {
      const imgPaths = entries.map(e => e.path);
      _item(menu, '\u{1F5DC}️', imgPaths.length > 1 ? 'Compress Images…' : 'Compress Image…', '',
        () => { hide(); Dialogs.openCompressImages(imgPaths); });
      _item(menu, '\u{1F501}', 'Convert Image Format…', '', () => { hide(); ConvertTools.openConvertImage(imgPaths); });
      if (imgPaths.length > 1) {
        // Several images → one multi-page PDF (reorderable) or one PDF each
        _item(menu, '\u{1F4CE}', 'Combine Images into One PDF…', '', () => { hide(); ConvertTools.openImagesToPdf(imgPaths, { mode: 'combine' }); });
        _item(menu, '\u{1F4C4}', 'Convert Each to PDF…', '', () => { hide(); ConvertTools.openImagesToPdf(imgPaths, { mode: 'separate' }); });
      } else {
        _item(menu, '\u{1F4C4}', 'Convert to PDF', '', () => { hide(); ConvertTools.quickImageToPdf(imgPaths[0]); });
        _item(menu, '\u{1F4C4}', 'Convert to PDF (options)…', '', () => { hide(); ConvertTools.openImagesToPdf(imgPaths); });
      }
      _sep(menu);
    } else if (isImg) {
      _sep(menu);
    }

    // Section: copy / move (always visible)
    _item(menu, '\u{1F4CB}', 'Copy To…', '', () => { hide(); Dialogs.openCopyTo(paths); });
    _item(menu, '\u{2702}\uFE0F', 'Move To…', '', () => { hide(); Dialogs.openMoveTo(paths); });
    _sep(menu);

    // Section: folder ops (directory only)
    if (isDir) {
      _item(menu, '\u{1F4C2}', 'Expand All', '', () => { hide(); FileTree.expandAll(); });
      _item(menu, '\u{1F4C1}', 'Collapse All', '', () => { hide(); FileTree.collapseAll(); });
      _sep(menu);
    }

    // Section: QR
    // QR from file (image / PDF) — primary path
    const _qrExts = new Set(['.jpg','.jpeg','.jfif','.png','.bmp','.webp','.gif',
                              '.tiff','.tif','.pdf']);
    const _qrPaths = entries.filter(e => !e.is_dir && _qrExts.has((e.ext || '').toLowerCase())).map(e => e.path);
    if (!isDir && _qrPaths.length) {
      _item(menu, '\u{1F4F7}', _qrPaths.length > 1 ? `Check QR Codes (${_qrPaths.length} files)` : 'Scan QR from File', '', () => {
        hide();
        QrScan.scanPaths(_qrPaths);
      });
      _item(menu, '\u{1F4F7}', 'Scan QR from Screen', '', () => { hide(); QrScan.openScreen(); });
    } else {
      // Screen scan — open click-overlay
      _item(menu, '\u{1F4F7}', 'Scan QR from Screen', '', () => { hide(); QrScan.openScreen(); });
    }

    // Section: copy path
    _sep(menu);
    _item(menu, '\u{1F517}', 'Copy Path', '', () => {
      hide();
      const text = paths.join('\n');
      navigator.clipboard.writeText(text).catch(() => SFM.setClipboard(text));
      App.toast('Path copied', 'success');
    });
    _item(menu, '\u{1F4CB}', 'Copy Name', '', () => {
      hide();
      const names = entries.map(e => e.name).join('\n');
      navigator.clipboard.writeText(names).catch(() => SFM.setClipboard(names));
      App.toast('Name copied', 'success');
    });

    // Position & show
    menu.classList.remove('hidden');
    const vw = window.innerWidth, vh = window.innerHeight;
    const mw = 220, mh = menu.scrollHeight || 400;
    const left = x + mw > vw ? vw - mw - 4 : x;
    const top  = y + mh > vh ? vh - mh - 4 : y;
    menu.style.left = left + 'px';
    menu.style.top  = top  + 'px';
  }

  function hide() {
    if (_menu) _menu.classList.add('hidden');
  }

  // ── Builders ──────────────────────────────────────────────────────────────
  function _item(menu, icon, label, shortcut, fn, disabled = false) {
    const div = document.createElement('div');
    div.className = 'ctx-item';
    div.innerHTML = '<span class="ctx-icon">' + icon + '</span>'
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

  return { show, hide };
})();
