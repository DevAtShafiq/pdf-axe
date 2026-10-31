/**
 * pdf-edit.js — full-window PDF text editor ("Edit text", like Acrobat's Edit PDF).
 *
 *   PdfEdit.open(path)      open the editor for a PDF
 *   PdfEdit.canEdit(path)   true for PDFs
 *   PdfEdit.open(path, {embed})  PDF Studio: lives inside embed.host without
 *                           its title and save bars; open() resolves to a
 *                           controller. Save / close / "nothing left to undo"
 *                           go to embed.onKey('save'|'close'|'undo'|'redo').
 *
 * Click a text block to edit it in place (Enter = new line, Esc = cancel,
 * click outside = keep). Drag a block to move it; the handle on its right edge
 * changes the wrap width. Toolbar: Add text, font, size, bold/italic, colour,
 * alignment, Delete, Undo/Redo, zoom, page navigation.
 *
 * Every other thing on the page is an object too (pdf_objects.py): pictures,
 * shapes, lines, table grids, logos. Click one to select it (handles appear),
 * drag to move (Shift = straight), drag a handle to resize (corners keep the
 * proportions of pictures; Shift toggles), arrows nudge (Shift = 10 pt),
 * Delete removes, Esc deselects, Tab walks through the objects of the page.
 * Pictures: replace, crop, rotate 90°; shapes: line / fill colour, line width.
 * "Add image" places a new picture. While you work the page picture is
 * re-rendered with the changes (pdfedit_page_png with the pending edits), and
 * a dragged object is shown as its own transparent sprite.
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
  async function open(path, opts) {
    const embed = (opts && opts.embed) || null;
    if (!isPdf(path)) { App.toast('Edit text works with PDF files', 'info'); return; }
    // Opens in PDF Studio (all PDF tools, one Undo and one Save).
    if (!embed && window.Studio && !(opts && opts.standalone)) return Studio.open(path, { tool: 'edit' });
    if (_open) { _open.focus(); return; }
    if (!_fonts) {
      const fr = await SFM.pdfeditFonts().catch(() => null);
      _fonts = (fr && fr.ok && fr.fonts) || [{ name: 'Arial', css: 'Arial, sans-serif' }];
    }

    const ov = document.createElement('div');
    ov.className = embed ? 'an-overlay pe-overlay an-embedded' : 'modal-overlay an-overlay pe-overlay';
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
            <button class="btn btn-sm btn-ghost pe-tool pe-add-btn" data-a="add-image" data-tooltip="Place a picture on the page · I" aria-label="Add image">${ic('image', 14)}Add image</button>
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
          <div class="an-tb-group pe-objtools hidden" id="pe-objtools" aria-label="Object tools">
            <span class="pe-objkind" id="pe-objkind"></span>
            <button class="btn btn-sm btn-ghost pe-tool" data-a="img-replace" data-tooltip="Replace this picture" aria-label="Replace picture">${ic('image', 14)}Replace…</button>
            <button class="icon-btn pe-tool" data-a="obj-crop" data-tooltip="Crop · C (Enter applies)" aria-label="Crop">${ic('crop')}</button>
            <button class="icon-btn pe-tool" data-a="obj-rotate" data-tooltip="Rotate 90° · R" aria-label="Rotate 90 degrees">${ic('rotate-cw')}</button>
            <span class="pe-objstyle" id="pe-objstyle">
              <span class="toolbar-sep"></span>
              <span class="pe-swlabel">Line</span>
              <label class="pe-color" data-tooltip="Line colour"><input type="color" id="pe-ostroke" aria-label="Line colour"><span class="pe-color-sw" id="pe-ostroke-sw"></span></label>
              <input class="input-text pe-size pe-owidth" id="pe-owidth" type="number" min="0" max="72" step="0.25" aria-label="Line width" title="Line width (pt)">
              <button class="icon-btn icon-btn-sm pe-tool pe-none" data-a="stroke-none" data-tooltip="No line" aria-label="No line" aria-pressed="false"><span class="pe-none-sw"></span></button>
              <span class="pe-swlabel">Fill</span>
              <label class="pe-color" data-tooltip="Fill colour"><input type="color" id="pe-ofill" aria-label="Fill colour"><span class="pe-color-sw" id="pe-ofill-sw"></span></label>
              <button class="icon-btn icon-btn-sm pe-tool pe-none" data-a="fill-none" data-tooltip="No fill" aria-label="No fill" aria-pressed="false"><span class="pe-none-sw"></span></button>
            </span>
            <span class="toolbar-sep"></span>
            <button class="icon-btn icon-btn-danger pe-tool" data-a="obj-delete" data-tooltip="Delete · Del" aria-label="Delete object">${ic('trash')}</button>
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
    (embed ? embed.host : document.body).appendChild(ov);
    const $ = s => ov.querySelector(s);
    const $$ = s => Array.from(ov.querySelectorAll(s));
    const root = $('.pe-editor'), view = $('#pe-view'), pagesEl = $('#pe-pages');

    // ── state ──────────────────────────────────────────────────────────────
    let src = path;                 // file being shown (switches to the output after Save)
    let info = null;                // {pages:[{w,h,scanned}]}
    let zoom = 1, curPage = 0, mode = 'select';
    let pageData = new Map();       // p -> {blocks, objects, scanned, error} | Promise
    // ov: text block changes · adds: new text boxes · objs: object changes
    // {bbox, rotate, crop, delete, stroke, fill, width, replace: imgStore id}
    // · addImgs: new pictures {id, page, rect, rotate, img: imgStore id}
    let model = { ov: {}, adds: [], objs: {}, addImgs: [] };
    const imgStore = new Map();     // id -> {data_url, w, h, name} (kept out of the undo snapshots)
    let undoStack = [], redoStack = [];
    let sel = null;                 // {kind:'block'|'add'|'obj'|'aimg', key}
    let editing = null;             // {kind, key, ta, before}
    let cropping = null;            // {key, rect} while the crop frame is shown
    let busy = false, closed = false;
    const pageEls = [], thumbEls = [];
    const rendered = new Map();     // p -> {scale, key, shown:{object id: edit json}}
    const sprites = new Map();      // "key|scale|style" -> data_url | Promise
    let lastNudge = null;           // {key, t}: arrow-key nudges in a row are one undo step

    const pages = () => (info ? info.pages : []);
    const snap = () => JSON.stringify(model);
    const nChanges = () => Object.keys(model.ov).length + model.adds.length + Object.keys(model.objs).length + model.addImgs.length;
    const dirty = () => nChanges() > 0;
    function pushUndo() { undoStack.push(snap()); if (undoStack.length > 300) undoStack.shift(); redoStack = []; lastNudge = null; }
    function undo() { commitEdit(); endCrop(false); if (!undoStack.length) return; redoStack.push(snap()); model = JSON.parse(undoStack.pop()); lastNudge = null; fixSel(); renderAll(); refreshBgs(); }
    function redo() { commitEdit(); endCrop(false); if (!redoStack.length) return; undoStack.push(snap()); model = JSON.parse(redoStack.pop()); lastNudge = null; fixSel(); renderAll(); refreshBgs(); }
    function fixSel() { if (sel && !itemOf(sel)) sel = null; }

    // ── model helpers ──────────────────────────────────────────────────────
    const keyOf = (p, id) => p + ':' + id;
    function blockOf(key) {
      const [p, id] = key.split(':'); const d = pageData.get(+p);
      return d && d.blocks ? d.blocks.find(b => b.id === id) : null;
    }
    function objOf(key) {
      const [p, id] = key.split(':'); const d = pageData.get(+p);
      return d && d.objects ? d.objects.find(o => o.id === id) : null;
    }
    const aimgOf = id => model.addImgs.find(a => a.id === id) || null;
    function itemOf(s) {
      if (!s) return null;
      if (s.kind === 'block') return blockOf(s.key);
      if (s.kind === 'obj') { const o = objOf(s.key); return o && !(model.objs[s.key] || {}).delete ? o : null; }
      if (s.kind === 'aimg') return aimgOf(s.key);
      return model.adds.find(a => a.id === s.key) || null;
    }
    const isObjSel = s => !!(s && (s.kind === 'obj' || s.kind === 'aimg'));
    /** effective view of a block or added box: {page, text, font, family, size, color, bold, italic, align, bbox, single, lh, deleted} */
    function eff(s) {
      if (isObjSel(s)) return null;
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

    // ── page objects: geometry ─────────────────────────────────────────────
    // Matrices [a,b,c,d,e,f] with row vectors, like PDF / PyMuPDF: p·A·B applies A first.
    const mMul = (A, B) => [A[0] * B[0] + A[1] * B[2], A[0] * B[1] + A[1] * B[3], A[2] * B[0] + A[3] * B[2],
                            A[2] * B[1] + A[3] * B[3], A[4] * B[0] + A[5] * B[2] + B[4], A[4] * B[1] + A[5] * B[3] + B[5]];
    const mInv = A => {
      const det = A[0] * A[3] - A[1] * A[2] || 1e-9;
      const a = A[3] / det, b = -A[1] / det, c = -A[2] / det, d = A[0] / det;
      return [a, b, c, d, -(A[4] * a + A[5] * c), -(A[4] * b + A[5] * d)];
    };
    const mPt = (A, x, y) => [x * A[0] + y * A[2] + A[4], x * A[1] + y * A[3] + A[5]];
    function mRect(A, r) {
      const ps = [[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]]].map(q => mPt(A, q[0], q[1]));
      const xs = ps.map(q => q[0]), ys = ps.map(q => q[1]);
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
    const normDeg = d => (((Math.round((d || 0) / 90) * 90) % 360) + 360) % 360;
    /** rotate *src* by deg (clockwise) about its centre, then scale / move it onto *dst* (= pdf_objects.rect_transform) */
    function rectT(src, deg, dst) {
      deg = normDeg(deg);
      let sw = Math.max(src[2] - src[0], 1e-6), sh = Math.max(src[3] - src[1], 1e-6);
      if (deg === 90 || deg === 270) [sw, sh] = [sh, sw];
      const c = Math.round(Math.cos(deg * Math.PI / 180)), s = Math.round(Math.sin(deg * Math.PI / 180));
      let m = [1, 0, 0, 1, -(src[0] + src[2]) / 2, -(src[1] + src[3]) / 2];
      m = mMul(m, [c, s, -s, c, 0, 0]);
      return mMul(m, [(dst[2] - dst[0]) / sw, 0, 0, (dst[3] - dst[1]) / sh, (dst[0] + dst[2]) / 2, (dst[1] + dst[3]) / 2]);
    }
    const swapBox = r => { const cx = (r[0] + r[2]) / 2, cy = (r[1] + r[3]) / 2, w = r[2] - r[0], h = r[3] - r[1];
                           return [cx - h / 2, cy - w / 2, cx + h / 2, cy + w / 2]; };
    const pageOfKey = k => +String(k).split(':')[0];
    /** effective view of an object or added picture → {page, full, src, rot, bbox, T, kind, …} */
    function effObj(s) {
      if (s.kind === 'aimg') {
        const a = aimgOf(s.key); if (!a) return null;
        const rot = normDeg(a.rotate), full = (rot === 90 || rot === 270) ? swapBox(a.rect) : a.rect.slice();
        return { page: a.page, kind: 'image', added: true, a, full, src: full, rot, bbox: a.rect, T: rectT(full, rot, a.rect), v: {}, aspect: true };
      }
      const o = objOf(s.key); if (!o) return null;
      const v = model.objs[s.key] || {};
      const rot = normDeg(v.rotate), src = v.crop || o.full, bbox = v.bbox || o.full;
      return { page: pageOfKey(s.key), kind: o.kind, o, v, full: o.full, src, rot, bbox, T: rectT(src, rot, bbox),
               deleted: !!v.delete, aspect: o.kind !== 'path' };
    }
    /** set / merge changes of object *key*; values equal to the original are dropped */
    function setObj(key, patch, { undoable = true } = {}) {
      if (undoable) pushUndo();
      const o = objOf(key); if (!o) return;
      const v = { ...(model.objs[key] || {}), ...patch };
      if (v.bbox && v.bbox.every((x, i) => Math.abs(x - o.full[i]) < 0.05)) delete v.bbox;
      if (!normDeg(v.rotate)) delete v.rotate;
      if (v.crop && v.crop.every((x, i) => Math.abs(x - o.full[i]) < 0.05)) delete v.crop;
      if (!v.delete) delete v.delete;
      if (v.stroke === undefined) delete v.stroke;
      if (v.fill === undefined) delete v.fill;
      if (v.width === undefined || v.width === null) delete v.width;
      if (v.replace == null) delete v.replace;
      if (Object.keys(v).length) model.objs[key] = v; else delete model.objs[key];
    }
    /** edit for the engine; preview=true: no picture data, replaced pictures hidden */
    function objEdit(key, v, o, preview) {
      const e = { type: 'object', page: pageOfKey(key), object_id: o.id };
      if (v.bbox) e.bbox = v.bbox.map(r2);
      if (v.rotate) e.rotate = v.rotate;
      if (v.crop) e.crop = v.crop.map(r2);
      if (v.delete) e.delete = true;
      ['stroke', 'fill', 'width'].forEach(x => { if (v[x] !== undefined) e[x] = v[x]; });
      if (v.replace) { if (preview) e.replace = true; else { e.data_url = (imgStore.get(v.replace) || {}).data_url; e.fit = 'contain'; } }
      if (!preview) e.orig_bbox = o.bbox;
      return e;
    }
    /** object edits drawn into the page picture of page p (the dragged object is hidden) */
    function bgEdits(p) {
      const out = [];
      const hide = drag && drag.moved && drag.s && drag.s.kind === 'obj' && pageOfKey(drag.s.key) === p ? drag.s.key
        : cropping && pageOfKey(cropping.key) === p ? cropping.key : null;
      Object.entries(model.objs).forEach(([k, v]) => {
        if (pageOfKey(k) !== p || k === hide) return;
        const o = objOf(k); if (o) out.push(objEdit(k, v, o, true));
      });
      if (hide) out.push({ type: 'object', page: p, object_id: objOf(hide).id, delete: true });
      return out;
    }
    const editJson = e => JSON.stringify(e);
    /** does the page picture already show object *key* as the model wants it? */
    function bgShows(key) {
      const p = pageOfKey(key), have = rendered.get(p), o = objOf(key);
      if (!have || !o) return false;
      const v = model.objs[key];
      const want = v ? editJson(objEdit(key, v, o, true)) : undefined;
      return have.shown[o.id] === want;
    }
    function refreshBgs() { pages().forEach((_, p) => { if (pageData.get(p) && !(pageData.get(p) instanceof Promise)) wantPage(p); }); }

    // sprite = the object alone (transparent around it), for dragging and while the page picture catches up
    function spriteFor(key, e) {
      const st = {};
      ['stroke', 'fill', 'width'].forEach(x => { if (e.v[x] !== undefined) st[x] = e.v[x]; });
      const sc = r2(clamp(zoom * dpr(), 0.75, 4));
      const id = `${key}|${sc}|${JSON.stringify(st)}`;
      const have = sprites.get(id);
      if (typeof have === 'string') return have;
      if (!have) {
        const file = src;
        sprites.set(id, SFM.pdfeditObjectPng(file, pageOfKey(key), e.o.id, sc, Object.keys(st).length ? st : null).then(r => {
          if (file !== src) return;
          if (r && r.ok) { sprites.set(id, r.data_url); renderPage(pageOfKey(key)); }
          else sprites.delete(id);
        }).catch(() => sprites.delete(id)));
      }
      // a slightly older sprite (other zoom / colour) is better than nothing
      for (const [k2, v2] of sprites) if (k2.startsWith(key + '|') && typeof v2 === 'string') return v2;
      return null;
    }

    // ── pages / rendering ──────────────────────────────────────────────────
    const dpr = () => Math.max(1, window.devicePixelRatio || 1);
    const neededScale = () => clamp(zoom * dpr(), 0.5, 4);
    let renderQ = [], pumping = false;
    function wantPage(p) {
      loadData(p);
      const need = neededScale(), have = rendered.get(p);
      const d = pageData.get(p), ready = d && !(d instanceof Promise);
      const key = ready ? JSON.stringify(bgEdits(p)) : (have ? have.key : '[]');
      if (have && have.scale >= need * 0.85 && have.scale <= need * 2.2 && have.key === key) return;
      if (!renderQ.includes(p)) renderQ.push(p);
      pump();
    }
    async function pump() {
      if (pumping) return;
      pumping = true;
      try {
        while (renderQ.length && !closed) {
          const p = renderQ.shift(), sc = neededScale(), file = src;
          const d = pageData.get(p), ready = d && !(d instanceof Promise);
          const edits = ready ? bgEdits(p) : [];
          const key = JSON.stringify(edits);
          const r = await SFM.pdfeditPagePng(file, p, sc, edits.length ? { objects: edits } : null).catch(() => null);
          if (closed || file !== src) return;
          const el = pageEls[p];
          if (r && r.ok && el) {
            const img = el.querySelector('.an-bg'); img.src = r.data_url; img.classList.add('on');
            el.querySelector('.an-ph')?.remove();
            const shown = {};
            edits.forEach(e => { shown[e.object_id] = editJson(e); });
            rendered.set(p, { scale: sc, key, shown });
            if (ready) renderPage(p);
            if (ready && JSON.stringify(bgEdits(p)) !== key && !renderQ.includes(p)) renderQ.push(p);   // changed meanwhile
          } else if (r && !r.ok && r.code === 'stale') {
            App.toast(esc(r.error), 'error', 6000);
          }
        }
      } finally { pumping = false; }
    }
    function loadData(p) {
      if (pageData.has(p)) return pageData.get(p);
      const file = src;
      const pr = Promise.all([SFM.pdfeditBlocks(file, p).catch(() => null), SFM.pdfeditObjects(file, p).catch(() => null)]).then(([r, ro]) => {
        if (file !== src) return;
        const objects = (ro && ro.ok && ro.objects) || [];
        let d;
        if (r && r.ok) d = { blocks: r.blocks || [], objects };
        else if (r && r.code === 'scanned_page') d = { blocks: [], objects, scanned: true };
        else d = { blocks: [], objects, error: (r && r.error) || 'Could not read the text on this page' };
        pageData.set(p, d);
        renderPage(p);
        if (Object.keys(model.objs).some(k => pageOfKey(k) === p)) wantPage(p);
        if (p === curPage) hint();
        return d;
      }).catch(() => { pageData.set(p, { blocks: [], objects: [], error: 'Could not read the text on this page' }); });
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
      // page objects (under the text): bigger ones first so smaller ones stay clickable
      const objs = (d.objects || []).map(o => ({ o, s: { kind: 'obj', key: keyOf(p, o.id) } }))
        .concat(model.addImgs.filter(a => a.page === p).map(a => ({ o: null, s: { kind: 'aimg', key: a.id } })));
      const area = r => (r[2] - r[0]) * (r[3] - r[1]);
      objs.forEach(x => { x.e = effObj(x.s); });
      objs.filter(x => x.e && !x.e.deleted).sort((a, b) => (a.o ? 0 : 1) - (b.o ? 0 : 1) || area(b.e.bbox) - area(a.e.bbox))
        .forEach(x => { h += objHtml(x.s, x.e); });
      if (dragBadge && dragBadge.p === p) h += `<div class="pe-badge" style="left:${px(dragBadge.x)};top:${px(dragBadge.y)}">${esc(dragBadge.text)}</div>`;
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
      if (isObjSel(sel)) { const se = effObj(sel); if (se && se.page === p && !se.deleted) h += selHtml(se); }
      layer.innerHTML = h;
      if (editing && editing.page === p) layer.appendChild(editing.ta);
      const tb = thumbEls[p];
      if (tb) {
        const n = Object.keys(model.ov).filter(k => +k.split(':')[0] === p).length + model.adds.filter(a => a.page === p).length +
                  Object.keys(model.objs).filter(k => pageOfKey(k) === p).length + model.addImgs.filter(a => a.page === p).length;
        const c = tb.querySelector('.an-thumb-n'); c.textContent = n || ''; c.classList.toggle('hidden', !n);
      }
    }
    function textHtml(e, tb) {
      const st = `font-family:${e.family};font-size:${px(e.size)};line-height:${px(tb.lineH)};color:${e.color};` +
        `font-weight:${e.bold ? 700 : 400};font-style:${e.italic ? 'italic' : 'normal'};text-align:${e.align};` +
        `white-space:${e.single ? 'pre' : 'pre-wrap'}`;
      return `<div class="pe-text" style="${st}">${esc(e.text)}</div>`;
    }
    // The whole original object (picture *url*), rotated / scaled like the
    // model says, positioned relative to *origin* (page units).
    function visImg(e, url, fit, origin, extra = '') {
      const tf = mRect(e.T, e.full);
      const cx = (tf[0] + tf[2]) / 2 - origin[0], cy = (tf[1] + tf[3]) / 2 - origin[1];
      const W = (e.full[2] - e.full[0]) * Math.hypot(e.T[0], e.T[1]), H = (e.full[3] - e.full[1]) * Math.hypot(e.T[2], e.T[3]);
      return `<img class="pe-objvis${extra}" src="${url}" alt="" draggable="false" style="left:${px(cx - W / 2)};top:${px(cy - H / 2)};` +
             `width:${px(W)};height:${px(H)};object-fit:${fit};${e.rot ? `transform:rotate(${e.rot}deg);` : ''}">`;
    }
    function visUrl(s, e, live) {
      if (s.kind === 'aimg') return [(imgStore.get(e.a.img) || {}).data_url, 'fill'];
      if (e.v.replace) return [(imgStore.get(e.v.replace) || {}).data_url, 'contain'];
      if (live) return [spriteFor(s.key, e), 'fill'];
      return [null];
    }
    function objHtml(s, e) {
      const k = s.key, isSel = !!(sel && sel.kind === s.kind && sel.key === k);
      const crop = !!(cropping && s.kind === 'obj' && cropping.key === k);
      const b = crop ? cropping.rect : e.bbox;          // while cropping: only the kept part at full strength
      const dragging = !!(drag && drag.moved && drag.s && drag.s.kind === s.kind && drag.s.key === k);
      const live = s.kind === 'obj' && (dragging || crop || (!!model.objs[k] && !bgShows(k)));
      const [url, fit] = visUrl(s, e, live);
      const bg = !!(e.o && e.o.background);
      const vis = url ? `<div class="pe-objclip">${visImg(e, url, fit, b)}</div>` : '';
      let hits = '';
      if (!bg) {
        const parts = (e.o && e.o.parts) || [e.full];
        const minH = 7 / zoom;
        hits = parts.map(r0 => {
          const q = mRect(e.T, r0);
          if (q[2] - q[0] < minH) { const c = (q[0] + q[2]) / 2; q[0] = c - minH / 2; q[2] = c + minH / 2; }
          if (q[3] - q[1] < minH) { const c = (q[1] + q[3]) / 2; q[1] = c - minH / 2; q[3] = c + minH / 2; }
          return `<div class="pe-hit" style="left:${px(q[0] - b[0])};top:${px(q[1] - b[1])};width:${px(q[2] - q[0])};height:${px(q[3] - q[1])}"></div>`;
        }).join('');
      }
      const cls = `pe-obj pe-obj-${e.kind}${s.kind === 'aimg' ? ' pe-obj-added' : ''}${bg ? ' pe-bgobj' : ''}${isSel ? ' is-sel' : ''}${dragging ? ' is-drag' : ''}`;
      return `<div class="${cls}" data-kind="${s.kind}" data-k="${esc(k)}"
               style="left:${px(b[0])};top:${px(b[1])};width:${px(b[2] - b[0])};height:${px(b[3] - b[1])}">${vis}${hits}</div>`;
    }
    // selection frame with 8 handles (crop mode: the crop frame over a faint full picture)
    function selHtml(e) {
      const r = cropping ? cropping.rect : e.bbox;
      let ghost = '';
      if (cropping) {
        const [url, fit] = visUrl(sel, e, true);
        if (url) ghost = `<div class="pe-cropghost">${visImg(e, url, fit, [0, 0])}</div>`;
      }
      const wpx = (r[2] - r[0]) * zoom, hpx = (r[3] - r[1]) * zoom;
      const hs = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].filter(hd => {
        if (hpx < 14 && /[ns]/.test(hd)) return false;        // a horizontal line: only left / right
        if (wpx < 14 && /[ew]/.test(hd)) return false;
        if ((hd === 'n' || hd === 's') && wpx < 40) return false;
        if ((hd === 'e' || hd === 'w') && hpx < 40) return false;
        return true;
      });
      return ghost + `<div class="pe-selbox${cropping ? ' is-crop' : ''}" style="left:${px(r[0])};top:${px(r[1])};width:${px(r[2] - r[0])};height:${px(r[3] - r[1])}">` +
        hs.map(hd => `<span class="pe-oh pe-oh-${hd}" data-oh="${hd}"></span>`).join('') + `</div>`;
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
      else if (cropping) { t = 'Drag the handles to crop · Enter applies · Esc cancels'; icon = 'crop'; }
      else if (isObjSel(sel)) t = 'Drag to move (Shift = straight) · handles resize · arrows nudge · Del deletes · Esc deselects · Tab = next object';
      else t = 'Click text to edit it · click a picture, shape or line to select it · drag to move · Enter = new line · Esc cancels';
      $('#pe-hint').innerHTML = `${ic(icon, 14)}<span>${esc(t)}</span>`;
      $('#pe-hint').classList.toggle('pe-hint-warn', !!(d && d.scanned));
    }
    function status() {
      const n = nChanges();
      $('#pe-status').textContent = n ? `${n} change${n === 1 ? '' : 's'} · unsaved` : 'No changes';
      $('#pe-status').className = 'pill ' + (n ? 'pill-yellow' : 'pill-neutral');
      ov.querySelector('[data-a="undo"]').disabled = busy || !undoStack.length;
      ov.querySelector('[data-a="redo"]').disabled = busy || !redoStack.length;
      if (embed && embed.onChange) embed.onChange();
    }
    function syncToolbar() {
      const s = sel, e = s && !isObjSel(s) ? eff(s) : null;
      const textOn = !!(e && !e.deleted && e.editable !== false);
      const oe = isObjSel(s) ? effObj(s) : null;
      $('#pe-style').classList.toggle('hidden', !!oe);
      $('#pe-style').classList.toggle('is-off', !textOn && !(s && s.kind === 'block'));
      $$('#pe-style select, #pe-style input, #pe-style button').forEach(c => { c.disabled = busy || !textOn; });
      ov.querySelector('[data-a="delete"]').disabled = busy || !(s && !isObjSel(s) && !(e && e.deleted));
      $('#pe-objtools').classList.toggle('hidden', !oe);
      if (oe) {
        const pic = oe.kind === 'image' || oe.kind === 'inline';
        const n = oe.o ? oe.o.paths : 0;
        $('#pe-objkind').textContent = oe.added ? 'New picture' : pic ? 'Picture' : oe.kind === 'path' ? (n > 1 ? `Graphic · ${n} parts` : 'Shape') :
          oe.kind === 'shade' ? 'Shading' : 'Graphic';
        ov.querySelector('[data-a="img-replace"]').classList.toggle('hidden', !pic);
        ov.querySelector('[data-a="obj-crop"]').classList.toggle('hidden', !pic || !!oe.added);
        ov.querySelector('[data-a="obj-crop"]').classList.toggle('active', !!cropping);
        const shape = oe.kind === 'path';
        $('#pe-objstyle').classList.toggle('hidden', !shape);
        if (shape) {
          const v = oe.v, o = oe.o;
          const sk = v.stroke !== undefined ? v.stroke : (o.has_stroke ? (o.stroke || '#000000') : 'none');
          const fl = v.fill !== undefined ? v.fill : (o.has_fill ? (o.fill || '#000000') : 'none');
          const setSw = (id, val) => {
            const none = val === 'none';
            if (!none && /^#[0-9a-f]{6}$/i.test(val)) $('#' + id).value = val;
            $('#' + id + '-sw').style.background = none ? 'transparent' : val;
            $('#' + id + '-sw').classList.toggle('is-none', none);
          };
          setSw('pe-ostroke', sk); setSw('pe-ofill', fl);
          ov.querySelector('[data-a="stroke-none"]').classList.toggle('active', sk === 'none');
          ov.querySelector('[data-a="stroke-none"]').setAttribute('aria-pressed', sk === 'none');
          ov.querySelector('[data-a="fill-none"]').classList.toggle('active', fl === 'none');
          ov.querySelector('[data-a="fill-none"]').setAttribute('aria-pressed', fl === 'none');
          if (document.activeElement !== $('#pe-owidth')) $('#pe-owidth').value = r2(v.width != null ? v.width : (o.width || (sk === 'none' ? 0 : 1)));
          $('#pe-owidth').disabled = busy || sk === 'none';
        }
        $$('#pe-objtools button, #pe-objtools input').forEach(c => { if (c.id !== 'pe-owidth') c.disabled = busy; });
      }
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
      if (cropping && !(s && sel && s.key === sel.key && s.kind === sel.kind)) endCrop(true);
      const old = sel;
      sel = s;
      if (old) renderPage(pageOfSel(old));
      if (s) renderPage(pageOfSel(s));
      syncToolbar(); hint();
    }
    function pageOfSel(s) {
      if (s.kind === 'add') { const a = model.adds.find(x => x.id === s.key); return a ? a.page : 0; }
      if (s.kind === 'aimg') { const a = aimgOf(s.key); return a ? a.page : 0; }
      return +s.key.split(':')[0];
    }
    // Character index in textarea *ta* nearest to the screen point (x, y):
    // a hidden copy of the box with one span per character, laid out the same.
    function caretAt(ta, x, y) {
      const text = ta.value;
      if (!text) return 0;
      const cs = getComputedStyle(ta), r = ta.getBoundingClientRect();
      const m = document.createElement('div');
      ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight', 'textAlign',
       'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderLeftWidth',
       'borderRightWidth', 'borderBottomWidth', 'boxSizing', 'wordSpacing', 'textIndent', 'tabSize']
        .forEach(k => { m.style[k] = cs[k]; });
      Object.assign(m.style, { position: 'fixed', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px',
        visibility: 'hidden', whiteSpace: 'pre-wrap', overflowWrap: 'break-word', borderStyle: 'solid', margin: '0' });
      const spans = [];
      for (const ch of text) { const sp = document.createElement('span'); sp.textContent = ch; spans.push(sp); m.appendChild(sp); }
      document.body.appendChild(m);
      let best = text.length, bestD = Infinity, idx = 0;
      try {
        for (const sp of spans) {
          const b = sp.getBoundingClientRect();
          const dy = y < b.top ? b.top - y : y > b.bottom ? y - b.bottom : 0;
          const before = Math.abs(x - b.left), after = Math.abs(x - b.right);
          const d = dy * 1000 + Math.min(before, after);
          if (d < bestD) { bestD = d; best = idx + (after < before ? sp.textContent.length : 0); }
          idx += sp.textContent.length;
        }
      } finally { m.remove(); }
      return best;
    }
    function startEdit(s, at = null) {
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
      const focusIt = () => {
        if (!editing || editing.ta !== ta || document.activeElement === ta) return;
        ta.focus();
        // A click puts the cursor where you clicked (like Word / Acrobat); Enter puts it at the end.
        const i = at ? caretAt(ta, at[0], at[1]) : ta.value.length;
        ta.setSelectionRange(i, i);
      };
      focusIt();
      setTimeout(focusIt, 0);       // the click that opened the editor may still move focus
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
          if (ed.before !== '') pushUndo();     // a brand-new box was snapshotted when it was created
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
    // While the inline editor is open its text lives only in the textarea.
    // Before a toolbar change takes an undo snapshot, write the typed text into
    // the model (as its own undo step for an existing box / block), so undoing
    // the style change never wipes what was typed.
    function flushEditText() {
      if (!editing) return;
      const val = editing.ta.value.replace(/\r\n?/g, '\n');
      if (val === editing.before || !val.trim()) return;
      if (editing.kind === 'block') setOv(editing.key, { text: val });
      else {
        const a = model.adds.find(x => x.id === editing.key); if (!a) return;
        if (editing.before !== '') pushUndo();     // a new box was snapshotted when created
        a.text = val;
      }
      editing.before = val;
    }
    function applyStyle(patch) {
      if (!sel || isObjSel(sel)) return;
      flushEditText();
      // no-op changes (e.g. the size box firing 'change' twice) must not add undo steps
      const cur = eff(sel);
      if (cur && Object.keys(patch).every(k => (k === 'font' ? (cur.font || 'original') : cur[k]) === patch[k])) {
        if (editing) editing.ta.focus();
        return;
      }
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
      if (cropping) endCrop(false);
      const p = pageOfSel(sel);
      if (sel.kind === 'block') setOv(sel.key, { deleted: true });
      else if (sel.kind === 'add') { pushUndo(); model.adds = model.adds.filter(a => a.id !== sel.key); sel = null; }
      else if (sel.kind === 'obj') { setObj(sel.key, { delete: true }); sel = null; wantPage(p); }
      else if (sel.kind === 'aimg') { pushUndo(); model.addImgs = model.addImgs.filter(a => a.id !== sel.key); sel = null; }
      renderAll(); hint();
    }

    // ── object commands ────────────────────────────────────────────────────
    let _imgN = 0;
    async function pickImage() {
      const pick = await SFM.annotPickPhoto().catch(() => null);
      if (!pick || !pick.ok || !pick.path) return null;
      App.setStatus('Opening picture…', true);
      const r = await SFM.pdfeditLoadImage(pick.path, 2400).catch(() => null);
      App.setStatus('Ready');
      if (!r || !r.ok) { App.toast(esc((r && r.error) || 'Could not open the picture'), 'error'); return null; }
      const id = 'im' + Date.now().toString(36) + (++_imgN);
      imgStore.set(id, { data_url: r.data_url, w: r.width, h: r.height, name: r.name });
      return id;
    }
    async function addImage() {
      commitEdit(); endCrop(true);
      if (mode !== 'select') setMode('select');
      const id = await pickImage(); if (!id || closed) return;
      const im = imgStore.get(id), p = clamp(curPage, 0, pages().length - 1), pg = pages()[p];
      // natural size at 96 dpi, at most half the page wide / 40 % high
      let w = im.w * 0.75, h = im.h * 0.75;
      const k = Math.min(1, pg.w * 0.5 / w, pg.h * 0.4 / h);
      w *= k; h *= k;
      // centre of the visible part of the page
      const r = pageEls[p].getBoundingClientRect(), vr = view.getBoundingClientRect();
      const cy = clamp(((Math.max(r.top, vr.top) + Math.min(r.bottom, vr.bottom)) / 2 - r.top) / zoom, h / 2, pg.h - h / 2);
      const cx = pg.w / 2;
      pushUndo();
      const a = { id: 'aimg' + Date.now().toString(36) + (++_uid), page: p, rect: [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2].map(r2), rotate: 0, img: id };
      model.addImgs.push(a);
      sel = { kind: 'aimg', key: a.id };
      renderAll(); hint();
    }
    async function replaceImg() {
      if (!isObjSel(sel)) return;
      const s = sel;
      const id = await pickImage(); if (!id || closed) return;
      if (s.kind === 'obj') { setObj(s.key, { replace: id }); wantPage(pageOfKey(s.key)); }
      else { const a = aimgOf(s.key); if (!a) return; pushUndo(); a.img = id; }
      renderAll();
    }
    function rotateSel() {
      if (!isObjSel(sel)) return;
      endCrop(true);
      const e = effObj(sel); if (!e) return;
      const rot = normDeg(e.rot + 90), box = swapBox(e.bbox).map(r2);
      if (sel.kind === 'obj') { setObj(sel.key, { rotate: rot, bbox: box }); wantPage(e.page); }
      else { pushUndo(); e.a.rotate = rot; e.a.rect = box; }
      renderAll();
    }
    function setObjStyle(patch) {
      if (!sel || sel.kind !== 'obj') return;
      const e = effObj(sel); if (!e || e.kind !== 'path') return;
      if (Object.keys(patch).every(k => e.v[k] === patch[k])) return;
      setObj(sel.key, patch);
      renderAll(); wantPage(e.page);
    }
    function startCrop() {
      if (!sel || sel.kind !== 'obj') return;
      const e = effObj(sel); if (!e || (e.kind !== 'image' && e.kind !== 'inline')) return;
      cropping = { key: sel.key, rect: e.bbox.slice() };
      renderPage(e.page); syncToolbar(); hint(); wantPage(e.page);
    }
    function endCrop(apply) {
      if (!cropping) return;
      const c = cropping; cropping = null;
      const s = { kind: 'obj', key: c.key }, e = effObj(s);
      if (apply && e && c.rect.some((v, i) => Math.abs(v - e.bbox[i]) > 0.05)) {
        const inv = mRect(mInv(e.T), c.rect);
        const crop = [Math.max(inv[0], e.full[0]), Math.max(inv[1], e.full[1]), Math.min(inv[2], e.full[2]), Math.min(inv[3], e.full[3])].map(r2);
        setObj(c.key, { crop, bbox: c.rect.map(r2) });
        wantPage(e.page);
      }
      if (e) { renderPage(e.page); wantPage(e.page); }
      syncToolbar(); hint(); status();
    }
    function nudge(dx, dy) {
      if (!sel || editing) return;
      const now = Date.now();
      const merge = lastNudge && lastNudge.key === sel.kind + sel.key && now - lastNudge.t < 1200;
      const mv = b => [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy].map(r2);
      if (!merge) pushUndo();
      if (sel.kind === 'obj') { const e = effObj(sel); if (!e) return; setObj(sel.key, { bbox: mv(e.bbox) }, { undoable: false }); }
      else if (sel.kind === 'aimg') { const a = aimgOf(sel.key); if (a) a.rect = mv(a.rect); }
      else if (sel.kind === 'block') { const e = eff(sel); if (!e || e.deleted) return; setOv(sel.key, { bbox: mv(e.bbox) }, { undoable: false }); }
      else if (sel.kind === 'add') { const a = model.adds.find(x => x.id === sel.key); if (a) a.rect = mv(a.rect); }
      lastNudge = { key: sel.kind + sel.key, t: now };
      renderPage(pageOfSel(sel)); status();
      clearTimeout(nudge._t);
      nudge._t = setTimeout(() => { if (sel) wantPage(pageOfSel(sel)); }, 350);
    }
    /** Tab / Shift+Tab: next / previous object of the current page (background ones included) */
    function cycleObj(dir) {
      const p = isObjSel(sel) ? pageOfSel(sel) : curPage;
      const d = pageData.get(p); if (!d || d instanceof Promise) return;
      const list = (d.objects || []).filter(o => !(model.objs[keyOf(p, o.id)] || {}).delete).map(o => ({ kind: 'obj', key: keyOf(p, o.id) }))
        .concat(model.addImgs.filter(a => a.page === p).map(a => ({ kind: 'aimg', key: a.id })));
      if (!list.length) return;
      let i = isObjSel(sel) ? list.findIndex(x => x.kind === sel.kind && x.key === sel.key) : -1;
      i = i < 0 ? (dir > 0 ? 0 : list.length - 1) : (i + dir + list.length) % list.length;
      select(list[i]);
    }
    $('#pe-ostroke').addEventListener('input', () => { $('#pe-ostroke-sw').style.background = $('#pe-ostroke').value; });
    $('#pe-ostroke').addEventListener('change', () => setObjStyle({ stroke: $('#pe-ostroke').value }));
    $('#pe-ofill').addEventListener('input', () => { $('#pe-ofill-sw').style.background = $('#pe-ofill').value; });
    $('#pe-ofill').addEventListener('change', () => setObjStyle({ fill: $('#pe-ofill').value }));
    $('#pe-owidth').addEventListener('change', () => { const v = parseFloat($('#pe-owidth').value); if (v >= 0 && v <= 72) setObjStyle({ width: r2(v) }); });
    function toggleNone(which) {
      const e = sel && sel.kind === 'obj' ? effObj(sel) : null; if (!e) return;
      const cur = e.v[which] !== undefined ? e.v[which] : (e.o['has_' + which] ? (e.o[which] || '#000000') : 'none');
      if (cur === 'none') setObjStyle({ [which]: e.o['has_' + which] ? undefined : (which === 'fill' ? '#ffffff' : '#000000') });
      else setObjStyle({ [which]: 'none' });
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
      else if (a === 'add-image') addImage();
      else if (a === 'img-replace') replaceImg();
      else if (a === 'obj-crop') { if (cropping) endCrop(true); else startCrop(); }
      else if (a === 'obj-rotate') rotateSel();
      else if (a === 'obj-delete') deleteSel();
      else if (a === 'stroke-none') toggleNone('stroke');
      else if (a === 'fill-none') toggleNone('fill');
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

    // ── pointer: click = edit, drag = move, handles = resize ───────────────
    // Text: click edits in place, drag moves, the right handle sets the width.
    // Objects: click selects, drag moves (Shift = straight), the 8 handles
    // resize (corners keep a picture's proportions; Shift toggles that), in
    // crop mode the handles move the crop frame.
    let drag = null;
    let dragBadge = null;            // {p, x, y, text} — size / position read-out while dragging
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
      const oh = e.target.closest('.pe-oh');
      const objEl = !item && e.target.closest('.pe-hit') ? e.target.closest('.pe-obj') : null;
      commitEdit();
      view.focus({ preventScroll: true });
      if (mode === 'add') {
        e.preventDefault();          // keep focus in the new text box (no mousedown focus change)
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
      if (oh && isObjSel(sel)) {                       // resize / crop handle of the selected object
        e.preventDefault();
        const oe = effObj(sel);
        drag = { mode: cropping ? 'crop' : 'oresize', hd: oh.dataset.oh, s: sel, p: oe.page, start: pt, moved: false,
                 bbox: (cropping ? cropping.rect : oe.bbox).slice(), aspect: oe.aspect, bounds: cropping ? mRect(oe.T, oe.full) : null };
        capture(e);
        return;
      }
      if (cropping) endCrop(true);
      // A signature / photo / stamp placed earlier is an annotation on top of
      // the page: clicking it hands it to the object selector (move, copy,
      // paste, delete) instead of editing what lies underneath.
      if (embed && embed.onAnnot && !drag) {
        const an = annotAt(p, pt);
        if (an) { e.preventDefault(); embed.onAnnot({ page: p, x: pt[0], y: pt[1], type: an.type }); return; }
      }
      if (objEl) {
        e.preventDefault();
        const s = { kind: objEl.dataset.kind, key: objEl.dataset.k };
        if (!(sel && sel.kind === s.kind && sel.key === s.key)) select(s);
        const oe = effObj(s); if (!oe) return;
        if (s.kind === 'obj') spriteFor(s.key, oe);    // have the sprite ready for the drag
        drag = { mode: 'omove', s, p: oe.page, start: pt, bbox: oe.bbox.slice(), moved: false };
        capture(e);
        return;
      }
      if (!item) { if (sel) select(null); drag = { mode: 'pan', sx: e.clientX, sy: e.clientY, sl: view.scrollLeft, st: view.scrollTop }; capture(e); return; }
      const s = { kind: item.dataset.kind, key: item.dataset.k };
      const handle = e.target.closest('.pe-handle');
      const already = sel && sel.kind === s.kind && sel.key === s.key;
      if (!already) select(s);
      const e0 = eff(s);
      drag = { mode: handle ? 'width' : 'move', s, p, start: pt, bbox: textBoxRect(e0), moved: false, snapped: false,
               at: [e.clientX, e.clientY] };
      capture(e);
    });
    // Signatures / photos / stamps already in the file (loaded once per file).
    let annList = null, annFor = null;
    async function loadAnnots() {
      if (!embed || !embed.onAnnot || annFor === src) return;
      annFor = src; annList = [];
      const r = await SFM.annotLoad(src).catch(() => null);
      if (annFor !== src) return;
      annList = ((r && r.ok && r.annotations) || []).filter(a => ['signature', 'photo', 'stamp'].includes(a.type) && a.rect);
    }
    function annotAt(p, pt) {
      if (annFor !== src) loadAnnots();
      const hit = (annList || []).filter(a => a.page === p && pt[0] >= a.rect[0] && pt[0] <= a.rect[2] && pt[1] >= a.rect[1] && pt[1] <= a.rect[3]);
      return hit.length ? hit[hit.length - 1] : null;    // the top-most one
    }
    if (embed && embed.onAnnot) setTimeout(loadAnnots, 0);
    // the box a move / width drag starts from: the block's (possibly moved) bbox
    const textBoxRect = e => e.bbox.slice();
    function capture(e) { try { pagesEl.setPointerCapture(e.pointerId); } catch (_) {} }
    /** new box for handle *hd* dragged by (dx, dy); keep=true keeps the proportions */
    function resizeBox(b, hd, dx, dy, keep, minS) {
      let [x0, y0, x1, y1] = b;
      if (hd.includes('w')) x0 = Math.min(x1 - minS, x0 + dx);
      if (hd.includes('e')) x1 = Math.max(x0 + minS, x1 + dx);
      if (hd.includes('n')) y0 = Math.min(y1 - minS, y0 + dy);
      if (hd.includes('s')) y1 = Math.max(y0 + minS, y1 + dy);
      if (keep && hd.length === 2) {
        const w0 = b[2] - b[0], h0 = b[3] - b[1];
        if (w0 > 0 && h0 > 0) {
          const sx = (x1 - x0) / w0, sy = (y1 - y0) / h0;
          const k = Math.abs(sx - 1) > Math.abs(sy - 1) ? sx : sy;
          const w = w0 * k, h = h0 * k;
          if (hd.includes('w')) x0 = x1 - w; else x1 = x0 + w;
          if (hd.includes('n')) y0 = y1 - h; else y1 = y0 + h;
        }
      }
      return [x0, y0, x1, y1];
    }
    pagesEl.addEventListener('pointermove', e => {
      if (!drag) return;
      if (drag.mode === 'pan') { view.scrollLeft = drag.sl - (e.clientX - drag.sx); view.scrollTop = drag.st - (e.clientY - drag.sy); return; }
      const pt = toUnits(e, drag.p);
      let dx = pt[0] - drag.start[0], dy = pt[1] - drag.start[1];
      if (!drag.moved && Math.hypot(dx, dy) * zoom < 4) return;
      const b = drag.bbox;
      let nb;
      if (drag.mode === 'crop') {
        drag.moved = true;
        nb = resizeBox(b, drag.hd, dx, dy, false, 2);
        const B = drag.bounds;            // the crop frame stays on the picture
        nb = [Math.max(nb[0], B[0]), Math.max(nb[1], B[1]), Math.min(nb[2], B[2]), Math.min(nb[3], B[3])];
        cropping.rect = nb.map(r2);
        dragBadge = { p: drag.p, x: nb[0], y: nb[3] + 4 / zoom, text: `${Math.round(nb[2] - nb[0])} × ${Math.round(nb[3] - nb[1])} pt` };
        renderPage(drag.p);
        return;
      }
      const first = !drag.moved;
      if (first) { drag.moved = true; pushUndo(); }
      if (drag.mode === 'omove' || drag.mode === 'oresize') {
        if (drag.mode === 'omove') {
          if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
          nb = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy];
        } else {
          nb = resizeBox(b, drag.hd, dx, dy, drag.aspect !== e.shiftKey, 2);
        }
        nb = nb.map(r2);
        if (drag.s.kind === 'obj') setObj(drag.s.key, { bbox: nb }, { undoable: false });
        else { const a = aimgOf(drag.s.key); if (a) a.rect = nb; }
        dragBadge = drag.mode === 'omove'
          ? { p: drag.p, x: nb[0], y: nb[3] + 4 / zoom, text: `${Math.round(nb[0])}, ${Math.round(nb[1])} pt` }
          : { p: drag.p, x: nb[0], y: nb[3] + 4 / zoom, text: `${Math.round(nb[2] - nb[0])} × ${Math.round(nb[3] - nb[1])} pt` };
        renderPage(drag.p);
        if (first && drag.s.kind === 'obj') wantPage(drag.p);     // page picture without the object
        return;
      }
      if (drag.mode === 'move') nb = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy];
      else nb = [b[0], b[1], Math.max(b[0] + 10, b[2] + dx), b[3]];
      nb = nb.map(r2);
      if (drag.s.kind === 'block') setOv(drag.s.key, { bbox: nb }, { undoable: false });
      else { const a = model.adds.find(x => x.id === drag.s.key); if (a) a.rect = nb; }
      renderPage(drag.p);
    });
    function endDrag() {
      const d = drag; drag = null;
      if (dragBadge) { const bp = dragBadge.p; dragBadge = null; renderPage(bp); }
      if (!d || d.mode === 'pan') return;
      if (d.mode === 'crop') return;
      if (d.mode === 'omove' || d.mode === 'oresize') {
        if (d.moved) { renderPage(d.p); status(); syncToolbar(); if (d.s.kind === 'obj') wantPage(d.p); }
        return;
      }
      if (!d.moved) startEdit(d.s, d.at);  // a click (no drag) edits in place, cursor at the click
      else { status(); syncToolbar(); }
    }
    pagesEl.addEventListener('pointerup', endDrag);
    pagesEl.addEventListener('pointercancel', endDrag);
    pagesEl.addEventListener('dblclick', e => { const item = e.target.closest('.pe-item'); if (item) startEdit({ kind: item.dataset.kind, key: item.dataset.k }); });

    // ── keyboard (case-insensitive: Caps Lock must not break shortcuts) ────
    function onKey(e) {
      if (closed || !document.body.contains(ov)) return;
      if (document.querySelector('.modal-overlay.dlg-top')) return;
      const t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
      const ctrl = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
      e.stopPropagation();
      const handled = () => e.preventDefault();
      if (ctrl && k === 's') { handled(); commitEdit(); if (embed) embed.onKey('save'); else save(); return; }
      if (k === 'escape') {
        handled();
        if (editing) { cancelEdit(); return; }
        if (typing) { t.blur(); view.focus(); return; }
        if (cropping) { endCrop(false); return; }
        if (mode !== 'select') { setMode('select'); return; }
        if (sel) { select(null); return; }
        if (embed) embed.onKey('close'); else tryClose();
        return;
      }
      if (typing) return;
      if (ctrl && k === 'z' && !e.shiftKey) { handled(); if (embed && !undoStack.length) embed.onKey('undo'); else undo(); return; }
      if (ctrl && (k === 'y' || (k === 'z' && e.shiftKey))) { handled(); if (embed && !redoStack.length) embed.onKey('redo'); else redo(); return; }
      if (ctrl && (k === '=' || k === '+')) { handled(); zoomStep(1); return; }
      if (ctrl && k === '-') { handled(); zoomStep(-1); return; }
      if (ctrl && k === '0') { handled(); fitWidth(); return; }
      if (ctrl || e.altKey) return;
      if ((k === 'delete' || k === 'backspace') && sel) { handled(); deleteSel(); return; }
      if (k === 'enter' && cropping) { handled(); endCrop(true); return; }
      if (k === 'enter' && sel && !isObjSel(sel)) { handled(); startEdit(sel); return; }
      const arrows = { arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] };
      if (arrows[k] && sel && !cropping) { handled(); const st = e.shiftKey ? 10 : 1; nudge(arrows[k][0] * st, arrows[k][1] * st); return; }
      if (k === 'tab') { handled(); cycleObj(e.shiftKey ? -1 : 1); return; }
      if (k === 'i') { handled(); addImage(); return; }
      if (k === 'r' && isObjSel(sel)) { handled(); rotateSel(); return; }
      if (k === 'c' && sel && sel.kind === 'obj') { handled(); if (cropping) endCrop(true); else startCrop(); return; }
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
      // objects first in the list (the engine applies them before the text anyway)
      const objs = [];
      Object.entries(model.objs).forEach(([k, v]) => { const o = objOf(k); if (o) objs.push(objEdit(k, v, o, false)); });
      model.addImgs.forEach(a => {
        const im = imgStore.get(a.img); if (!im) return;
        objs.push({ type: 'add_image', page: a.page, rect: a.rect.map(r2), rotate: normDeg(a.rotate), data_url: im.data_url });
      });
      return objs.concat(out);
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
      commitEdit(); endCrop(true);
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
        if (over && Preview.getCurrentPath && (Preview.getCurrentPath() || '').toLowerCase() === src.toLowerCase()) Preview.previewFile(src, '.pdf', { reload: true });
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

    _open = { focus: () => view.focus(), path,
              state: () => ({ src, model: JSON.parse(snap()), undo: undoStack.length, redo: redoStack.length, sel, mode, editing: !!editing,
                              cropping: !!cropping, pageW: (pages()[0] || {}).w,
                              bgInSync: !pumping && !renderQ.length && pages().every((_, p) => {
                                const d = pageData.get(p); if (!d || d instanceof Promise) return true;
                                const h = rendered.get(p); return !h || h.key === JSON.stringify(bgEdits(p));
                              }) }) };

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
      model = { ov: {}, adds: [], objs: {}, addImgs: [] }; undoStack = []; redoStack = [];
      sel = null; editing = null; cropping = null; lastNudge = null; sprites.clear();
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
    // ── PDF Studio: write the text edits into a new step file ──────────────
    async function commit(outPath) {
      if (busy || !info) return { ok: false, error: 'Busy' };
      commitEdit(); endCrop(true);
      const edits = buildEdits();
      if (!edits.length) return { ok: true, unchanged: true };
      setBusy(true);
      const r = await SFM.pdfeditApply(src, edits, { mode: 'new', out_path: outPath }).catch(e => ({ ok: false, error: String(e) }));
      setBusy(false);
      return r || { ok: false, error: 'Could not apply the text edits' };
    }
    const controller = {
      kind: 'edit',
      dirty, flush: () => { commitEdit(); endCrop(true); },   // dirty() has no side effects (it runs on every refresh)
      canUndo: () => undoStack.length > 0, canRedo: () => redoStack.length > 0,
      undo, redo, commit, close: forceClose, focus: () => view.focus({ preventScroll: true }),
      page: () => curPage, goPage: p => goPage(p),
      tool: t => { if (t === 'image') { setMode('select'); addImage(); } else setMode(t === 'add' ? 'add' : 'select'); },
      busy: () => busy,
    };

    if (!(await load(path))) { forceClose(); return; }
    if (embed && Number.isInteger(embed.page) && embed.page > 0) goPage(embed.page);
    view.focus({ preventScroll: true });
    return controller;
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

  return { open, canEdit: isPdf, isOpen: () => !!_open, _state: () => (_open && _open.state ? _open.state() : null) };
})();
