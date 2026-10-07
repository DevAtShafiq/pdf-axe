/**
 * pdf-edit.js — full-window PDF text editor ("Edit text", like Acrobat's Edit PDF).
 *
 *   PdfEdit.open(path)      open the editor for a PDF
 *   PdfEdit.canEdit(path)   true for PDFs
 *
 * Click a text block to edit it in place (Enter = new line, Esc = cancel,
 * click outside = keep). Drag a block to move it; the handle on its right edge
 * changes the wrap width. Toolbar: Add text, font, size, bold/italic, colour,
 * alignment, Delete, image replace/delete, Undo/Redo, zoom, page navigation.
 * Edits are shown live (white patch + new text) and written on Save by
 * pdf_edit_bridge.py → pdf_edit.py, to a NEW file (<name>_edited.pdf) unless
 * "Save over original" is chosen (backup to _to_review/ first).
 *
 * Coordinates are page units (PDF points, display orientation, origin top-left).
 */

var PdfEdit = (() => {

  const isPdf = p => /\.pdf$/i.test(p || '');
  const baseOf = p => String(p || '').split(/[\\/]/).pop();
  const dirOf = p => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const stemOf = p => baseOf(p).replace(/\.[^.]+$/, '');
  const sepOf = d => (String(d).includes('/') && !String(d).includes('\\')) ? '/' : '\\';
  const joinP = (d, n) => String(d).replace(/[\\/]+$/, '') + sepOf(d) + n;
  const cleanName = n => String(n || '').trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim();
  const ic = (n, s = 16, c = '') => Icons.svg(n, s, c);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const r2 = v => Math.round(v * 100) / 100;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem('pedit.' + key);
      localStorage.setItem('pedit.' + key, String(val));
    } catch (_) { return null; }
    return null;
  }
  const SCAN_MSG = 'This page is a scanned image — text can’t be edited directly.';

  let _open = null;
  let _uid = 0;
  let _fonts = null;          // [{name, css, bold, italic}]
  const fontCss = name => ((_fonts || []).find(f => f.name === name) || {}).css || 'Arial, sans-serif';

  // canvas text measurement (the inline editor and the live preview share it)
  const _mctx = document.createElement('canvas').getContext('2d');
  function measure(text, sizePx, family, bold, italic) {
    _mctx.font = `${italic ? 'italic ' : ''}${bold ? '700 ' : '400 '}${sizePx}px ${family}`;
    return _mctx.measureText(text).width;
  }

  // =========================================================================
  async function open(path) {
    if (!isPdf(path)) { App.toast('Edit text works with PDF files', 'info'); return; }
    if (_open) { _open.focus(); return; }
    if (!_fonts) {
      const fr = await SFM.pdfeditFonts().catch(() => null);
      _fonts = (fr && fr.ok && fr.fonts) || [{ name: 'Arial', css: 'Arial, sans-serif' }];
    }

    const ov = document.createElement('div');
    ov.className = 'modal-overlay an-overlay pe-overlay';
    const fontOpts = `<option value="original">Original font</option>` +
      _fonts.map(f => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('');
    ov.innerHTML = `
      <div class="an-editor pe-editor" role="dialog" aria-label="Edit PDF text" data-mode="select">
        <div class="an-head">
          <span class="modal-head-icon">${ic('file-pen', 18)}</span>
          <div class="modal-heading">
            <h2 class="modal-title">Edit text</h2>
            <div class="modal-subtitle" id="pe-sub" title="${esc(path)}">${esc(baseOf(path))}</div>
          </div>
          <span class="pill pill-neutral" id="pe-status"></span>
          <button class="modal-close" id="pe-x" aria-label="Close" title="Close (Esc)">${ic('x')}</button>
        </div>
        <div class="an-bar pe-bar" role="toolbar" aria-label="Text editing tools">
          <div class="an-tb-group">
            <button class="icon-btn an-tool pe-tool" data-a="mode-select" data-tooltip="Select / edit text · V" aria-label="Select and edit text">${ic('text-select')}</button>
            <button class="btn btn-sm btn-ghost pe-tool pe-add-btn" data-a="mode-add" data-tooltip="Click on the page to add a text box · T" aria-label="Add text">${ic('type', 14)}Add text</button>
          </div>
          <span class="toolbar-sep"></span>
          <div class="an-tb-group pe-style" id="pe-style">
            <select class="input-text pe-font" id="pe-font" aria-label="Font" title="Font">${fontOpts}</select>
            <input class="input-text pe-size" id="pe-size" type="number" min="4" max="200" step="0.5" aria-label="Font size" title="Font size (pt)">
            <button class="icon-btn pe-tool" data-a="bold" data-tooltip="Bold" aria-label="Bold" aria-pressed="false">${ic('bold')}</button>
            <button class="icon-btn pe-tool" data-a="italic" data-tooltip="Italic" aria-label="Italic" aria-pressed="false">${ic('italic')}</button>
            <label class="pe-color" data-tooltip="Text colour"><input type="color" id="pe-color" aria-label="Text colour"><span class="pe-color-sw" id="pe-color-sw"></span></label>
            <div class="segmented pe-align" role="group" aria-label="Alignment">
              <button class="seg-btn pe-tool" data-a="align-left" title="Align left" aria-label="Align left">${ic('align-left', 14)}</button>
              <button class="seg-btn pe-tool" data-a="align-center" title="Center" aria-label="Center">${ic('align-center', 14)}</button>
              <button class="seg-btn pe-tool" data-a="align-right" title="Align right" aria-label="Align right">${ic('align-right', 14)}</button>
            </div>
            <button class="icon-btn icon-btn-danger pe-tool" data-a="delete" data-tooltip="Delete block · Del" aria-label="Delete block">${ic('trash')}</button>
          </div>
          <div class="an-tb-group pe-imgtools hidden" id="pe-imgtools">
            <button class="btn btn-sm btn-ghost pe-tool" data-a="img-replace" title="Replace the selected image">${ic('image', 14)}Replace image…</button>
            <button class="btn btn-sm btn-ghost pe-tool" data-a="img-delete" title="Delete the selected image">${ic('trash', 14)}Delete image</button>
          </div>
          <span class="toolbar-sep"></span>
          <div class="an-tb-group">
            <button class="icon-btn pe-tool" data-a="undo" data-tooltip="Undo · Ctrl+Z" aria-label="Undo">${ic('undo')}</button>
            <button class="icon-btn pe-tool" data-a="redo" data-tooltip="Redo · Ctrl+Y" aria-label="Redo">${ic('redo')}</button>
          </div>
          <span class="an-spacer"></span>
          <div class="an-tb-group an-pagenav" id="pe-pagenav">
            <button class="icon-btn pe-tool" data-a="prev" data-tooltip="Previous page" aria-label="Previous page">${ic('chevron-up')}</button>
            <span class="an-pageno"><input class="input-text" id="pe-pg" value="1" aria-label="Page"><span id="pe-pgn">/ 1</span></span>
            <button class="icon-btn pe-tool" data-a="next" data-tooltip="Next page" aria-label="Next page">${ic('chevron-down')}</button>
          </div>
          <span class="toolbar-sep"></span>
          <div class="an-tb-group">
            <button class="icon-btn pe-tool" data-a="zout" data-tooltip="Zoom out · Ctrl+−" aria-label="Zoom out">${ic('zoom-out')}</button>
            <button class="btn btn-sm btn-ghost an-zoom pe-tool" data-a="z100" id="pe-zoom" title="Actual size">100%</button>
            <button class="icon-btn pe-tool" data-a="zin" data-tooltip="Zoom in · Ctrl+=" aria-label="Zoom in">${ic('zoom-in')}</button>
            <button class="icon-btn pe-tool" data-a="fitw" data-tooltip="Fit width · Ctrl+0" aria-label="Fit width">${ic('fit-width')}</button>
            <button class="icon-btn pe-tool" data-a="fitp" data-tooltip="Fit page" aria-label="Fit page">${ic('scan-fit')}</button>
          </div>
        </div>
        <div class="an-hintbar" id="pe-hint"></div>
        <div class="pe-notice hidden" id="pe-notice"></div>
        <div class="an-body">
          <div class="an-thumbs hidden" id="pe-thumbs" aria-label="Pages"></div>
          <div class="an-view" id="pe-view" tabindex="0"><div class="an-pages" id="pe-pages">
            <div class="an-loading"><span class="loading-spinner"></span><span>Opening…</span></div>
          </div></div>
        </div>
        <div class="an-foot">
          <div class="segmented an-out-seg" role="radiogroup" aria-label="Save mode">
            <label class="seg-btn"><input type="radio" name="pe-out" value="new" checked>${ic('file', 14)}New file</label>
            <label class="seg-btn" title="The current version is copied to _to_review/ first"><input type="radio" name="pe-out" value="over">${ic('refresh', 14)}Save over original</label>
          </div>
          <div class="an-foot-fields" id="pe-fields">
            <div class="field-row"><input class="input-text an-name" id="pe-name" aria-label="File name"><span class="field-suffix">.pdf in</span></div>
            <div class="field-row"><input class="input-text an-dir" id="pe-dir" aria-label="Folder"><button class="btn" id="pe-browse">Browse…</button></div>
          </div>
          <div class="an-progress progress-block" id="pe-progress"><div class="progress-wrap"><div class="progress-bar indeterminate"></div></div><div class="progress-label">Saving…</div></div>
          <span class="an-spacer"></span>
          <button class="btn" id="pe-close">Close</button>
          <button class="btn btn-primary" id="pe-save">${ic('save')}Save</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    const $ = s => ov.querySelector(s);
    const $$ = s => Array.from(ov.querySelectorAll(s));
    const root = $('.pe-editor'), view = $('#pe-view'), pagesEl = $('#pe-pages');

    // ── state ──────────────────────────────────────────────────────────────
    let src = path;                 // file being shown (switches to the output after Save)
    let info = null;                // {pages:[{w,h,scanned}]}
    let zoom = 1, curPage = 0, mode = 'select';
    let pageData = new Map();       // p -> {blocks, images, scanned, error} | Promise
    let model = { ov: {}, adds: [], imgs: {} };
    let undoStack = [], redoStack = [];
    let sel = null;                 // {kind:'block'|'add'|'image', key}
    let editing = null;             // {kind, key, ta, before}
    let busy = false, closed = false;
    const pageEls = [], thumbEls = [];
    const rendered = new Map();

    const pages = () => (info ? info.pages : []);
    const snap = () => JSON.stringify(model);
    const dirty = () => Object.keys(model.ov).length + model.adds.length + Object.keys(model.imgs).length > 0;
    function pushUndo() { undoStack.push(snap()); if (undoStack.length > 300) undoStack.shift(); redoStack = []; }
    function undo() { commitEdit(); if (!undoStack.length) return; redoStack.push(snap()); model = JSON.parse(undoStack.pop()); fixSel(); renderAll(); }
    function redo() { commitEdit(); if (!redoStack.length) return; undoStack.push(snap()); model = JSON.parse(redoStack.pop()); fixSel(); renderAll(); }
    function fixSel() { if (sel && !itemOf(sel)) sel = null; }

    // ── model helpers ──────────────────────────────────────────────────────
    const keyOf = (p, id) => p + ':' + id;
    function blockOf(key) {
      const [p, id] = key.split(':'); const d = pageData.get(+p);
      return d && d.blocks ? d.blocks.find(b => b.id === id) : null;
    }
    function imageOf(key) {
      const [p, id] = key.split(':'); const d = pageData.get(+p);
      return d && d.images ? d.images.find(b => b.id === id) : null;
    }
    function itemOf(s) {
      if (!s) return null;
      if (s.kind === 'block') return blockOf(s.key);
      if (s.kind === 'image') return imageOf(s.key);
      return model.adds.find(a => a.id === s.key) || null;
    }
    /** effective view of a block or added box: {page, text, font, family, size, color, bold, italic, align, bbox, single, lh, deleted} */
    function eff(s) {
      if (s.kind === 'add') {
        const a = model.adds.find(x => x.id === s.key); if (!a) return null;
        return { ...a, family: fontCss(a.font), bbox: a.rect, single: false, lh: a.size * 1.2, editable: true };
      }
      const b = blockOf(s.key); if (!b) return null;
      const o = model.ov[s.key] || {};
      const size = o.size || b.size;
      const font = o.font || 'original';
      const text = o.text != null ? o.text : b.text;
      return {
        page: +s.key.split(':')[0], text, font, size,
        family: font === 'original' ? b.css_family : fontCss(font),
        color: o.color || b.color, bold: o.bold != null ? o.bold : b.bold, italic: o.italic != null ? o.italic : b.italic,
        align: o.align || b.align, bbox: o.bbox || b.bbox, deleted: !!o.deleted, editable: b.editable,
        single: b.lines.length === 1 && !text.includes('\n') && !(o.bbox && Math.abs((o.bbox[2] - o.bbox[0]) - (b.bbox[2] - b.bbox[0])) > 0.5),
        lh: (b.line_height ? b.line_height * size / b.size : size * 1.2),
        asc: (b.lines[0].origin[1] - b.lines[0].bbox[1]) * size / b.size,
        changed: !!model.ov[s.key],
      };
    }
    /** where the text sits (page units): {x, y, w, h} */
    function textBox(e) {
      const [x0, y0, x1, y1] = e.bbox;
      const lines = String(e.text || '').split('\n');
      if (e.single) {
        const w = Math.max(4, measure(e.text || ' ', e.size, e.family, e.bold, e.italic));
        const x = e.align === 'center' ? (x0 + x1) / 2 - w / 2 : e.align === 'right' ? x1 - w : x0;
        return { x, y: y0, w, h: Math.max(y1 - y0, e.lh), lineH: Math.max(y1 - y0, e.size * 1.05) };
      }
      const w = Math.max(20, x1 - x0);
      let n = 0;     // rough wrapped line count
      lines.forEach(l => { n += Math.max(1, Math.ceil(measure(l || ' ', e.size, e.family, e.bold, e.italic) / w)); });
      return { x: x0, y: y0 + (e.asc ? 0 : 0), w, h: Math.max(y1 - y0, n * e.lh), lineH: e.lh };
    }
    function setOv(key, patch, { undoable = true } = {}) {
      if (undoable) pushUndo();
      const b = blockOf(key);
      const o = { ...(model.ov[key] || {}), ...patch };
      // drop values equal to the original so an undone edit is "no edit"
      if (o.text === b.text) delete o.text;
      ['size', 'color', 'bold', 'italic', 'align'].forEach(k => { if (o[k] === b[k] || o[k] == null) delete o[k]; });
      if (o.font === 'original' || !o.font) delete o.font;
      if (o.bbox && o.bbox.every((v, i) => Math.abs(v - b.bbox[i]) < 0.3)) delete o.bbox;
      if (!o.deleted) delete o.deleted;
      if (Object.keys(o).length) model.ov[key] = o; else delete model.ov[key];
    }

    // ── pages / rendering ──────────────────────────────────────────────────
    const dpr = () => Math.max(1, window.devicePixelRatio || 1);
    const neededScale = () => clamp(zoom * dpr(), 0.5, 4);
    let renderQ = [], pumping = false;
    function wantPage(p) {
      loadData(p);
      const need = neededScale(), have = rendered.get(p);
      if (have && have >= need * 0.85 && have <= need * 2.2) return;
      if (!renderQ.includes(p)) renderQ.push(p);
      pump();
    }
    async function pump() {
      if (pumping) return;
      pumping = true;
      try {
        while (renderQ.length && !closed) {
          const p = renderQ.shift(), sc = neededScale(), file = src;
          const r = await SFM.pdfeditPagePng(file, p, sc).catch(() => null);
          if (closed || file !== src) return;
          const el = pageEls[p];
          if (r && r.ok && el) {
            const img = el.querySelector('.an-bg'); img.src = r.data_url; img.classList.add('on');
            el.querySelector('.an-ph')?.remove();
            rendered.set(p, sc);
          }
        }
      } finally { pumping = false; }
    }
    function loadData(p) {
      if (pageData.has(p)) return pageData.get(p);
      const file = src;
      const pr = SFM.pdfeditBlocks(file, p).then(r => {
        if (file !== src) return;
        let d;
        if (r && r.ok) d = { blocks: r.blocks || [], images: r.images || [] };
        else if (r && r.code === 'scanned_page') d = { blocks: [], images: [], scanned: true };
        else d = { blocks: [], images: [], error: (r && r.error) || 'Could not read the text on this page' };
        pageData.set(p, d);
        renderPage(p);
        if (p === curPage) hint();
        return d;
      }).catch(() => { pageData.set(p, { blocks: [], images: [], error: 'Could not read the text on this page' }); });
      pageData.set(p, pr);
      return pr;
    }
    const px = v => (v * zoom) + 'px';

    function renderPage(p) {
      const el = pageEls[p]; if (!el) return;
      const d = pageData.get(p);
      const layer = el.querySelector('.pe-layer');
      el.querySelector('.pe-scan').classList.toggle('hidden', !(d && d.scanned));
      if (!d || d instanceof Promise) { layer.innerHTML = ''; return; }
      let h = '';
      // images (under the text)
      d.images.forEach(im => {
        const k = keyOf(p, im.id), op = model.imgs[k];
        const [x0, y0, x1, y1] = im.bbox;
        const isSel = sel && sel.kind === 'image' && sel.key === k;
        let inner = '';
        if (op) inner = `<div class="pe-patch"></div>` + (op.op === 'replace' ? `<img class="pe-newimg" src="${op.data_url}" alt="">` : '');
        h += `<div class="pe-item pe-imgbox${im.background ? ' pe-bgimg' : ''}${isSel ? ' is-sel' : ''}" data-kind="image" data-k="${esc(k)}"
               style="left:${px(x0)};top:${px(y0)};width:${px(x1 - x0)};height:${px(y1 - y0)}">${inner}</div>`;
      });
      // text blocks
      d.blocks.forEach(b => {
        const k = keyOf(p, b.id), s = { kind: 'block', key: k }, e = eff(s);
        const isSel = sel && sel.kind === 'block' && sel.key === k, isEd = editing && editing.key === k;
        const [x0, y0, x1, y1] = b.bbox;
        if (e.changed || isEd) h += `<div class="pe-patch pe-patch-abs" style="left:${px(x0 - 1)};top:${px(y0 - 1)};width:${px(x1 - x0 + 2)};height:${px(y1 - y0 + 2)}"></div>`;
        const tb = textBox(e);
        const box = e.changed && !e.deleted ? [tb.x, tb.y, tb.x + tb.w, tb.y + tb.h] : b.bbox;
        let inner = '';
        if (e.changed && !e.deleted && !isEd) inner = textHtml(e, tb);
        h += `<div class="pe-item pe-block${b.editable ? '' : ' pe-locked'}${isSel ? ' is-sel' : ''}${e.deleted ? ' is-deleted' : ''}${isEd ? ' is-editing' : ''}"
               data-kind="block" data-k="${esc(k)}" title="${b.editable ? '' : 'Rotated text can be deleted but not edited'}"
               style="left:${px(box[0])};top:${px(box[1])};width:${px(box[2] - box[0])};height:${px(box[3] - box[1])}">${inner}${isSel && !isEd && !e.deleted ? '<span class="pe-handle" data-h="e"></span>' : ''}</div>`;
      });
      // added text boxes
      model.adds.filter(a => a.page === p).forEach(a => {
        const s = { kind: 'add', key: a.id }, e = eff(s), tb = textBox(e);
        const isSel = sel && sel.kind === 'add' && sel.key === a.id, isEd = editing && editing.key === a.id;
        h += `<div class="pe-item pe-block pe-added${isSel ? ' is-sel' : ''}${isEd ? ' is-editing' : ''}" data-kind="add" data-k="${esc(a.id)}"
               style="left:${px(tb.x)};top:${px(tb.y)};width:${px(tb.w)};height:${px(tb.h)}">${isEd ? '' : textHtml(e, tb)}${isSel && !isEd ? '<span class="pe-handle" data-h="e"></span>' : ''}</div>`;
      });
      layer.innerHTML = h;
      if (editing && editing.page === p) layer.appendChild(editing.ta);
      const tb = thumbEls[p];
      if (tb) {
        const n = Object.keys(model.ov).filter(k => +k.split(':')[0] === p).length + model.adds.filter(a => a.page === p).length +
                  Object.keys(model.imgs).filter(k => +k.split(':')[0] === p).length;
        const c = tb.querySelector('.an-thumb-n'); c.textContent = n || ''; c.classList.toggle('hidden', !n);
      }
    }
    function textHtml(e, tb) {
      const st = `font-family:${e.family};font-size:${px(e.size)};line-height:${px(tb.lineH)};color:${e.color};` +
        `font-weight:${e.bold ? 700 : 400};font-style:${e.italic ? 'italic' : 'normal'};text-align:${e.align};` +
        `white-space:${e.single ? 'pre' : 'pre-wrap'}`;
      return `<div class="pe-text" style="${st}">${esc(e.text)}</div>`;
    }
    function renderAll() { pages().forEach((_, p) => renderPage(p)); syncToolbar(); status(); }

    function buildPages() {
      pagesEl.innerHTML = '';
      pageEls.length = 0;
      pages().forEach((pg, p) => {
        const el = document.createElement('div');
        el.className = 'an-page pe-page';
        el.dataset.p = p;
        el.innerHTML = `<img class="an-bg" alt="" draggable="false"><div class="an-ph">Loading page ${p + 1}…</div>
          <div class="pe-layer"></div>
          <div class="pe-scan hidden">${ic('scan', 16)}<span>${esc(SCAN_MSG)}</span></div>`;
        pagesEl.appendChild(el);
        pageEls.push(el);
      });
      if (io) io.disconnect();
      io = new IntersectionObserver(ents => ents.forEach(en => { if (en.isIntersecting) wantPage(+en.target.dataset.p); }), { root: view, rootMargin: '300px' });
      pageEls.forEach(el => io.observe(el));
      const th = $('#pe-thumbs');
      thumbEls.length = 0;
      th.innerHTML = '';
      th.classList.toggle('hidden', pages().length < 2);
      if (pages().length > 1) {
        th.innerHTML = pages().map((pg, p) => `
          <button class="an-thumb" data-p="${p}" title="Page ${p + 1}">
            <span class="an-thumb-img" style="aspect-ratio:${pg.w}/${pg.h}"><img alt="" draggable="false"></span>
            <span class="an-thumb-label">${p + 1}<span class="an-thumb-n hidden"></span></span>
          </button>`).join('');
        th.querySelectorAll('.an-thumb').forEach(b => thumbEls.push(b));
        const file = src;
        const tio = new IntersectionObserver(ents => ents.forEach(async en => {
          if (!en.isIntersecting) return;
          tio.unobserve(en.target);
          const p = +en.target.dataset.p;
          const r = await SFM.pdfeditPagePng(file, p, 110 / Math.max(1, pages()[p].w)).catch(() => null);
          if (r && r.ok && file === src) en.target.querySelector('img').src = r.data_url;
        }), { root: th, rootMargin: '200px' });
        thumbEls.forEach(b => tio.observe(b));
      }
      $('#pe-pagenav').classList.toggle('hidden', pages().length < 2);
      $('#pe-pgn').textContent = '/ ' + pages().length;
      applyZoom();
    }
    $('#pe-thumbs').addEventListener('click', e => { const b = e.target.closest('.an-thumb'); if (b) goPage(+b.dataset.p); });
    let io = null;

    // ── zoom / pages ───────────────────────────────────────────────────────
    const zbase = () => 96 / 72;
    function applyZoom() {
      pages().forEach((pg, p) => {
        const el = pageEls[p]; if (!el) return;
        el.style.width = Math.round(pg.w * zoom) + 'px';
        el.style.height = Math.round(pg.h * zoom) + 'px';
      });
      $('#pe-zoom').textContent = Math.round(zoom / zbase() * 100) + '%';
      if (info) { visiblePages().forEach(wantPage); renderAll(); positionEditor(); }
    }
    function setZoom(z, keepCenter = true) {
      const old = zoom;
      z = clamp(z, 0.1, 8);
      if (Math.abs(z - old) < 1e-4) return;
      const cx = view.scrollLeft + view.clientWidth / 2, cy = view.scrollTop + view.clientHeight / 2;
      zoom = z;
      applyZoom();
      if (keepCenter) { view.scrollLeft = cx * z / old - view.clientWidth / 2; view.scrollTop = cy * z / old - view.clientHeight / 2; }
    }
    const zoomSteps = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6];
    function zoomStep(dir) {
      const cur = zoom / zbase();
      const next = dir > 0 ? zoomSteps.find(s => s > cur + 0.01) : [...zoomSteps].reverse().find(s => s < cur - 0.01);
      if (next) setZoom(next * zbase());
    }
    function fitWidth() { const mw = Math.max(...pages().map(p => p.w)); setZoom((view.clientWidth - 64) / mw, false); }
    function fitPage() {
      const pg = pages()[curPage] || pages()[0];
      setZoom(Math.min((view.clientWidth - 64) / pg.w, (view.clientHeight - 48) / pg.h), false);
      goPage(curPage);
    }
    function visiblePages() {
      const vr = view.getBoundingClientRect(), out = [];
      pageEls.forEach((el, i) => { const r = el.getBoundingClientRect(); if (r.bottom > vr.top - 200 && r.top < vr.bottom + 200) out.push(i); });
      return out;
    }
    function goPage(p) {
      p = clamp(p, 0, pages().length - 1);
      const el = pageEls[p]; if (!el) return;
      view.scrollTop = el.offsetTop - 16;
      setCur(p);
    }
    function setCur(p) {
      if (p === curPage && $('#pe-pg').value === String(p + 1)) return;
      curPage = p;
      if (document.activeElement !== $('#pe-pg')) $('#pe-pg').value = p + 1;
      thumbEls.forEach((b, i) => b.classList.toggle('active', i === p));
      thumbEls[p]?.scrollIntoView({ block: 'nearest' });
      hint();
    }
    view.addEventListener('scroll', () => {
      const vr = view.getBoundingClientRect(), mid = vr.top + vr.height / 3;
      let best = 0;
      pageEls.forEach((el, i) => { if (el.getBoundingClientRect().top <= mid) best = i; });
      setCur(best);
    }, { passive: true });
    view.addEventListener('wheel', e => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setZoom(zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
    }, { passive: false });
    $('#pe-pg').addEventListener('change', () => goPage((+$('#pe-pg').value || 1) - 1));
    $('#pe-pg').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); goPage((+$('#pe-pg').value || 1) - 1); view.focus(); } });

    // ── hint / status / toolbar sync ───────────────────────────────────────
    function hint() {
      const d = pageData.get(curPage);
      let t, icon = 'info';
      if (d && d.scanned) { t = SCAN_MSG + ' You can still add new text on top.'; icon = 'scan'; }
      else if (d && d.error) { t = d.error; icon = 'alert-circle'; }
      else if (mode === 'add') { t = 'Click on the page where the new text should go'; icon = 'type'; }
      else t = 'Click text to edit it · drag to move · Enter = new line · Esc cancels · click outside to keep';
      $('#pe-hint').innerHTML = `${ic(icon, 14)}<span>${esc(t)}</span>`;
      $('#pe-hint').classList.toggle('pe-hint-warn', !!(d && d.scanned));
    }
    function status() {
      const n = Object.keys(model.ov).length + model.adds.length + Object.keys(model.imgs).length;
      $('#pe-status').textContent = n ? `${n} change${n === 1 ? '' : 's'} · unsaved` : 'No changes';
      $('#pe-status').className = 'pill ' + (n ? 'pill-yellow' : 'pill-neutral');
      ov.querySelector('[data-a="undo"]').disabled = busy || !undoStack.length;
      ov.querySelector('[data-a="redo"]').disabled = busy || !redoStack.length;
    }
    function syncToolbar() {
      const s = sel, e = s && s.kind !== 'image' ? eff(s) : null;
      const textOn = !!(e && !e.deleted && e.editable !== false);
      $('#pe-style').classList.toggle('is-off', !textOn && !(s && s.kind === 'block'));
      $$('#pe-style select, #pe-style input, #pe-style button').forEach(c => { c.disabled = busy || !textOn; });
      ov.querySelector('[data-a="delete"]').disabled = busy || !(s && s.kind !== 'image' && !(e && e.deleted));
      $('#pe-imgtools').classList.toggle('hidden', !(s && s.kind === 'image'));
      const imOp = s && s.kind === 'image' ? model.imgs[s.key] : null;
      const im = s && s.kind === 'image' ? imageOf(s.key) : null;
      $$('#pe-imgtools button').forEach(b => { b.disabled = busy || !im || !im.replaceable || (b.dataset.a === 'img-delete' && imOp && imOp.op === 'delete'); });
      if (e) {
        $('#pe-font').value = e.font || 'original';
        if (document.activeElement !== $('#pe-size')) $('#pe-size').value = r2(e.size);
        $('#pe-color').value = e.color || '#000000';
        $('#pe-color-sw').style.background = e.color || '#000000';
        ov.querySelector('[data-a="bold"]').classList.toggle('active', !!e.bold);
        ov.querySelector('[data-a="bold"]').setAttribute('aria-pressed', !!e.bold);
        ov.querySelector('[data-a="italic"]').classList.toggle('active', !!e.italic);
        ov.querySelector('[data-a="italic"]').setAttribute('aria-pressed', !!e.italic);
        ['left', 'center', 'right'].forEach(a => ov.querySelector(`[data-a="align-${a}"]`).classList.toggle('active', e.align === a));
      } else {
        ['bold', 'italic', 'align-left', 'align-center', 'align-right'].forEach(a => ov.querySelector(`[data-a="${a}"]`).classList.remove('active'));
        $('#pe-color-sw').style.background = 'transparent';
      }
      ov.querySelector('[data-a="mode-select"]').classList.toggle('active', mode === 'select');
      ov.querySelector('[data-a="mode-add"]').classList.toggle('active', mode === 'add');
    }
    function setMode(m) {
      commitEdit();
      mode = m;
      root.dataset.mode = m;
      if (m === 'add' && sel) { sel = null; renderAll(); }
      syncToolbar(); hint();
    }

    // ── selection / inline editor ──────────────────────────────────────────
    function select(s) {
      commitEdit();
      const old = sel;
      sel = s;
      if (old) renderPage(pageOfSel(old));
      if (s) renderPage(pageOfSel(s));
      syncToolbar();
    }
    function pageOfSel(s) {
      if (s.kind === 'add') { const a = model.adds.find(x => x.id === s.key); return a ? a.page : 0; }
      return +s.key.split(':')[0];
    }
    function startEdit(s) {
      const e = eff(s);
      if (!e || e.deleted) return;
      if (e.editable === false) { App.toast('Rotated text can be deleted, but not edited', 'info'); return; }
      commitEdit();
      sel = s;
      const ta = document.createElement('textarea');
      ta.className = 'pe-editbox';
      ta.spellcheck = false;
      ta.value = e.text;
      editing = { kind: s.kind, key: s.key, ta, before: e.text, page: e.page };
      ta.addEventListener('input', () => positionEditor());
      ta.addEventListener('keydown', ev => {
        if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); cancelEdit(); view.focus({ preventScroll: true }); }
        else if ((ev.ctrlKey || ev.metaKey) && (ev.key === 's' || ev.key === 'S')) { ev.preventDefault(); ev.stopPropagation(); commitEdit(); save(); }
        // Enter = new line (textarea default)
      });
      ta.addEventListener('blur', () => setTimeout(() => {
        // focus moved to a toolbar control: keep editing state until it is done
        if (editing && editing.ta === ta && !ov.contains(document.activeElement)) commitEdit();
      }, 0));
      renderPage(e.page);
      positionEditor();
      syncToolbar();
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
      if (s.kind === 'block') ta.select();
    }
    function positionEditor() {
      if (!editing) return;
      const s = { kind: editing.kind, key: editing.key };
      const e0 = eff(s); if (!e0) return;
      const e = { ...e0, text: editing.ta.value };
      if (e.kind !== 'add' && s.kind === 'block') e.single = e0.single && !editing.ta.value.includes('\n');
      const tb = textBox(e);
      const ta = editing.ta;
      Object.assign(ta.style, {
        left: px(tb.x - 2), top: px(tb.y - 1), width: px(tb.w + 6), height: px(tb.h + 2),
        fontFamily: e.family, fontSize: px(e.size), lineHeight: px(tb.lineH), color: e.color,
        fontWeight: e.bold ? 700 : 400, fontStyle: e.italic ? 'italic' : 'normal', textAlign: e.align,
        whiteSpace: e.single ? 'pre' : 'pre-wrap', padding: `${px(0.5)} ${px(2)}`,
      });
    }
    function commitEdit() {
      if (!editing) return;
      const ed = editing; editing = null;
      const val = ed.ta.value.replace(/\r\n?/g, '\n');
      ed.ta.remove();
      if (val !== ed.before) {
        if (ed.kind === 'block') {
          if (!val.trim()) setOv(ed.key, { text: blockOf(ed.key).text, deleted: true });
          else setOv(ed.key, { text: val });
        } else {
          pushUndo();
          const a = model.adds.find(x => x.id === ed.key);
          if (a) { if (!val.trim()) { model.adds = model.adds.filter(x => x !== a); sel = null; } else a.text = val; }
        }
      } else if (ed.kind === 'add' && !val.trim()) {
        model.adds = model.adds.filter(x => x.id !== ed.key); sel = null;
      }
      renderPage(ed.page); syncToolbar(); status();
    }
    function cancelEdit() {
      if (!editing) return;
      editing.ta.value = editing.before;
      commitEdit();
    }

    // ── style changes (toolbar) ────────────────────────────────────────────
    function applyStyle(patch) {
      if (!sel || sel.kind === 'image') return;
      if (sel.kind === 'block') setOv(sel.key, patch);
      else {
        pushUndo();
        const a = model.adds.find(x => x.id === sel.key); if (!a) return;
        Object.assign(a, patch);
        ['font', 'size', 'color', 'bold', 'italic'].forEach(k => { if (k in patch) store('add.' + k, patch[k]); });
      }
      renderPage(pageOfSel(sel)); positionEditor(); syncToolbar(); status();
      if (editing) editing.ta.focus();
    }
    $('#pe-font').addEventListener('change', () => applyStyle({ font: $('#pe-font').value }));
    $('#pe-size').addEventListener('change', () => { const v = parseFloat($('#pe-size').value); if (v >= 2 && v <= 400) applyStyle({ size: r2(v) }); });
    $('#pe-color').addEventListener('input', () => { $('#pe-color-sw').style.background = $('#pe-color').value; });
    $('#pe-color').addEventListener('change', () => applyStyle({ color: $('#pe-color').value }));

    function deleteSel() {
      if (!sel) return;
      if (editing) { const ed = editing; editing = null; ed.ta.remove(); }
      if (sel.kind === 'block') setOv(sel.key, { deleted: true });
      else if (sel.kind === 'add') { pushUndo(); model.adds = model.adds.filter(a => a.id !== sel.key); sel = null; }
      else if (sel.kind === 'image') return imgOp('delete');
      renderAll();
    }
    async function imgOp(op) {
      if (!sel || sel.kind !== 'image') return;
      const key = sel.key, im = imageOf(key); if (!im) return;
      if (op === 'delete') { pushUndo(); model.imgs[key] = { op: 'delete' }; renderAll(); return; }
      const pick = await SFM.annotPickPhoto().catch(() => null);
      if (!pick || !pick.ok || !pick.path) return;
      const r = await SFM.annotPhotoLoad(pick.path, 2000).catch(() => null);
      if (!r || !r.ok) { App.toast(esc((r && r.error) || 'Could not open the image'), 'error'); return; }
      pushUndo();
      model.imgs[key] = { op: 'replace', data_url: r.data_url, name: r.name };
      renderAll();
    }

    // ── toolbar clicks: match buttons by their own class (never the root) ──
    ov.querySelector('.pe-bar').addEventListener('click', e => {
      const b = e.target.closest('.pe-tool');
      if (!b || b.disabled || !ov.contains(b)) return;
      const a = b.dataset.a;
      const keepFocus = () => { if (editing) editing.ta.focus(); };
      if (a === 'mode-select') setMode('select');
      else if (a === 'mode-add') setMode(mode === 'add' ? 'select' : 'add');
      else if (a === 'bold') { const e2 = sel && eff(sel); if (e2) applyStyle({ bold: !e2.bold }); }
      else if (a === 'italic') { const e2 = sel && eff(sel); if (e2) applyStyle({ italic: !e2.italic }); }
      else if (a.startsWith('align-')) applyStyle({ align: a.slice(6) });
      else if (a === 'delete') deleteSel();
      else if (a === 'img-replace') imgOp('replace');
      else if (a === 'img-delete') imgOp('delete');
      else if (a === 'undo') undo();
      else if (a === 'redo') redo();
      else if (a === 'zin') { zoomStep(1); keepFocus(); }
      else if (a === 'zout') { zoomStep(-1); keepFocus(); }
      else if (a === 'z100') setZoom(zbase());
      else if (a === 'fitw') fitWidth();
      else if (a === 'fitp') fitPage();
      else if (a === 'prev') goPage(curPage - 1);
      else if (a === 'next') goPage(curPage + 1);
    });
    // toolbar buttons must not steal focus from the inline editor
    ov.querySelector('.pe-bar').addEventListener('mousedown', e => {
      if (editing && e.target.closest('button.pe-tool')) e.preventDefault();
    });

    // ── pointer: click = edit, drag = move, right handle = width ───────────
    let drag = null;
    function toUnits(e, p) {
      const r = pageEls[p].getBoundingClientRect(), pg = pages()[p];
      return [r2((e.clientX - r.left) / r.width * pg.w), r2((e.clientY - r.top) / r.height * pg.h)];
    }
    pagesEl.addEventListener('pointerdown', e => {
      if (e.button !== 0 || busy) return;
      const pageEl = e.target.closest('.pe-page'); if (!pageEl) return;
      if (editing && e.target.closest('.pe-editbox')) return;
      const p = +pageEl.dataset.p;
      const pt = toUnits(e, p);
      const item = e.target.closest('.pe-item');
      commitEdit();
      view.focus({ preventScroll: true });
      if (mode === 'add') {
        const size = parseFloat(store('add.size')) || 12;
        const a = { id: 'add' + Date.now().toString(36) + (++_uid), page: p, text: '',
                    font: store('add.font') || 'Arial', size, color: store('add.color') || '#000000',
                    bold: store('add.bold') === 'true', italic: store('add.italic') === 'true', align: 'left',
                    rect: [pt[0], pt[1] - size * 0.2, Math.min(pages()[p].w - 4, pt[0] + 220), pt[1] + size * 1.2].map(r2) };
        pushUndo();
        model.adds.push(a);
        setMode('select');
        startEdit({ kind: 'add', key: a.id });
        status();
        return;
      }
      if (!item) { if (sel) select(null); drag = { mode: 'pan', sx: e.clientX, sy: e.clientY, sl: view.scrollLeft, st: view.scrollTop }; capture(e); return; }
      const s = { kind: item.dataset.kind, key: item.dataset.k };
      const handle = e.target.closest('.pe-handle');
      const already = sel && sel.kind === s.kind && sel.key === s.key;
      if (!already) select(s);
      if (s.kind === 'image') { drag = null; return; }
      const e0 = eff(s);
      drag = { mode: handle ? 'width' : 'move', s, p, start: pt, bbox: textBoxRect(e0), moved: false, snapped: false };
      capture(e);
    });
    function textBoxRect(e) {
      if (e.kind === 'add' || !e.changed) { if (e.kind !== 'add' && !e.changed) return e.bbox.slice(); }
      const tb = textBox(e); return e.single ? [e.bbox[0], e.bbox[1], e.bbox[2], e.bbox[3]] : [tb.x, tb.y, tb.x + tb.w, tb.y + tb.h];
    }
    function capture(e) { try { pagesEl.setPointerCapture(e.pointerId); } catch (_) {} }
    pagesEl.addEventListener('pointermove', e => {
      if (!drag) return;
      if (drag.mode === 'pan') { view.scrollLeft = drag.sl - (e.clientX - drag.sx); view.scrollTop = drag.st - (e.clientY - drag.sy); return; }
      const pt = toUnits(e, drag.p);
      const dx = pt[0] - drag.start[0], dy = pt[1] - drag.start[1];
      if (!drag.moved && Math.hypot(dx, dy) * zoom < 4) return;
      if (!drag.moved) { drag.moved = true; pushUndo(); }
      const b = drag.bbox;
      let nb;
      if (drag.mode === 'move') nb = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy];
      else nb = [b[0], b[1], Math.max(b[0] + 10, b[2] + dx), b[3]];
      nb = nb.map(r2);
      if (drag.s.kind === 'block') setOv(drag.s.key, { bbox: nb }, { undoable: false });
      else { const a = model.adds.find(x => x.id === drag.s.key); if (a) a.rect = nb; }
      renderPage(drag.p);
    });
    pagesEl.addEventListener('pointerup', () => {
      const d = drag; drag = null;
      if (!d || d.mode === 'pan') return;
      if (!d.moved) startEdit(d.s);       // a click (no drag) edits in place
      else { status(); syncToolbar(); }
    });
    pagesEl.addEventListener('dblclick', e => { const item = e.target.closest('.pe-item'); if (item && item.dataset.kind !== 'image') startEdit({ kind: item.dataset.kind, key: item.dataset.k }); });

    // ── keyboard (case-insensitive: Caps Lock must not break shortcuts) ────
    function onKey(e) {
      if (closed || !document.body.contains(ov)) return;
      if (document.querySelector('.modal-overlay.dlg-top')) return;
      const t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
      const ctrl = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
      e.stopPropagation();
      const handled = () => e.preventDefault();
      if (ctrl && k === 's') { handled(); commitEdit(); save(); return; }
      if (k === 'escape') {
        handled();
        if (editing) { cancelEdit(); return; }
        if (typing) { t.blur(); view.focus(); return; }
        if (mode !== 'select') { setMode('select'); return; }
        if (sel) { select(null); return; }
        tryClose(); return;
      }
      if (typing) return;
      if (ctrl && k === 'z' && !e.shiftKey) { handled(); undo(); return; }
      if (ctrl && (k === 'y' || (k === 'z' && e.shiftKey))) { handled(); redo(); return; }
      if (ctrl && (k === '=' || k === '+')) { handled(); zoomStep(1); return; }
      if (ctrl && k === '-') { handled(); zoomStep(-1); return; }
      if (ctrl && k === '0') { handled(); fitWidth(); return; }
      if (ctrl || e.altKey) return;
      if ((k === 'delete' || k === 'backspace') && sel) { handled(); deleteSel(); return; }
      if (k === 'enter' && sel && sel.kind !== 'image') { handled(); startEdit(sel); return; }
      if (k === 'pagedown') { handled(); goPage(curPage + 1); return; }
      if (k === 'pageup') { handled(); goPage(curPage - 1); return; }
      if (k === 't') { handled(); setMode('add'); return; }
      if (k === 'v') { handled(); setMode('select'); return; }
    }
    document.addEventListener('keydown', onKey, true);

    // ── save / close ───────────────────────────────────────────────────────
    const overChosen = () => (ov.querySelector('input[name="pe-out"]:checked') || {}).value === 'over';
    $$('input[name="pe-out"]').forEach(rb => rb.addEventListener('change', () => {
      const over = overChosen();
      ['#pe-name', '#pe-dir', '#pe-browse'].forEach(s => { $(s).disabled = over; });
      $('#pe-fields').classList.toggle('is-dim', over);
      Dialogs.setBtn($('#pe-save'), over ? 'Save over original…' : 'Save', over ? 'refresh' : 'save');
    }));
    $('#pe-browse').addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder').catch(() => null);
      if (r && r.ok && r.path) $('#pe-dir').value = r.path;
    });
    $('#pe-name').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
    function setBusy(on) {
      busy = on;
      $('#pe-progress').classList.toggle('on', on);
      $$('.an-foot .btn').forEach(b => { b.disabled = on; });
      $('#pe-save').classList.toggle('is-loading', on);
      syncToolbar(); status();
    }
    function buildEdits() {
      const out = [];
      Object.entries(model.ov).forEach(([k, o]) => {
        const [p, id] = k.split(':'), b = blockOf(k); if (!b) return;
        if (o.deleted) { out.push({ type: 'delete', page: +p, block_id: id, orig_text: b.text }); return; }
        const e = { type: 'text', page: +p, block_id: id, orig_text: b.text, new_text: o.text != null ? o.text : b.text };
        ['font', 'size', 'color', 'bold', 'italic', 'align', 'bbox'].forEach(x => { if (o[x] != null) e[x] = o[x]; });
        out.push(e);
      });
      model.adds.forEach(a => { if (a.text.trim()) out.push({ type: 'add_text', page: a.page, rect: a.rect, text: a.text, font: a.font, size: a.size, color: a.color, bold: a.bold, italic: a.italic, align: a.align }); });
      Object.entries(model.imgs).forEach(([k, op]) => {
        const [p, id] = k.split(':');
        out.push(op.op === 'delete' ? { type: 'delete_image', page: +p, image_id: id }
                                     : { type: 'replace_image', page: +p, image_id: id, data_url: op.data_url, fit: 'contain' });
      });
      return out;
    }
    function notice(html, tone = 'warning') {
      const n = $('#pe-notice');
      if (!html) { n.classList.add('hidden'); n.innerHTML = ''; return; }
      n.className = `pe-notice callout ${tone}`;
      n.innerHTML = `${ic(tone === 'success' ? 'check-circle' : 'alert-triangle', 16)}<div class="callout-body">${html}</div>
        <button class="icon-btn icon-btn-sm pe-notice-x" aria-label="Dismiss" title="Dismiss">${ic('x', 14)}</button>`;
      n.querySelector('.pe-notice-x').addEventListener('click', () => notice(''));
    }
    async function save() {
      if (busy || !info) return;
      commitEdit();
      const edits = buildEdits();
      if (!edits.length) { App.toast('No changes to save', 'info'); return; }
      const over = overChosen();
      let out = '';
      if (!over) {
        const nm = cleanName($('#pe-name').value).replace(/\.pdf$/i, '');
        const dir = $('#pe-dir').value.trim();
        if (!nm || !dir) { App.toast('Enter a file name and folder', 'warning'); return; }
        const de = await SFM.pathExists(dir).catch(() => null);
        if (!de || !de.ok || !de.is_dir) { App.toast('Choose an existing output folder', 'warning'); return; }
        out = joinP(dir, nm + '.pdf');
      }
      if (over && !(await Dialogs.confirm({ title: 'Save over original', icon: 'alert-triangle', tone: 'warning', okLabel: 'Save over original', okIcon: 'refresh',
            message: `Write your text changes into “${baseOf(src)}”?`,
            detail: 'The original text is removed from the file. The current version is first copied to the “_to_review” folder next to it, so nothing is lost.' }))) return;
      setBusy(true);
      App.setStatus('Saving text edits…', true);
      const r = await SFM.pdfeditApply(src, edits, { mode: over ? 'over' : 'new', out_path: out }).catch(e => ({ ok: false, error: String(e) }));
      App.setStatus('Ready');
      setBusy(false);
      if (!r || !r.ok) {
        if (r && r.code === 'stale') App.toast(esc(r.error), 'error', 8000);
        else App.toast(esc((r && r.error) || 'Could not save'), 'error', 8000);
        return;
      }
      const name = baseOf(r.out_path);
      App.toast(over ? `Saved <b>${esc(name)}</b> · backup in _to_review/` : `Saved edited copy → <b>${esc(name)}</b>`, 'success', 5000);
      try { if (FileTree.refreshAndSelect) FileTree.refreshAndSelect(r.out_path); else FileTree.refresh(); } catch (_) {}
      try {
        if (over && Preview.getCurrentPath && (Preview.getCurrentPath() || '').toLowerCase() === src.toLowerCase()) Preview.previewFile(src, '.pdf');
      } catch (_) {}
      // re-render from the saved file
      const subs = r.substituted || [], warns = r.warnings || [];
      await load(r.out_path, curPage);
      const parts = [];
      parts.push(`<div class="callout-title">Saved ${esc(name)}</div>`);
      if (subs.length) {
        const uniq = [...new Map(subs.map(s => [s.wanted + '→' + s.used, s])).values()];
        parts.push(`<div>Some text uses a replacement font because the original isn't fully embedded: ` +
          uniq.map(s => `<b>${esc(s.wanted)}</b> → ${esc(s.used)}`).join(', ') + '.</div>');
      }
      warns.forEach(w => parts.push(`<div>${esc(w)}</div>`));
      notice(parts.join(''), subs.length || warns.length ? 'warning' : 'success');
    }
    function forceClose() {
      if (closed) return;
      closed = true;
      if (editing) { editing.ta.remove(); editing = null; }
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', onResize);
      if (io) io.disconnect();
      ov.remove();
      _open = null;
    }
    async function tryClose() {
      if (busy) return;
      commitEdit();
      if (dirty() && !(await Dialogs.confirm({ title: 'Unsaved text edits', icon: 'alert-triangle', tone: 'warning', okLabel: 'Discard changes',
            message: 'Close without saving your text edits?', detail: 'Your changes will be lost.' }))) return;
      forceClose();
    }
    $('#pe-x').addEventListener('click', tryClose);
    $('#pe-close').addEventListener('click', tryClose);
    $('#pe-save').addEventListener('click', save);
    const onResize = () => { if (info) visiblePages().forEach(wantPage); };
    window.addEventListener('resize', onResize);

    _open = { focus: () => view.focus(), path };

    // ── load (also after Save: re-render from the output) ──────────────────
    async function load(file, keepPage = 0) {
      App.setStatus('Opening PDF…', true);
      const r = await SFM.pdfeditOpen(file).catch(e => ({ ok: false, error: String(e) }));
      App.setStatus('Ready');
      if (closed) return false;
      if (!r || !r.ok) { App.toast(esc((r && r.error) || 'Could not open the PDF'), 'error', 7000); return false; }
      const first = !info;
      src = file;
      info = r;
      pageData = new Map(); rendered.clear(); renderQ = [];
      model = { ov: {}, adds: [], imgs: {} }; undoStack = []; redoStack = [];
      sel = null; editing = null;
      $('#pe-sub').textContent = baseOf(file); $('#pe-sub').title = file;
      $('#pe-name').value = stemOf(file).replace(/_edited( \(\d+\))?$/, '') + '_edited';
      $('#pe-dir').value = dirOf(file);
      buildPages();
      if (first) {
        await new Promise(res => requestAnimationFrame(res));
        fitWidth();
        if (zoom > 2.5) setZoom(2.5, false);
      }
      if (keepPage) goPage(keepPage);
      setMode('select');
      renderAll();
      return true;
    }
    if (!(await load(path))) { forceClose(); return; }
    view.focus({ preventScroll: true });
  }

  // =========================================================================
  // ENTRY POINTS — preview toolbar button, details Tools button, Ctrl+Shift+T
  // (context menu: context-menu.js)
  // =========================================================================
  function _currentCandidate() {
    try {
      const sel = FileTree.getSelected ? FileTree.getSelected() : [];
      if (sel && sel.length === 1 && isPdf(sel[0])) return sel[0];
      if (sel && sel.length > 1) return null;
    } catch (_) {}
    try { const p = Preview.getCurrentPath && Preview.getCurrentPath(); if (p && isPdf(p)) return p; } catch (_) {}
    return null;
  }
  function _init() {
    const pb = document.getElementById('preview-edit-text');
    if (pb) {
      pb.addEventListener('click', () => { const p = Preview.getCurrentPath && Preview.getCurrentPath(); if (p) open(p); });
      const sync = () => {
        let p = null; try { p = Preview.getCurrentPath && Preview.getCurrentPath(); } catch (_) {}
        pb.classList.toggle('hidden', !(p && isPdf(p)));
      };
      const nameEl = document.getElementById('preview-file-name');
      if (nameEl && window.MutationObserver) new MutationObserver(() => setTimeout(sync, 0)).observe(nameEl, { childList: true, characterData: true, subtree: true });
      sync();
    }
    document.addEventListener('keydown', e => {
      if (!(e.ctrlKey || e.metaKey) || !e.shiftKey || e.altKey) return;
      if ((e.key || '').toLowerCase() !== 't' && e.code !== 'KeyT') return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (_open || document.querySelector('.modal-overlay')) return;
      e.preventDefault();
      const p = _currentCandidate();
      if (p) open(p); else App.toast('Select one PDF to edit its text', 'info');
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();

  return { open, canEdit: isPdf, isOpen: () => !!_open };
})();
