/**
 * dialogs.js — Modal dialogs
 *
 * Stacked modal system — each open() creates its own overlay div.
 * Escape / close only pops the TOP modal.
 *
 * Dialogs: CombinePdf, ArrangePages, PdfFullView, Templates, Uppercase,
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

  function closeModal() {
    if (_stack.length) _stack[_stack.length - 1].overlay._close();
  }

  function closeAllModals() {
    while (_stack.length) closeModal();
  }

  // ── 1. Combine PDFs ───────────────────────────────────────────────────────
  // Full-window Page Organizer — port of the old system's Combine/Arrange
  // window: one tile per PDF page, drag (or Space+arrows) to reorder,
  // click = green-✓ select, right-click menu (rotate / remove / extract),
  // zoom controls, merge → mergedN.pdf (+sources → _to_review/),
  // arrange → save in place over the original.
  async function openPageOrganizer(paths, opts) {
    const mode      = opts?.mode || 'merge';           // 'merge' | 'arrange'
    const arrange   = mode === 'arrange';
    const inplace   = arrange ? paths[0] : null;
    const outDirDef = (opts?.folder || paths[0].replace(/[\\/][^\\/]+$/, '')).replace(/[\\/]$/, '');

    // ── build slot list: one entry per page, in file order ──────────────────
    App.setStatus('Reading pages…', true);
    const slots = [];                                   // {path,page,rotate,sel}
    for (const p of paths) {
      const rc = await SFM.getPdfPageCount(p);
      const n  = Math.max(1, rc.page_count || rc.count || 1);
      for (let i = 0; i < n; i++) slots.push({ path: p, page: i, rotate: 0, sel: false });
    }
    App.setStatus('Ready');
    if (!slots.length) { App.toast('No pages found', 'error'); return; }

    // next free mergedN in outDir (old _next_merged_stem)
    async function nextMergedStem(dir) {
      const lr = await SFM.listFolder(dir).catch(() => null);
      const names = new Set((lr?.entries || []).map(e => (e.name || '').toLowerCase()));
      let n = 1;
      while (names.has(`merged${n}.pdf`)) n++;
      return `merged${n}`;
    }

    // ── overlay DOM (fills the whole window, like the old maximized dialog) ─
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = 9600;
    const title = arrange
      ? 'Arrange PDF — ' + inplace.split(/[\\/]/).pop()
      : `Combine PDFs (${paths.length} files · ${slots.length} pages)`;
    overlay.innerHTML = `
      <div style="width:calc(100vw - 20px);height:calc(100vh - 20px);display:flex;flex-direction:column;
                  background:var(--bg-panel);border:1px solid var(--border-strong);border-radius:10px;overflow:hidden">
        <div style="display:flex;align-items:center;gap:6px;padding:6px 10px;background:var(--bg-surface);
                    border-bottom:1px solid var(--border);flex-wrap:wrap">
          <strong style="font-size:13px;margin-right:8px">${_esc(title)}</strong>
          <button class="btn" id="pg-addpdf">Add PDF…</button>
          <span class="text-muted" style="font-size:11px;margin-left:8px">Size:</span>
          <button class="btn" id="pg-zoom-out" title="Smaller thumbnails" style="padding:2px 9px">−</button>
          <button class="btn" id="pg-zoom-in"  title="Larger thumbnails"  style="padding:2px 9px">+</button>
          <button class="btn" id="pg-zoom-reset" style="padding:2px 9px">Reset</button>
          <span id="pg-zoom-label" class="text-muted" style="font-size:11px;min-width:40px">100%</span>
          <div style="width:1px;height:20px;background:var(--border);margin:0 6px"></div>
          <button class="btn" id="pg-selall">Select All</button>
          <button class="btn" id="pg-extract" disabled>✂ Extract Selected (0)</button>
          <div style="flex:1"></div>
          <button class="btn" id="pg-cancel">Cancel</button>
          <button class="btn btn-primary" id="pg-save">${arrange ? 'Save to PDF file' : 'OK — Merge PDFs'}</button>
        </div>
        ${arrange ? '' : `
        <div style="display:flex;gap:8px;align-items:center;padding:6px 10px;background:var(--bg-surface);border-bottom:1px solid var(--border)">
          <label class="text-muted" style="font-size:12px;white-space:nowrap">Save as (no .pdf):</label>
          <input id="pg-basename" class="input-text" style="flex:1;max-width:280px">
          <label class="text-muted" style="font-size:12px;white-space:nowrap">Folder:</label>
          <input id="pg-outdir" class="input-text" style="flex:2" value="${_esc(outDirDef)}">
          <button class="btn" id="pg-browse">Browse…</button>
        </div>`}
        <div id="pg-grid" tabindex="0" style="flex:1;overflow:auto;background:#3a3d42;padding:14px;outline:none;
             display:flex;flex-wrap:wrap;gap:16px;align-content:flex-start"></div>
        <div id="pg-menu" style="display:none;position:fixed;z-index:9700;background:var(--bg-panel);
             border:1px solid var(--border-strong);border-radius:6px;box-shadow:0 8px 32px #000a;
             padding:4px;min-width:230px;font-size:12px"></div>
      </div>`;
    document.body.appendChild(overlay);

    const grid    = overlay.querySelector('#pg-grid');
    const menu    = overlay.querySelector('#pg-menu');
    const selBtn  = overlay.querySelector('#pg-selall');
    const extBtn  = overlay.querySelector('#pg-extract');
    const zoomLbl = overlay.querySelector('#pg-zoom-label');
    const baseInp = overlay.querySelector('#pg-basename');
    const dirInp  = overlay.querySelector('#pg-outdir');
    if (baseInp) nextMergedStem(outDirDef).then(s => { if (!baseInp.value) baseInp.value = s; });

    // ── geometry / zoom (old: 10 columns at 100%) ────────────────────────────
    const ZOOM_MIN = 0.4, ZOOM_MAX = 10, ZOOM_STEP = 1.15;
    let zoom = 1;
    const baseW = Math.max(90, Math.floor((grid.clientWidth - 14 * 2 - 16 * 9) / 10) || 120);
    function tileW() { return Math.round(baseW * zoom); }

    // ── thumbnail cache ──────────────────────────────────────────────────────
    const thumbs = new Map();          // "path|page|tier" -> data_url / 'pending'
    function tier() { return tileW() > 260 ? 110 : 36; }
    let thumbGen = 0;
    async function fillThumbs() {
      const gen = ++thumbGen, t = tier();
      for (const el of Array.from(grid.children)) {
        if (gen !== thumbGen) return;                    // superseded
        const i = +el.dataset.i, s = slots[i];
        if (!s) continue;
        const key = `${s.path}|${s.page}|${t}`;
        let du = thumbs.get(key);
        if (du === undefined) {
          thumbs.set(key, 'pending');
          const r = await SFM.getPdfPage(s.path, s.page, t).catch(() => null);
          du = r?.ok ? r.data_url : null;
          thumbs.set(key, du);
        }
        if (du && du !== 'pending') {
          const img = el.querySelector('img');
          if (img && img.dataset.key !== key) { img.src = du; img.dataset.key = key; img.style.visibility = 'visible'; }
        }
      }
    }

    // ── state ────────────────────────────────────────────────────────────────
    let focusIdx = 0, pickIdx = null, pickSnapshot = null, lastClickIdx = null;

    function selCount() { return slots.filter(s => s.sel).length; }
    function updateButtons() {
      const n = selCount();
      extBtn.disabled = n === 0;
      extBtn.textContent = `✂ Extract Selected (${n})`;
      selBtn.textContent = (slots.length && n === slots.length) ? 'Deselect All' : 'Select All';
      zoomLbl.textContent = Math.round(zoom * 100) + '%';
    }

    function render() {
      const w = tileW(), h = Math.round(w * 1.29);
      grid.innerHTML = slots.map((s, i) => {
        const odd = s.rotate === 90 || s.rotate === 270;
        const fit = odd ? `rotate(${s.rotate}deg) scale(${(w / h).toFixed(3)})` : `rotate(${s.rotate}deg)`;
        return `
        <div class="pg-tile" data-i="${i}" style="width:${w}px;user-select:none;cursor:grab;position:relative">
          <div style="width:${w}px;height:${h}px;background:#fff;border:3px solid ${
            i === pickIdx ? '#ff9800' : (s.sel ? '#2e9e4f' : (i === focusIdx ? 'var(--accent,#4a9eff)' : '#00000033'))
          };border-radius:4px;overflow:hidden;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px #0006">
            <img draggable="false" style="max-width:100%;max-height:100%;visibility:hidden;transform:${fit}">
            ${s.sel ? '<div style="position:absolute;top:4px;left:4px;background:#2e9e4f;color:#fff;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;font-size:13px">✓</div>' : ''}
          </div>
          <div style="text-align:center;font-size:11px;color:#ddd;padding-top:3px" title="${_esc(s.path.split(/[\\/]/).pop())}">
            ${i + 1}${paths.length > 1 ? ' · ' + _esc(s.path.split(/[\\/]/).pop().slice(0, 14)) : ''}
          </div>
        </div>`;
      }).join('');
      updateButtons();
      fillThumbs();
    }

    function syncFromDom() {
      const order = Array.from(grid.children).map(el => slots[+el.dataset.i]);
      slots.length = 0; slots.push(...order);
    }

    // ── close / cleanup ──────────────────────────────────────────────────────
    function close() {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousemove', onMove, true);
      document.removeEventListener('mouseup', onUp, true);
      overlay.remove();
    }
    overlay.querySelector('#pg-cancel').addEventListener('click', close);
    overlay.addEventListener('mousedown', e => { if (!menu.contains(e.target)) menu.style.display = 'none'; }, true);

    // ── drag to reorder (pointer-based, like the old B1-Motion drag) ────────
    let drag = null, suppressClick = false;
    grid.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      const tile = e.target.closest('.pg-tile');
      if (!tile) return;
      drag = { el: tile, sx: e.clientX, sy: e.clientY, moved: false };
      e.preventDefault();
    });
    function onMove(e) {
      if (!drag) return;
      if (!drag.moved && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > 6) {
        drag.moved = true;
        drag.el.style.opacity = '.45';
        drag.el.style.cursor = 'grabbing';
        drag.el.style.pointerEvents = 'none';   // so elementFromPoint sees the tile UNDER the cursor
      }
      if (!drag.moved) return;
      const under = document.elementFromPoint(e.clientX, e.clientY)?.closest('.pg-tile');
      if (under && under !== drag.el && under.parentNode === grid) {
        const kids = Array.from(grid.children);
        if (kids.indexOf(drag.el) < kids.indexOf(under)) under.after(drag.el); else under.before(drag.el);
      }
    }
    function onUp(e) {
      if (!drag) return;
      const wasDrag = drag.moved, el = drag.el;
      el.style.opacity = ''; el.style.cursor = 'grab'; el.style.pointerEvents = '';
      drag = null;
      if (wasDrag) {
        suppressClick = true;                   // swallow the click generated by this mouseup
        syncFromDom();
        focusIdx = Array.from(grid.children).indexOf(el);
        pickIdx = null;
        render();
      }
    }
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('mouseup', onUp, true);

    // ── click = toggle green-✓ selection (shift = range, like old) ──────────
    grid.addEventListener('click', e => {
      if (suppressClick) { suppressClick = false; return; }
      const tile = e.target.closest('.pg-tile');
      if (!tile) return;
      const i = Array.from(grid.children).indexOf(tile);
      focusIdx = i;
      if (e.shiftKey && lastClickIdx !== null) {
        const [a, b] = [Math.min(lastClickIdx, i), Math.max(lastClickIdx, i)];
        for (let k = a; k <= b; k++) slots[k].sel = true;
      } else {
        slots[i].sel = !slots[i].sel;
        lastClickIdx = i;
      }
      grid.focus();
      render();
    });

    // ── right-click context menu (rotate / remove / extract) ────────────────
    grid.addEventListener('contextmenu', e => {
      const tile = e.target.closest('.pg-tile');
      if (!tile) return;
      e.preventDefault();
      const i = Array.from(grid.children).indexOf(tile);
      focusIdx = i;
      const item = (label) => `<div class="pg-mi" style="padding:6px 12px;border-radius:4px;cursor:pointer" data-act="${label}">${label}</div>`;
      menu.innerHTML = [
        item('Rotate clockwise'),
        item('Rotate counter-clockwise'),
        '<div style="height:1px;background:var(--border);margin:3px 6px"></div>',
        item(arrange ? 'Remove page from layout…' : 'Remove from merge…'),
        '<div style="height:1px;background:var(--border);margin:3px 6px"></div>',
        item('✂ Extract this page → New PDF…'),
        item('✂ Extract selected pages → New PDF…'),
      ].join('');
      menu.style.left = Math.min(e.clientX, window.innerWidth - 250) + 'px';
      menu.style.top  = Math.min(e.clientY, window.innerHeight - 200) + 'px';
      menu.style.display = 'block';
      menu.querySelectorAll('.pg-mi').forEach(mi => {
        mi.addEventListener('mouseenter', () => mi.style.background = 'var(--bg-hover,#ffffff14)');
        mi.addEventListener('mouseleave', () => mi.style.background = '');
        mi.addEventListener('click', () => { menu.style.display = 'none'; menuAction(mi.dataset.act, i); });
      });
    });

    function menuAction(act, i) {
      if (act.startsWith('Rotate clockwise'))              { slots[i].rotate = (slots[i].rotate + 90) % 360; render(); }
      else if (act.startsWith('Rotate counter'))           { slots[i].rotate = (slots[i].rotate + 270) % 360; render(); }
      else if (act.startsWith('Remove'))                   {
        slots.splice(i, 1);
        if (!slots.length) { close(); return; }
        focusIdx = Math.min(focusIdx, slots.length - 1);
        pickIdx = null; render();
      }
      else if (act.includes('this page'))                  { doExtract([i]); }
      else if (act.includes('selected pages'))             {
        const sel = slots.map((s, k) => s.sel ? k : -1).filter(k => k >= 0);
        if (!sel.length) { App.toast('No pages selected. Click pages to select them (green ✓).', 'warning'); return; }
        doExtract(sel);
      }
    }

    // ── keyboard: Space pick/drop, arrows move, Escape, Enter (old bindings) ─
    function cols() {
      const w = tileW() + 16;
      return Math.max(1, Math.floor((grid.clientWidth - 28) / w));
    }
    function moveSlot(from, to) {
      to = Math.max(0, Math.min(slots.length - 1, to));
      if (from === to) return from;
      const [s] = slots.splice(from, 1);
      slots.splice(to, 0, s);
      return to;
    }
    function onKey(e) {
      if (!document.body.contains(overlay)) return;
      if (e.target === baseInp || e.target === dirInp) {
        if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); doSave(); }
        return;
      }
      const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols(), ArrowDown: cols() }[e.key];
      if (e.key === ' ') {
        e.preventDefault(); e.stopPropagation();
        if (pickIdx === null) { pickIdx = focusIdx; pickSnapshot = slots.slice(); }
        else pickIdx = null;                             // drop keeps position
        render();
      } else if (step !== undefined) {
        e.preventDefault(); e.stopPropagation();
        if (pickIdx !== null) { pickIdx = focusIdx = moveSlot(pickIdx, pickIdx + step); }
        else focusIdx = Math.max(0, Math.min(slots.length - 1, focusIdx + step));
        render();
        grid.children[focusIdx]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation();
        if (menu.style.display === 'block') { menu.style.display = 'none'; return; }
        if (pickIdx !== null) {                          // cancel pick → restore order
          slots.length = 0; slots.push(...pickSnapshot);
          pickIdx = null; render(); return;
        }
        close();
      } else if (e.key === 'Enter') {
        e.preventDefault(); e.stopPropagation(); doSave();
      }
    }
    document.addEventListener('keydown', onKey, true);

    // ── zoom ─────────────────────────────────────────────────────────────────
    function setZoom(z) { zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z)); render(); }
    overlay.querySelector('#pg-zoom-in').addEventListener('click',    () => setZoom(zoom * ZOOM_STEP));
    overlay.querySelector('#pg-zoom-out').addEventListener('click',   () => setZoom(zoom / ZOOM_STEP));
    overlay.querySelector('#pg-zoom-reset').addEventListener('click', () => setZoom(1));
    grid.addEventListener('wheel', e => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setZoom(e.deltaY < 0 ? zoom * ZOOM_STEP : zoom / ZOOM_STEP);
    }, { passive: false });

    // ── Select All / Extract ────────────────────────────────────────────────
    selBtn.addEventListener('click', () => {
      const all = slots.length && selCount() === slots.length;
      slots.forEach(s => s.sel = !all);
      render();
    });
    extBtn.addEventListener('click', () => {
      const sel = slots.map((s, k) => s.sel ? k : -1).filter(k => k >= 0);
      if (sel.length) doExtract(sel);
    });

    // ── Add PDF… (old arrange feature; native multi-select picker) ──────────
    overlay.querySelector('#pg-addpdf').addEventListener('click', async () => {
      const r = await SFM.browseForPdfs();
      if (!r.ok || !r.paths?.length) return;
      let added = 0;
      for (const p of r.paths) {
        const rc = await SFM.getPdfPageCount(p);
        const n  = Math.max(1, rc.page_count || rc.count || 1);
        for (let i = 0; i < n; i++) { slots.push({ path: p, page: i, rotate: 0, sel: false }); added++; }
      }
      if (added) { App.toast(`Added ${added} page(s) from ${r.paths.length} file(s)`, 'success'); render(); }
    });

    // ── Browse output folder (merge mode) ───────────────────────────────────
    overlay.querySelector('#pg-browse')?.addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r.ok && r.path) { dirInp.value = r.path; if (baseInp && /^merged\d+$/.test(baseInp.value)) baseInp.value = await nextMergedStem(r.path); }
    });

    // ── extract / save (exact old semantics) ────────────────────────────────
    function slotPayload(indices) {
      return indices.map(k => ({ path: slots[k].path, page: slots[k].page, rotate: slots[k].rotate }));
    }

    async function doExtract(indices) {
      const dir = (dirInp?.value?.trim() || outDirDef);
      const def = (await nextMergedStem(dir)) + '_extract';
      const name = prompt('Save extracted pages as (no .pdf):', def);
      if (!name || !name.trim()) return;
      const out = dir.replace(/[\\/]$/, '') + '\\' + name.trim().replace(/\.pdf$/i, '') + '.pdf';
      const ex = await SFM.call('path_exists', out);
      if (ex.ok && ex.exists && !confirm(`'${name.trim()}.pdf' already exists.\nOverwrite?`)) return;
      App.setStatus('Extracting pages…', true);
      const r = await SFM.buildPdfFromPages(slotPayload(indices.slice().sort((a, b) => a - b)), out, false);
      App.setStatus('Ready');
      if (r.ok) { App.toast(`Extracted ${indices.length} page(s) → ` + out.split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
      else       { App.toast('Extract failed: ' + r.error, 'error'); }
    }

    async function doSave() {
      if (!slots.length) { App.toast('No pages to save', 'error'); return; }
      const payload = slotPayload(slots.map((_, k) => k));
      if (arrange) {
        // Old: save rearranged pages back into the original PDF (tmp + replace).
        App.setStatus('Saving PDF…', true);
        const r = await SFM.buildPdfFromPages(payload, inplace, true);
        App.setStatus('Ready');
        if (r.ok) {
          App.toast('Saved rearranged PDF → ' + inplace.split(/[\\/]/).pop(), 'success');
          close(); FileTree.refresh();
          try { Preview.clear(); Preview.previewFile(inplace, '.pdf'); } catch (_) {}
        }
        else       { App.toast('Save failed: ' + r.error, 'error'); }
      } else {
        const dir = dirInp.value.trim();
        const ok  = await SFM.call('path_exists', dir);
        if (!dir || !ok.ok || !ok.is_dir) { App.toast('Choose a valid output folder first.', 'error'); return; }
        let base = baseInp.value.trim() || await nextMergedStem(dir);
        base = base.replace(/\.pdf$/i, '');
        const out = dir.replace(/[\\/]$/, '') + '\\' + base + '.pdf';
        const ex = await SFM.call('path_exists', out);
        if (ex.ok && ex.exists && !confirm(`'${base}.pdf' already exists.\nOverwrite?`)) return;
        App.setStatus('Merging PDFs…', true);
        const r = await SFM.buildPdfFromPages(payload, out, false);
        if (!r.ok) { App.setStatus('Ready'); App.toast('Merge failed: ' + r.error, 'error'); return; }
        // Old: move source PDFs to _to_review/ so they don't sit next to the merged file.
        const srcs = [...new Set(slots.map(s => s.path))].filter(p => p.toLowerCase() !== out.toLowerCase());
        const mv = await SFM.softDelete(srcs);
        App.setStatus('Ready');
        const movedN = (mv.results || []).filter(x => x.ok).length;
        App.toast(`Merged ${payload.length} pages → ${base}.pdf` + (movedN ? ` · ${movedN} source file(s) → _to_review/` : ''), 'success');
        if (!mv.ok) App.toast('Some sources not moved: ' + mv.error, 'warning');
        close(); FileTree.refresh();
      }
    }
    overlay.querySelector('#pg-save').addEventListener('click', doSave);

    render();
    grid.focus();
  }

  function openCombinePdf(paths, folderPath) {
    return openPageOrganizer(paths, { mode: 'merge', folder: folderPath });
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
      document.getElementById('fv-append').addEventListener('click', async () => {
        const r = await SFM.call('browse_for_folder');
        // browse for a file instead — use openFileDialog via prompt fallback
        const other = prompt('Path of PDF to append:');
        if (!other || !other.trim()) return;
        const out = path.replace(/(\.[^.]+)$/, '_appended$1');
        App.setStatus('Appending…', true);
        const mr = await SFM.mergePdfs([path, other.trim()], out);
        App.setStatus('Ready');
        if (mr.ok) { App.toast('Appended → ' + out.split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
        else App.toast('Append failed: ' + mr.error, 'error');
      });
      document.getElementById('fv-merge').addEventListener('click',   () => openCombinePdf([path], path.replace(/[\\/][^\\/]+$/, '')));
      document.getElementById('fv-arrange').addEventListener('click', () => openArrangePages(path, total));
    }

    renderPage(page);
  }

  // ── 3. Arrange / Reorder PDF Pages — full-window organizer (old system) ──
  function openArrangePages(path /*, pageCount */) {
    return openPageOrganizer([path], { mode: 'arrange' });
  }

  // Legacy small-modal version (superseded by openPageOrganizer, kept unused).
  async function _openArrangePagesLegacy(path, pageCount) {
    if (!pageCount) {
      const r = await SFM.getPdfPageCount(path);
      if (!r.ok || !r.page_count) { App.toast('Could not read PDF page count' + (r.error ? ': ' + r.error : ''), 'error'); return; }
      pageCount = r.page_count;
    }
    const cards = Array.from({length: pageCount}, (_,i) =>
      `<div class="arrange-card" draggable="true" data-idx="${i}"
            style="width:110px;border:1px solid var(--border);border-radius:8px;background:var(--bg-app);padding:6px;display:flex;flex-direction:column;align-items:center;gap:4px;cursor:grab">
         <div class="arrange-thumb" data-thumb="${i}"
              style="width:96px;height:128px;display:flex;align-items:center;justify-content:center;background:#222;border-radius:4px;overflow:hidden;font-size:11px;color:var(--text-muted)">&#8987;</div>
         <span style="font-size:11px;color:var(--text-muted)">Page ${i+1}</span>
         <div style="display:flex;gap:4px">
           <button class="btn arrange-left"  data-idx="${i}" title="Move earlier" style="padding:2px 10px">&#9664;</button>
           <button class="btn arrange-right" data-idx="${i}" title="Move later"   style="padding:2px 10px">&#9654;</button>
         </div>
       </div>`
    ).join('');

    const body = `
      <p class="text-muted" style="font-size:12px;margin-bottom:8px">Reorder pages with the &#9664;/&#9654; buttons (or drag the cards). A reordered copy is saved alongside the original.</p>
      <div id="arrange-list" style="display:flex;flex-wrap:wrap;gap:8px;max-height:52vh;overflow-y:auto;padding:2px">${cards}</div>`;

    _openModal('arrange', 'Arrange Pages — ' + path.split(/[\\/]/).pop(), body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Save Reordered', primary: true, onClick: async () => {
        const cardEls = document.querySelectorAll('#arrange-list .arrange-card');
        const order   = Array.from(cardEls).map(c => +c.dataset.idx);
        closeModal();
        App.setStatus('Reordering pages…', true);
        const outPath = path.replace(/(\.[^.]+)$/, '_reordered$1');
        const r = await SFM.reorderPages(path, order, outPath);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Saved: ' + (r.out_path || outPath).split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
        else       { App.toast('Reorder failed: ' + r.error, 'error'); }
      }},
    ]);

    const list = document.getElementById('arrange-list');
    if (!list) return;

    // ◀ / ▶ move buttons — always work, no dragging needed
    list.addEventListener('click', e => {
      const btn = e.target.closest('.arrange-left, .arrange-right');
      if (!btn) return;
      const card = btn.closest('.arrange-card');
      if (btn.classList.contains('arrange-left')) {
        const prev = card.previousElementSibling;
        if (prev) card.after(prev);          // swap with previous
      } else {
        const next = card.nextElementSibling;
        if (next) card.before(next);         // swap with next
      }
    });

    // Drag & drop (works with a real mouse)
    let dragEl = null;
    list.querySelectorAll('.arrange-card').forEach(card => {
      card.addEventListener('dragstart', () => { dragEl = card; card.style.opacity = '.4'; });
      card.addEventListener('dragend',   () => { if (dragEl) dragEl.style.opacity = ''; dragEl = null; });
      card.addEventListener('dragover',  e => e.preventDefault());
      card.addEventListener('drop', e => {
        e.preventDefault();
        if (!dragEl || dragEl === card) return;
        const kids = Array.from(list.children);
        if (kids.indexOf(dragEl) < kids.indexOf(card)) card.after(dragEl); else card.before(dragEl);
      });
    });

    // Load thumbnails asynchronously (low dpi keeps this fast)
    (async () => {
      const maxThumbs = Math.min(pageCount, 200);
      for (let i = 0; i < maxThumbs; i++) {
        const slot = list.querySelector(`[data-thumb="${i}"]`);
        if (!slot) break;                    // modal was closed
        try {
          const r = await SFM.getPdfPage(path, i, 30);
          if (r.ok && r.data_url) slot.innerHTML = `<img src="${r.data_url}" style="width:100%;height:100%;object-fit:contain">`;
          else slot.textContent = 'p.' + (i+1);
        } catch (_) { slot.textContent = 'p.' + (i+1); }
      }
    })();
  }

  // ── 8. Document Name Templates ────────────────────────────────────────────
  async function openTemplates() {
    const r = await SFM.getRenameTemplates();
    const templates = r.ok ? (r.templates || []) : [];
    const listHtml  = templates.map((t, i) =>
      `<div class="template-row" data-idx="${i}">
        <span class="template-name">${_esc(t.name || t)}</span>
        <span class="template-pattern text-muted">${_esc(t.pattern || '')}</span>
      </div>`
    ).join('') || '<p class="text-muted">No templates saved yet.</p>';

    const body = `
      <div id="template-list" style="max-height:200px;overflow:auto;margin-bottom:12px">${listHtml}</div>
      <div>
        <label class="detail-label">New template name</label>
        <input id="tmpl-name" class="input-text" placeholder="e.g. Passport_John Doe" style="width:100%;margin-top:4px">
      </div>
      <div style="margin-top:8px">
        <label class="detail-label">Pattern (use {name}, {date}, {ext})</label>
        <input id="tmpl-pattern" class="input-text" placeholder="{name}_{date}" style="width:100%;margin-top:4px">
      </div>`;

    _openModal('templates', 'Document Name Templates', body, [
      { label: 'Close', onClick: closeModal },
      { label: 'Save Template', primary: true, onClick: async () => {
        const name = document.getElementById('tmpl-name')?.value.trim();
        const pat  = document.getElementById('tmpl-pattern')?.value.trim();
        if (!name) { App.toast('Enter a template name', 'error'); return; }
        const r = await SFM.saveRenameTemplate({ name, pattern: pat || name });
        if (r.ok) { App.toast('Template saved', 'success'); closeModal(); }
        else       { App.toast('Failed: ' + r.error, 'error'); }
      }},
    ]);
  }

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

  // ── 10. Compress PDF ──────────────────────────────────────────────────────
  function openCompressPdf(path) {
    const name = path.split(/[\\/]/).pop();
    const body = `
      <p class="text-muted" style="font-size:12px">Compress: <strong>${_esc(name)}</strong></p>
      <div style="margin-bottom:12px">
        <label class="detail-label">Quality preset</label>
        <select id="cmp-quality" class="input-text" style="margin-top:4px;width:100%">
          <option value="screen">Screen (smallest)</option>
          <option value="ebook" selected>eBook (balanced)</option>
          <option value="printer">Printer (high quality)</option>
          <option value="prepress">Prepress (maximum)</option>
        </select>
      </div>
      <div>
        <label class="detail-label">Output filename</label>
        <input id="cmp-out" class="input-text" value="${_esc(name.replace(/\.pdf$/i,'_compressed.pdf'))}" style="width:100%;margin-top:4px">
      </div>`;

    _openModal('compress', 'Compress PDF', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Compress', primary: true, onClick: async () => {
        const quality = document.getElementById('cmp-quality')?.value || 'ebook';
        const outName = document.getElementById('cmp-out')?.value.trim() || name;
        const out     = path.replace(/[\\/][^\\/]+$/, '') + '\\' + outName;
        closeModal();
        App.setStatus('Compressing…', true);
        const r = await SFM.call('compress_pdf_quality', path, quality, out);
        App.setStatus('Ready');
        if (r.ok) { App.toast(`Compressed: ${outName}${r.reduction ? ' ('+r.reduction+'% saved)' : ''}`, 'success'); FileTree.refresh(); }
        else       { App.toast('Compress failed: ' + r.error, 'error'); }
      }},
    ]);
  }

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
          <label class="detail-label">Watch folder auto-rename</label>
          <label style="display:flex;gap:8px;align-items:center;margin-top:4px;cursor:pointer">
            <input type="checkbox" id="s-watchrename" ${s.watch_auto_rename ? 'checked' : ''}> Enable
          </label>
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
          watch_auto_rename: document.getElementById('s-watchrename')?.checked || false,
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

  // ── 12. Extract PDF Pages ─────────────────────────────────────────────────
  function openExtractPages(path) {
    const name = path.split(/[\\/]/).pop();
    const body = `
      <p class="text-muted" style="font-size:12px">Extract pages from: <strong>${_esc(name)}</strong></p>
      <div style="margin-bottom:12px">
        <label class="detail-label">Page range (e.g. 1-3, 5, 7-9)</label>
        <input id="ep-range" class="input-text" placeholder="e.g. 1-3, 5" style="width:100%;margin-top:4px">
      </div>
      <div>
        <label class="detail-label">Output filename</label>
        <input id="ep-out" class="input-text" value="${_esc(name.replace(/\.pdf$/i,'_extract.pdf'))}" style="width:100%;margin-top:4px">
      </div>`;

    _openModal('extract-pages', 'Extract PDF Pages', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Extract', primary: true, onClick: async () => {
        const spec    = document.getElementById('ep-range')?.value.trim();
        const outName = document.getElementById('ep-out')?.value.trim() || name;
        if (!spec) { App.toast('Enter a page range', 'error'); return; }
        const out = path.replace(/[\\/][^\\/]+$/, '') + '\\' + outName;
        closeModal();
        App.setStatus('Extracting pages…', true);
        const r = await SFM.extractPdfPages(path, spec, out);
        App.setStatus('Ready');
        if (r.ok) { App.toast('Extracted: ' + outName, 'success'); FileTree.refresh(); }
        else       { App.toast('Extract failed: ' + r.error, 'error'); }
      }},
    ]);
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

  // ── 21. PDF to Images ─────────────────────────────────────────────────────
  async function openPdfToImages(path) {
    const name = path.split(/[\\\/]/).pop();
    const body = `
      <p class="text-muted" style="font-size:12px">Convert each page of <strong>${_esc(name)}</strong> to an image file.</p>
      <div style="margin-bottom:12px">
        <label class="detail-label">Format</label>
        <select id="pi-fmt" class="input-text" style="margin-top:4px;width:100%">
          <option value="png" selected>PNG (lossless)</option>
          <option value="jpg">JPEG (smaller)</option>
        </select>
      </div>
      <div style="margin-bottom:12px">
        <label class="detail-label">DPI / Resolution</label>
        <select id="pi-dpi" class="input-text" style="margin-top:4px;width:100%">
          <option value="72">72 dpi (screen)</option>
          <option value="150" selected>150 dpi (balanced)</option>
          <option value="300">300 dpi (print quality)</option>
        </select>
      </div>
      <div id="pi-status" style="font-size:12px;color:var(--accent);min-height:18px"></div>`;

    _openModal('pdf-to-images', 'PDF → Images', body, [
      { label: 'Cancel', onClick: closeModal },
      { label: 'Convert', primary: true, onClick: async () => {
        const fmt = document.getElementById('pi-fmt')?.value || 'png';
        const dpi = +document.getElementById('pi-dpi')?.value || 150;
        const statusEl = document.getElementById('pi-status');
        if (statusEl) statusEl.textContent = 'Converting…';
        App.setStatus('Converting PDF to images…', true);
        const r = await SFM.pdfToImages(path, dpi, fmt);
        App.setStatus('Ready');
        if (r.ok) {
          const n = (r.files || []).length;
          if (statusEl) statusEl.textContent = '✅ ' + n + ' image(s) saved alongside PDF';
          App.toast('Converted ' + n + ' page(s) to ' + fmt.toUpperCase(), 'success');
          FileTree.refresh();
        } else {
          if (statusEl) { statusEl.style.color = 'var(--text-danger)'; statusEl.textContent = '❌ ' + r.error; }
          App.toast('Conversion failed: ' + r.error, 'error');
        }
      }},
    ]);
  }

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

  // ── 26b. Compress Image(s) ────────────────────────────────────────────────
  function _fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }

  function openCompressImages(paths) {
    paths = (Array.isArray(paths) ? paths : [paths]).filter(Boolean);
    if (!paths.length) { App.toast('Select image(s) first', 'warning'); return; }
    const label = paths.length === 1
      ? '<strong>' + _esc(paths[0].split(/[\\/]/).pop()) + '</strong>'
      : '<strong>' + paths.length + '</strong> images';
    const body = `
      <p class="text-muted" style="font-size:12px;margin-bottom:10px">Compress: ${label}<br>
        Originals are kept; output is saved as <em>name_compressed.ext</em>.</p>
      <div style="margin-bottom:12px">
        <label class="detail-label">Quality: <span id="ci-q-val">70</span></label>
        <input id="ci-q" type="range" min="10" max="95" step="1" value="70" style="width:100%;margin-top:4px">
      </div>
      <div style="display:flex;gap:10px;margin-bottom:12px">
        <div style="flex:1">
          <label class="detail-label">Max size (longest edge)</label>
          <select id="ci-edge" class="input-text" style="margin-top:4px;width:100%">
            <option value="0">Original</option>
            <option value="3000">3000 px</option>
            <option value="2000">2000 px</option>
            <option value="1600" selected>1600 px</option>
            <option value="1200">1200 px</option>
            <option value="1024">1024 px</option>
          </select>
        </div>
        <div style="flex:1">
          <label class="detail-label">Output format</label>
          <select id="ci-fmt" class="input-text" style="margin-top:4px;width:100%">
            <option value="" selected>Same as original</option>
            <option value="jpg">JPG</option>
            <option value="webp">WEBP</option>
            <option value="png">PNG</option>
          </select>
        </div>
      </div>
      <div id="ci-status" style="font-size:12px;min-height:18px"></div>`;

    let busy = false;
    _openModal('compress-images', paths.length === 1 ? '🗜 Compress Image' : '🗜 Compress Images', body, [
      { label: 'Close', onClick: closeModal },
      { label: 'Compress', primary: true, onClick: async () => {
        if (busy) return;
        busy = true;
        const q    = parseInt(document.getElementById('ci-q')?.value, 10) || 70;
        const edge = parseInt(document.getElementById('ci-edge')?.value, 10) || 0;
        const fmt  = document.getElementById('ci-fmt')?.value || '';
        const st   = document.getElementById('ci-status');
        if (st) st.textContent = 'Compressing ' + paths.length + ' image(s)…';
        App.setStatus('Compressing image(s)…', true);
        let r;
        try { r = await SFM.compressImages(paths, q, edge, fmt); }
        catch (e) { r = { ok: false, error: String(e) }; }
        App.setStatus('Ready');
        busy = false;
        if (!r || !r.ok) {
          if (st) st.textContent = '❌ ' + (r?.error || 'Failed');
          App.toast('Compress failed: ' + (r?.error || ''), 'error');
          return;
        }
        const okCount = r.results.length - (r.failed || 0);
        const pct = r.reduction || 0;
        const sizeLine = _fmtBytes(r.before) + ' → ' + _fmtBytes(r.after) +
          ' (' + (pct >= 0 ? pct + '% saved' : Math.abs(pct) + '% larger') + ')';
        let html = (okCount ? '✅ ' : '❌ ') + okCount + ' of ' + r.results.length + ' compressed: <strong>' + _esc(sizeLine) + '</strong>';
        if (okCount === 1 && r.results.length === 1) {
          html += '<br>Saved as <strong>' + _esc(r.results[0].out.split(/[\\/]/).pop()) + '</strong>';
        }
        const fails = r.results.filter(x => !x.ok);
        if (fails.length) {
          html += '<br>' + fails.map(f => '⚠ ' + _esc(f.path.split(/[\\/]/).pop()) + ': ' + _esc(f.error)).join('<br>');
        }
        if (st) st.innerHTML = html;
        if (okCount) {
          App.toast('Compressed ' + okCount + ' image(s): ' + sizeLine, 'success');
          FileTree.refresh();
        } else {
          App.toast('Compress failed', 'error');
        }
      }},
    ]);
    const qEl = document.getElementById('ci-q');
    qEl?.addEventListener('input', () => {
      const v = document.getElementById('ci-q-val');
      if (v) v.textContent = qEl.value;
    });
  }

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


  // ── Crop Image ────────────────────────────────────────────────────────────
  async function openCropImage(imagePath) {
    const inf = await SFM.getImagePreview(imagePath, 2400);
    if (!inf.ok) { App.toast('Cannot load image: ' + inf.error, 'error'); return; }

    let _cropX = 0, _cropY = 0, _cropW = 0, _cropH = 0;
    let _dragging = false, _dragStart = {x:0, y:0};
    let _imgNatW = 0, _imgNatH = 0, _ratio = null;

    const id = _nextId();
    const z  = _nextZ();

    const html = `
<div class="modal-overlay" id="mo-${id}" style="z-index:${z}">
<div class="modal" style="width:820px;max-width:98vw;max-height:94vh;display:flex;flex-direction:column;">
  <div class="modal-header">
    <span class="modal-title">✂️ Crop Image</span>
    <button class="modal-close" onclick="Dialogs.closeModal('${id}')">&#x2715;</button>
  </div>
  <div style="display:flex;gap:8px;padding:8px 14px;background:var(--bg-surface-2);border-bottom:1px solid var(--border);align-items:center;flex-wrap:wrap;">
    <span style="font-size:12px;color:var(--text-secondary);">Ratio:</span>
    <button class="btn btn-sm" id="cr-free-${id}">Free</button>
    <button class="btn btn-sm" id="cr-11-${id}">1:1</button>
    <button class="btn btn-sm" id="cr-43-${id}">4:3</button>
    <button class="btn btn-sm" id="cr-34-${id}">3:4</button>
    <button class="btn btn-sm" id="cr-pp-${id}">35x45</button>
    <span style="margin-left:auto;font-size:12px;color:var(--text-muted);" id="cr-dims-${id}">Drag to select crop area</span>
  </div>
  <div style="flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;padding:10px;background:#0d0d0d;min-height:280px;">
    <div style="position:relative;display:inline-block;user-select:none;" id="cr-wrap-${id}">
      <img id="cr-img-${id}" src="${inf.data_url}" draggable="false"
           style="max-width:760px;max-height:54vh;display:block;cursor:crosshair;">
      <canvas id="cr-canvas-${id}" style="position:absolute;top:0;left:0;cursor:crosshair;"></canvas>
    </div>
  </div>
  <div class="modal-footer">
    <button class="btn" onclick="Dialogs.closeModal('${id}')">Cancel</button>
    <button class="btn btn-primary" id="cr-save-${id}" disabled>Save Crop</button>
  </div>
</div></div>`;
    document.body.insertAdjacentHTML('beforeend', html);
    _attachClose(id);

    const img     = document.getElementById('cr-img-'    + id);
    const canvas  = document.getElementById('cr-canvas-' + id);
    const ctx     = canvas.getContext('2d');
    const dimsEl  = document.getElementById('cr-dims-'   + id);
    const saveBtn = document.getElementById('cr-save-'   + id);

    function _resize() {
      canvas.width  = img.offsetWidth;
      canvas.height = img.offsetHeight;
      _draw();
    }
    function _draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (_cropW < 4 || _cropH < 4) return;
      ctx.fillStyle = 'rgba(0,0,0,0.52)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.clearRect(_cropX, _cropY, _cropW, _cropH);
      ctx.strokeStyle = '#58a6ff'; ctx.lineWidth = 2;
      ctx.strokeRect(_cropX + 1, _cropY + 1, _cropW - 2, _cropH - 2);
      const hs = 7; ctx.fillStyle = '#58a6ff';
      [[_cropX,_cropY],[_cropX+_cropW-hs,_cropY],[_cropX,_cropY+_cropH-hs],[_cropX+_cropW-hs,_cropY+_cropH-hs]]
        .forEach(([x,y]) => ctx.fillRect(x, y, hs, hs));
      const sx = _imgNatW / canvas.width, sy = _imgNatH / canvas.height;
      dimsEl.textContent = Math.round(_cropW*sx) + ' x ' + Math.round(_cropH*sy) + ' px';
      saveBtn.disabled = (_cropW < 4 || _cropH < 4);
    }

    function _init() {
      _imgNatW = img.naturalWidth; _imgNatH = img.naturalHeight; _resize();
    }
    if (img.complete && img.naturalWidth) _init(); else img.onload = _init;

    function _pos(e) {
      const r = canvas.getBoundingClientRect();
      return { x: Math.max(0,Math.min(canvas.width, e.clientX-r.left)),
               y: Math.max(0,Math.min(canvas.height,e.clientY-r.top)) };
    }
    canvas.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      const p = _pos(e); _dragging = true; _dragStart = p;
      _cropX=p.x; _cropY=p.y; _cropW=0; _cropH=0; _draw();
    });
    const _mm = e => {
      if (!_dragging) return;
      const p = _pos(e);
      let dx = p.x - _dragStart.x, dy = p.y - _dragStart.y;
      if (_ratio !== null) { const s = dy<0?-1:1; dy = s*Math.abs(dx)/_ratio; }
      _cropX = dx>=0?_dragStart.x:_dragStart.x+dx;
      _cropY = dy>=0?_dragStart.y:_dragStart.y+dy;
      _cropW = Math.abs(dx); _cropH = Math.abs(dy); _draw();
    };
    const _mu = () => { _dragging = false; };
    document.addEventListener('mousemove', _mm);
    document.addEventListener('mouseup',   _mu);

    // Clean up global listeners when dialog closes
    const obs = new MutationObserver(() => {
      if (!document.getElementById('mo-' + id)) {
        document.removeEventListener('mousemove', _mm);
        document.removeEventListener('mouseup',   _mu);
        obs.disconnect();
      }
    });
    obs.observe(document.body, {childList:true});

    const ratios = {'cr-free':null,'cr-11':1,'cr-43':4/3,'cr-34':3/4,'cr-pp':35/45};
    Object.entries(ratios).forEach(([k, r]) => {
      const btn = document.getElementById(k+'-'+id);
      if (!btn) return;
      btn.addEventListener('click', () => {
        _ratio = r;
        Object.keys(ratios).forEach(k2 => document.getElementById(k2+'-'+id)?.classList.remove('active'));
        btn.classList.add('active');
      });
    });
    document.getElementById('cr-free-'+id)?.classList.add('active');

    saveBtn.addEventListener('click', async () => {
      const sx = _imgNatW/canvas.width, sy = _imgNatH/canvas.height;
      const px=Math.round(_cropX*sx), py=Math.round(_cropY*sy);
      const pw=Math.round(_cropW*sx), ph=Math.round(_cropH*sy);
      saveBtn.disabled = true; saveBtn.textContent = 'Saving…';
      const r = await SFM.cropImage(imagePath, px, py, pw, ph);
      if (r.ok) { App.toast('Image cropped ✓', 'success'); Dialogs.closeModal(id); FileTree.refresh(); }
      else       { App.toast('Crop failed: ' + r.error, 'error'); saveBtn.disabled=false; saveBtn.textContent='Save Crop'; }
    });
  }

  // ── QR Overlay ─────────────────────────────────────────────────────────────
  // Full-screen screenshot overlay: user clicks QR code → decode → open URL.
  function openQrOverlay() {
    const id = _modal('qr-overlay', {
      title: '📷 QR Scanner — Click on a QR Code',
      width: '96vw',
      extraStyle: 'max-width:none;',
      body: [
        '<div id="qro-status" style="text-align:center;padding:10px 0;color:var(--accent);">',
        '  Minimising window — please wait…',
        '</div>',
        '<div id="qro-img-wrap" style="display:none;position:relative;line-height:0;overflow:auto;max-height:70vh;">',
        '  <img id="qro-img" style="max-width:100%;cursor:crosshair;display:block;user-select:none;" alt="Screen capture" />',
        '  <div id="qro-crosshair" style="display:none;position:absolute;width:36px;height:36px;pointer-events:none;',
        '    transform:translate(-50%,-50%);border:2px solid var(--accent);border-radius:50%;',
        '    box-shadow:0 0 0 1px rgba(0,0,0,.6);"></div>',
        '</div>',
        '<div id="qro-result" style="display:none;margin-top:12px;padding:12px;',
        '  background:var(--surface2,#1e1e2a);border-radius:8px;word-break:break-all;">',
        '  <div id="qro-result-text" style="margin-bottom:8px;font-size:13px;line-height:1.5;"></div>',
        '  <div style="display:flex;gap:8px;flex-wrap:wrap;">',
        '    <button id="qro-open-btn" class="btn-primary" style="display:none;">🌐 Open Link</button>',
        '    <button id="qro-copy-btn" class="btn-secondary">📋 Copy Text</button>',
        '    <button id="qro-again-btn" class="btn-secondary">🔄 Click Another</button>',
        '  </div>',
        '</div>',
      ].join('\n'),
      footer: '<button id="qro-close" class="btn-secondary">Close</button>',
    });

    const statusEl  = document.getElementById('qro-status');
    const imgWrap   = document.getElementById('qro-img-wrap');
    const img       = document.getElementById('qro-img');
    const crosshair = document.getElementById('qro-crosshair');
    const resultEl  = document.getElementById('qro-result');
    const resultTxt = document.getElementById('qro-result-text');
    const openBtn   = document.getElementById('qro-open-btn');
    const copyBtn   = document.getElementById('qro-copy-btn');
    const againBtn  = document.getElementById('qro-again-btn');

    document.getElementById('qro-close').addEventListener('click', () => closeModal(id));

    let _screenW = 0, _screenH = 0, _lastText = '', _lastIsUrl = false;

    // Listen for screenshot ready event from Python
    const unsub = SFM.on('screen_capture_ready', r => {
      if (!document.getElementById('mo-' + id)) { unsub(); return; }
      if (!r.ok) {
        statusEl.textContent = '❌ Screenshot failed: ' + (r.error || 'unknown error');
        statusEl.style.color = 'var(--danger,#f55)';
        return;
      }
      _screenW = r.width;
      _screenH = r.height;
      img.src  = r.data_url;
      imgWrap.style.display = 'block';
      statusEl.style.color  = '';
      statusEl.textContent  = '👆 Click on the QR code in the screenshot below';
    });

    // Click on image -> compute real pixel coords -> decode
    img.addEventListener('click', async e => {
      const rect = img.getBoundingClientRect();
      const relX = (e.clientX - rect.left) / rect.width;
      const relY = (e.clientY - rect.top)  / rect.height;
      const px   = Math.round(relX * _screenW);
      const py   = Math.round(relY * _screenH);

      // Show crosshair at click position
      crosshair.style.left    = (e.clientX - rect.left) + 'px';
      crosshair.style.top     = (e.clientY - rect.top)  + 'px';
      crosshair.style.display = 'block';

      statusEl.style.color   = '';
      statusEl.textContent   = '🔍 Decoding…';
      resultEl.style.display = 'none';

      const r = await SFM.decodeQrAtPoint(px, py).catch(err => ({ ok: false, error: String(err) }));
      if (r && r.ok) {
        _lastText  = r.text  || '';
        _lastIsUrl = r.is_url || false;
        resultTxt.textContent      = _lastText;
        openBtn.style.display      = _lastIsUrl ? 'inline-flex' : 'none';
        resultEl.style.display     = 'block';
        statusEl.textContent       = _lastIsUrl
          ? '✅ URL decoded — click Open Link or click another QR'
          : '✅ Decoded — click another QR code or close';
        // Also populate rename bar
        const ri = document.getElementById('rename-input');
        if (ri && _lastText) ri.value = _lastText;
      } else {
        statusEl.style.color   = 'var(--danger,#f55)';
        statusEl.textContent   = '❌ ' + (r && r.error ? r.error : 'No QR code found at that point');
        crosshair.style.display = 'none';
      }
    });

    openBtn.addEventListener('click', () => {
      // Python already opened the browser; this is a fallback via window.open
      if (_lastText) { try { window.open(_lastText, '_blank'); } catch(e) {} }
    });

    copyBtn.addEventListener('click', () => {
      if (_lastText) {
        SFM.setClipboard(_lastText).catch(() => {});
        App.toast('Copied to clipboard', 'success', 2000);
      }
    });

    againBtn.addEventListener('click', () => {
      resultEl.style.display  = 'none';
      crosshair.style.display = 'none';
      statusEl.style.color    = '';
      statusEl.textContent    = '👆 Click on another QR code in the screenshot';
    });

    // Start screen capture (minimise -> screenshot -> restore -> emit event)
    SFM.getScreenCapture().catch(err => {
      statusEl.style.color = 'var(--danger,#f55)';
      statusEl.textContent = '❌ Failed to start capture: ' + err;
    });
  }

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
  };
})();
