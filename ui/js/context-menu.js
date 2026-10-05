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
    const isOffice = single && ['.docx','.doc','.xlsx','.xls','.pptx','.ppt'].includes((entry?.ext||'').toLowerCase());
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
    _item(menu, '\u{1F4C4}+', 'New Text Document', '', async () => {
      hide();
      const folder = isDir ? mainPath : (FileTree.getCurrentFolder() || mainPath.replace(/[\\\\/][^\\\\/]+$/, ''));
      const name = prompt('File name:', 'New Document.txt');
      if (!name) return;
      const r = await SFM.createTextFile(folder, name);
      if (r.ok) { App.toast('Created: ' + name, 'success'); FileTree.refresh(); }
      else       { App.toast('Failed: ' + r.error, 'error'); }
    });
    if (single && !isDir) {
      _item(menu, '\u{1F4C1}', 'Make Folder for File', '', async () => {
        hide();
        const fname = entry.name.replace(/\.[^.]+$/, '');
        const parent = mainPath.replace(/[\\\\/][^\\\\/]+$/, '');
        const cr = await SFM.createFolder(parent, fname);
        if (!cr.ok) { App.toast('Failed: ' + cr.error, 'error'); return; }
        const mr = await SFM.moveFiles([mainPath], cr.path);
        if (mr.ok) { App.toast('Moved into ' + fname + '/', 'success'); FileTree.refresh(); }
        else        { App.toast('Move failed: ' + mr.error, 'error'); }
      });
    }
    _item(menu, '\u{1F3E0}', 'Move to Root Folder', '', async () => {
      hide();
      const root = FileTree.getCurrentFolder();
      if (!root) { App.toast('No folder open', 'error'); return; }
      if (!confirm('Move ' + paths.length + ' item(s) to root of\n' + root + '?')) return;
      App.setStatus('Moving…', true);
      const r = await SFM.moveToRoot(paths, root);
      App.setStatus('Ready');
      if (r.ok) {
        const n = r.moved?.length || 0;
        const e = r.errors?.length || 0;
        App.toast('Moved ' + n + ' item(s)' + (e ? ' (' + e + ' error(s))' : ''), n ? 'success' : 'error');
        FileTree.refresh();
      } else { App.toast('Failed: ' + r.error, 'error'); }
    });
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
    if (allPdf) {
      if (multi) {
        _item(menu, '\u{1F4CE}', 'Combine PDFs…', '', () => { hide(); Dialogs.openCombinePdf(paths, FileTree.getCurrentFolder()); });
      }
      if (isPdf) {
        _item(menu, '\u{2B0D}', 'Arrange Pages…', '', () => { hide(); Dialogs.openArrangePages(mainPath); });
        _item(menu, '✂️', 'Split PDF', '', async () => {
          hide();
          App.setStatus('Splitting…', true);
          const r = await SFM.splitPdf(mainPath, mainPath.replace(/[\\\/][^\\\/]+$/, ''));
          App.setStatus('Ready');
          if (r.ok) { App.toast('Split into ' + (r.files?.length||'?') + ' pages', 'success'); FileTree.refresh(); }
          else       { App.toast('Split failed: ' + r.error, 'error'); }
        });
        _item(menu, '\u{1F4C4}', 'Extract Pages…',      '', () => { hide(); Dialogs.openExtractPages(mainPath); });
        _item(menu, '\u{1F4C4}\u2192', 'Extract Current Page → New PDF', '', async () => {
          hide();
          const pr = await SFM.getPdfPageCount(mainPath);
          if (!pr.ok) { App.toast('Cannot read PDF', 'error'); return; }
          const page = prompt('Page number to extract (1 - ' + pr.count + '):', '1');
          if (!page) return;
          const pg = parseInt(page) - 1;
          if (isNaN(pg) || pg < 0 || pg >= pr.count) { App.toast('Invalid page number', 'error'); return; }
          const base = mainPath.replace(/(\.[^.]+)$/, '_p' + page + '$1');
          App.setStatus('Extracting page…', true);
          const r = await SFM.extractPdfPages(mainPath, String(page), base);
          App.setStatus('Ready');
          if (r.ok) { App.toast('Extracted page ' + page + ' → ' + base.split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
          else        { App.toast('Extract failed: ' + r.error, 'error'); }
        });
        _item(menu, '\u{1F520}', 'Smart Split & Rename…','', () => {
          hide(); Dialogs.openSmartSplitProgress(mainPath);
        });
        _item(menu, '\u{1F50D}', 'Rename by Doc Type (OCR)\u2026', '', () => { hide(); Dialogs.openOcrRenameProgress([mainPath]); });
        _item(menu, '\u{1F5DC}️', 'Compress PDF…',  '', () => { hide(); Dialogs.openCompressPdf(mainPath); });
        _item(menu, '\u{1F4D0}', 'Place on A4…',         '', () => { hide(); Dialogs.openA4Placer(mainPath); });
        _item(menu, '\u{1FAA7}', 'ID Card on A4…',       '', () => { hide(); Dialogs.openIdCard(mainPath); });
        _item(menu, '\u{1F50E}', 'Find Duplicate Pages',      '', () => { hide(); Dialogs.openDuplicatePages(mainPath); });
        _item(menu, '\u{1F5D2}️', 'Open with Acrobat',  '', () => { hide(); SFM.openWithAcrobat([mainPath]); });
        _item(menu, '\u{1F4F7}', 'Convert to Images…',        '', () => { hide(); Dialogs.openPdfToImages(mainPath); });
        _item(menu, '\u{1F50D}', 'Split & Rename by OCR…',   '', () => { hide(); Dialogs.openSplitRenameOcrProgress(mainPath); });
        _item(menu, '\u{1F4C4}', 'Make OCR Searchable',       '', async () => {
          hide(); App.setStatus('Running OCR…', true);
          const r = await SFM.makeOcrSearchable(mainPath);
          App.setStatus('Ready');
          if (r.ok) { App.toast('OCR searchable saved', 'success'); FileTree.refresh(); }
          else       { App.toast('OCR failed: ' + r.error, 'error'); }
        });
        _sep(menu);
      }
    }

    // Section: image ops
    if (isImg) {
      _item(menu, '\u{1F5A8}️', 'Print…',          '', () => { hide(); Dialogs.openPrint(mainPath); });
      _item(menu, '\u{1F4D0}', 'Resize to Photo Size\u2026', '', () => { hide(); Dialogs.openPhotoSizer(mainPath); });
      _item(menu, '✂️', 'Crop Image…', '', () => { hide(); Dialogs.openCropImage(mainPath); });
      _item(menu, '\u{1F454}', 'Wear Suit & Tie (AI)', '', () => { hide(); Details.runAiPhoto(mainPath, 'wear_suit'); });
      _item(menu, '\u{1F504}', 'Rotate CW 90°', '', async () => {
        hide(); App.setStatus('Rotating…', true);
        const r = await SFM.rotateImage(mainPath, 90);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Rotated CW → ' + r.out.split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
        else App.toast('Rotate failed: ' + r.error, 'error');
      });
      _item(menu, '\u{1F503}', 'Rotate CCW 90°', '', async () => {
        hide(); App.setStatus('Rotating…', true);
        const r = await SFM.rotateImage(mainPath, -90);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Rotated CCW → ' + r.out.split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
        else App.toast('Rotate failed: ' + r.error, 'error');
      });
      _item(menu, '\u{2194}\uFE0F', 'Flip Horizontal', '', async () => {
        hide(); App.setStatus('Flipping…', true);
        const r = await SFM.flipImage(mainPath, 'horizontal');
        App.setStatus('Ready');
        if (r.ok) { App.toast('Flipped → ' + r.out.split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
        else App.toast('Flip failed: ' + r.error, 'error');
      });
      _item(menu, '\u{2195}\uFE0F', 'Flip Vertical', '', async () => {
        hide(); App.setStatus('Flipping…', true);
        const r = await SFM.flipImage(mainPath, 'vertical');
        App.setStatus('Ready');
        if (r.ok) { App.toast('Flipped → ' + r.out.split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
        else App.toast('Flip failed: ' + r.error, 'error');
      });
      _item(menu, '\u{1F3A8}', 'Brightness / Contrast\u2026', '', () => { hide(); Dialogs.openImageAdjust(mainPath); });
      _item(menu, '\u{1FAA7}',        'Check Passport Photo', '', () => { hide(); Dialogs.openPassportCheck(mainPath); });
      _sep(menu);
    }

    // Section: Office
    if (isOffice) {
      _item(menu, '\u{1F4C4}', 'Convert to PDF', '', async () => {
        hide(); App.setStatus('Converting…', true);
        const r = await SFM.convertToPdf(mainPath);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Converted: ' + r.out, 'success'); FileTree.refresh(); }
        else       { App.toast('Failed: ' + r.error, 'error'); }
      });
      if ((entry?.ext||'').toLowerCase().match(/\.xlsx?$/)) {
        _item(menu, '\u{1F4CA}', 'Export Sheets as CSV', '', async () => {
          hide(); App.setStatus('Exporting CSV\u2026', true);
          const r = await SFM.excelToCsv(mainPath);
          App.setStatus('Ready');
          if (r.ok) { App.toast('Exported ' + (r.files?.length||'?') + ' CSV file(s)', 'success'); FileTree.refresh(); }
          else       { App.toast('Failed: ' + r.error, 'error'); }
        });
      }
      _sep(menu);
    }

    // Section: multi-selection extras / copy+move always visible
    if (multi) {
      _item(menu, '\u{1F500}', 'Similar Move…', '', () => { hide(); Dialogs.openSimilarMove(paths); });
    }
    _item(menu, '\u{1F4CB}', 'Copy To…', '', () => { hide(); Dialogs.openCopyTo(paths); });
    _item(menu, '\u{2702}\uFE0F', 'Move To…', '', () => { hide(); Dialogs.openMoveTo(paths); });
    _sep(menu);

    // Section: ZIP
    _item(menu, '\u{1F4E6}', 'Zip…', 'Ctrl+Shift+Z', () => { hide(); FileTree.zipSelection(); });
    if (single && ['.zip'].includes((entry?.ext||'').toLowerCase())) {
      _item(menu, '\u{1F4C2}', 'Extract Here', '', () => { hide(); FileTree.unzipSelection(); });
      _item(menu, '\u{1F4CB}', 'View Contents…', '', () => { hide(); Dialogs.openZipContents(mainPath); });
    }
    if (entries.length > 1 && entries.every(e => (e.ext||'').toLowerCase() === '.zip')) {
      _item(menu, '\u{1F4C2}', 'Extract All…', '', () => { hide(); FileTree.unzipSelection(); });
    }
    _sep(menu);

    // Section: folder ops (directory only)
    if (isDir) {
      _item(menu, '\u{1F4CA}', 'Generate Report', '', () => { hide(); SFM.generateReport(mainPath); App.toast('Report queued', 'info'); });
      _item(menu, '\u{1F441}️', 'Watch Folder', '', () => { hide(); SFM.watchStart(mainPath, ''); App.toast('Watching ' + entry.name, 'info'); });
      _item(menu, '\u{2702}\uFE0F', 'Split PDFs in Folder\u2026', '', () => { hide(); Dialogs.openBulkSplitPdfs(mainPath); });
      _item(menu, '\u{1F4E6}', 'Zip Each Subfolder', '', () => { hide(); FileTree.zipEachSubfolder(); });
      _item(menu, '\u{1F4C2}', 'Expand All', '', () => { hide(); FileTree.expandAll(); });
      _item(menu, '\u{1F4C1}', 'Collapse All', '', () => { hide(); FileTree.collapseAll(); });
      _sep(menu);
    }

    // Section: apostille
    _item(menu, '\u{1F3DB}️', 'Apostille Merge…', '', () => {
      hide();
      const folder = isDir ? mainPath : mainPath.replace(/[\\\/][^\\\/]+$/, '');
      Dialogs.openApostille(folder);
    });

    // Section: translate / QR
    _sep(menu);
    _item(menu, '\u{1F310}', 'Translate to Korean…', '', async () => {
      hide();
      const name = entry?.name || paths[0].split(/[\\\/]/).pop();
      const r    = await SFM.translateKorean(name.replace(/\.[^.]+$/, ''));
      if (r.ok) App.toast('Korean: ' + r.text, 'info', 6000);
      else       App.toast('Translation failed', 'error');
    });
    // QR from file (image / PDF) — primary path
    const _qrExts = new Set(['.jpg','.jpeg','.png','.bmp','.webp','.gif',
                              '.tiff','.tif','.pdf']);
    const _qrExt  = (entry?.ext || '').toLowerCase();
    if (!isDir && _qrExts.has(_qrExt)) {
      _item(menu, '\u{1F4F7}', 'Scan QR from File', '', async () => {
        hide();
        App.setStatus('Scanning QR…', true);
        App.toast('Scanning for QR code…', 'info', 2000);
        SFM.scanQrFromFile(mainPath);
      });
    } else {
      // Screen scan — open click-overlay
      _item(menu, '\u{1F4F7}', 'Scan QR from Screen', '', () => {
        hide();
        Dialogs.openQrOverlay();
      });
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
