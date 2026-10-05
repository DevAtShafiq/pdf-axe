/**
 * dialogs.js — Modal dialogs
 *
 * Stacked modal system — each open() creates its own overlay div.
 * Escape / close only pops the TOP modal.
 *
 * Dialogs: CombinePdf/ArrangePages/ExtractPages (→ pdf-tools.js), PdfFullView, Templates, Uppercase,
 *          CompressPdf, Settings, ExtractPages, MoreMenu, SmartSplitProgress,
 *          CopyTo, MoveTo, PdfToImages, SplitRenameOcrProgress, CropImage,
 *          CompressImages, OcrRenameProgress, QrOverlay
 */

const Dialogs = (() => {

  const _stack = [];
  const _Z_BASE = 9100;

  // ── Core modal open / close ───────────────────────────────────────────────
  function _openModal(id, title, bodyHtml, buttons) {
    const z = _Z_BASE + _stack.length * 50;
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = z;
    document.body.appendChild(overlay);

    const btnHtml = (buttons || []).map(b =>
      `<button class="btn ${b.primary ? 'btn-primary' : ''}" data-modal-btn="${b.label}">${_esc(b.label)}</button>`
    ).join('');

    overlay.innerHTML = `
      <div class="modal" id="modal-${id}" role="dialog" aria-labelledby="modal-title-${id}">
        <div class="modal-header">
          <h2 class="modal-title" id="modal-title-${id}">${_esc(title)}</h2>
          <button class="modal-close" aria-label="Close">&#x2715;</button>
        </div>
        <div class="modal-body" id="modal-body-${id}">${bodyHtml}</div>
        ${btnHtml ? `<div class="modal-footer">${btnHtml}</div>` : ''}
      </div>`;

    function close() {
      const idx = _stack.findIndex(s => s.overlay === overlay);
      if (idx !== -1) {
        document.removeEventListener('keydown', _stack[idx].onKeyDown);
        _stack.splice(idx, 1);
      }
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }

    overlay.querySelector('.modal-close').addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

    if (buttons) {
      buttons.forEach(b => {
        const el = overlay.querySelector(`[data-modal-btn="${b.label}"]`);
        if (el) el.addEventListener('click', () => { b.onClick && b.onClick(); });
      });
    }

    const onKeyDown = e => {
      if (e.key === 'Escape' && _stack[_stack.length - 1]?.overlay === overlay) close();
    };
    document.addEventListener('keydown', onKeyDown);
    _stack.push({ overlay, onKeyDown });
    overlay._close = close;
    return overlay;
  }

  // closeModal()    → close the top-most modal
  // closeModal(id)  → close the overlay #mo-<id> (dialogs built with _modal /
  //                   _attachClose, e.g. Crop Image and the QR overlay)
  function closeModal(id) {
    if (typeof id === 'string' && id) {
      const ov = document.getElementById('mo-' + id);
      if (ov && ov._close) { ov._close(); return; }
    }
    if (_stack.length) _stack[_stack.length - 1].overlay._close();
  }

  function closeAllModals() {
    while (_stack.length) closeModal();
  }

  // ── Helpers for dialogs that build their own overlay markup ──────────────
  // openCropImage / openQrOverlay render <div class="modal-overlay" id="mo-<id>">
  // themselves; these register it on the same stack as _openModal so Escape and
  // closeModal() behave the same. (No backdrop-click close: a crop drag that
  // ends outside the image must not dismiss the dialog.)
  let _uid = 0;
  function _nextId() { return 'dlg' + (++_uid); }
  function _nextZ()  { return _Z_BASE + _stack.length * 50; }

  function _attachClose(id) {
    const overlay = document.getElementById('mo-' + id);
    if (!overlay) return;
    function close() {
      const idx = _stack.findIndex(s => s.overlay === overlay);
      if (idx !== -1) {
        document.removeEventListener('keydown', _stack[idx].onKeyDown);
        _stack.splice(idx, 1);
      }
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }
    const onKeyDown = e => {
      if (e.key === 'Escape' && _stack[_stack.length - 1]?.overlay === overlay) close();
    };
    document.addEventListener('keydown', onKeyDown);
    _stack.push({ overlay, onKeyDown });
    overlay._close = close;
  }

  // _modal(name, {title, width, extraStyle, body, footer}) → id
  function _modal(name, opts) {
    const o  = opts || {};
    const id = _nextId();
    const html = `
<div class="modal-overlay" id="mo-${id}" style="z-index:${_nextZ()}">
<div class="modal" id="modal-${_esc(name)}" style="width:${o.width || '560px'};max-width:98vw;${o.extraStyle || ''}">
  <div class="modal-header">
    <h2 class="modal-title">${_esc(o.title || '')}</h2>
    <button class="modal-close" aria-label="Close">&#x2715;</button>
  </div>
  <div class="modal-body">${o.body || ''}</div>
  ${o.footer ? `<div class="modal-footer">${o.footer}</div>` : ''}
</div></div>`;
    document.body.insertAdjacentHTML('beforeend', html);
    _attachClose(id);
    document.querySelector('#mo-' + id + ' .modal-close')
      ?.addEventListener('click', () => closeModal(id));
    return id;
  }

  // ── 1. Merge PDFs — see js/pdf-tools.js (PdfTools.openMerge) ─────────────
  function openCombinePdf(paths, folderPath) {
    return PdfTools.openMerge(paths, folderPath);
  }

  // ── 2. PDF Full View ──────────────────────────────────────────────────────
  async function openFullView(path, ext, startPage = 0) {
    const name  = path.split(/[\\/]/).pop();
    const isPdf = (ext || '').toLowerCase() === '.pdf';
    const body  = `
      <div id="fullview-wrap" style="width:100%;flex:1 1 auto;min-height:0;overflow:auto;background:#111;display:flex;padding:16px;box-sizing:border-box">
        <div id="fullview-inner" style="margin:auto;display:flex;flex-direction:column;gap:12px;align-items:center"></div>
      </div>
      ${isPdf ? `
      <div style="display:flex;align-items:center;justify-content:center;gap:12px;margin-top:8px;flex-wrap:wrap;flex-shrink:0">
        <button class="btn" id="fv-prev">&#9664;</button>
        <span style="font-size:13px">Page <input id="fv-page-input" type="number" min="1" value="${startPage+1}" style="width:50px;text-align:center;background:var(--bg-app);color:var(--text-primary);border:1px solid var(--border);border-radius:4px;padding:2px 4px"> / <span id="fv-total">?</span></span>
        <button class="btn" id="fv-next">&#9654;</button>
        <div style="width:1px;height:20px;background:var(--border);margin:0 4px"></div>
        <button class="btn" id="fv-zoom-out" title="Zoom out (−)">&#8722;</button>
        <select id="fv-zoom" class="btn" title="Ctrl + scroll to zoom" style="padding:4px 8px"><option value="fit" selected>Fit Page</option><option value="0.5">50%</option><option value="0.75">75%</option><option value="1">100%</option><option value="1.5">150%</option><option value="2">200%</option><option value="3">300%</option><option value="4">400%</option></select>
        <button class="btn" id="fv-zoom-in" title="Zoom in (+)">&#43;</button>
        <div style="width:1px;height:20px;background:var(--border);margin:0 4px"></div>
        <button class="btn" id="fv-rot-ccw" title="Rotate counter-clockwise">&#8634;</button>
        <button class="btn" id="fv-rot-cw"  title="Rotate clockwise">&#8635;</button>
        <button class="btn" id="fv-del-page" title="Delete this page" style="color:var(--text-danger)">&#128465; Del Page</button>
        <div style="width:1px;height:20px;background:var(--border);margin:0 4px"></div>
        <button class="btn" id="fv-append" title="Append another PDF to this one">&#128196;+ Append</button>
        <button class="btn" id="fv-merge" title="Merge with other PDFs">&#128206; Merge</button>
        <button class="btn" id="fv-arrange" title="Arrange / reorder pages">&#8645; Arrange</button>
      </div>` : ''}`;

    _openModal('fullview', name, body, [{ label: 'Close', onClick: closeModal }]);
    // Old system's full view was a maximized window — full-height + draggable.
    const _fvModal = document.getElementById('modal-fullview');
    if (_fvModal) {
      _fvModal.style.width     = 'calc(100vw - 28px)';
      _fvModal.style.maxWidth  = 'calc(100vw - 28px)';
      _fvModal.style.height     = 'calc(100vh - 20px)';
      _fvModal.style.maxHeight  = 'calc(100vh - 20px)';
      const _fvBody = document.getElementById('modal-body-fullview');
      if (_fvBody) _fvBody.style.overflow = 'hidden';   // wrap scrolls, not the body

      // Draggable by the title bar (old system had a moveable window).
      const _hdr = _fvModal.querySelector('.modal-header');
      if (_hdr) {
        _hdr.style.cursor = 'move';
        _hdr.style.userSelect = 'none';
        let _sx = 0, _sy = 0, _ox = 0, _oy = 0, _drag = false;
        const _onDrag = e => {
          if (!_drag) return;
          const w = _fvModal.offsetWidth;
          let nx = _ox + (e.clientX - _sx);
          let ny = _oy + (e.clientY - _sy);
          nx = Math.max(-(w - 100), Math.min(window.innerWidth - 100, nx));
          ny = Math.max(0, Math.min(window.innerHeight - 40, ny));
          _fvModal.style.left = nx + 'px';
          _fvModal.style.top  = ny + 'px';
        };
        const _endDrag = () => {
          _drag = false;
          document.body.style.userSelect = '';
          window.removeEventListener('mousemove', _onDrag);
          window.removeEventListener('mouseup', _endDrag);
        };
        _hdr.addEventListener('mousedown', e => {
          if (e.target.closest('.modal-close')) return;   // let the X button work
          const rect = _fvModal.getBoundingClientRect();
          _fvModal.style.position = 'fixed';   // break out of the centering flex
          _fvModal.style.margin   = '0';
          _fvModal.style.left = rect.left + 'px';
          _fvModal.style.top  = rect.top + 'px';
          _ox = rect.left; _oy = rect.top;
          _sx = e.clientX; _sy = e.clientY;
          _drag = true;
          document.body.style.userSelect = 'none';
          window.addEventListener('mousemove', _onDrag);
          window.addEventListener('mouseup', _endDrag);
          e.preventDefault();
        });
      }
    }

    let page = startPage, total = 1, zoom = 'fit';
    const inner = document.getElementById('fullview-inner');
    const wrap  = document.getElementById('fullview-wrap');

    // Scale a page/image to fit the available viewport (whole A4 visible).
    function _fitDims(natW, natH) {
      const availH = Math.max(120, wrap.clientHeight - 32);
      const availW = Math.max(120, wrap.clientWidth  - 32);
      const ar = natH / natW;
      let w = availW, h = w * ar;
      if (h > availH) { h = availH; w = h / ar; }
      return { w: Math.round(w), h: Math.round(h) };
    }

    // Re-fit on window resize while the modal is open (self-cleans when closed).
    const _onResize = () => {
      if (!document.body.contains(_fvModal)) { window.removeEventListener('resize', _onResize); return; }
      if (zoom === 'fit') renderPage(page);
    };
    window.addEventListener('resize', _onResize);

    let _baseW = 0, _baseH = 0;          // page size at 150 DPI — the zoom-scale reference

    async function renderPage(p, after) {
      inner.innerHTML = '<div class="loading-spinner"></div>';
      if (isPdf) {
        const fit  = zoom === 'fit';
        const mult = fit ? 1.5 : zoom;                    // render DPI multiplier
        const r = await SFM.getPdfPage(path, p, Math.round(150 * mult));
        if (!r.ok) { inner.textContent = r.error; return; }
        _baseW = r.width / mult; _baseH = r.height / mult;
        let dw, dh;
        if (fit) {
          const d = _fitDims(r.width, r.height);          // whole page fits, no scroll
          dw = d.w; dh = d.h;
        } else {
          dw = Math.round(r.width); dh = Math.round(r.height);   // 1:1 at the rendered DPI (crisp)
        }
        inner.innerHTML = `<img src="${r.data_url}" style="width:${dw}px;height:${dh}px;border-radius:2px;box-shadow:0 4px 24px #0008">`;
      } else {
        const r = await SFM.getImagePreview(path, 1600);
        if (!r.ok) { inner.textContent = r.error || 'Preview unavailable'; return; }
        let dim = 'max-width:100%;max-height:100%;';
        if (r.width && r.height) { const d = _fitDims(r.width, r.height); dim = `width:${d.w}px;height:${d.h}px;`; }
        inner.innerHTML = `<img src="${r.data_url}" style="${dim}border-radius:4px">`;
      }
      requestAnimationFrame(() => {
        // Center the page when it fits; anchor top-left so it can scroll when zoomed in.
        const ov = inner.scrollWidth > wrap.clientWidth + 1 || inner.scrollHeight > wrap.clientHeight + 1;
        inner.style.margin = ov ? '0' : 'auto';
        if (after) after();
      });
    }

    if (isPdf) {
      const totalEl   = document.getElementById('fv-total');
      const pageInput = document.getElementById('fv-page-input');
      const rc = await SFM.getPdfPageCount(path);
      total = rc.ok ? (rc.page_count || rc.count || 1) : 1;
      if (totalEl) totalEl.textContent = total;

      document.getElementById('fv-prev').addEventListener('click', () => { if(page>0){page--;if(pageInput)pageInput.value=page+1;renderPage(page);} });
      document.getElementById('fv-next').addEventListener('click', () => { if(page<total-1){page++;if(pageInput)pageInput.value=page+1;renderPage(page);} });
      if (pageInput) pageInput.addEventListener('change', () => { page=Math.max(0,Math.min(total-1,+pageInput.value-1));renderPage(page); });
      // ── Zooming system: dropdown + buttons + Ctrl-wheel (to cursor) + keys ──
      const _zoomSel = document.getElementById('fv-zoom');
      function _syncZoomSelect() {
        if (!_zoomSel) return;
        let custom = _zoomSel.querySelector('option[data-custom]');
        if (zoom === 'fit') { if (custom) custom.remove(); _zoomSel.value = 'fit'; return; }
        const zs = String(zoom);
        const isPreset = Array.from(_zoomSel.options).some(o => !o.dataset.custom && o.value === zs);
        if (isPreset) { if (custom) custom.remove(); _zoomSel.value = zs; return; }
        if (!custom) { custom = document.createElement('option'); custom.dataset.custom = '1'; _zoomSel.appendChild(custom); }
        custom.value = zs; custom.textContent = Math.round(zoom * 100) + '%';
        _zoomSel.value = zs;
      }
      function _curScale() {
        const img = inner.querySelector('img');
        return (img && _baseW) ? img.offsetWidth / _baseW : (zoom === 'fit' ? 1 : zoom);
      }
      function _applyZoom(nz, clientX, clientY) {
        zoom = Math.min(4, Math.max(0.25, Math.round(nz * 1000) / 1000));
        const img  = inner.querySelector('img');
        const rect = wrap.getBoundingClientRect();
        // remember the content point under the cursor (or viewport centre) to keep it fixed
        let fx = 0.5, fy = 0.5, cvx = wrap.clientWidth / 2, cvy = wrap.clientHeight / 2, ox = 0, oy = 0;
        if (img) {
          const ir = img.getBoundingClientRect();
          if (clientX != null) {
            fx = Math.min(1, Math.max(0, (clientX - ir.left) / ir.width));
            fy = Math.min(1, Math.max(0, (clientY - ir.top)  / ir.height));
            cvx = clientX - rect.left; cvy = clientY - rect.top;
          }
          ox = (ir.left - rect.left) + wrap.scrollLeft;
          oy = (ir.top  - rect.top)  + wrap.scrollTop;
        }
        _syncZoomSelect();
        renderPage(page, () => {
          const im = inner.querySelector('img'); if (!im) return;
          wrap.scrollLeft = ox + fx * im.offsetWidth  - cvx;
          wrap.scrollTop  = oy + fy * im.offsetHeight - cvy;
        });
      }
      if (_zoomSel) _zoomSel.addEventListener('change', e => {
        if (e.target.value === 'fit') { zoom = 'fit'; _syncZoomSelect(); renderPage(page); }
        else _applyZoom(+e.target.value);
      });
      const _zi = document.getElementById('fv-zoom-in');
      const _zo = document.getElementById('fv-zoom-out');
      if (_zi) _zi.addEventListener('click', () => _applyZoom(_curScale() * 1.2));
      if (_zo) _zo.addEventListener('click', () => _applyZoom(_curScale() / 1.2));
      // Ctrl + scroll zooms toward the pointer; plain scroll still pans.
      wrap.addEventListener('wheel', e => {
        if (!e.ctrlKey) return;
        e.preventDefault();
        _applyZoom(_curScale() * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
      }, { passive: false });
      // Keyboard: +/- to zoom, 0 to reset to Fit (self-cleans when the modal closes).
      const _onZoomKey = e => {
        if (!document.body.contains(_fvModal)) { document.removeEventListener('keydown', _onZoomKey); return; }
        const tag = (document.activeElement || {}).tagName;
        if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
        if (e.key === '+' || e.key === '=') { e.preventDefault(); _applyZoom(_curScale() * 1.2); }
        else if (e.key === '-' || e.key === '_') { e.preventDefault(); _applyZoom(_curScale() / 1.2); }
        else if (e.key === '0') { e.preventDefault(); zoom = 'fit'; _syncZoomSelect(); renderPage(page); }
      };
      document.addEventListener('keydown', _onZoomKey);
      document.getElementById('fv-rot-ccw').addEventListener('click', async () => {
        const r = await SFM.rotatePage(path, page, -90);
        if (r.ok) { renderPage(page); App.toast('Rotated CCW', 'success'); }
        else App.toast('Rotate failed: ' + r.error, 'error');
      });
      document.getElementById('fv-rot-cw').addEventListener('click', async () => {
        const r = await SFM.rotatePage(path, page, 90);
        if (r.ok) { renderPage(page); App.toast('Rotated CW', 'success'); }
        else App.toast('Rotate failed: ' + r.error, 'error');
      });
      document.getElementById('fv-del-page').addEventListener('click', async () => {
        if (total <= 1) { App.toast('Cannot delete the only page', 'error'); return; }
        if (!confirm('Delete page ' + (page + 1) + ' of ' + total + '?')) return;
        const r = await SFM.deletePage(path, page);
        if (r.ok) {
          total--;
          page = Math.min(page, total - 1);
          if (totalEl) totalEl.textContent = total;
          if (pageInput) pageInput.value = page + 1;
          renderPage(page);
          App.toast('Page deleted', 'success');
          FileTree.refresh();
        } else {
          App.toast('Delete page failed: ' + r.error, 'error');
        }
      });
      // Append / Merge → merge dialog (file picker opens via “Add files…”).
      document.getElementById('fv-append').addEventListener('click',  () => PdfTools.openMerge([path], path.replace(/[\\/][^\\/]+$/, '')));
      document.getElementById('fv-merge').addEventListener('click',   () => openCombinePdf([path], path.replace(/[\\/][^\\/]+$/, '')));
      document.getElementById('fv-arrange').addEventListener('click', () => openArrangePages(path, total));
    }

    renderPage(page);
  }

  // ── 3. Arrange PDF pages — see js/pdf-tools.js (PdfTools.openArrange) ──
  function openArrangePages(path /*, pageCount */) {
    return PdfTools.openArrange([path], { mode: 'arrange' });
  }

  // ── 8. Document Name Templates ────────────────────────────────────────────
  // Full manager lives in rename-templates.js (language, list, search,
  // Add / Apply / Remove / Save to file).
  function openTemplates() { return RenameTemplates.openManager(); }

  // ── 9. Change Case / Uppercase ────────────────────────────────────────────
  function openUppercase() {
    const body = `
      <p class="text-muted" style="font-size:12px">Convert selected filenames to UPPERCASE, Title Case, or lowercase.</p>
      <div id="uc-preview" style="max-height:160px;overflow:auto;font-size:12px;background:var(--bg-app);border-radius:6px;padding:8px;margin-bottom:12px;font-family:monospace"></div>
      <div style="display:flex;gap:8px">
        <button class="btn" id="uc-upper">UPPERCASE</button>
        <button class="btn" id="uc-title">Title Case</button>
        <button class="btn" id="uc-lower">lowercase</button>
      </div>`;

    _openModal('uppercase', 'Change Case', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Apply', primary: true, onClick: async () => {
        const rows = document.querySelectorAll('#uc-preview .uc-row');
        closeModal();
        let done = 0;
        for (const row of rows) {
          if (row.dataset.old && row.dataset.new)
            { await SFM.renameFile(row.dataset.old, row.dataset.new); done++; }
        }
        App.toast(`Renamed ${done} file(s)`, 'success');
        FileTree.refresh();
      }},
    ]);

    const selected = App.state.selectedPaths || [];
    const preview  = document.getElementById('uc-preview');

    function buildPreview(fn) {
      preview.innerHTML = selected.map(p => {
        const name    = p.split(/[\\/]/).pop();
        const newName = fn(name);
        return `<div class="uc-row" data-old="${_esc(p)}" data-new="${_esc(newName)}">
          <span style="color:var(--text-muted)">${_esc(name)}</span> &#8594; <span style="color:var(--accent)">${_esc(newName)}</span>
        </div>`;
      }).join('') || '<p class="text-muted">No files selected.</p>';
    }

    const toTitle = s => s.replace(/\b\w/g, c => c.toUpperCase());
    buildPreview(s => s.toUpperCase());
    document.getElementById('uc-upper').addEventListener('click', () => buildPreview(s => s.toUpperCase()));
    document.getElementById('uc-title').addEventListener('click', () => buildPreview(toTitle));
    document.getElementById('uc-lower').addEventListener('click', () => buildPreview(s => s.toLowerCase()));
  }

  // ── 10. Compress PDF — implemented in convert-tools.js (single or batch) ──
  function openCompressPdf(paths) { return ConvertTools.openCompressPdf(paths); }

  // ── 11. Settings ──────────────────────────────────────────────────────────
  async function openSettings() {
    const rs = await SFM.getSettings();
    const s  = rs.ok ? rs.settings : {};
    const rk = await SFM.getApiKey();
    const apiKey = rk.key || '';

    const body = `
      <div style="display:flex;flex-direction:column;gap:14px">
        <div>
          <label class="detail-label">OpenAI / GPT API Key</label>
          <input id="s-apikey" class="input-text" type="password" value="${_esc(apiKey)}" placeholder="sk-..." style="width:100%;margin-top:4px">
        </div>
        <div>
          <label class="detail-label">Default output folder</label>
          <div style="display:flex;gap:8px;margin-top:4px">
            <input id="s-outfolder" class="input-text" value="${_esc(s.output_folder||'')}" style="flex:1">
            <button class="btn" id="s-outfolder-browse">Browse&#x2026;</button>
          </div>
        </div>
        <div>
          <label class="detail-label">OCR language</label>
          <select id="s-ocrlang" class="input-text" style="margin-top:4px;width:100%">
            ${['eng','kor','jpn','chi_sim','chi_tra','ara','fra','deu','spa','rus'].map(l =>
              `<option value="${l}" ${s.ocr_lang===l?'selected':''}>${l}</option>`
            ).join('')}
          </select>
        </div>
        <div>
          <label class="detail-label">App theme</label>
          <select id="s-theme" class="input-text" style="margin-top:4px;width:100%">
            <option value="dark"  ${(s.theme||'dark')==='dark' ?'selected':''}>Dark</option>
            <option value="light" ${s.theme==='light'          ?'selected':''}>Light</option>
          </select>
        </div>
      </div>`;

    _openModal('settings', 'Settings', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Save', primary: true, onClick: async () => {
        const newKey = document.getElementById('s-apikey')?.value.trim() || '';
        if (newKey !== apiKey) await SFM.setApiKey(newKey);
        const ns = {
          output_folder:     document.getElementById('s-outfolder')?.value.trim() || '',
          ocr_lang:          document.getElementById('s-ocrlang')?.value || 'eng',
          theme:             document.getElementById('s-theme')?.value || 'dark',
        };
        const r = await SFM.saveSettings(ns);
        closeModal();
        if (r.ok) App.toast('Settings saved', 'success');
        else       App.toast('Save failed: ' + r.error, 'error');
      }},
    ]);

    document.getElementById('s-outfolder-browse')?.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) document.getElementById('s-outfolder').value = r.path;
    });
  }

  // ── 12. Extract PDF Pages — see js/pdf-tools.js (PdfTools.openExtract) ─
  function openExtractPages(path, page) {
    return PdfTools.openExtract(path, { page });
  }

  // ── 16. More Menu ─────────────────────────────────────────────────────────
  function openMoreMenu() {
    const body = `
      <div style="display:flex;flex-direction:column;gap:6px">
        <button class="qa-btn" id="mm-templates">&#x1F4DD;  Document Name Templates&#x2026;</button>
        <button class="qa-btn" id="mm-uppercase">&#x1F520;  Change Case / Uppercase Tool&#x2026;</button>
        <button class="qa-btn" id="mm-copyto">&#x1F4CB;  Copy To&#x2026;</button>
        <button class="qa-btn" id="mm-moveto">&#x2702;&#xFE0F;  Move To&#x2026;</button>
        <button class="qa-btn" id="mm-expand-all">&#x1F4C2;  Expand All Folders</button>
        <button class="qa-btn" id="mm-collapse-all">&#x1F4C1;  Collapse All Folders</button>
      </div>`;

    _openModal('more', 'More Actions', body, [
      { label: 'Close', onClick: closeModal },
    ]);

    const wire = (id, fn) => {
      const e = document.getElementById(id);
      if (e) e.addEventListener('click', () => { closeModal(); setTimeout(fn, 50); });
    };
    wire('mm-templates', () => openTemplates());
    wire('mm-uppercase', () => openUppercase());
    wire('mm-copyto',    () => openCopyTo(App.state.selectedPaths || []));
    wire('mm-moveto',    () => openMoveTo(App.state.selectedPaths || []));
    wire('mm-expand-all',    () => FileTree.expandAll());
    wire('mm-collapse-all',  () => FileTree.collapseAll());
  }

  // ── 17. Smart Split Progress ──────────────────────────────────────────────
  function openSmartSplitProgress(path) {
    const name = path.split(/[\\/]/).pop();
    const body = `
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:8px">
        GPT-4o is identifying, splitting and merging pages in:<br>
        <strong>${_esc(name)}</strong>
      </p>
      <div id="ss-log"
           style="font-family:monospace;font-size:11px;line-height:1.6;
                  background:var(--bg-app);border-radius:6px;padding:10px;
                  height:260px;overflow-y:auto;white-space:pre-wrap;
                  color:var(--text-primary)">Starting…\n</div>
      <div id="ss-status"
           style="font-size:12px;color:var(--accent);margin-top:8px;min-height:18px"></div>`;

    _openModal('smart-split', 'Smart Split & Merge', body, [
      { label: 'Close', onClick: () => {
          SFM.off('smart_rename_log', _onLog);
          SFM.off('smart_rename_done', _onDone);
          closeModal();
        }
      },
    ]);

    const logEl    = document.getElementById('ss-log');
    const statusEl = document.getElementById('ss-status');

    function _onLog({ log }) {
      if (!logEl) return;
      logEl.textContent += log + '\n';
      logEl.scrollTop = logEl.scrollHeight;
    }

    function _onDone(r) {
      SFM.off('smart_rename_log', _onLog);
      SFM.off('smart_rename_done', _onDone);
      App.setStatus('Ready');
      if (r.ok) {
        if (statusEl) statusEl.textContent = '✅ Complete — files saved alongside original.';
        App.toast('Smart Split complete', 'success');
        FileTree.refresh();
      } else {
        if (statusEl) { statusEl.style.color = 'var(--text-danger)'; statusEl.textContent = '❌ ' + (r.error || 'Failed'); }
        App.toast('Smart Split failed: ' + r.error, 'error', 7000);
      }
    }

    SFM.on('smart_rename_log', _onLog);
    SFM.on('smart_rename_done', _onDone);

    App.setStatus('Smart Split running…', true);
    SFM.smartSplit(path);
  }

  // ── 18. Copy To ─────────────────────────────────────────────────────────
  async function openCopyTo(paths) {
    if (!paths || paths.length === 0) { App.toast('Select files first', 'warning'); return; }
    const body = `
      <p class="text-muted" style="font-size:12px">Copy ${paths.length} file(s) to a destination folder.</p>
      <div style="margin-top:10px">
        <label class="detail-label">Destination folder</label>
        <div style="display:flex;gap:8px;margin-top:4px">
          <input id="ct-dest" class="input-text" placeholder="Select destination…" style="flex:1">
          <button class="btn" id="ct-browse">Browse&#x2026;</button>
        </div>
      </div>
      <div id="ct-status" style="font-size:12px;color:var(--accent);min-height:18px;margin-top:8px"></div>`;

    _openModal('copyto', 'Copy To…', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Copy', primary: true, onClick: async () => {
        const dest = document.getElementById('ct-dest')?.value.trim();
        if (!dest) { App.toast('Choose a destination folder', 'error'); return; }
        closeModal();
        App.setStatus('Copying…', true);
        const r = await SFM.copyFiles(paths, dest);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Copied ' + paths.length + ' file(s)', 'success'); FileTree.refresh(); }
        else       { App.toast('Copy failed: ' + r.error, 'error'); }
      }},
    ]);

    document.getElementById('ct-browse')?.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) document.getElementById('ct-dest').value = r.path;
    });
  }

  // ── 19. Move To ───────────────────────────────────────────────────────────
  async function openMoveTo(paths) {
    if (!paths || paths.length === 0) { App.toast('Select files first', 'warning'); return; }
    const body = `
      <p class="text-muted" style="font-size:12px">Move ${paths.length} file(s) to a destination folder.</p>
      <div style="margin-top:10px">
        <label class="detail-label">Destination folder</label>
        <div style="display:flex;gap:8px;margin-top:4px">
          <input id="mv-dest" class="input-text" placeholder="Select destination…" style="flex:1">
          <button class="btn" id="mv-browse">Browse…</button>
        </div>
      </div>
      <div id="mv-status" style="font-size:12px;color:var(--accent);min-height:18px;margin-top:8px"></div>`;

    _openModal('moveto', 'Move To…', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Move', primary: true, onClick: async () => {
        const dest = document.getElementById('mv-dest')?.value.trim();
        if (!dest) { App.toast('Choose a destination folder', 'error'); return; }
        closeModal();
        App.setStatus('Moving…', true);
        const r = await SFM.moveFiles(paths, dest);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Moved ' + paths.length + ' file(s)', 'success'); FileTree.refresh(); }
        else       { App.toast('Move failed: ' + r.error, 'error'); }
      }},
    ]);

    document.getElementById('mv-browse')?.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) document.getElementById('mv-dest').value = r.path;
    });
  }

  // ── 21. PDF to Images — implemented in convert-tools.js ──────────────────
  function openPdfToImages(path) { return ConvertTools.openPdfToImages(path); }

  // ── 22. Split & Rename OCR Progress ──────────────────────────────────────
  function openSplitRenameOcrProgress(path) {
    const name = path.split(/[\\\/]/).pop();
    const body = `
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:8px">
        OCR scanning and splitting: <strong>${_esc(name)}</strong>
      </p>
      <div id="sor-log"
           style="font-family:monospace;font-size:11px;line-height:1.6;
                  background:var(--bg-app);border-radius:6px;padding:10px;
                  height:220px;overflow-y:auto;white-space:pre-wrap;
                  color:var(--text-primary)">Starting OCR split…
</div>
      <div id="sor-status" style="font-size:12px;color:var(--accent);margin-top:8px;min-height:18px"></div>`;

    _openModal('split-ocr', 'Split & Rename by OCR', body, [
      { label: 'Close', onClick: () => {
          SFM.off('ocr_rename_log', _onLog);
          SFM.off('ocr_rename_done', _onDone);
          closeModal();
        }
      },
    ]);

    const logEl    = document.getElementById('sor-log');
    const statusEl = document.getElementById('sor-status');

    function _onLog(p) {
      if (!logEl) return;
      logEl.textContent += (p.log || String(p)) + '\n';
      logEl.scrollTop = logEl.scrollHeight;
    }
    function _onDone(r) {
      SFM.off('ocr_rename_log', _onLog);
      SFM.off('ocr_rename_done', _onDone);
      App.setStatus('Ready');
      if (r.ok) {
        if (statusEl) statusEl.textContent = '✅ Done — ' + (r.files?.length || '?') + ' file(s) created';
        App.toast('OCR Split complete', 'success');
        FileTree.refresh();
      } else {
        if (statusEl) { statusEl.style.color = 'var(--text-danger)'; statusEl.textContent = '❌ ' + (r.error || 'Failed'); }
        App.toast('OCR Split failed: ' + r.error, 'error', 7000);
      }
    }

    SFM.on('ocr_rename_log', _onLog);
    SFM.on('ocr_rename_done', _onDone);
    App.setStatus('OCR Split running…', true);
    SFM.splitAndRenameOcr(path);
  }

  // ── 26b. Compress Image(s) — implemented in convert-tools.js ─────────────
  function openCompressImages(paths) { return ConvertTools.openCompressImages(paths); }

  // ── 27. OCR Rename Progress ───────────────────────────────────────────────
  function openOcrRenameProgress(paths) {
    const body = `
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:10px">
        Renaming <strong>${paths.length}</strong> PDF(s) by detected document type using OCR.
        This may take a few seconds per file.
      </p>
      <div id="ocr-log" style="background:var(--bg-panel);border:1px solid var(--border);border-radius:6px;padding:10px;height:200px;overflow-y:auto;font-family:monospace;font-size:11px;white-space:pre-wrap"></div>
      <div id="ocr-status" style="margin-top:8px;font-size:12px;color:var(--accent);min-height:18px">Starting…</div>`;

    _openModal('ocr-rename-prog', '🔍 Rename by Document Type (OCR)', body, [
      { label: 'Close', onClick: closeModal },
    ]);

    const logEl  = document.getElementById('ocr-log');
    const statEl = document.getElementById('ocr-status');
    const _appendLog = msg => {
      if (!logEl) return;
      logEl.textContent += msg + '\n';
      logEl.scrollTop = logEl.scrollHeight;
    };

    const _unsubLog  = SFM.on('ocr_rename_log',  e => _appendLog(e.log || e.message || JSON.stringify(e)));
    const _unsubDone = SFM.on('ocr_rename_done', e => {
      _unsubLog(); _unsubDone();
      if (e.ok) {
        if (statEl) statEl.textContent = '✅ Done — all files renamed.';
        App.toast('OCR rename complete', 'success');
      } else {
        if (statEl) statEl.textContent = '❌ Error: ' + (e.error || 'unknown');
        App.toast('OCR rename failed', 'error');
      }
      FileTree.refresh();
    });

    SFM.ocrRenameProgress(paths).catch(err => {
      _appendLog('Error starting: ' + err);
      if (statEl) statEl.textContent = '❌ Could not start.';
    });
  }

  // ── Helper ────────────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s||'')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }


  // ── Crop Image / QR screen overlay ───────────────────────────────────────
  // Implemented in photo-tools.js (Cropper) and qr.js (QrScan); kept here so
  // existing callers of Dialogs.openCropImage / openQrOverlay keep working.
  function openCropImage(imagePath) { return Cropper.open(imagePath); }
  function openQrOverlay() { return QrScan.openScreen(); }

  return {
    openCombinePdf, openFullView, openArrangePages,
    openTemplates, openUppercase,
    openCompressPdf, openSettings, openExtractPages, openMoreMenu,
    openSmartSplitProgress,
    openCopyTo, openMoveTo,
    openPdfToImages, openSplitRenameOcrProgress,
    openCropImage,
    openCompressImages,
    openOcrRenameProgress,
    openQrOverlay,
    closeModal, closeAllModals,
    openModal: _openModal,
    modal: _modal,   // _modal(name, {title, width, extraStyle, body, footer}) → id (for qr.js / photo-tools.js)
  };
})();
