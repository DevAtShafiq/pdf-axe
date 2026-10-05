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
  // Shared modal header: optional icon tile + title + subtitle + close button.
  // opts: { icon?, tone? ('danger'|'warning'), subtitle? (plain text), titleId? }
  function _header(title, opts) {
    const o = opts || {};
    const icon = o.icon ? `<span class="modal-head-icon${o.tone ? ' tone-' + o.tone : ''}">${Icons.svg(o.icon, 18)}</span>` : '';
    return `<div class="modal-header">${icon}
        <div class="modal-heading">
          <h2 class="modal-title"${o.titleId ? ` id="${o.titleId}"` : ''}>${_esc(title)}</h2>
          ${o.subtitle ? `<div class="modal-subtitle" title="${_esc(o.subtitle)}">${_esc(o.subtitle)}</div>` : ''}
        </div>
        <button class="modal-close" aria-label="Close" title="Close (Esc)">${Icons.svg('x', 16)}</button>
      </div>`;
  }

  // Set a button's icon + label (keeps data-modal-btn etc.)
  function _setBtn(btn, label, icon) {
    if (!btn) return;
    btn.innerHTML = (icon ? Icons.svg(icon, btn.classList.contains('btn-sm') ? 14 : 16) : '') + _esc(label);
  }

  // _openModal(id, title, bodyHtml, buttons, opts?)
  //   opts: { icon, tone, subtitle, size: 'sm'|'lg'|'xl', cls }
  function _openModal(id, title, bodyHtml, buttons, opts) {
    const o = opts || {};
    const z = _Z_BASE + _stack.length * 50;
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = z;
    document.body.appendChild(overlay);

    // buttons: [{ label, primary?, danger?, icon? (Icons name), left?, onClick }]
    const btnHtml = (buttons || []).map(b =>
      `<button class="btn ${b.primary ? 'btn-primary' : ''} ${b.danger ? 'btn-danger' : ''} ${b.left ? 'footer-left' : ''}" data-modal-btn="${b.label}">${b.icon ? Icons.svg(b.icon, 16) : ''}${_esc(b.label)}</button>`
    ).join('');

    const cls = [o.size ? 'modal-' + o.size : '', o.cls || ''].join(' ').trim();
    overlay.innerHTML = `
      <div class="modal ${cls}" id="modal-${id}" role="dialog" aria-labelledby="modal-title-${id}">
        ${_header(title, { ...o, titleId: 'modal-title-' + id })}
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

  // _modal(name, {title, icon?, tone?, subtitle?, width, extraStyle, cls?, body, footer}) → id
  function _modal(name, opts) {
    const o  = opts || {};
    const id = _nextId();
    const html = `
<div class="modal-overlay" id="mo-${id}" style="z-index:${_nextZ()}">
<div class="modal ${o.cls || ''}" id="modal-${_esc(name)}" style="width:${o.width || '560px'};max-width:98vw;${o.extraStyle || ''}">
  ${_header(o.title || '', o)}
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
      <div id="fullview-wrap" style="width:100%;flex:1 1 auto;min-height:0;overflow:auto;background:var(--bg-preview);border-radius:var(--radius-lg);display:flex;padding:16px;box-sizing:border-box">
        <div id="fullview-inner" style="margin:auto;display:flex;flex-direction:column;gap:12px;align-items:center"></div>
      </div>
      ${isPdf ? `
      <div class="fv-bar">
        <button class="icon-btn" id="fv-prev" title="Previous page" aria-label="Previous page">${Icons.svg('chevron-left', 16)}</button>
        <span class="fv-page">Page <input id="fv-page-input" type="number" min="1" value="${startPage+1}" style="width:56px;text-align:center"> of <span id="fv-total">?</span></span>
        <button class="icon-btn" id="fv-next" title="Next page" aria-label="Next page">${Icons.svg('chevron-right', 16)}</button>
        <span class="toolbar-sep toolbar-sep-sm"></span>
        <button class="icon-btn" id="fv-zoom-out" title="Zoom out (−)" aria-label="Zoom out">${Icons.svg('zoom-out', 16)}</button>
        <select id="fv-zoom" title="Ctrl + scroll to zoom" style="width:110px"><option value="fit" selected>Fit page</option><option value="0.5">50%</option><option value="0.75">75%</option><option value="1">100%</option><option value="1.5">150%</option><option value="2">200%</option><option value="3">300%</option><option value="4">400%</option></select>
        <button class="icon-btn" id="fv-zoom-in" title="Zoom in (+)" aria-label="Zoom in">${Icons.svg('zoom-in', 16)}</button>
        <span class="toolbar-sep toolbar-sep-sm"></span>
        <button class="icon-btn" id="fv-rot-ccw" title="Rotate counter-clockwise" aria-label="Rotate counter-clockwise">${Icons.svg('rotate-ccw', 16)}</button>
        <button class="icon-btn" id="fv-rot-cw"  title="Rotate clockwise" aria-label="Rotate clockwise">${Icons.svg('rotate-cw', 16)}</button>
        <button class="icon-btn icon-btn-danger" id="fv-del-page" title="Delete this page" aria-label="Delete this page">${Icons.svg('trash', 16)}</button>
        <span class="toolbar-sep toolbar-sep-sm"></span>
        <button class="btn btn-sm btn-ghost" id="fv-append" title="Append another PDF to this one">${Icons.svg('plus', 14)}Append</button>
        <button class="btn btn-sm btn-ghost" id="fv-merge" title="Merge with other PDFs">${Icons.svg('merge', 14)}Merge</button>
        <button class="btn btn-sm btn-ghost" id="fv-arrange" title="Arrange / reorder pages">${Icons.svg('layers', 14)}Arrange</button>
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
    const selected = App.state.selectedPaths || [];
    const body = `
      <div class="field">
        <span class="field-label">Convert names to</span>
        <div class="segmented segmented-block" id="uc-seg" role="radiogroup" aria-label="Case">
          <button type="button" class="seg-btn active" id="uc-upper">UPPERCASE</button>
          <button type="button" class="seg-btn" id="uc-title">Title Case</button>
          <button type="button" class="seg-btn" id="uc-lower">lowercase</button>
        </div>
      </div>
      <div class="field">
        <span class="field-label">Preview</span>
        <div id="uc-preview" class="result-list"></div>
      </div>`;

    _openModal('uppercase', 'Change case', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Rename', primary: true, icon: 'case', onClick: async () => {
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
    ], { icon: 'case', subtitle: selected.length ? `${selected.length} selected item${selected.length === 1 ? '' : 's'}` : 'No selection', size: 'sm' });

    const preview  = document.getElementById('uc-preview');

    function buildPreview(fn) {
      preview.innerHTML = selected.map(p => {
        const name    = p.split(/[\\/]/).pop();
        const newName = fn(name);
        return `<div class="uc-row result-row" data-old="${_esc(p)}" data-new="${_esc(newName)}">
          <span class="result-name text-muted" title="${_esc(name)}">${_esc(name)}</span>
          ${Icons.svg('arrow-right', 14, 'text-muted')}
          <span class="result-name" title="${_esc(newName)}">${_esc(newName)}</span>
        </div>`;
      }).join('') || `<div class="empty-state empty-state-sm"><div class="empty-state-icon">${Icons.svg('files', 22)}</div><div class="empty-state-text">Select files in the list first.</div></div>`;
    }

    const toTitle = s => s.replace(/\b\w/g, c => c.toUpperCase());
    const pick = (id, fn) => document.getElementById(id).addEventListener('click', () => {
      document.querySelectorAll('#uc-seg .seg-btn').forEach(b => b.classList.toggle('active', b.id === id));
      buildPreview(fn);
    });
    buildPreview(s => s.toUpperCase());
    pick('uc-upper', s => s.toUpperCase());
    pick('uc-title', toTitle);
    pick('uc-lower', s => s.toLowerCase());
  }

  // ── 10. Compress PDF — implemented in convert-tools.js (single or batch) ──
  function openCompressPdf(paths) { return ConvertTools.openCompressPdf(paths); }

  // ── 11. Settings ──────────────────────────────────────────────────────────
  async function openSettings() {
    const rs = await SFM.getSettings();
    const s  = rs.ok ? rs.settings : {};
    const rk = await SFM.getApiKey();
    const apiKey = rk.key || '';

    const OCR_LANGS = [
      ['eng', 'English'], ['kor', 'Korean'], ['jpn', 'Japanese'], ['chi_sim', 'Chinese (Simplified)'],
      ['chi_tra', 'Chinese (Traditional)'], ['ara', 'Arabic'], ['fra', 'French'], ['deu', 'German'],
      ['spa', 'Spanish'], ['rus', 'Russian'],
    ];
    const curTheme = s.theme || (document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    const body = `
      <div class="settings-form">
        <section class="settings-group">
          <div class="settings-group-head">${Icons.svg('folder', 16)}General</div>
          <div class="settings-row">
            <label class="field-label" for="s-outfolder">Default output folder</label>
            <div>
              <div class="field-row">
                <input id="s-outfolder" class="input-text" value="${_esc(s.output_folder||'')}" placeholder="Same folder as the original file">
                <button class="btn" id="s-outfolder-browse">Browse&#x2026;</button>
              </div>
              <div class="field-hint">Where new PDFs and images are saved. Leave empty to save next to the original.</div>
            </div>
          </div>
          <div class="settings-row">
            <label class="field-label" for="s-ocrlang">OCR language</label>
            <div>
              <select id="s-ocrlang" class="input-text">
                ${OCR_LANGS.map(([v, n]) =>
                  `<option value="${v}" ${s.ocr_lang===v?'selected':''}>${n} (${v})</option>`
                ).join('')}
              </select>
              <div class="field-hint">Used when reading text from scanned documents.</div>
            </div>
          </div>
        </section>

        <section class="settings-group">
          <div class="settings-group-head">${Icons.svg('palette', 16)}Appearance</div>
          <div class="settings-row">
            <span class="field-label">Theme</span>
            <div>
              <div class="segmented segmented-block" id="s-theme-seg" role="radiogroup" aria-label="Theme">
                <button type="button" class="seg-btn ${curTheme==='dark'?'active':''}" data-theme-val="dark">${Icons.svg('moon', 14)}Dark</button>
                <button type="button" class="seg-btn ${curTheme==='light'?'active':''}" data-theme-val="light">${Icons.svg('sun', 14)}Light</button>
              </div>
              <select id="s-theme" class="hidden" aria-hidden="true">
                <option value="dark"  ${curTheme==='dark' ?'selected':''}>Dark</option>
                <option value="light" ${curTheme==='light'?'selected':''}>Light</option>
              </select>
            </div>
          </div>
        </section>

        <section class="settings-group">
          <div class="settings-group-head">${Icons.svg('sparkles', 16)}AI features</div>
          <div class="settings-row">
            <label class="field-label" for="s-apikey">OpenAI API key</label>
            <div>
              <div class="input-group">
                <span class="input-icon">${Icons.svg('key', 14)}</span>
                <input id="s-apikey" class="input-text mono" type="password" value="${_esc(apiKey)}" placeholder="sk-…" autocomplete="off" spellcheck="false">
                <button type="button" class="icon-btn input-action" id="s-apikey-toggle" title="Show key" aria-label="Show key">${Icons.svg('eye', 16)}</button>
              </div>
              <div class="field-hint">Needed for Smart Split and AI photo edits. Stored on this computer only.</div>
            </div>
          </div>
        </section>
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
        if (r.ok) { App.toast('Settings saved', 'success'); if (App.applyTheme) App.applyTheme(ns.theme); }
        else       App.toast('Save failed: ' + r.error, 'error');
      }},
    ]);
    const _sm = document.getElementById('modal-settings');
    if (_sm) _sm.style.width = 'min(640px, 94vw)';

    document.getElementById('s-outfolder-browse')?.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) document.getElementById('s-outfolder').value = r.path;
    });
    // Theme segmented control mirrors the (hidden) #s-theme select
    document.querySelectorAll('#s-theme-seg [data-theme-val]').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('#s-theme-seg .seg-btn').forEach(x => x.classList.toggle('active', x === b));
      const sel = document.getElementById('s-theme');
      if (sel) sel.value = b.dataset.themeVal;
    }));
    // Show / hide the API key
    document.getElementById('s-apikey-toggle')?.addEventListener('click', () => {
      const inp = document.getElementById('s-apikey');
      const t = document.getElementById('s-apikey-toggle');
      if (!inp || !t) return;
      const show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      t.innerHTML = Icons.svg(show ? 'eye-off' : 'eye', 16);
      t.title = show ? 'Hide key' : 'Show key';
      t.setAttribute('aria-label', t.title);
    });
  }

  // ── 12. Extract PDF Pages — see js/pdf-tools.js (PdfTools.openExtract) ─
  function openExtractPages(path, page) {
    return PdfTools.openExtract(path, { page });
  }

  // ── 16. More Menu ─────────────────────────────────────────────────────────
  function openMoreMenu() {
    const item = (id, icon, label, hint) =>
      `<button class="qa-btn more-item" id="${id}"><span class="icon">${Icons.svg(icon, 16)}</span>
         <span class="flex-1">${label}</span>${hint ? `<span class="hint">${hint}</span>` : ''}</button>`;
    const body = `
      <div class="qa-list">
        <div class="section-title" style="padding:4px 8px 2px">Rename</div>
        ${item('mm-templates', 'templates', 'Document name templates&#x2026;')}
        ${item('mm-uppercase', 'case', 'Change case&#x2026;', 'UPPER · Title · lower')}
        <div class="section-title" style="padding:12px 8px 2px">Files</div>
        ${item('mm-copyto', 'copy', 'Copy selection to&#x2026;')}
        ${item('mm-moveto', 'folder-input', 'Move selection to&#x2026;')}
        <div class="section-title" style="padding:12px 8px 2px">Folder tree</div>
        ${item('mm-expand-all', 'chevrons-up-down', 'Expand all folders')}
        ${item('mm-collapse-all', 'chevrons-down-up', 'Collapse all folders')}
      </div>`;

    _openModal('more', 'More actions', body, [
      { label: 'Close', onClick: closeModal },
    ]);
    const _mm = document.getElementById('modal-more');
    if (_mm) _mm.style.width = 'min(420px, 94vw)';

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
    const body = _jobBody('ss', 'AI identifies each document in the PDF, splits it and merges related pages. Files are saved next to the original.', 'Starting…\n', 'Analysing pages…');

    _openModal('smart-split', 'Smart Split & Merge', body, [
      { label: 'Close', onClick: () => {
          SFM.off('smart_rename_log', _onLog);
          SFM.off('smart_rename_done', _onDone);
          closeModal();
        }
      },
    ], { icon: 'sparkles', subtitle: name });

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
        _jobDone('ss', true, 'Smart Split complete', 'The new files are saved next to the original.');
        App.toast('Smart Split complete', 'success');
        FileTree.refresh();
      } else {
        _jobDone('ss', false, 'Smart Split failed', r.error || 'Failed');
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
    const body = _destBody('ct', 'Copies are added to the destination; the originals stay where they are.');

    _openModal('copyto', 'Copy to folder', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Copy', primary: true, icon: 'copy', onClick: async () => {
        const dest = document.getElementById('ct-dest')?.value.trim();
        if (!dest) { App.toast('Choose a destination folder', 'error'); return; }
        closeModal();
        App.setStatus('Copying…', true);
        const r = await SFM.copyFiles(paths, dest);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Copied ' + paths.length + ' file(s)', 'success'); FileTree.refresh(); }
        else       { App.toast('Copy failed: ' + r.error, 'error'); }
      }},
    ], { icon: 'copy', subtitle: _countLabel(paths), size: 'sm' });

    document.getElementById('ct-browse')?.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) document.getElementById('ct-dest').value = r.path;
    });
  }

  // ── 19. Move To ───────────────────────────────────────────────────────────
  async function openMoveTo(paths) {
    if (!paths || paths.length === 0) { App.toast('Select files first', 'warning'); return; }
    const body = _destBody('mv', 'The files are moved out of the current folder. An existing file is never replaced.');

    _openModal('moveto', 'Move to folder', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Move', primary: true, icon: 'folder-input', onClick: async () => {
        const dest = document.getElementById('mv-dest')?.value.trim();
        if (!dest) { App.toast('Choose a destination folder', 'error'); return; }
        closeModal();
        App.setStatus('Moving…', true);
        const r = await SFM.moveFiles(paths, dest);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Moved ' + paths.length + ' file(s)', 'success'); FileTree.refresh(); }
        else       { App.toast('Move failed: ' + r.error, 'error'); }
      }},
    ], { icon: 'folder-input', subtitle: _countLabel(paths), size: 'sm' });

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
    const body = _jobBody('sor', 'Reads every page with OCR, splits the PDF into documents and names each one by its type.', 'Starting OCR split…\n', 'Reading pages…');

    _openModal('split-ocr', 'Split & rename by OCR', body, [
      { label: 'Close', onClick: () => {
          SFM.off('ocr_rename_log', _onLog);
          SFM.off('ocr_rename_done', _onDone);
          closeModal();
        }
      },
    ], { icon: 'scan-text', subtitle: name });

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
        _jobDone('sor', true, 'OCR split complete', (r.files?.length || '?') + ' file(s) created next to the original.');
        App.toast('OCR Split complete', 'success');
        FileTree.refresh();
      } else {
        _jobDone('sor', false, 'OCR split failed', r.error || 'Failed');
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
    const body = _jobBody('ocr', 'Each PDF is renamed by the document type detected with OCR. This takes a few seconds per file.', '', 'Starting…');

    _openModal('ocr-rename-prog', 'Rename by document type', body, [
      { label: 'Close', onClick: closeModal },
    ], { icon: 'scan-text', subtitle: _countLabel(paths, 'PDF') });

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
        _jobDone('ocr', true, 'Rename complete', 'All files were renamed.');
        App.toast('OCR rename complete', 'success');
      } else {
        _jobDone('ocr', false, 'Rename failed', e.error || 'unknown error');
        App.toast('OCR rename failed', 'error');
      }
      FileTree.refresh();
    });

    SFM.ocrRenameProgress(paths).catch(err => {
      _appendLog('Error starting: ' + err);
      if (statEl) _jobDone('ocr', false, 'Could not start', String(err));
    });
  }

  // ── Shared bodies for the job / destination dialogs ──────────────────────
  function _countLabel(paths, noun) {
    const n = (paths || []).length;
    if (n === 1) return String(paths[0]).split(/[\\/]/).pop();
    return `${n} ${noun || 'file'}${n === 1 ? '' : 's'}`;
  }
  function _destBody(p, hint) {
    return `
      <div class="field">
        <label class="field-label" for="${p}-dest">Destination folder</label>
        <div class="field-row">
          <div class="input-group flex-1">
            <span class="input-icon">${Icons.svg('folder', 14)}</span>
            <input id="${p}-dest" class="input-text" placeholder="Choose a folder…">
          </div>
          <button class="btn" id="${p}-browse">Browse&#x2026;</button>
        </div>
        <div class="field-hint">${hint}</div>
      </div>
      <div id="${p}-status" class="progress-label"></div>`;
  }
  // Progress body: intro, indeterminate bar + status line, activity log.
  function _jobBody(p, intro, logInit, statusText) {
    return `
      <p class="text-sm text-muted">${_esc(intro)}</p>
      <div class="progress-block" id="${p}-progress">
        <div class="progress-wrap"><div class="progress-bar indeterminate"></div></div>
        <div class="progress-label" id="${p}-status"><span class="spinner"></span><span>${_esc(statusText || 'Working…')}</span></div>
      </div>
      <div class="field">
        <span class="field-label">Activity</span>
        <div id="${p}-log" class="log-output log-tall">${_esc(logInit || '')}</div>
      </div>`;
  }
  // Replace the progress bar + status with a success / error callout.
  function _jobDone(p, ok, title, text) {
    const prog = document.getElementById(p + '-progress');
    const st = document.getElementById(p + '-status');
    if (!prog || !st) return;
    prog.querySelector('.progress-wrap')?.remove();
    st.className = 'callout ' + (ok ? 'success' : 'error');
    st.innerHTML = `${Icons.svg(ok ? 'check-circle' : 'alert-circle', 16)}
      <div class="callout-body"><span class="callout-title">${_esc(title)}</span><span>${_esc(text || '')}</span></div>`;
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
  function openQrOverlay() { return QrScan.pick(); }

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
    modal: _modal,   // _modal(name, {title, icon, subtitle, width, extraStyle, body, footer}) → id (for qr.js / photo-tools.js)
    header: _header, // header HTML for dialogs that build their own overlay (pdf-tools, rename-templates)
    setBtn: _setBtn, // setBtn(button, label, icon) — relabel without losing the icon
  };
})();
