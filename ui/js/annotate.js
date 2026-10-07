/**
 * annotate.js — full-window annotation editor for PDFs and images.
 *
 *   Annotate.open(path)          open the editor (PDF / JPG / PNG / WEBP / BMP)
 *   Annotate.canAnnotate(path)   true for supported files
 *
 * Tools: select (V), highlight (H), underline (U), strikeout (S), squiggly,
 * pen (P), rectangle (R), ellipse (O), arrow (A), line (L), text box (T),
 * sticky note (C), stamp menu, signature pad, cover box (X), redaction,
 * eraser (E); undo/redo, zoom, page thumbnails, properties + comments panel.
 *
 * Backend: annotate_bridge.py (annot_load / annot_page_png / annot_words /
 * annot_save / annot_signatures …). All coordinates are page units (PDF
 * points, or pixels for images) with the origin at the top-left of the page.
 * Saving writes a NEW file (<name>_annotated.<ext>) unless the user picks
 * "Save over original" — that asks first and backs the old file up into
 * _to_review/ (done by the backend).
 */

var Annotate = (() => {  

  // ── helpers ───────────────────────────────────────────────────────────────
  const IMG_RE = /\.(jpe?g|png|webp|bmp)$/i;
  const isPdf = p => /\.pdf$/i.test(p || '');
  const isImg = p => IMG_RE.test(p || '');
  const canAnnotate = p => isPdf(p) || isImg(p);
  const baseOf = p => String(p || '').split(/[\\/]/).pop();
  const dirOf = p => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const stemOf = p => baseOf(p).replace(/\.[^.]+$/, '');
  const extOf = p => { const m = /\.[^.\\/]+$/.exec(p || ''); return m ? m[0].toLowerCase() : ''; };
  const sepOf = d => (String(d).includes('/') && !String(d).includes('\\')) ? '/' : '\\';
  const joinP = (d, n) => String(d).replace(/[\\/]+$/, '') + sepOf(d) + n;
  const cleanName = n => String(n || '').trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim();
  const ic = (n, s = 16, c = '') => Icons.svg(n, s, c);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const r2 = v => Math.round(v * 100) / 100;
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem('annot.' + key);
      localStorage.setItem('annot.' + key, String(val));
    } catch (_) { return null; }
    return null;
  }
  const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }
  const today = () => new Date().toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });

  // ── tools ─────────────────────────────────────────────────────────────────
  const PALETTE = [
    ['#ffd400', 'Yellow'], ['#22c55e', 'Green'], ['#2563eb', 'Blue'],
    ['#ec4899', 'Pink'], ['#dc2626', 'Red'], ['#111111', 'Black'],
  ];
  const TOOLS = [
    { id: 'select',    icon: 'mouse-pointer', label: 'Select / move', key: 'V', hint: 'Click an annotation to select it · drag to move · handles resize · Del removes' },
    { sep: true },
    { id: 'highlight', icon: 'highlighter',   label: 'Highlight',     key: 'H', hint: 'Drag across text to highlight it (drag on an empty area to mark a region)' },
    { id: 'underline', icon: 'underline',     label: 'Underline',     key: 'U', hint: 'Drag across text to underline it' },
    { id: 'strikeout', icon: 'strikethrough', label: 'Strikeout',     key: 'S', hint: 'Drag across text to strike it out' },
    { id: 'squiggly',  icon: 'squiggly',      label: 'Squiggly',      key: 'Q', hint: 'Drag across text for a squiggly underline' },
    { sep: true },
    { id: 'ink',       icon: 'pen',           label: 'Pen',           key: 'P', hint: 'Draw freehand' },
    { id: 'rect',      icon: 'square',        label: 'Rectangle',     key: 'R', hint: 'Drag to draw a rectangle · Shift for a square' },
    { id: 'ellipse',   icon: 'circle',        label: 'Ellipse',       key: 'O', hint: 'Drag to draw an ellipse · Shift for a circle' },
    { id: 'arrow',     icon: 'arrow-up-right',label: 'Arrow',         key: 'A', hint: 'Drag to draw an arrow · Shift snaps to 45°' },
    { id: 'line',      icon: 'line',          label: 'Line',          key: 'L', hint: 'Drag to draw a line · Shift snaps to 45°' },
    { sep: true },
    { id: 'text',      icon: 'type',          label: 'Text box',      key: 'T', hint: 'Click (or drag a box) and type' },
    { id: 'note',      icon: 'message-square',label: 'Comment',       key: 'C', hint: 'Click to pin a sticky-note comment' },
    { id: 'stamp',     icon: 'stamp',         label: 'Stamp',         key: '',  hint: 'Click on the page to place the stamp', menu: true },
    { id: 'signature', icon: 'signature',     label: 'Signature',     key: 'G', hint: 'Click on the page to place your signature' },
    { sep: true },
    { id: 'cover',     icon: 'cover',         label: 'Cover box',     key: 'X', hint: 'Drag to cover an area with a white or black box (the content stays underneath)' },
    { id: 'redact',    icon: 'redact',        label: 'Redact (removes content)', key: '', hint: 'Drag over content to remove it permanently when you save', danger: true },
    { id: 'eraser',    icon: 'eraser',        label: 'Eraser',        key: 'E', hint: 'Click or drag over annotations to remove them' },
  ];
  const TOOL = Object.fromEntries(TOOLS.filter(t => t.id).map(t => [t.id, t]));
  const TYPE_LABEL = {
    highlight: 'Highlight', underline: 'Underline', strikeout: 'Strikeout', squiggly: 'Squiggly',
    ink: 'Drawing', rect: 'Rectangle', ellipse: 'Ellipse', arrow: 'Arrow', line: 'Line',
    text: 'Text box', note: 'Comment', stamp: 'Stamp', signature: 'Signature', cover: 'Cover box',
    redact: 'Redaction', other: 'Annotation',
  };
  const TYPE_ICON = {
    highlight: 'highlighter', underline: 'underline', strikeout: 'strikethrough', squiggly: 'squiggly',
    ink: 'pen', rect: 'square', ellipse: 'circle', arrow: 'arrow-up-right', line: 'line', text: 'type',
    note: 'message-square', stamp: 'stamp', signature: 'signature', cover: 'cover', redact: 'redact', other: 'file',
  };
  const MARKUP = new Set(['highlight', 'underline', 'strikeout', 'squiggly']);
  const BOXY = new Set(['rect', 'ellipse', 'cover', 'redact', 'text', 'stamp', 'signature']);
  const DEFAULTS = {
    highlight: { color: '#ffd400', opacity: 1 },
    underline: { color: '#2563eb', opacity: 1 },
    strikeout: { color: '#dc2626', opacity: 1 },
    squiggly:  { color: '#ec4899', opacity: 1 },
    ink:       { color: '#dc2626', width: 2, opacity: 1 },
    rect:      { color: '#2563eb', width: 2, opacity: 1, fill: null },
    ellipse:   { color: '#dc2626', width: 2, opacity: 1, fill: null },
    arrow:     { color: '#dc2626', width: 2, opacity: 1 },
    line:      { color: '#111111', width: 2, opacity: 1 },
    text:      { color: '#111111', font_size: 12, opacity: 1, fill: null },
    note:      { color: '#ffd400', opacity: 1 },
    stamp:     { color: '#15803d', font_size: 14, width: 2, opacity: 1 },
    signature: { opacity: 1 },
    cover:     { color: '#ffffff', opacity: 1 },
    redact:    { color: '#000000', opacity: 1 },
  };
  const STAMPS = [
    { label: 'Approved', color: '#15803d' },
    { label: 'Verified', color: '#1d4ed8' },
    { label: 'Received', color: '#0e7490' },
    { label: 'Rejected', color: '#b91c1c' },
    { label: 'Date stamp', color: '#475569', date: true },
  ];

  function toolStyle(tool) {
    const d = { ...(DEFAULTS[tool] || {}) };
    try { Object.assign(d, JSON.parse(store('style.' + tool) || '{}')); } catch (_) {}
    return d;
  }
  function saveToolStyle(tool, st) {
    const keep = {};
    ['color', 'width', 'opacity', 'font_size', 'fill'].forEach(k => { if (k in st) keep[k] = st[k]; });
    store('style.' + tool, JSON.stringify(keep));
  }

  let _open = null;          // the editor that is open (one at a time)
  let _uid = 0;
  const newId = () => 'n' + Date.now().toString(36) + (++_uid);

  // =========================================================================
  // EDITOR
  // =========================================================================
  async function open(path) {
    if (!path || !canAnnotate(path)) { App.toast('Annotate works with PDFs and images (JPG, PNG, WEBP, BMP)', 'info'); return; }
    if (_open) { _open.focus(); return; }

    const ov = document.createElement('div');
    ov.className = 'modal-overlay an-overlay';
    ov.innerHTML = `
      <div class="an-editor" role="dialog" aria-label="Annotate">
        <div class="an-head">
          <span class="modal-head-icon">${ic('annotate', 18)}</span>
          <div class="modal-heading">
            <h2 class="modal-title">Annotate</h2>
            <div class="modal-subtitle" title="${esc(path)}">${esc(baseOf(path))}</div>
          </div>
          <span class="pill pill-neutral" id="an-status"></span>
          <button class="modal-close" id="an-x" aria-label="Close" title="Close (Esc)">${ic('x')}</button>
        </div>
        <div class="an-bar" role="toolbar" aria-label="Annotation tools">
          <div class="an-tools" id="an-tools"></div>
          <span class="toolbar-sep"></span>
          <div class="an-tb-group">
            <button class="icon-btn" data-a="undo" data-tooltip="Undo · Ctrl+Z" aria-label="Undo">${ic('undo')}</button>
            <button class="icon-btn" data-a="redo" data-tooltip="Redo · Ctrl+Y" aria-label="Redo">${ic('redo')}</button>
          </div>
          <span class="an-spacer"></span>
          <div class="an-tb-group an-pagenav" id="an-pagenav">
            <button class="icon-btn" data-a="prev" data-tooltip="Previous page" aria-label="Previous page">${ic('chevron-up')}</button>
            <span class="an-pageno"><input class="input-text" id="an-pg" value="1" aria-label="Page"><span id="an-pgn">/ 1</span></span>
            <button class="icon-btn" data-a="next" data-tooltip="Next page" aria-label="Next page">${ic('chevron-down')}</button>
          </div>
          <span class="toolbar-sep"></span>
          <div class="an-tb-group">
            <button class="icon-btn" data-a="zout" data-tooltip="Zoom out · Ctrl+−" aria-label="Zoom out">${ic('zoom-out')}</button>
            <button class="btn btn-sm btn-ghost an-zoom" data-a="z100" id="an-zoom" title="Actual size">100%</button>
            <button class="icon-btn" data-a="zin" data-tooltip="Zoom in · Ctrl+=" aria-label="Zoom in">${ic('zoom-in')}</button>
            <button class="icon-btn" data-a="fitw" data-tooltip="Fit width · Ctrl+0" aria-label="Fit width">${ic('fit-width')}</button>
            <button class="icon-btn" data-a="fitp" data-tooltip="Fit page" aria-label="Fit page">${ic('scan-fit')}</button>
          </div>
          <span class="toolbar-sep"></span>
          <button class="icon-btn" data-a="side" data-tooltip="Show / hide panel" aria-label="Toggle side panel">${ic('panel-right')}</button>
        </div>
        <div class="an-hintbar" id="an-hint"></div>
        <div class="an-body">
          <div class="an-thumbs hidden" id="an-thumbs" aria-label="Pages"></div>
          <div class="an-view" id="an-view" tabindex="0"><div class="an-pages" id="an-pages">
            <div class="an-loading"><span class="loading-spinner"></span><span>Opening…</span></div>
          </div></div>
          <aside class="an-side" id="an-side">
            <div class="an-side-tabs segmented segmented-block">
              <button class="seg-btn active" data-tab="props">Properties</button>
              <button class="seg-btn" data-tab="comments">Comments <span class="an-count" id="an-ccount">0</span></button>
            </div>
            <div class="an-side-body" id="an-props"></div>
            <div class="an-side-body hidden" id="an-comments"></div>
          </aside>
        </div>
        <div class="an-foot">
          <div class="segmented an-out-seg" role="radiogroup" aria-label="Save mode">
            <label class="seg-btn"><input type="radio" name="an-out" value="new" checked>${ic('file', 14)}New file</label>
            <label class="seg-btn" title="The current version is copied to _to_review/ first"><input type="radio" name="an-out" value="over">${ic('refresh', 14)}Save over original</label>
          </div>
          <div class="an-foot-fields" id="an-fields">
            <div class="field-row"><input class="input-text an-name" id="an-name" aria-label="File name"><span class="field-suffix" id="an-ext">.pdf in</span></div>
            <div class="field-row"><input class="input-text an-dir" id="an-dir" aria-label="Folder"><button class="btn" id="an-browse">Browse…</button></div>
          </div>
          <label class="switch an-flatten" id="an-flatten-wrap" title="Bake the annotations into the page so they can no longer be edited or removed">
            <input type="checkbox" id="an-flatten"><span class="switch-track"></span>Flatten</label>
          <div class="an-progress progress-block"><div class="progress-wrap"><div class="progress-bar indeterminate"></div></div><div class="progress-label">Saving…</div></div>
          <span class="an-spacer"></span>
          <button class="btn" id="an-close">Close</button>
          <button class="btn btn-primary" id="an-save">${ic('save')}Save</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    const $ = s => ov.querySelector(s);
    const $$ = s => Array.from(ov.querySelectorAll(s));
    const view = $('#an-view'), pagesEl = $('#an-pages');

    // ── state ──────────────────────────────────────────────────────────────
    let doc = null;                 // annot_load result
    let anns = [];
    let initialSig = '[]';
    let tool = 'select', sel = null, editing = null;
    let zoom = 1, curPage = 0;
    let undoStack = [], redoStack = [];
    let busy = false, closed = false;
    let pendingStamp = null, pendingSig = null;
    let stampMenu = null;
    let sideTab = 'props';
    const pageEls = [], thumbEls = [];
    const wordsCache = new Map();
    const rendered = new Map();     // page -> scale rendered
    let propSnap = false;

    const pages = () => (doc ? doc.pages : []);
    const unit = p => { const pg = pages()[p] || { w: 600, h: 800 }; return Math.max(1, Math.max(pg.w, pg.h) / 842); };
    const byId = id => anns.find(a => a.id === id);
    const sig = () => JSON.stringify(anns.map(a => { const c = { ...a }; delete c.excerpt; return c; }));
    const dirty = () => sig() !== initialSig;

    function snapshot() {
      undoStack.push(JSON.stringify(anns));
      if (undoStack.length > 300) undoStack.shift();
      redoStack = [];
    }
    function undo() {
      commitEdit();
      if (!undoStack.length) return;
      redoStack.push(JSON.stringify(anns));
      anns = JSON.parse(undoStack.pop());
      if (sel && !byId(sel)) sel = null;
      renderAll();
    }
    function redo() {
      commitEdit();
      if (!redoStack.length) return;
      undoStack.push(JSON.stringify(anns));
      anns = JSON.parse(redoStack.pop());
      if (sel && !byId(sel)) sel = null;
      renderAll();
    }
    function touch(a) { a.modified = nowIso(); }

    // ── geometry ───────────────────────────────────────────────────────────
    function bbox(a) {
      if (MARKUP.has(a.type) && a.quads && a.quads.length) {
        const q = a.quads;
        return [Math.min(...q.map(r => r[0])), Math.min(...q.map(r => r[1])), Math.max(...q.map(r => r[2])), Math.max(...q.map(r => r[3]))];
      }
      if (a.type === 'ink' && a.points && a.points.length) {
        const pts = a.points.flat();
        const pad = (a.width || 2) / 2;
        return [Math.min(...pts.map(p => p[0])) - pad, Math.min(...pts.map(p => p[1])) - pad, Math.max(...pts.map(p => p[0])) + pad, Math.max(...pts.map(p => p[1])) + pad];
      }
      if ((a.type === 'line' || a.type === 'arrow') && a.line) {
        const [x1, y1, x2, y2] = a.line;
        return [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
      }
      if (a.type === 'note') { const u = unit(a.page) * 20; return [a.rect[0], a.rect[1], a.rect[0] + u, a.rect[1] + u]; }
      return a.rect;
    }
    function translate(o, dx, dy) {
      const a = JSON.parse(JSON.stringify(o));
      if (a.rect) a.rect = [a.rect[0] + dx, a.rect[1] + dy, a.rect[2] + dx, a.rect[3] + dy].map(r2);
      if (a.line) a.line = [a.line[0] + dx, a.line[1] + dy, a.line[2] + dx, a.line[3] + dy].map(r2);
      if (a.points) a.points = a.points.map(s => s.map(p => [r2(p[0] + dx), r2(p[1] + dy)]));
      if (a.quads) a.quads = a.quads.map(q => [q[0] + dx, q[1] + dy, q[2] + dx, q[3] + dy].map(r2));
      return a;
    }
    function scaleTo(o, from, to) {
      const a = JSON.parse(JSON.stringify(o));
      const sx = (to[2] - to[0]) / Math.max(0.01, from[2] - from[0]);
      const sy = (to[3] - to[1]) / Math.max(0.01, from[3] - from[1]);
      const X = x => r2(to[0] + (x - from[0]) * sx), Y = y => r2(to[1] + (y - from[1]) * sy);
      if (a.type === 'ink') a.points = a.points.map(s => s.map(p => [X(p[0]), Y(p[1])]));
      else a.rect = [to[0], to[1], to[2], to[3]].map(r2);
      return a;
    }
    function toUnits(e, p) {
      const el = pageEls[p], r = el.getBoundingClientRect(), pg = pages()[p];
      return [r2((e.clientX - r.left) / r.width * pg.w), r2((e.clientY - r.top) / r.height * pg.h)];
    }
    function pageAt(e) {
      const el = e.target.closest && e.target.closest('.an-page');
      return el ? +el.dataset.p : -1;
    }

    // ── SVG rendering ──────────────────────────────────────────────────────
    function svgFor(a) {
      const op = a.opacity == null ? 1 : a.opacity;
      const c = a.color || '#000000';
      const w = a.width || 0;
      const id = esc(a.id);
      const g = (inner, extra = '') => `<g class="an-a an-t-${a.type}${a.id === sel ? ' is-sel' : ''}" data-id="${id}" opacity="${op}" ${extra}>${inner}</g>`;
      const [x0, y0, x1, y1] = a.rect || [0, 0, 0, 0];
      switch (a.type) {
        case 'highlight':
          return g((a.quads || []).map(q => `<rect x="${q[0]}" y="${q[1]}" width="${q[2] - q[0]}" height="${q[3] - q[1]}" fill="${c}"/>`).join(''), 'style="mix-blend-mode:multiply"');
        case 'underline': case 'strikeout': case 'squiggly':
          return g((a.quads || []).map(q => {
            const h = q[3] - q[1], lw = Math.max(0.8, h * 0.08);
            const hit = `<rect x="${q[0]}" y="${q[1]}" width="${q[2] - q[0]}" height="${h}" fill="transparent"/>`;
            if (a.type === 'underline') return hit + `<line x1="${q[0]}" y1="${q[3] - lw / 2}" x2="${q[2]}" y2="${q[3] - lw / 2}" stroke="${c}" stroke-width="${lw}"/>`;
            if (a.type === 'strikeout') { const y = (q[1] + q[3]) / 2; return hit + `<line x1="${q[0]}" y1="${y}" x2="${q[2]}" y2="${y}" stroke="${c}" stroke-width="${lw}"/>`; }
            const amp = Math.max(1, h * 0.07), step = Math.max(2, h * 0.22);
            let d = `M${q[0]} ${q[3] - amp}`, k = 0;
            for (let x = q[0] + step; x <= q[2]; x += step) d += ` L${x} ${q[3] - amp - (++k % 2 ? amp : 0)}`;
            return hit + `<path d="${d}" fill="none" stroke="${c}" stroke-width="${lw}"/>`;
          }).join(''));
        case 'ink': {
          const d = (a.points || []).map(s => s.length ? 'M' + s.map(p => p[0] + ' ' + p[1]).join(' L') : '').join(' ');
          return g(`<path d="${d}" fill="none" stroke="transparent" stroke-width="${Math.max(w, 6 / zoom) + 4 / zoom}" stroke-linecap="round" stroke-linejoin="round"/>` +
                   `<path d="${d}" fill="none" stroke="${c}" stroke-width="${w || 2}" stroke-linecap="round" stroke-linejoin="round"/>`);
        }
        case 'rect': case 'cover': case 'redact': {
          if (a.type === 'redact') {
            return g(`<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="${c}" fill-opacity=".55" stroke="var(--red)" stroke-width="${1.5 / zoom}" stroke-dasharray="${4 / zoom} ${3 / zoom}"/>` +
                     `<path d="M${x0} ${y0}L${x1} ${y1}M${x1} ${y0}L${x0} ${y1}" stroke="var(--red)" stroke-width="${1 / zoom}" opacity=".7"/>`);
          }
          if (a.type === 'cover') return g(`<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="${c}"/>`);
          const ww = w || 0;
          return g(`<rect x="${x0 + ww / 2}" y="${y0 + ww / 2}" width="${Math.max(0, x1 - x0 - ww)}" height="${Math.max(0, y1 - y0 - ww)}" fill="${a.fill || 'transparent'}" stroke="${c}" stroke-width="${ww}"/>`);
        }
        case 'ellipse': {
          const ww = w || 0;
          return g(`<ellipse cx="${(x0 + x1) / 2}" cy="${(y0 + y1) / 2}" rx="${Math.max(0, (x1 - x0 - ww) / 2)}" ry="${Math.max(0, (y1 - y0 - ww) / 2)}" fill="${a.fill || 'transparent'}" stroke="${c}" stroke-width="${ww}"/>`);
        }
        case 'line': case 'arrow': {
          const [lx1, ly1, lx2, ly2] = a.line || [x0, y0, x1, y1];
          const ww = w || 2;
          let head = '', ex = lx2, ey = ly2;
          if (a.type === 'arrow') {
            const ang = Math.atan2(ly2 - ly1, lx2 - lx1), L = Math.max(6, ww * 4);
            const p1 = [lx2 - L * Math.cos(ang - 0.45), ly2 - L * Math.sin(ang - 0.45)];
            const p2 = [lx2 - L * Math.cos(ang + 0.45), ly2 - L * Math.sin(ang + 0.45)];
            head = `<path d="M${lx2} ${ly2}L${p1[0]} ${p1[1]}L${p2[0]} ${p2[1]}Z" fill="${c}" stroke="${c}" stroke-width="${ww / 2}" stroke-linejoin="round"/>`;
            ex = lx2 - L * 0.8 * Math.cos(ang); ey = ly2 - L * 0.8 * Math.sin(ang);
          }
          return g(`<line x1="${lx1}" y1="${ly1}" x2="${lx2}" y2="${ly2}" stroke="transparent" stroke-width="${Math.max(ww, 6 / zoom) + 4 / zoom}"/>` +
                   `<line x1="${lx1}" y1="${ly1}" x2="${ex}" y2="${ey}" stroke="${c}" stroke-width="${ww}" stroke-linecap="round"/>` + head);
        }
        case 'text': case 'stamp': {
          if (a.id === editing) return '';
          const fs = a.font_size || 12, pad = Math.max(1.5, fs * 0.25);
          const style = a.type === 'stamp'
            ? `font-size:${fs}px;color:${c};border:${w || 2}px solid ${c};border-radius:${fs * 0.3}px;padding:${pad / 2}px ${pad}px`
            : `font-size:${fs}px;color:${c};background:${a.fill || 'transparent'};padding:${pad}px`;
          return g(`<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="transparent"/>` +
            `<foreignObject x="${x0}" y="${y0}" width="${Math.max(1, x1 - x0)}" height="${Math.max(1, y1 - y0)}">` +
            `<div xmlns="http://www.w3.org/1999/xhtml" class="an-ft${a.type === 'stamp' ? ' an-stamp' : ''}" style="${style}">${esc(a.text || '')}</div></foreignObject>`);
        }
        case 'note': {
          const u = unit(a.page), s = 20 * u, x = x0, y = y0;
          return g(`<rect x="${x}" y="${y}" width="${s}" height="${s * 0.8}" rx="${s * 0.15}" fill="${c}" stroke="rgba(0,0,0,.35)" stroke-width="${0.6 * u}"/>` +
                   `<path d="M${x + s * 0.22} ${y + s * 0.8}L${x + s * 0.45} ${y + s * 0.8}L${x + s * 0.22} ${y + s}Z" fill="${c}" stroke="rgba(0,0,0,.35)" stroke-width="${0.6 * u}"/>` +
                   `<path d="M${x + s * 0.22} ${y + s * 0.3}H${x + s * 0.78}M${x + s * 0.22} ${y + s * 0.52}H${x + s * 0.62}" stroke="rgba(0,0,0,.55)" stroke-width="${1.2 * u}" stroke-linecap="round"/>`);
        }
        case 'signature':
          return g(`<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="transparent"/>` +
                   `<image href="${a.image}" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" preserveAspectRatio="xMidYMid meet"/>`);
        default: // other (kept as is)
          return g(`<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="transparent" stroke="var(--text-muted)" stroke-dasharray="${3 / zoom} ${3 / zoom}" stroke-width="${1 / zoom}"/>` +
                   (a.image ? `<image href="${a.image}" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" preserveAspectRatio="none"/>` : ''));
      }
    }
    function handlesFor(a) {
      if (!a || a.id === editing) return '';
      const b = bbox(a), hs = 8 / zoom, pad = 3 / zoom;
      const box = `<rect class="an-selbox" x="${b[0] - pad}" y="${b[1] - pad}" width="${b[2] - b[0] + 2 * pad}" height="${b[3] - b[1] + 2 * pad}" stroke-width="${1.25 / zoom}" stroke-dasharray="${4 / zoom} ${3 / zoom}"/>`;
      const h = (k, x, y) => `<rect class="an-handle" data-handle="${k}" x="${x - hs / 2}" y="${y - hs / 2}" width="${hs}" height="${hs}" rx="${1.5 / zoom}" stroke-width="${1.25 / zoom}"/>`;
      if (a.type === 'line' || a.type === 'arrow') return h('p1', a.line[0], a.line[1]) + h('p2', a.line[2], a.line[3]);
      if (!(BOXY.has(a.type) || a.type === 'ink')) return box;
      const [x0, y0, x1, y1] = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad], mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
      return box + h('nw', x0, y0) + h('n', mx, y0) + h('ne', x1, y0) + h('e', x1, my) +
             h('se', x1, y1) + h('s', mx, y1) + h('sw', x0, y1) + h('w', x0, my);
    }
    function renderPage(p) {
      const el = pageEls[p]; if (!el) return;
      const list = anns.filter(a => a.page === p);
      el.querySelector('.an-anns').innerHTML = list.map(svgFor).join('');
      const s = byId(sel);
      el.querySelector('.an-ui').innerHTML = s && s.page === p ? handlesFor(s) : '';
      const tb = thumbEls[p];
      if (tb) { const n = tb.querySelector('.an-thumb-n'); n.textContent = list.length || ''; n.classList.toggle('hidden', !list.length); }
    }
    function renderAll() {
      pages().forEach((_, p) => renderPage(p));
      renderProps(); renderComments(); status();
    }

    // ── page images (lazy, re-rendered on zoom) ────────────────────────────
    const dpr = () => Math.max(1, window.devicePixelRatio || 1);
    function neededScale() {
      return doc.kind === 'image' ? Math.min(1, zoom * dpr()) : clamp(zoom * dpr(), 0.5, 4);
    }
    let renderQ = [], rendering = false;
    function wantPage(p) {
      const need = neededScale(), have = rendered.get(p);
      if (have && have >= need * 0.85 && have <= need * 2.2) return;
      if (!renderQ.includes(p)) renderQ.push(p);
      pump();
    }
    async function pump() {
      if (rendering) return;
      rendering = true;
      try {
        while (renderQ.length && !closed) {
          const vis = visiblePages();
          renderQ.sort((a, b) => (vis.includes(a) ? 0 : 1) - (vis.includes(b) ? 0 : 1));
          const p = renderQ.shift();
          if (!vis.includes(p) && rendered.has(p)) continue;
          const sc = neededScale();
          const r = await SFM.annotPagePng(doc.source, p, sc).catch(() => null);
          if (closed) return;
          const el = pageEls[p];
          if (r && r.ok && el) {
            const img = el.querySelector('.an-bg');
            img.src = r.data_url; img.classList.add('on');
            el.querySelector('.an-ph')?.remove();
            rendered.set(p, sc);
          } else if (el) {
            const ph = el.querySelector('.an-ph'); if (ph) ph.textContent = 'Could not render this page';
          }
        }
      } finally { rendering = false; }
    }
    function visiblePages() {
      const vr = view.getBoundingClientRect(), out = [];
      pageEls.forEach((el, i) => { const r = el.getBoundingClientRect(); if (r.bottom > vr.top - 200 && r.top < vr.bottom + 200) out.push(i); });
      return out;
    }
    let io = null;

    function buildPages() {
      pagesEl.innerHTML = '';
      pageEls.length = 0;
      pages().forEach((pg, p) => {
        const el = document.createElement('div');
        el.className = 'an-page';
        el.dataset.p = p;
        el.innerHTML = `<img class="an-bg" alt="" draggable="false"><div class="an-ph">Loading page ${p + 1}…</div>
          <svg class="an-svg" viewBox="0 0 ${pg.w} ${pg.h}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <g class="an-anns"></g><g class="an-live"></g><g class="an-ui"></g></svg>`;
        pagesEl.appendChild(el);
        pageEls.push(el);
      });
      io = new IntersectionObserver(ents => ents.forEach(en => { if (en.isIntersecting) wantPage(+en.target.dataset.p); }), { root: view, rootMargin: '300px' });
      pageEls.forEach(el => io.observe(el));
      applyZoom();
      // thumbnails (PDF with several pages)
      const th = $('#an-thumbs');
      if (doc.kind === 'pdf' && pages().length > 1) {
        th.classList.remove('hidden');
        th.innerHTML = pages().map((pg, p) => `
          <button class="an-thumb" data-p="${p}" title="Page ${p + 1}">
            <span class="an-thumb-img" style="aspect-ratio:${pg.w}/${pg.h}"><img alt="" draggable="false"></span>
            <span class="an-thumb-label">${p + 1}<span class="an-thumb-n hidden"></span></span>
          </button>`).join('');
        thumbEls.length = 0;
        th.querySelectorAll('.an-thumb').forEach(b => thumbEls.push(b));
        const tio = new IntersectionObserver(ents => ents.forEach(async en => {
          if (!en.isIntersecting) return;
          tio.unobserve(en.target);
          const p = +en.target.dataset.p;
          const r = await SFM.annotPagePng(doc.source, p, 110 / Math.max(1, pages()[p].w)).catch(() => null);
          if (r && r.ok) en.target.querySelector('img').src = r.data_url;
        }), { root: th, rootMargin: '200px' });
        thumbEls.forEach(b => tio.observe(b));
        th.addEventListener('click', e => { const b = e.target.closest('.an-thumb'); if (b) goPage(+b.dataset.p); });
      }
      $('#an-pagenav').classList.toggle('hidden', pages().length < 2);
      $('#an-pgn').textContent = '/ ' + pages().length;
    }

    // ── zoom / pages ───────────────────────────────────────────────────────
    function applyZoom() {
      pages().forEach((pg, p) => {
        const el = pageEls[p]; if (!el) return;
        el.style.width = Math.round(pg.w * zoom) + 'px';
        el.style.height = Math.round(pg.h * zoom) + 'px';
      });
      $('#an-zoom').textContent = Math.round(zoom / zbase() * 100) + '%';
      if (doc) { visiblePages().forEach(wantPage); renderAll(); }
    }
    function setZoom(z, keepCenter = true) {
      const old = zoom;
      z = clamp(z, 0.05, 8);
      if (Math.abs(z - old) < 1e-4) return;
      const cx = view.scrollLeft + view.clientWidth / 2, cy = view.scrollTop + view.clientHeight / 2;
      zoom = z;
      store('zoom', '');
      applyZoom();
      if (keepCenter) {
        view.scrollLeft = cx * z / old - view.clientWidth / 2;
        view.scrollTop = cy * z / old - view.clientHeight / 2;
      }
      editing && positionEditor();
    }
    function fitWidth() {
      const mw = Math.max(...pages().map(p => p.w));
      setZoom((view.clientWidth - 64) / mw, false);
    }
    function fitPage() {
      const pg = pages()[curPage] || pages()[0];
      setZoom(Math.min((view.clientWidth - 64) / pg.w, (view.clientHeight - 48) / pg.h), false);
      goPage(curPage);
    }
    function goPage(p) {
      p = clamp(p, 0, pages().length - 1);
      const el = pageEls[p]; if (!el) return;
      view.scrollTop = el.offsetTop - 16;
      setCur(p);
    }
    function setCur(p) {
      if (p === curPage && $('#an-pg').value === String(p + 1)) return;
      curPage = p;
      if (document.activeElement !== $('#an-pg')) $('#an-pg').value = p + 1;
      thumbEls.forEach((b, i) => b.classList.toggle('active', i === p));
      thumbEls[p]?.scrollIntoView({ block: 'nearest' });
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
    // PDF units are points: 100% = 96 dpi on screen (1 pt = 4/3 px)
    function zbase() { return doc && doc.kind === 'pdf' ? 96 / 72 : 1; }
    const zoomSteps = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 6, 8];
    function zoomStep(dir) {
      const base = zbase(), cur = zoom / base;
      const next = dir > 0 ? zoomSteps.find(s => s > cur + 0.01) : [...zoomSteps].reverse().find(s => s < cur - 0.01);
      if (next) setZoom(next * base);
    }

    // ── words (text selection for markup) ──────────────────────────────────
    async function wordsFor(p) {
      if (doc.kind !== 'pdf') return [];
      if (wordsCache.has(p)) return wordsCache.get(p);
      const pr = SFM.annotWords(doc.source, p).then(r => (r && r.ok ? r.words : []).map((w, i) => ({ r: w.slice(0, 4), t: w[4], b: w[5], l: w[6], i }))).catch(() => []);
      wordsCache.set(p, pr);
      return pr;
    }
    function nearestWord(ws, x, y, maxDist) {
      let best = -1, bd = Infinity;
      ws.forEach((w, i) => {
        const [x0, y0, x1, y1] = w.r;
        const dx = x < x0 ? x0 - x : x > x1 ? x - x1 : 0, dy = y < y0 ? y0 - y : y > y1 ? y - y1 : 0;
        const d = dx * dx * 0.25 + dy * dy;           // prefer the same line
        if (d < bd) { bd = d; best = i; }
      });
      return (maxDist == null || bd <= maxDist * maxDist) ? best : -1;
    }
    function lineRects(ws, i0, i1) {
      const [a, b] = [Math.min(i0, i1), Math.max(i0, i1)];
      const out = [], text = [];
      let cur = null, key = null;
      for (let i = a; i <= b; i++) {
        const w = ws[i], k = w.b + ':' + w.l;
        text.push(w.t);
        if (k !== key) { if (cur) out.push(cur); cur = w.r.slice(); key = k; }
        else cur = [Math.min(cur[0], w.r[0]), Math.min(cur[1], w.r[1]), Math.max(cur[2], w.r[2]), Math.max(cur[3], w.r[3])];
      }
      if (cur) out.push(cur);
      return { quads: out.map(q => q.map(r2)), text: text.join(' ') };
    }

    // ── create annotations ────────────────────────────────────────────────
    function base(type, p) {
      const st = toolStyle(type);
      return { id: newId(), type, page: p, color: st.color, opacity: st.opacity == null ? 1 : st.opacity,
               width: st.width != null ? st.width * unit(p) : 0, fill: st.fill || null,
               font_size: st.font_size ? st.font_size * unit(p) : undefined,
               text: '', author: doc.author || '', created: nowIso(), modified: nowIso() };
    }
    function add(a, { select = true } = {}) {
      snapshot();
      anns.push(a);
      if (select) sel = a.id;
      renderPage(a.page); renderProps(); renderComments(); status();
      return a;
    }
    function removeIds(ids) {
      if (!ids.length) return;
      snapshot();
      const pg = new Set(anns.filter(a => ids.includes(a.id)).map(a => a.page));
      anns = anns.filter(a => !ids.includes(a.id));
      if (ids.includes(sel)) sel = null;
      pg.forEach(renderPage); renderProps(); renderComments(); status();
    }
    function select(id, { scroll = false } = {}) {
      commitEdit();
      const old = byId(sel);
      sel = id;
      propSnap = false;
      if (old) renderPage(old.page);
      const a = byId(id);
      if (a) {
        renderPage(a.page);
        if (scroll) {
          const el = pageEls[a.page], b = bbox(a);
          const top = el.offsetTop + b[1] * zoom - view.clientHeight / 3;
          view.scrollTo({ top, left: Math.max(0, el.offsetLeft + b[0] * zoom - view.clientWidth / 3), behavior: 'smooth' });
          const g = el.querySelector(`.an-a[data-id="${CSS.escape(id)}"]`);
          if (g) { g.classList.add('an-flash'); setTimeout(() => g.classList.remove('an-flash'), 900); }
        }
      }
      renderProps(); renderComments();
    }

    // ── pointer interaction ───────────────────────────────────────────────
    let drag = null, ptrDown = false;
    pagesEl.addEventListener('pointerdown', async e => {
      if (e.button !== 0 || busy) return;
      const p = pageAt(e); if (p < 0) return;
      if (editing && e.target.closest('.an-editbox')) return;
      commitEdit();
      closeStampMenu();
      const pt = toUnits(e, p);
      const hEl = e.target.closest('[data-handle]');
      // Ignore the live/ghost preview (stamp or signature following the mouse,
      // id "_ghost"/"_live"): it is not an annotation and has no entry in anns.
      const aEl = e.target.closest('.an-a');
      const hitId = aEl && !aEl.closest('.an-live') && byId(aEl.dataset.id) ? aEl.dataset.id : null;
      view.focus({ preventScroll: true });

      if (tool === 'eraser') {
        drag = { mode: 'erase', p, erased: new Set() };
        if (hitId) eraseAt(hitId);
        capture(e); return;
      }
      if (hEl && sel) {
        const a = byId(sel);
        drag = { mode: 'resize', handle: hEl.dataset.handle, p: a.page, start: pt, orig: JSON.parse(JSON.stringify(a)), box: bbox(a), moved: false };
        capture(e); return;
      }
      const hitType = hitId && byId(hitId) ? byId(hitId).type : null;
      if (tool === 'text' && (hitType === 'text' || hitType === 'stamp')) { select(hitId); editText(byId(hitId)); return; }
      if (tool === 'select' || (hitId && (tool === 'note' || tool === 'stamp' || tool === 'signature'))) {
        if (hitId) {
          if (sel !== hitId) select(hitId);
          const a = byId(hitId);
          const movable = a && a.type !== 'other' && !MARKUP.has(a.type);
          drag = { mode: movable ? 'move' : 'none', p: a.page, start: pt, orig: JSON.parse(JSON.stringify(a)), moved: false };
          capture(e);
          if (e.detail === 2 && a && (a.type === 'text' || a.type === 'stamp')) { drag = null; editText(a); }
          if (e.detail === 2 && a && a.type === 'note') { drag = null; showTab('props'); setTimeout(() => $('#an-p-text')?.focus(), 30); }
        } else {
          if (sel) select(null);
          drag = { mode: 'pan', sx: e.clientX, sy: e.clientY, sl: view.scrollLeft, st: view.scrollTop };
          capture(e);
        }
        return;
      }
      // drawing tools
      if (sel) select(null);
      if (MARKUP.has(tool)) {
        ptrDown = true;
        const ws = await wordsFor(p);
        if (!ptrDown) return;                   // released while the words were loading
        const i = nearestWord(ws, pt[0], pt[1], 6 * unit(p));
        drag = { mode: 'markup', p, start: pt, cur: pt, ws, i0: i, i1: i, area: i < 0 };
      } else if (tool === 'ink') {
        drag = { mode: 'ink', p, points: [pt] };
      } else if (tool === 'note') {
        const a = base('note', p);
        a.rect = [pt[0] - 4 * unit(p), pt[1] - 4 * unit(p), pt[0] + 16 * unit(p), pt[1] + 16 * unit(p)].map(r2);
        add(a);
        showTab('props');
        setTimeout(() => $('#an-p-text')?.focus(), 30);
        return;
      } else if (tool === 'stamp' || tool === 'signature') {
        placePending(p, pt);
        return;
      } else {
        drag = { mode: 'shape', p, start: pt, cur: pt };
      }
      capture(e);
      drawLive();
    });
    function capture(e) { try { pagesEl.setPointerCapture(e.pointerId); } catch (_) {} }

    pagesEl.addEventListener('pointermove', e => {
      if (!drag) { ghost(e); hoverErase(e); return; }
      if (drag.mode === 'pan') {
        view.scrollLeft = drag.sl - (e.clientX - drag.sx);
        view.scrollTop = drag.st - (e.clientY - drag.sy);
        return;
      }
      if (drag.mode === 'erase') {
        const el = document.elementFromPoint(e.clientX, e.clientY);
        const g = el && el.closest && el.closest('.an-a');
        if (g) eraseAt(g.dataset.id);
        return;
      }
      if (drag.mode === 'none') return;
      const pt = toUnits(e, drag.p);
      const pg = pages()[drag.p];
      pt[0] = clamp(pt[0], 0, pg.w); pt[1] = clamp(pt[1], 0, pg.h);
      if (drag.mode === 'move' || drag.mode === 'resize') {
        const dx = pt[0] - drag.start[0], dy = pt[1] - drag.start[1];
        if (!drag.moved && Math.hypot(dx, dy) * zoom < 3) return;
        if (!drag.moved) { snapshot(); drag.moved = true; }
        const i = anns.findIndex(a => a.id === drag.orig.id); if (i < 0) return;
        let next;
        if (drag.mode === 'move') {
          const b = bbox(drag.orig);
          const cdx = clamp(dx, -b[0], pg.w - b[2]), cdy = clamp(dy, -b[1], pg.h - b[3]);
          next = translate(drag.orig, cdx, cdy);
        } else next = resized(drag, pt, e.shiftKey);
        next.modified = nowIso();
        anns[i] = next;
        renderPage(drag.p);
        return;
      }
      if (drag.mode === 'ink') {
        const last = drag.points[drag.points.length - 1];
        if (Math.hypot(pt[0] - last[0], pt[1] - last[1]) * zoom < 1.5) return;
        drag.points.push(pt);
      } else if (drag.mode === 'markup') {
        drag.cur = pt;
        if (!drag.area) drag.i1 = nearestWord(drag.ws, pt[0], pt[1]);
      } else {
        drag.cur = constrain(drag.start, pt, e.shiftKey);
      }
      drawLive();
    });
    function endDrag(e) {
      ptrDown = false;
      if (!drag) return;
      const d = drag; drag = null;
      const live = pageEls[d.p] && pageEls[d.p].querySelector('.an-live');
      if (live) live.innerHTML = '';
      if (d.mode === 'move' || d.mode === 'resize') { renderProps(); renderComments(); status(); return; }
      if (d.mode === 'erase' || d.mode === 'pan' || d.mode === 'none') return;
      const p = d.p, u = unit(p);
      if (d.mode === 'ink') {
        if (d.points.length < 2) d.points.push([d.points[0][0] + 0.5, d.points[0][1] + 0.5]);
        const a = base('ink', p);
        a.points = [simplify(d.points, 0.6 / zoom)];
        add(a, { select: false });
        return;
      }
      if (d.mode === 'markup') {
        const a = base(tool, p);
        if (d.area) {
          const r = normRect(d.start, d.cur);
          if ((r[2] - r[0]) * zoom < 4 || (r[3] - r[1]) * zoom < 4) return;
          a.quads = [r]; a.rect = r;
        } else {
          if (d.i0 < 0 || d.i1 < 0) return;
          const lr = lineRects(d.ws, d.i0, d.i1);
          a.quads = lr.quads; a.excerpt = lr.text;
          a.rect = bbox(a);
        }
        add(a, { select: false });
        return;
      }
      // shapes
      const r = normRect(d.start, d.cur);
      const small = (r[2] - r[0]) * zoom < 4 && (r[3] - r[1]) * zoom < 4;
      if (tool === 'text') {
        const a = base('text', p);
        const fs = a.font_size || 12;
        a.rect = small ? [d.start[0], d.start[1], d.start[0] + 180 * u, d.start[1] + fs * 2.2].map(r2) : r;
        add(a);
        editText(a, true);
        return;
      }
      if (small) return;
      if (tool === 'line' || tool === 'arrow') {
        const a = base(tool, p);
        a.line = [d.start[0], d.start[1], d.cur[0], d.cur[1]].map(r2);
        a.rect = bbox(a);
        add(a, { select: false });
        return;
      }
      const a = base(tool, p);
      a.rect = r;
      if (tool === 'cover' || tool === 'redact') a.width = 0;
      add(a, { select: tool === 'cover' || tool === 'redact' });
    }
    pagesEl.addEventListener('pointerup', endDrag);
    pagesEl.addEventListener('pointercancel', endDrag);
    pagesEl.addEventListener('lostpointercapture', endDrag);
    pagesEl.addEventListener('pointerleave', () => { pageEls.forEach(el => { if (!drag) el.querySelector('.an-live').innerHTML = ''; }); });

    function normRect(a, b) { return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])].map(r2); }
    function constrain(s, pt, shift) {
      if (!shift) return pt;
      const dx = pt[0] - s[0], dy = pt[1] - s[1];
      if (tool === 'line' || tool === 'arrow') {
        const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4), L = Math.hypot(dx, dy);
        return [r2(s[0] + L * Math.cos(ang)), r2(s[1] + L * Math.sin(ang))];
      }
      const m = Math.max(Math.abs(dx), Math.abs(dy));
      return [r2(s[0] + Math.sign(dx || 1) * m), r2(s[1] + Math.sign(dy || 1) * m)];
    }
    function resized(d, pt, shift) {
      const a = d.orig;
      if (a.type === 'line' || a.type === 'arrow') {
        const ln = a.line.slice();
        if (d.handle === 'p1') { ln[0] = pt[0]; ln[1] = pt[1]; } else { ln[2] = pt[0]; ln[3] = pt[1]; }
        const n = { ...JSON.parse(JSON.stringify(a)), line: ln.map(r2) };
        n.rect = bbox(n);
        return n;
      }
      let [x0, y0, x1, y1] = d.box;
      const h = d.handle, min = 4 / zoom;
      if (h.includes('w')) x0 = Math.min(pt[0], x1 - min);
      if (h.includes('e')) x1 = Math.max(pt[0], x0 + min);
      if (h.includes('n')) y0 = Math.min(pt[1], y1 - min);
      if (h.includes('s')) y1 = Math.max(pt[1], y0 + min);
      const keep = (shift || a.type === 'signature') && h.length === 2;
      if (keep) {
        const ar = (d.box[2] - d.box[0]) / Math.max(0.01, d.box[3] - d.box[1]);
        const w = x1 - x0, hh = y1 - y0;
        if (w / hh > ar) { const nw = hh * ar; if (h.includes('w')) x0 = x1 - nw; else x1 = x0 + nw; }
        else { const nh = w / ar; if (h.includes('n')) y0 = y1 - nh; else y1 = y0 + nh; }
      }
      return scaleTo(a, d.box, [x0, y0, x1, y1]);
    }
    function simplify(pts, eps) {      // Ramer–Douglas–Peucker
      if (pts.length < 3) return pts.map(p => p.map(r2));
      const keep = new Array(pts.length).fill(false); keep[0] = keep[pts.length - 1] = true;
      const st = [[0, pts.length - 1]];
      while (st.length) {
        const [a, b] = st.pop(); let md = 0, mi = -1;
        const [ax, ay] = pts[a], [bx, by] = pts[b], L = Math.hypot(bx - ax, by - ay) || 1;
        for (let i = a + 1; i < b; i++) {
          const d = Math.abs((by - ay) * pts[i][0] - (bx - ax) * pts[i][1] + bx * ay - by * ax) / L;
          if (d > md) { md = d; mi = i; }
        }
        if (md > eps && mi > 0) { keep[mi] = true; st.push([a, mi], [mi, b]); }
      }
      return pts.filter((_, i) => keep[i]).map(p => p.map(r2));
    }
    function drawLive() {
      if (!drag) return;
      const live = pageEls[drag.p].querySelector('.an-live');
      const st = toolStyle(tool), u = unit(drag.p);
      const c = st.color || '#2563eb';
      if (drag.mode === 'ink') {
        live.innerHTML = `<path d="M${drag.points.map(p => p.join(' ')).join(' L')}" fill="none" stroke="${c}" stroke-width="${(st.width || 2) * u}" stroke-linecap="round" stroke-linejoin="round" opacity="${st.opacity ?? 1}"/>`;
      } else if (drag.mode === 'markup') {
        let qs;
        if (drag.area) qs = [normRect(drag.start, drag.cur)];
        else if (drag.i0 >= 0 && drag.i1 >= 0) qs = lineRects(drag.ws, drag.i0, drag.i1).quads;
        else qs = [];
        live.innerHTML = qs.map(q => `<rect x="${q[0]}" y="${q[1]}" width="${q[2] - q[0]}" height="${q[3] - q[1]}" fill="${tool === 'highlight' ? c : 'var(--accent)'}" opacity="${tool === 'highlight' ? 0.45 : 0.18}"/>`).join('');
      } else {
        const fake = { id: '_live', type: tool, page: drag.p, color: c, width: (st.width || 0) * u, fill: st.fill, opacity: st.opacity ?? 1,
                       rect: normRect(drag.start, drag.cur), line: [...drag.start, ...drag.cur], text: '', font_size: (st.font_size || 12) * u };
        if (tool === 'text') live.innerHTML = `<rect x="${fake.rect[0]}" y="${fake.rect[1]}" width="${fake.rect[2] - fake.rect[0]}" height="${fake.rect[3] - fake.rect[1]}" fill="none" stroke="var(--accent)" stroke-dasharray="${4 / zoom} ${3 / zoom}" stroke-width="${1 / zoom}"/>`;
        else live.innerHTML = svgFor(fake);
      }
    }
    function ghost(e) {
      if (tool !== 'stamp' && tool !== 'signature') return;
      const p = pageAt(e); pageEls.forEach((el, i) => { if (i !== p) el.querySelector('.an-live').innerHTML = ''; });
      if (p < 0) return;
      const a = pendingAnn(p, toUnits(e, p)); if (!a) return;
      a.id = '_ghost'; a.opacity = 0.6;
      pageEls[p].querySelector('.an-live').innerHTML = svgFor(a);
    }
    let hoverId = null;
    function hoverErase(e) {
      if (tool !== 'eraser') return;
      const g = e.target.closest && e.target.closest('.an-a');
      const id = g ? g.dataset.id : null;
      if (id === hoverId) return;
      ov.querySelectorAll('.an-a.an-erase-hover').forEach(x => x.classList.remove('an-erase-hover'));
      hoverId = id;
      if (g) g.classList.add('an-erase-hover');
    }
    function eraseAt(id) {
      if (!id || drag?.erased?.has(id)) return;
      drag?.erased?.add(id);
      removeIds([id]);
    }

    // ── stamps & signatures ────────────────────────────────────────────────
    function pendingAnn(p, pt) {
      const u = unit(p);
      if (tool === 'stamp' && pendingStamp) {
        const a = base('stamp', p);
        const lines = pendingStamp.text.split('\n');
        const fs = 14 * u;
        a.color = pendingStamp.color; a.font_size = fs; a.width = 2 * u; a.text = pendingStamp.text;
        const longest = Math.max(...lines.map(l => l.length));
        const w = Math.max(110 * u, longest * fs * 0.62 + 24 * u), h = lines.length * fs * 1.25 + 14 * u;
        a.rect = [pt[0] - w / 2, pt[1] - h / 2, pt[0] + w / 2, pt[1] + h / 2].map(r2);
        return a;
      }
      if (tool === 'signature' && pendingSig) {
        const a = base('signature', p);
        const w = 170 * u, h = w / pendingSig.ratio;
        a.image = pendingSig.url; a.color = null;
        a.rect = [pt[0] - w / 2, pt[1] - h / 2, pt[0] + w / 2, pt[1] + h / 2].map(r2);
        return a;
      }
      return null;
    }
    function placePending(p, pt) {
      const a = pendingAnn(p, pt);
      if (!a) { if (tool === 'stamp') openStampMenu(); else if (tool === 'signature') openSignaturePad(); return; }
      const pg = pages()[p];
      const b = a.rect, dx = clamp(0, -b[0], pg.w - b[2]), dy = clamp(0, -b[1], pg.h - b[3]);
      add(translate(a, dx, dy));
      pageEls[p].querySelector('.an-live').innerHTML = '';
      setTool('select');
    }
    function stampText(s) {
      if (s.date) return today();
      return s.label.toUpperCase() + '\n' + today();
    }
    function openStampMenu() {
      closeStampMenu();
      const btn = ov.querySelector('[data-tool="stamp"]');
      const m = document.createElement('div');
      m.className = 'menu an-menu';
      m.innerHTML = STAMPS.map((s, i) => `<div class="menu-item" data-i="${i}" role="menuitem"><span class="an-stamp-chip" style="--chip:${s.color}">${esc(s.date ? today() : s.label.toUpperCase())}</span></div>`).join('') +
        `<div class="menu-sep"></div><div class="menu-item" data-i="custom" role="menuitem"><span class="icon">${ic('pencil')}</span>Custom text…</div>`;
      document.body.appendChild(m);
      const r = btn.getBoundingClientRect();
      m.style.left = Math.min(r.left, window.innerWidth - 240) + 'px';
      m.style.top = (r.bottom + 4) + 'px';
      m.style.zIndex = 10040;
      stampMenu = m;
      m.addEventListener('mousedown', e => e.stopPropagation());
      m.addEventListener('click', async e => {
        const it = e.target.closest('[data-i]'); if (!it) return;
        closeStampMenu();
        if (it.dataset.i === 'custom') {
          const t = await Dialogs.ask({ title: 'Custom stamp', label: 'Stamp text', value: store('stamp.custom') || '', icon: 'stamp', okLabel: 'Use stamp' });
          if (!t) return;
          store('stamp.custom', t);
          pendingStamp = { text: t.toUpperCase() + '\n' + today(), color: toolStyle('stamp').color || '#475569' };
        } else {
          const s = STAMPS[+it.dataset.i];
          pendingStamp = { text: stampText(s), color: s.color };
        }
        setTool('stamp', true);
      });
      setTimeout(() => document.addEventListener('mousedown', closeStampMenu, { once: true }), 0);
    }
    function closeStampMenu() { if (stampMenu) { stampMenu.remove(); stampMenu = null; } }

    async function openSignaturePad() {
      const url = await signaturePad();
      if (!url) return;
      const img = new Image();
      img.onload = () => { pendingSig = { url, ratio: img.width / Math.max(1, img.height) }; setTool('signature', true); };
      img.src = url;
    }

    // ── inline text editing ───────────────────────────────────────────────
    let editBox = null, editBefore = null, editIsNew = false;
    function editText(a, isNew = false) {
      commitEdit();
      editing = a.id; editIsNew = isNew;
      editBefore = JSON.stringify(anns);
      const ta = document.createElement('textarea');
      ta.className = 'an-editbox' + (a.type === 'stamp' ? ' an-stamp' : '');
      ta.value = a.text || '';
      ta.spellcheck = true;
      pageEls[a.page].appendChild(ta);
      editBox = ta;
      positionEditor();
      renderPage(a.page);
      ta.addEventListener('input', () => { const x = byId(editing); if (x) { x.text = ta.value; touch(x); autoGrow(x); } });
      ta.addEventListener('keydown', e => {
        if (e.key === 'Escape' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); e.stopPropagation(); commitEdit(); }
      });
      ta.addEventListener('blur', () => setTimeout(() => { if (editBox === ta) commitEdit(); }, 0));
      setTimeout(() => { ta.focus(); ta.select(); }, 20);
    }
    function autoGrow(a) {
      if (!editBox) return;
      const needed = (editBox.scrollHeight + 2) / zoom;
      if (needed > a.rect[3] - a.rect[1]) { a.rect[3] = r2(a.rect[1] + needed); positionEditor(); }
    }
    function positionEditor() {
      const a = byId(editing); if (!a || !editBox) return;
      const [x0, y0, x1, y1] = a.rect, fs = (a.font_size || 12) * zoom;
      Object.assign(editBox.style, {
        left: x0 * zoom + 'px', top: y0 * zoom + 'px', width: (x1 - x0) * zoom + 'px', height: (y1 - y0) * zoom + 'px',
        fontSize: fs + 'px', color: a.color || '#111', padding: Math.max(1.5, (a.font_size || 12) * 0.25) * zoom + 'px',
        background: a.type === 'text' && a.fill ? a.fill : '', borderColor: a.type === 'stamp' ? a.color : '',
      });
    }
    function commitEdit() {
      if (!editing) return;
      const id = editing, ta = editBox;
      editing = null; editBox = null;
      if (ta) ta.remove();
      const a = byId(id);
      if (a && !String(a.text || '').trim()) {
        anns = anns.filter(x => x.id !== id);         // empty box → drop it
        if (sel === id) sel = null;
        if (!editIsNew) { undoStack.push(editBefore); redoStack = []; }
        else if (undoStack.length) undoStack.pop();   // the "add" snapshot
      } else if (a && !editIsNew && editBefore && editBefore !== JSON.stringify(anns)) {
        undoStack.push(editBefore); redoStack = [];
      }
      editBefore = null;
      if (a) renderPage(a.page);
      renderProps(); renderComments(); status();
    }

    // ── toolbar ────────────────────────────────────────────────────────────
    $('#an-tools').innerHTML = TOOLS.map(t => t.sep ? '<span class="toolbar-sep"></span>' :
      `<button class="icon-btn an-tool${t.danger ? ' an-tool-danger' : ''}" data-tool="${t.id}" aria-label="${esc(t.label)}" aria-pressed="false"
        data-tooltip="${esc(t.label)}${t.key ? ' · ' + t.key : ''}">${ic(t.icon)}${t.menu ? `<span class="an-caret">${ic('chevron-down', 10)}</span>` : ''}</button>`).join('');
    function setTool(t, keepPending = false) {
      if (!TOOL[t]) return;
      if (doc && doc.kind === 'image' && t === 'redact') t = 'cover';
      commitEdit();
      if (!keepPending) { if (t !== 'stamp') pendingStamp = null; if (t !== 'signature') pendingSig = null; }
      tool = t;
      $$('.an-tool').forEach(b => { const on = b.dataset.tool === t; b.classList.toggle('active', on); b.setAttribute('aria-pressed', on); });
      ov.querySelector('.an-editor').dataset.tool = t;
      pageEls.forEach(el => { el.querySelector('.an-live').innerHTML = ''; });
      let hint = TOOL[t].hint;
      if (t === 'stamp' && !pendingStamp) hint = 'Choose a stamp from the menu';
      if (doc && doc.kind === 'image' && MARKUP.has(t)) hint = 'Drag to mark a region';
      $('#an-hint').innerHTML = `${ic(TOOL[t].icon, 14)}<span>${esc(hint)}</span>`;
      if (MARKUP.has(t) && doc && doc.kind === 'pdf') visiblePages().forEach(p => wordsFor(p));   // prefetch
      if (t !== 'select' && t !== 'eraser' && sel) select(null);
      else renderProps();
    }
    ov.querySelector('.an-bar').addEventListener('click', e => {
      const tb = e.target.closest('[data-tool]');
      if (tb) {
        const t = tb.dataset.tool;
        if (t === 'stamp') { stampMenu ? closeStampMenu() : openStampMenu(); return; }
        if (t === 'signature') { openSignaturePad(); return; }
        setTool(t); view.focus({ preventScroll: true }); return;
      }
      const b = e.target.closest('[data-a]'); if (!b || b.disabled) return;
      const a = b.dataset.a;
      if (a === 'undo') undo();
      else if (a === 'redo') redo();
      else if (a === 'zin') zoomStep(1);
      else if (a === 'zout') zoomStep(-1);
      else if (a === 'z100') setZoom(zbase());
      else if (a === 'fitw') fitWidth();
      else if (a === 'fitp') fitPage();
      else if (a === 'prev') goPage(curPage - 1);
      else if (a === 'next') goPage(curPage + 1);
      else if (a === 'side') { $('#an-side').classList.toggle('hidden'); store('side', $('#an-side').classList.contains('hidden') ? '0' : '1'); }
    });
    $('#an-pg').addEventListener('change', () => goPage((+$('#an-pg').value || 1) - 1));
    $('#an-pg').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); goPage((+$('#an-pg').value || 1) - 1); view.focus(); } });

    // ── side panel: properties ─────────────────────────────────────────────
    function showTab(t) {
      sideTab = t;
      $('#an-side').classList.remove('hidden');
      $$('.an-side-tabs .seg-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
      $('#an-props').classList.toggle('hidden', t !== 'props');
      $('#an-comments').classList.toggle('hidden', t !== 'comments');
    }
    $$('.an-side-tabs .seg-btn').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

    function swatchRow(name, value, opts = {}) {
      const list = opts.withNone ? [[null, 'None']].concat(opts.list || PALETTE) : (opts.list || PALETTE);
      const known = list.some(([c]) => (c || null) === (value || null));
      return `<div class="swatches an-swatches" data-prop="${name}">` +
        list.map(([c, label]) => `<button class="swatch${(c || null) === (value || null) ? ' active' : ''}${c ? '' : ' an-swatch-none'}" style="--swatch:${c || 'transparent'}" data-v="${c || ''}" title="${label}" aria-label="${label}"></button>`).join('') +
        `<label class="swatch an-swatch-custom${!known && value ? ' active' : ''}" title="Custom colour" style="--swatch:${!known && value ? value : 'conic-gradient(red,yellow,lime,aqua,blue,magenta,red)'}">` +
        `<input type="color" value="${value && /^#[0-9a-f]{6}$/i.test(value) ? value : '#888888'}" aria-label="Custom colour"></label></div>`;
    }
    function renderProps() {
      const box = $('#an-props');
      if (!box || !doc) return;
      const a = byId(sel);
      const t = a ? a.type : tool;
      const st = a || toolStyle(t);
      const u = a ? unit(a.page) : unit(curPage);
      if (!a && (t === 'select' || t === 'eraser' || t === 'signature' || !DEFAULTS[t])) {
        box.innerHTML = `<div class="empty-state empty-state-sm an-empty">
          <div class="empty-state-icon">${ic(t === 'eraser' ? 'eraser' : 'mouse-pointer', 22)}</div>
          <div class="empty-state-title">${t === 'eraser' ? 'Eraser' : t === 'signature' ? 'Signature' : 'Nothing selected'}</div>
          <div class="empty-state-text">${t === 'eraser' ? 'Click or drag over annotations to remove them.' : t === 'signature' ? 'Click on the page to place your signature.' : 'Pick a tool above, or click an annotation to edit its colour, size and comment.'}</div></div>
          <div class="an-kbd-help">${shortcutHelp()}</div>`;
        return;
      }
      const isText = t === 'text' || t === 'stamp';
      const hasWidth = ['ink', 'rect', 'ellipse', 'line', 'arrow', 'stamp'].includes(t);
      const hasFill = ['rect', 'ellipse', 'text'].includes(t);
      const hasColor = !['signature', 'other', 'redact'].includes(t);
      const coverCols = [['#ffffff', 'White'], ['#000000', 'Black']];
      const width = (st.width || 0) / (a ? u : 1), fs = (st.font_size || 12) / (a ? u : 1);
      let html = `<div class="an-props-head">${ic(TYPE_ICON[t] || 'file', 16)}<span>${a ? esc(TYPE_LABEL[t] || t) : 'New ' + esc((TYPE_LABEL[t] || t).toLowerCase())}</span>
        ${a ? `<span class="an-spacer"></span><button class="icon-btn icon-btn-sm icon-btn-danger" id="an-p-del" title="Delete (Del)" aria-label="Delete annotation">${ic('trash', 14)}</button>` : ''}</div>`;
      if (a && a.type === 'other') {
        html += `<div class="callout info"><div class="callout-body">This ${esc(a.label || 'annotation')} was made in another app. It is kept as it is; you can delete it.</div></div>`;
      }
      if (hasColor && a?.type !== 'other') {
        html += `<div class="field"><span class="field-label">${t === 'cover' ? 'Box colour' : isText ? 'Text colour' : 'Colour'}</span>${swatchRow('color', st.color, t === 'cover' ? { list: coverCols } : {})}</div>`;
      }
      if (hasFill) html += `<div class="field"><span class="field-label">Fill</span>${swatchRow('fill', st.fill, { withNone: true, list: [['#ffffff', 'White'], ['#fef9c3', 'Light yellow'], ['#dbeafe', 'Light blue'], ['#dcfce7', 'Light green'], ['#fce7f3', 'Light pink'], ['#111111', 'Black']] })}</div>`;
      if (hasWidth) html += rangeField('width', t === 'stamp' ? 'Border' : 'Stroke width', width, 0.5, 12, 0.5, v => v + ' pt');
      if (isText) html += rangeField('font_size', 'Font size', fs, 6, 72, 1, v => v + ' pt');
      if (t !== 'cover' && t !== 'redact' && a?.type !== 'other') html += rangeField('opacity', 'Opacity', Math.round((st.opacity ?? 1) * 100), 10, 100, 5, v => v + '%');
      if (t === 'redact') html += `<div class="callout warning"><div class="callout-body">Redaction permanently removes the text and images under this box when you save. The original file is not changed.</div></div>`;
      if (a && a.type !== 'other') {
        const label = isText ? 'Text' : a.type === 'note' ? 'Comment' : 'Comment (optional)';
        html += `<div class="field"><label class="field-label" for="an-p-text">${label}</label>
          <textarea class="input-text an-p-text" id="an-p-text" rows="${a.type === 'note' || isText ? 4 : 3}" placeholder="${a.type === 'note' ? 'Write a comment…' : 'Add a note…'}">${esc(a.text || '')}</textarea></div>`;
        if (a.excerpt) html += `<div class="an-excerpt" title="Marked text">“${esc(a.excerpt.length > 160 ? a.excerpt.slice(0, 160) + '…' : a.excerpt)}”</div>`;
        html += `<div class="an-meta">${ic('user', 12)}<span>${esc(a.author || 'Unknown')}</span>${a.modified || a.created ? `<span class="an-dot">·</span>${ic('clock', 12)}<span>${esc(fmtTime(a.modified || a.created))}</span>` : ''}</div>`;
      } else if (!a) {
        html += `<div class="field-hint">These settings are used for new ${esc((TYPE_LABEL[t] || t).toLowerCase())} annotations.</div>`;
      }
      box.innerHTML = html;
      wireProps(a, t);
    }
    function rangeField(prop, label, v, min, max, step, fmt) {
      return `<div class="field an-range"><div class="an-range-head"><span class="field-label">${label}</span><span class="an-range-val" data-for="${prop}">${fmt(v)}</span></div>
        <input type="range" min="${min}" max="${max}" step="${step}" value="${v}" data-prop="${prop}" aria-label="${label}"></div>`;
    }
    function setProp(prop, value, live) {
      const a = byId(sel);
      if (!a) {           // tool defaults
        const st = toolStyle(tool);
        st[prop] = value;
        saveToolStyle(tool, st);
        if (tool === 'stamp' && prop === 'color' && pendingStamp) pendingStamp.color = value;
        return;
      }
      if (!propSnap) { snapshot(); propSnap = true; }
      const u = unit(a.page);
      if (prop === 'width' || prop === 'font_size') value = value * u;
      a[prop] = value;
      touch(a);
      if (a.type === 'text' && prop === 'font_size') a.rect[3] = Math.max(a.rect[3], a.rect[1] + value * 1.8);
      renderPage(a.page);
      if (!live) renderComments();
      // keep the tool defaults in step with the last used style
      if (['color', 'width', 'opacity', 'font_size', 'fill'].includes(prop) && TOOL[a.type] && a.type !== 'stamp') {
        const st = toolStyle(a.type); st[prop] = (prop === 'width' || prop === 'font_size') ? value / u : value; saveToolStyle(a.type, st);
      }
    }
    function wireProps(a, t) {
      const box = $('#an-props');
      box.querySelectorAll('.an-swatches').forEach(row => {
        const prop = row.dataset.prop;
        row.addEventListener('click', e => {
          const b = e.target.closest('button.swatch'); if (!b) return;
          propSnap = false;
          setProp(prop, b.dataset.v || null);
          row.querySelectorAll('.swatch').forEach(s => s.classList.toggle('active', s === b));
        });
        const ci = row.querySelector('input[type=color]');
        ci.addEventListener('focus', () => { propSnap = false; });
        ci.addEventListener('input', () => {
          setProp(prop, ci.value, true);
          row.querySelectorAll('.swatch').forEach(s => s.classList.remove('active'));
          const lab = ci.parentNode; lab.classList.add('active'); lab.style.setProperty('--swatch', ci.value);
        });
      });
      box.querySelectorAll('input[type=range]').forEach(r => {
        r.addEventListener('pointerdown', () => { propSnap = false; });
        r.addEventListener('keydown', () => { propSnap = false; });
        r.addEventListener('input', () => {
          const prop = r.dataset.prop, v = +r.value;
          const lab = box.querySelector(`.an-range-val[data-for="${prop}"]`);
          if (lab) lab.textContent = prop === 'opacity' ? v + '%' : v + ' pt';
          setProp(prop, prop === 'opacity' ? v / 100 : v, true);
        });
      });
      const txt = box.querySelector('#an-p-text');
      if (txt) {
        txt.addEventListener('focus', () => { propSnap = false; });
        txt.addEventListener('input', () => {
          const x = byId(sel); if (!x) return;
          if (!propSnap) { snapshot(); propSnap = true; }
          x.text = txt.value; touch(x);
          if (x.type === 'text' || x.type === 'stamp') renderPage(x.page);
          renderComments(); status();
        });
        txt.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); txt.blur(); view.focus(); } });
      }
      box.querySelector('#an-p-del')?.addEventListener('click', () => removeIds([sel]));
    }
    function shortcutHelp() {
      const k = (key, label) => `<span><span class="kbd">${key}</span>${label}</span>`;
      return k('V', 'Select') + k('H', 'Highlight') + k('P', 'Pen') + k('R', 'Rectangle') + k('T', 'Text') + k('C', 'Comment') +
             k('E', 'Eraser') + k('Del', 'Delete') + k('Ctrl+Z', 'Undo') + k('Ctrl+S', 'Save');
    }

    // ── side panel: comments ───────────────────────────────────────────────
    function renderComments() {
      const box = $('#an-comments'); if (!box || !doc) return;
      $('#an-ccount').textContent = anns.length;
      if (!anns.length) {
        box.innerHTML = `<div class="empty-state empty-state-sm an-empty"><div class="empty-state-icon">${ic('messages', 22)}</div>
          <div class="empty-state-title">No annotations yet</div><div class="empty-state-text">Highlights, comments and shapes you add appear here.</div></div>`;
        return;
      }
      const sorted = anns.slice().sort((x, y) => x.page - y.page || (bbox(x)[1] - bbox(y)[1]) || (bbox(x)[0] - bbox(y)[0]));
      let html = '', lastPage = -1;
      sorted.forEach(a => {
        if (a.page !== lastPage && pages().length > 1) { html += `<div class="an-c-page">Page ${a.page + 1}</div>`; lastPage = a.page; }
        const body = a.text ? esc(a.text) : a.excerpt ? `<span class="an-c-ex">“${esc(a.excerpt)}”</span>` : `<span class="text-muted">No comment</span>`;
        html += `<button class="an-c${a.id === sel ? ' active' : ''}" data-id="${esc(a.id)}">
          <span class="an-c-icon" style="--chip:${esc(a.color || 'var(--text-muted)')}">${ic(TYPE_ICON[a.type] || 'file', 14)}</span>
          <span class="an-c-main"><span class="an-c-top"><b>${esc(TYPE_LABEL[a.type] || a.type)}</b><span class="an-c-who">${esc(a.author || '')}</span></span>
          <span class="an-c-text">${body}</span>
          <span class="an-c-time">${esc(fmtTime(a.modified || a.created))}</span></span></button>`;
      });
      box.innerHTML = html;
    }
    $('#an-comments').addEventListener('click', e => {
      const b = e.target.closest('.an-c'); if (!b) return;
      if (tool !== 'select') setTool('select');
      select(b.dataset.id, { scroll: true });
    });

    function status() {
      const n = anns.length;
      $('#an-status').textContent = (n ? n + ' annotation' + (n === 1 ? '' : 's') : 'No annotations') + (dirty() ? ' · unsaved' : '');
      $('#an-status').className = 'pill ' + (dirty() ? 'pill-yellow' : 'pill-neutral');
      ov.querySelector('[data-a="undo"]').disabled = !undoStack.length;
      ov.querySelector('[data-a="redo"]').disabled = !redoStack.length;
    }

    // ── keyboard ───────────────────────────────────────────────────────────
    function onKey(e) {
      if (closed || !document.body.contains(ov)) return;
      if (document.querySelector('.modal-overlay.dlg-top')) return;   // a confirm/ask/signature pad on top
      const t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      const ctrl = e.ctrlKey || e.metaKey, k = e.key;
      e.stopPropagation();                         // the file list must not see our keys
      const handled = () => e.preventDefault();
      if (ctrl && (k === 's' || k === 'S')) { handled(); save(); return; }
      if (k === 'Escape') {
        handled();
        if (stampMenu) { closeStampMenu(); return; }
        if (editing) { commitEdit(); return; }
        if (typing) { t.blur(); view.focus(); return; }
        if (drag) { drag = null; pageEls.forEach(el => { el.querySelector('.an-live').innerHTML = ''; }); renderAll(); return; }
        if (tool !== 'select') { setTool('select'); return; }
        if (sel) { select(null); return; }
        tryClose();
        return;
      }
      if (typing) return;
      if (ctrl && (k === 'z' || k === 'Z') && !e.shiftKey) { handled(); undo(); return; }
      if (ctrl && (k === 'y' || k === 'Y' || ((k === 'z' || k === 'Z') && e.shiftKey))) { handled(); redo(); return; }
      if (ctrl && (k === '=' || k === '+')) { handled(); zoomStep(1); return; }
      if (ctrl && k === '-') { handled(); zoomStep(-1); return; }
      if (ctrl && k === '0') { handled(); fitWidth(); return; }
      if (ctrl) return;
      if ((k === 'Delete' || k === 'Backspace') && sel) { handled(); removeIds([sel]); return; }
      if (k === 'Enter' && sel) { const a = byId(sel); if (a && (a.type === 'text' || a.type === 'stamp')) { handled(); editText(a); } return; }
      if (/^Arrow/.test(k) && sel) {
        const a = byId(sel); if (!a || a.type === 'other' || MARKUP.has(a.type)) return;
        handled();
        const s = (e.shiftKey ? 10 : 1) * unit(a.page);
        const d = { ArrowLeft: [-s, 0], ArrowRight: [s, 0], ArrowUp: [0, -s], ArrowDown: [0, s] }[k];
        if (!e.repeat || !propSnap) { snapshot(); propSnap = true; }
        anns[anns.indexOf(a)] = translate(a, d[0], d[1]);
        renderPage(a.page);
        return;
      }
      if (k === 'PageDown') { handled(); goPage(curPage + 1); return; }
      if (k === 'PageUp') { handled(); goPage(curPage - 1); return; }
      if (e.altKey) return;
      const tk = TOOLS.find(x => x.key && x.key.toLowerCase() === k.toLowerCase());
      if (tk) {
        handled();
        if (tk.id === 'signature') openSignaturePad(); else setTool(tk.id);
      }
    }
    document.addEventListener('keydown', onKey, true);

    // ── save / close ───────────────────────────────────────────────────────
    const overChosen = () => (ov.querySelector('input[name="an-out"]:checked') || {}).value === 'over';
    $$('input[name="an-out"]').forEach(rb => rb.addEventListener('change', () => {
      const over = overChosen();
      ['#an-name', '#an-dir', '#an-browse'].forEach(s => { $(s).disabled = over; });
      $('#an-fields').classList.toggle('is-dim', over);
      Dialogs.setBtn($('#an-save'), over ? 'Save over original…' : 'Save', over ? 'refresh' : 'save');
    }));
    $('#an-browse').addEventListener('click', async () => {
      const r = await SFM.call('browse_for_folder').catch(() => null);
      if (r && r.ok && r.path) $('#an-dir').value = r.path;
    });
    $('#an-name').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });

    function setBusy(on) {
      busy = on;
      ov.querySelector('.an-progress').classList.toggle('on', on);
      $$('.an-foot .btn, .an-bar .icon-btn, .an-bar .btn').forEach(b => { b.disabled = on; });
      $('#an-save').classList.toggle('is-loading', on);
      if (!on) status();
    }
    async function save() {
      if (busy || !doc) return;
      commitEdit();
      const over = overChosen();
      const ext = doc.kind === 'pdf' ? '.pdf' : extOf(path);
      let out = '';
      if (!over) {
        const nm = cleanName($('#an-name').value).replace(new RegExp(ext.replace('.', '\\.') + '$', 'i'), '');
        const dir = $('#an-dir').value.trim();
        if (!nm || !dir) { App.toast('Enter a file name and folder', 'warning'); return; }
        const de = await SFM.pathExists(dir).catch(() => null);
        if (!de || !de.ok || !de.is_dir) { App.toast('Choose an existing output folder', 'warning'); return; }
        out = joinP(dir, nm + ext);
      }
      const nRedact = anns.filter(a => a.type === 'redact').length;
      let applyRedactions = false;
      if (nRedact && doc.kind === 'pdf') {
        if (!(await Dialogs.confirm({ title: 'Apply redactions', danger: true, icon: 'redact', okLabel: 'Redact and save',
              message: `Permanently remove the content under ${nRedact} redaction area${nRedact === 1 ? '' : 's'}?`,
              detail: 'Text, images and drawings under the boxes are deleted from the saved PDF and cannot be recovered from it.' + (over ? ' A backup of the original is kept in _to_review/.' : ' Your original file is not changed.') }))) return;
        applyRedactions = true;
      }
      if (over && !(await Dialogs.confirm({ title: 'Save over original', icon: 'alert-triangle', tone: 'warning', okLabel: 'Save over original', okIcon: 'refresh',
            message: `Save the annotations into “${baseOf(path)}”?`,
            detail: 'The current version is first copied to the “_to_review” folder next to it, so nothing is lost.' }))) return;
      const flatten = doc.kind === 'pdf' && $('#an-flatten').checked;
      store('flatten', flatten ? '1' : '0');
      setBusy(true);
      App.setStatus('Saving annotations…', true);
      const payload = anns.map(a => { const c = { ...a }; delete c.excerpt; return c; });
      const r = await SFM.annotSave(path, payload, { mode: over ? 'over' : 'new', out_path: out, flatten, apply_redactions: applyRedactions })
        .catch(e => ({ ok: false, error: String(e) }));
      App.setStatus('Ready');
      setBusy(false);
      if (!r || !r.ok) { App.toast(esc((r && r.error) || 'Could not save'), 'error', 8000); return; }
      forceClose();
      const name = esc(baseOf(r.out_path));
      App.toast(over ? `Saved <b>${name}</b> · backup in _to_review/` : `Saved annotations → <b>${name}</b>`, 'success', 5000);
      if (FileTree.refreshAndSelect) FileTree.refreshAndSelect(r.out_path); else FileTree.refresh();
      try {
        if (over && Preview.getCurrentPath && (Preview.getCurrentPath() || '').toLowerCase() === path.toLowerCase()) Preview.previewFile(path, extOf(path));
      } catch (_) {}
    }
    function forceClose() {
      if (closed) return;
      closed = true;
      commitEdit();
      closeStampMenu();
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', onResize);
      if (io) io.disconnect();
      ov.remove();
      _open = null;
    }
    async function tryClose() {
      if (busy) return;
      commitEdit();
      if (dirty() && !(await Dialogs.confirm({ title: 'Unsaved annotations', icon: 'alert-triangle', tone: 'warning', okLabel: 'Discard changes',
            message: 'Close without saving your annotations?', detail: 'Your changes will be lost.' }))) return;
      forceClose();
    }
    $('#an-x').addEventListener('click', tryClose);
    $('#an-close').addEventListener('click', tryClose);
    $('#an-save').addEventListener('click', save);
    const onResize = () => { if (doc) visiblePages().forEach(wantPage); };
    window.addEventListener('resize', onResize);

    _open = { focus: () => view.focus(), path };

    // ── load ───────────────────────────────────────────────────────────────
    App.setStatus('Opening for annotation…', true);
    const r = await SFM.annotLoad(path).catch(e => ({ ok: false, error: String(e) }));
    App.setStatus('Ready');
    if (closed) return;
    if (!r || !r.ok) { forceClose(); App.toast(esc((r && r.error) || 'Could not open the file'), 'error', 7000); return; }
    doc = r;
    anns = (r.annotations || []).map((a, i) => ({ ...a, id: a.id || ('l' + i) }));
    initialSig = sig();
    const ext = r.kind === 'pdf' ? '.pdf' : extOf(path);
    $('#an-name').value = stemOf(r.source || path).replace(/_annotated( \(\d+\))?$/, '') + '_annotated';
    $('#an-ext').textContent = ext + ' in';
    $('#an-dir').value = dirOf(path);
    $('#an-flatten-wrap').classList.toggle('hidden', r.kind !== 'pdf');
    $('#an-flatten').checked = store('flatten') === '1';
    if (r.kind === 'image') {
      const red = ov.querySelector('[data-tool="redact"]'); if (red) red.classList.add('hidden');
    }
    if (store('side') === '0') $('#an-side').classList.add('hidden');
    buildPages();
    await new Promise(res => requestAnimationFrame(res));
    fitWidth();
    if (r.kind === 'image') fitPage();
    if (zoom > 2.5) setZoom(2.5, false);
    setTool('select');
    renderAll();
    view.focus({ preventScroll: true });
  }

  // =========================================================================
  // SIGNATURE PAD — draw or type; remembered signatures (settings)
  // =========================================================================
  function signaturePad() {
    return new Promise(async resolve => {
      const ov = document.createElement('div');
      ov.className = 'modal-overlay dlg-top an-sig-overlay';
      ov.style.zIndex = 10050;
      ov.innerHTML = `
        <div class="modal an-sig" role="dialog">
          ${Dialogs.header('Signature', { icon: 'signature', subtitle: 'Draw or type your signature' })}
          <div class="modal-body">
            <div class="an-sig-top">
              <div class="segmented" role="tablist">
                <button class="seg-btn active" data-mode="draw">${ic('pen', 14)}Draw</button>
                <button class="seg-btn" data-mode="type">${ic('type', 14)}Type</button>
              </div>
              <span class="an-spacer"></span>
              <div class="swatches" id="an-sig-cols">
                <button class="swatch active" style="--swatch:#111111" data-c="#111111" title="Black" aria-label="Black"></button>
                <button class="swatch" style="--swatch:#1e3a8a" data-c="#1e3a8a" title="Blue" aria-label="Blue"></button>
                <button class="swatch" style="--swatch:#b91c1c" data-c="#b91c1c" title="Red" aria-label="Red"></button>
              </div>
            </div>
            <div class="an-sig-pad"><canvas id="an-sig-canvas"></canvas><div class="an-sig-line"></div><div class="an-sig-ph" id="an-sig-ph">Sign here</div></div>
            <div class="field hidden" id="an-sig-typef"><input class="input-text" id="an-sig-type" placeholder="Type your name" autocomplete="off" spellcheck="false"></div>
            <div class="an-sig-saved hidden" id="an-sig-saved"></div>
          </div>
          <div class="modal-footer">
            <button class="btn footer-left" id="an-sig-clear">${ic('eraser')}Clear</button>
            <label class="check an-sig-remember"><input type="checkbox" id="an-sig-rem" checked> Remember for next time</label>
            <button class="btn" id="an-sig-cancel">Cancel</button>
            <button class="btn btn-primary" id="an-sig-ok">${ic('check')}Use signature</button>
          </div>
        </div>`;
      document.body.appendChild(ov);
      const $ = s => ov.querySelector(s);
      const cv = $('#an-sig-canvas'), ctx = cv.getContext('2d');
      let mode = 'draw', color = '#111111', strokes = [], cur = null, done = false;
      const W = 560, H = 190, D = Math.max(2, window.devicePixelRatio || 1);
      cv.width = W * D; cv.height = H * D; cv.style.width = W + 'px'; cv.style.height = H + 'px';
      const finish = v => { if (done) return; done = true; document.removeEventListener('keydown', onKey, true); ov.remove(); resolve(v); };

      function redraw() {
        ctx.setTransform(D, 0, 0, D, 0, 0);
        ctx.clearRect(0, 0, W, H);
        if (mode === 'draw') {
          ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
          strokes.forEach(s => {
            if (s.length === 1) { ctx.beginPath(); ctx.arc(s[0][0], s[0][1], 1.6, 0, 7); ctx.fill(); return; }
            ctx.beginPath(); ctx.moveTo(s[0][0], s[0][1]);
            for (let i = 1; i < s.length - 1; i++) {
              const mx = (s[i][0] + s[i + 1][0]) / 2, my = (s[i][1] + s[i + 1][1]) / 2;
              ctx.lineWidth = s[i][2]; ctx.quadraticCurveTo(s[i][0], s[i][1], mx, my);
            }
            const l = s[s.length - 1]; ctx.lineTo(l[0], l[1]); ctx.stroke();
          });
          $('#an-sig-ph').classList.toggle('hidden', strokes.length > 0);
        } else {
          const t = $('#an-sig-type').value.trim();
          $('#an-sig-ph').classList.toggle('hidden', !!t);
          if (t) {
            let fs = 64;
            ctx.fillStyle = color; ctx.textBaseline = 'alphabetic';
            const font = s => `${s}px "Segoe Script", "Brush Script MT", "Lucida Handwriting", cursive`;
            ctx.font = font(fs);
            while (ctx.measureText(t).width > W - 40 && fs > 18) { fs -= 2; ctx.font = font(fs); }
            ctx.fillText(t, (W - ctx.measureText(t).width) / 2, H * 0.68);
          }
        }
      }
      const pos = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * W / r.width, (e.clientY - r.top) * H / r.height]; };
      cv.addEventListener('pointerdown', e => {
        if (mode !== 'draw') return;
        cv.setPointerCapture(e.pointerId);
        cur = [[...pos(e), 2.6]]; strokes.push(cur); redraw();
      });
      cv.addEventListener('pointermove', e => {
        if (!cur) return;
        const p = pos(e), l = cur[cur.length - 1];
        const v = Math.hypot(p[0] - l[0], p[1] - l[1]);
        if (v < 1) return;
        const w = clamp(3.4 - v * 0.08, 1.4, 3.4) * (e.pressure && e.pointerType === 'pen' ? 0.5 + e.pressure : 1);
        cur.push([p[0], p[1], (l[2] * 0.6 + w * 0.4)]); redraw();
      });
      const up = () => { cur = null; };
      cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);

      ov.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => {
        mode = b.dataset.mode;
        ov.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('active', x === b));
        $('#an-sig-typef').classList.toggle('hidden', mode !== 'type');
        $('#an-sig-ph').textContent = mode === 'draw' ? 'Sign here' : 'Your typed signature appears here';
        redraw();
        if (mode === 'type') setTimeout(() => $('#an-sig-type').focus(), 20);
      }));
      $('#an-sig-type').addEventListener('input', redraw);
      $('#an-sig-cols').addEventListener('click', e => {
        const b = e.target.closest('[data-c]'); if (!b) return;
        color = b.dataset.c;
        $('#an-sig-cols').querySelectorAll('.swatch').forEach(s => s.classList.toggle('active', s === b));
        redraw();
      });
      $('#an-sig-clear').addEventListener('click', () => { strokes = []; $('#an-sig-type').value = ''; redraw(); });
      $('#an-sig-cancel').addEventListener('click', () => finish(null));
      ov.querySelector('.modal-close').addEventListener('click', () => finish(null));
      const onKey = e => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(null); }
        else if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); e.stopPropagation(); $('#an-sig-ok').click(); }
        else e.stopPropagation();
      };
      document.addEventListener('keydown', onKey, true);

      function trimmed() {
        // crop to the ink with a small margin → PNG data URL (transparent)
        const img = ctx.getImageData(0, 0, cv.width, cv.height), d = img.data;
        let x0 = cv.width, y0 = cv.height, x1 = -1, y1 = -1;
        for (let y = 0; y < cv.height; y++) for (let x = 0; x < cv.width; x++) {
          if (d[(y * cv.width + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
        }
        if (x1 < 0) return null;
        const m = 6 * D;
        x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m); x1 = Math.min(cv.width, x1 + m); y1 = Math.min(cv.height, y1 + m);
        const out = document.createElement('canvas');
        out.width = x1 - x0; out.height = y1 - y0;
        out.getContext('2d').drawImage(cv, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
        return out.toDataURL('image/png');
      }
      $('#an-sig-ok').addEventListener('click', async () => {
        const url = trimmed();
        if (!url) { App.toast(mode === 'draw' ? 'Draw your signature first' : 'Type your name first', 'warning'); return; }
        if ($('#an-sig-rem').checked) SFM.annotSignatureSave(url).catch(() => {});
        finish(url);
      });

      // remembered signatures
      async function loadSaved() {
        const r = await SFM.annotSignatures().catch(() => null);
        const list = (r && r.ok && r.signatures) || [];
        const box = $('#an-sig-saved');
        box.classList.toggle('hidden', !list.length);
        box.innerHTML = list.length ? `<div class="section-title">Saved signatures</div><div class="an-sig-list">` +
          list.map((s, i) => `<div class="an-sig-item"><button class="an-sig-use" data-i="${i}" title="Use this signature"><img src="${s}" alt="Saved signature ${i + 1}"></button>
            <button class="icon-btn icon-btn-sm an-sig-forget" data-i="${i}" title="Forget this signature" aria-label="Forget this signature">${ic('x', 12)}</button></div>`).join('') + '</div>' : '';
        box.querySelectorAll('.an-sig-use').forEach(b => b.addEventListener('click', () => finish(list[+b.dataset.i])));
        box.querySelectorAll('.an-sig-forget').forEach(b => b.addEventListener('click', async () => { await SFM.annotSignatureForget(+b.dataset.i).catch(() => {}); loadSaved(); }));
      }
      loadSaved();
      redraw();
    });
  }

  // =========================================================================
  // ENTRY POINTS — preview toolbar button, details action, Ctrl+Shift+A
  // =========================================================================
  function _currentCandidate() {
    try {
      const sel = FileTree.getSelected ? FileTree.getSelected() : [];
      if (sel && sel.length === 1 && canAnnotate(sel[0])) return sel[0];
      if (sel && sel.length > 1) return null;
    } catch (_) {}
    try { const p = Preview.getCurrentPath && Preview.getCurrentPath(); if (p && canAnnotate(p)) return p; } catch (_) {}
    return null;
  }
  function _init() {
    const pb = document.getElementById('preview-annotate');
    if (pb) {
      pb.addEventListener('click', () => { const p = Preview.getCurrentPath && Preview.getCurrentPath(); if (p) open(p); });
      const sync = () => {
        let p = null; try { p = Preview.getCurrentPath && Preview.getCurrentPath(); } catch (_) {}
        pb.classList.toggle('hidden', !(p && canAnnotate(p)));
      };
      const nameEl = document.getElementById('preview-file-name');
      if (nameEl && window.MutationObserver) new MutationObserver(() => setTimeout(sync, 0)).observe(nameEl, { childList: true, characterData: true, subtree: true });
      sync();
    }
    document.addEventListener('keydown', e => {
      if (!(e.ctrlKey || e.metaKey) || !e.shiftKey || (e.key !== 'A' && e.key !== 'a')) return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (_open || document.querySelector('.modal-overlay')) return;
      e.preventDefault();
      const p = _currentCandidate();
      if (p) open(p); else App.toast('Select one PDF or image to annotate', 'info');
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();

  return { open, canAnnotate, isOpen: () => !!_open };
})();
