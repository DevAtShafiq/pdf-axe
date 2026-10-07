/**
 * preview.js — Center preview panel
 *
 * Handles:
 *   - PDF: page-by-page PNG render via bridge base64, zoom, page nav, plus a
 *     transparent selectable text layer per page (pdf-text.js)
 *   - Image: <img> display, zoom
 *   - Text: plain text
 *   - Word (.docx): HTML via python-docx (read-only, selectable)
 *   - Excel (.xlsx/.xlsm) and .csv: sheet tabs + grid (read-only, selectable)
 *   - .doc/.xls/.pptx …: “Preview via Office” → converted PDF in the PDF view
 *   - PDF contextual toolbar (rotate, delete page, arrange, split, compress)
 */

const Preview = (() => {

  // ── State ─────────────────────────────────────────────────────────────────
  let _path        = null;   // the selected file
  let _ext         = '';
  let _view        = 'empty';// 'pdf' | 'img' | 'text' | 'doc' | 'empty'
  let _pdfSrc      = null;   // PDF actually rendered (= _path, or an Office conversion)
  let _converted   = false;  // _pdfSrc is a read-only Office → PDF conversion
  let _pdfCount    = 0;
  let _pdfCurPage  = 0;
  let _zoom        = 1.0;
  let _zoomTimer   = null;
  let _token       = 0;      // bumps on every new preview; async work checks it
  let _pageObs     = null;
  let _docFull     = null;   // {html, cls} for the full view of a Word/Excel preview

  const ZOOM_STEP  = 0.15;
  const ZOOM_MIN   = 0.2;
  const ZOOM_MAX   = 4.0;

  const IMG_EXT   = ['.jpg','.jpeg','.png','.bmp','.webp','.gif','.tiff'];
  const TEXT_EXT  = ['.txt','.md','.log','.py','.js','.json','.xml','.html','.ini','.bat'];
  const DOCX_EXT  = ['.docx','.dotx'];
  const SHEET_EXT = ['.xlsx','.xlsm','.xltx','.xltm','.csv','.tsv'];
  const OFFICE_EXT = ['.doc','.docm','.dot','.dotm','.rtf','.odt',
                      '.xls','.xlsb','.xlt','.ods',
                      '.pptx','.ppt','.pptm','.pps','.ppsx','.potx','.odp'];

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const $  = id  => document.getElementById(id);

  const pdfWrap     = () => $('pdf-canvas-wrap');
  const imgEl       = () => $('img-preview');
  const textEl      = () => $('text-preview');
  const docEl       = () => $('doc-preview');
  const emptyEl     = () => $('preview-empty');
  const floatNav    = () => $('pdf-float-nav');
  const ctxToolbar  = () => $('pdf-ctx-toolbar');
  const zoomLabel   = () => $('preview-zoom-label');
  const fileNameEl  = () => $('preview-file-name');

  // ── Show / Hide panes ────────────────────────────────────────────────────
  function _showOnly(which) {
    _view = which;
    pdfWrap().classList.add('hidden');
    imgEl().classList.add('hidden');
    textEl().classList.add('hidden');
    docEl().classList.add('hidden');
    emptyEl().classList.add('hidden');
    floatNav().classList.add('hidden');
    ctxToolbar().classList.add('hidden');

    if (which === 'pdf')  {
      pdfWrap().classList.remove('hidden'); floatNav().classList.remove('hidden');
      if (!_converted) ctxToolbar().classList.remove('hidden');
    }
    if (which === 'img')  { imgEl().classList.remove('hidden'); }
    if (which === 'text') { textEl().classList.remove('hidden'); }
    if (which === 'doc')  { docEl().classList.remove('hidden'); }
    if (which === 'empty'){ emptyEl().classList.remove('hidden'); }
    const isPdf = which === 'pdf';
    ['preview-copy-page', 'preview-copy-all'].forEach(id => { const b = $(id); if (b) b.classList.toggle('hidden', !isPdf); });
  }

  // ── Main entry ────────────────────────────────────────────────────────────
  async function previewFile(path, ext) {
    if (_path === path) return;   // already showing
    const tok = ++_token;
    _path = path;
    _ext  = (ext || '').toLowerCase();
    _pdfSrc = null; _converted = false; _docFull = null;
    _zoom = 1.0;
    _updateZoomLabel();

    const name = path.split(/[\\/]/).pop();
    fileNameEl().textContent = name;

    // ZIPs show their contents; password-protected PDFs ask first (archive.js).
    if (typeof Archive !== 'undefined' && (_ext === '.zip' || _ext === '.pdf')) {
      const handled = await Archive.previewHook(path, _ext, {
        el: emptyEl(), show: () => _showOnly('empty'),
        openPdf: () => _openPdf(path), isCurrent: () => _path === path,
      });
      if (handled || tok !== _token) return;
    }

    if (_ext === '.pdf') {
      await _openPdf(path);
    } else if (IMG_EXT.includes(_ext)) {
      await _openImage(path);
    } else if (DOCX_EXT.includes(_ext)) {
      await _openDocx(path, tok);
    } else if (SHEET_EXT.includes(_ext)) {
      await _openSheet(path, '', tok);
    } else if (TEXT_EXT.includes(_ext)) {
      await _openText(path);
    } else if (OFFICE_EXT.includes(_ext)) {
      await _openOffice(path, tok);
    } else {
      _showOnly('empty');
      emptyEl().innerHTML = `<div class="empty-state-icon">${Icons.file({ ext: _ext, name }, 28)}</div>`
        + `<p class="empty-state-title">${_esc(name)}</p><p class="empty-state-text">No preview for this file type. Use “Open” in the details pane.</p>`;
    }
  }

  function clear() {
    _token++;
    _path = null; _pdfSrc = null; _converted = false; _pdfCount = 0; _pdfCurPage = 0; _docFull = null;
    _showOnly('empty');
    if (_emptyDefault !== null) emptyEl().innerHTML = _emptyDefault;
    fileNameEl().textContent = 'No file selected';
  }

  // ── PDF ──────────────────────────────────────────────────────────────────
  // src: the PDF to render (an Office file's converted copy when converted=true)
  async function _openPdf(src, converted = false) {
    const tok = _token;
    _pdfSrc = src;
    _converted = !!converted;
    _showOnly('pdf');
    pdfWrap().innerHTML = '<div class="loading-overlay" style="position:relative;height:300px"><div class="loading-spinner"></div></div>';

    try {
      const r = await SFM.getPdfPageCount(src);
      if (tok !== _token) return;
      if (!r.ok) { _showError(r.error); return; }
      _pdfCount   = r.count;
      _pdfCurPage = 0;
      await _loadVisiblePages();
    } catch(e) {
      _showError(String(e));
    }
  }

  async function _loadVisiblePages() {
    if (!_pdfSrc || _view !== 'pdf') return;
    const pdfSrc = _pdfSrc;
    const tok = _token;
    // Render all pages lazily via IntersectionObserver
    if (_pageObs) _pageObs.disconnect();
    pdfWrap().innerHTML = '';
    for (let i = 0; i < _pdfCount; i++) {
      const placeholder = document.createElement('div');
      placeholder.style.cssText = `width:595px;height:842px;background:var(--bg-surface);margin:0 auto;border-radius:2px;box-shadow:var(--shadow-lg)`;
      placeholder.dataset.pageSlot = i;
      pdfWrap().appendChild(placeholder);
    }
    const zoom = _zoom;
    const obs = _pageObs = new IntersectionObserver(async entries => {
      for (const ent of entries) {
        if (!ent.isIntersecting) continue;
        const slot = ent.target;
        const page = +slot.dataset.pageSlot;
        obs.unobserve(slot);
        const dpi = Math.round(150 * zoom);
        try {
          const r = await SFM.getPdfPage(pdfSrc, page, Math.min(dpi, 300));
          if (tok !== _token || !slot.isConnected) continue;
          if (r.ok) {
            const img = document.createElement('img');
            img.src = r.data_url;
            img.className = 'pdf-page-canvas';
            const w = Math.round(r.width * zoom);
            const box = PdfText.wrapPage(img, pdfSrc, page, { width: w });
            slot.replaceWith(box);
            PdfText.attach(box);
          }
        } catch(e) {}
      }
    }, { root: pdfWrap(), rootMargin: '400px' });
    pdfWrap().querySelectorAll('[data-page-slot]').forEach(s => obs.observe(s));
    _updatePageNav();
  }

  function _updatePageNav() {
    $('pdf-total-pages').textContent = _pdfCount;
    $('pdf-page-input').value = _pdfCurPage + 1;
    $('pdf-page-input').max   = _pdfCount;
  }

  function goPrev() {
    if (_pdfCurPage > 0) { _pdfCurPage--; _scrollToPage(_pdfCurPage); _updatePageNav(); }
  }
  function goNext() {
    if (_pdfCurPage < _pdfCount - 1) { _pdfCurPage++; _scrollToPage(_pdfCurPage); _updatePageNav(); }
  }
  function goToPage(n) {
    n = Math.max(0, Math.min(_pdfCount - 1, n - 1));
    _pdfCurPage = n; _scrollToPage(n); _updatePageNav();
  }

  function _pageEl(page) {
    return pdfWrap().querySelector(`[data-page="${page}"]`) || pdfWrap().querySelector(`[data-page-slot="${page}"]`);
  }

  function _scrollToPage(page) {
    const el = _pageEl(page);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // Track current page from scroll position
  function _initScrollPageTrack() {
    pdfWrap().addEventListener('scroll', () => {
      const wrap = pdfWrap();
      const pages = wrap.querySelectorAll('[data-page], [data-page-slot]');
      const wrapRect = wrap.getBoundingClientRect();
      for (const el of pages) {
        const rect = el.getBoundingClientRect();
        if (rect.bottom >= wrapRect.top + 40) {
          const p = el.dataset.page != null ? +el.dataset.page : +el.dataset.pageSlot;
          if (p !== _pdfCurPage) {
            _pdfCurPage = p; _updatePageNav();
          }
          break;
        }
      }
    });
  }

  // ── Selectable text: copy buttons, right-click menu, Ctrl+A ──────────────
  function _initTextSelection() {
    const body = $('preview-body');
    const wrap = pdfWrap();
    PdfText.enablePan(wrap);

    wrap.addEventListener('contextmenu', e => {
      const box = e.target.closest && e.target.closest('.pdf-page');
      if (!box || !_pdfSrc) return;
      const page = +box.dataset.page;
      PdfText.menu(e, { path: _pdfSrc, page, box });
    });

    // Word / Excel views: Copy / Select all menu
    docEl().addEventListener('contextmenu', e => {
      e.preventDefault();
      const sel = PdfText.selectionIn(docEl());
      _docMenu(e, sel);
    });
    docEl().addEventListener('click', e => {
      const a = e.target.closest && e.target.closest('[data-href]');
      if (a) { e.preventDefault(); SFM.qrOpenUrl(a.dataset.href); }
    });

    const wire = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };
    wire('preview-copy-page', () => { if (_pdfSrc) PdfText.copyPage(_pdfSrc, _pdfCurPage); });
    wire('preview-copy-all',  () => { if (_pdfSrc) PdfText.copyAll(_pdfSrc); });

    // Clicking in the preview gives it focus, so Ctrl+A / arrows act here.
    body.addEventListener('keydown', e => {
      if (!(e.ctrlKey || e.metaKey) || (e.key !== 'a' && e.key !== 'A') || e.shiftKey) return;
      if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
      if (_view === 'pdf') {
        const box = pdfWrap().querySelector(`.pdf-page[data-page="${_pdfCurPage}"]`);
        if (box && PdfText.selectPage(box)) { e.preventDefault(); e.stopPropagation(); }
        else { e.preventDefault(); e.stopPropagation(); }
      } else if (_view === 'doc') {
        _selectDoc(); e.preventDefault(); e.stopPropagation();
      } else if (_view === 'text') {
        const range = document.createRange(); range.selectNodeContents(textEl());
        const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        e.preventDefault(); e.stopPropagation();
      }
    });
  }

  function _selectDoc() {
    const target = docEl().querySelector('.doc-sheet, .xl-grid') || docEl();
    const range = document.createRange(); range.selectNodeContents(target);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  }

  // Word / Excel view menu. Selected table cells copy tab-separated, rows on
  // new lines (the browser's own text serialisation of a table selection).
  function _docMenu(e, selected) {
    PdfText.menu(e, { items: [
      { icon: 'copy', label: 'Copy', shortcut: 'Ctrl+C', fn: () => PdfText.copyText(selected), disabled: !selected },
      { icon: 'text-cursor', label: 'Select all', shortcut: 'Ctrl+A', fn: _selectDoc },
      { sep: true },
      { icon: 'external-link', label: 'Open in default app', fn: () => { if (_path) SFM.openNative(_path); } },
    ] });
  }

  // ── Image ────────────────────────────────────────────────────────────────
  async function _openImage(path) {
    _showOnly('img');
    const img = imgEl();
    img.src = '';
    img.style.opacity = '0.3';
    try {
      const r = await SFM.getImagePreview(path, 1600);
      if (_path !== path) return;
      if (!r.ok) { _showError(r.error); return; }
      img.src = r.data_url;
      img.style.opacity = '1';
      img.style.transform = `scale(${_zoom})`;
      img.style.transformOrigin = 'center center';
    } catch(e) { _showError(String(e)); }
  }

  // ── Plain text ────────────────────────────────────────────────────────────
  async function _openText(path) {
    _showOnly('text');
    textEl().textContent = 'Loading…';
    try {
      const r = await SFM.getTextPreview(path);
      if (_path !== path) return;
      textEl().textContent = r.ok ? r.text : r.error;
    } catch(e) { textEl().textContent = String(e); }
  }

  // ── Word (.docx) ─────────────────────────────────────────────────────────
  function _docLoading(label) {
    _showOnly('doc');
    docEl().innerHTML = `<div class="doc-loading"><span class="spinner"></span><span>${_esc(label)}</span></div>`;
  }

  function _notice(html, actions = '') {
    return `<div class="doc-notice">${Icons.svg('info', 14)}<span class="doc-notice-text">${html}</span>${actions}</div>`;
  }

  function _officeBtn(app) {
    return app ? `<button class="btn btn-ghost btn-sm" data-act="office">${Icons.svg('file-pdf', 14)}Exact layout via ${_esc(app)}</button>` : '';
  }

  async function _openDocx(path, tok) {
    _docLoading('Reading document…');
    let r;
    try { r = await SFM.call('docx_preview_html', path); } catch (e) { r = { ok: false, error: String(e) }; }
    if (tok !== _token) return;
    if (!r.ok) { return _officeOrNoPreview(path, tok, r.error); }
    _applyZoomDoc();
    const s = r.stats || {};
    const meta = [s.pages ? `${s.pages} page${s.pages === 1 ? '' : 's'}` : '',
                  s.words != null ? `${(s.words || 0).toLocaleString()} words` : '',
                  s.tables ? `${s.tables} table${s.tables === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
    let top = '';
    if (r.truncated) {
      top = _notice('This document is long — the preview shows the first part only.',
        `<button class="btn btn-sm" data-act="open">${Icons.svg('external-link', 14)}Open</button>`);
    }
    const bar = `<div class="doc-bar"><span class="doc-bar-meta">${_esc(meta)}</span><span class="flex-1"></span>`
      + (r.office ? _officeBtn('Word') : '') + '</div>';
    const missing = s.images_skipped ? _notice(`${s.images_skipped} image${s.images_skipped === 1 ? ' is' : 's are'} too large to preview.`) : '';
    docEl().innerHTML = bar + top + missing + `<div class="doc-page-wrap"><article class="doc-sheet">${r.html || '<p class="doc-empty-text">This document is empty.</p>'}</article></div>`;
    _docFull = { html: `<article class="doc-sheet">${r.html}</article>`, cls: 'doc-preview doc-full' };
    _wireDocActions(path, tok);
  }

  function _wireDocActions(path, tok) {
    docEl().querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => {
      const act = b.dataset.act;
      if (act === 'open') SFM.openNative(path);
      if (act === 'office') _convertViaOffice(path, tok);
    }));
  }

  // ── Excel (.xlsx / .xlsm) and CSV ────────────────────────────────────────
  async function _openSheet(path, sheet, tok) {
    const keepTabs = sheet && docEl().querySelector('.xl-tabs');
    if (!keepTabs) _docLoading('Reading workbook…');
    else docEl().querySelector('.xl-scroll')?.classList.add('is-loading');
    let r;
    try { r = await SFM.call('sheet_preview_html', path, sheet || ''); } catch (e) { r = { ok: false, error: String(e) }; }
    if (tok !== _token) return;
    if (!r.ok) { return _officeOrNoPreview(path, tok, r.error); }
    const tabs = (r.sheets || []).length > 1 || _ext !== '.csv'
      ? `<div class="xl-tabs" role="tablist">${(r.sheets || []).map(s =>
          `<button class="xl-tab${s.name === r.sheet ? ' active' : ''}${s.hidden ? ' is-hidden' : ''}" role="tab" data-sheet="${_esc(s.name)}" title="${_esc(s.name)}${s.hidden ? ' (hidden sheet)' : ''}">${_esc(s.name)}</button>`).join('')}</div>`
      : '';
    const dims = `${(r.rows || 0).toLocaleString()} row${r.rows === 1 ? '' : 's'} × ${r.cols || 0} column${r.cols === 1 ? '' : 's'}`;
    const bar = `<div class="doc-bar"><span class="doc-bar-meta">${_esc(dims)}</span><span class="flex-1"></span>`
      + (r.office ? _officeBtn('Excel') : '') + '</div>';
    let notice = '';
    if (r.truncated) {
      notice = _notice(`Showing the first ${Math.min(r.rows, r.max_rows).toLocaleString()} rows and ${Math.min(r.cols, r.max_cols)} columns.`,
        `<button class="btn btn-sm" data-act="open">${Icons.svg('external-link', 14)}Open</button>`);
    }
    const empty = !r.rows_shown || !r.cols_shown;
    const grid = empty ? '<div class="doc-empty-text">This sheet is empty.</div>' : r.html;
    docEl().innerHTML = bar + notice + `<div class="xl-scroll">${grid}</div>` + tabs;
    _docFull = { html: `<div class="xl-scroll">${grid}</div>`, cls: 'doc-preview doc-full' };
    _applyZoomDoc();
    docEl().querySelectorAll('.xl-tab').forEach(b => b.addEventListener('click', () => {
      if (b.classList.contains('active')) return;
      _openSheet(path, b.dataset.sheet, tok);
    }));
    _wireDocActions(path, tok);
  }

  // ── Other Office files → PDF via Microsoft Office ────────────────────────
  async function _openOffice(path, tok) {
    _showOnly('empty');
    let info;
    try { info = await SFM.call('office_preview_info', path); } catch (e) { info = { ok: false }; }
    if (tok !== _token) return;
    if (info && info.ok && info.cached_pdf) {      // converted before — show right away
      return _openPdf(info.cached_pdf, true);
    }
    _officeOrNoPreview(path, tok, '', info);
  }

  async function _officeOrNoPreview(path, tok, error = '', info = null) {
    if (!info) {
      try { info = await SFM.call('office_preview_info', path); } catch (e) { info = { ok: false }; }
      if (tok !== _token) return;
    }
    const name = path.split(/[\\/]/).pop();
    const app = (info && info.app) || _appFor(_ext);
    _showOnly('empty');
    const err = error ? `<p class="empty-state-text text-red">${_esc(error)}</p>` : '';
    if (info && info.ok && info.available) {
      emptyEl().innerHTML = `<div class="empty-state-icon">${Icons.file({ ext: _ext, name }, 28)}</div>`
        + `<p class="empty-state-title">${_esc(name)}</p>${err}`
        + `<p class="empty-state-text">Show this file using Microsoft ${_esc(app)}. The original is opened read-only and is not changed.</p>`
        + `<div class="row preview-empty-actions"><button class="btn btn-primary" data-act="office">${Icons.svg('eye', 16)}Preview via ${_esc(app)}</button>`
        + `<button class="btn" data-act="open">${Icons.svg('external-link', 16)}Open</button></div>`;
    } else {
      emptyEl().innerHTML = `<div class="empty-state-icon">${Icons.file({ ext: _ext, name }, 28)}</div>`
        + `<p class="empty-state-title">${_esc(name)}</p>${err}`
        + `<p class="empty-state-text">No preview for this file${app ? ` — Microsoft ${_esc(app)} is not installed` : ''}.</p>`
        + `<div class="row preview-empty-actions"><button class="btn" data-act="open">${Icons.svg('external-link', 16)}Open${app ? ` with ${_esc(app)}` : ''}</button></div>`;
    }
    emptyEl().querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => {
      if (b.dataset.act === 'open') SFM.openNative(path);
      if (b.dataset.act === 'office') _convertViaOffice(path, tok);
    }));
  }

  async function _convertViaOffice(path, tok) {
    const app = _appFor(_ext) || 'Office';
    _showOnly('empty');
    emptyEl().innerHTML = `<div class="loading-spinner" style="margin:0 auto 8px"></div>`
      + `<p class="empty-state-title">Preparing preview via ${_esc(app)}…</p>`
      + `<p class="empty-state-text">This can take a few seconds the first time.</p>`;
    let r;
    try { r = await SFM.call('office_preview_pdf', path); } catch (e) { r = { ok: false, error: String(e) }; }
    if (tok !== _token) return;
    if (!r.ok) {
      App.toast(`Preview via ${app} failed: ${r.error}`, 'error', 6000);
      return _officeOrNoPreview(path, tok, r.error);
    }
    _openPdf(r.pdf_path, true);
  }

  function _appFor(ext) {
    if (['.doc','.docx','.docm','.dot','.dotm','.dotx','.rtf','.odt'].includes(ext)) return 'Word';
    if (['.xls','.xlsx','.xlsm','.xlsb','.xlt','.xltx','.xltm','.ods','.csv'].includes(ext)) return 'Excel';
    if (['.ppt','.pptx','.pptm','.pps','.ppsx','.potx','.odp'].includes(ext)) return 'PowerPoint';
    return '';
  }

  // ── Error state ───────────────────────────────────────────────────────────
  function _showError(msg) {
    _showOnly('empty');
    emptyEl().innerHTML = `<div class="empty-state-icon text-red">${Icons.svg('alert-triangle', 26)}</div>`
      + `<p class="empty-state-title">Can't show a preview</p><p class="empty-state-text text-red">${_esc(String(msg))}</p>`;
  }

  // ── Zoom ──────────────────────────────────────────────────────────────────
  function zoomIn()    { _setZoom(_zoom + ZOOM_STEP); }
  function zoomOut()   { _setZoom(_zoom - ZOOM_STEP); }
  function zoomReset() { _setZoom(1.0); }

  function _setZoom(z) {
    _zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z));
    _updateZoomLabel();
    clearTimeout(_zoomTimer);
    _zoomTimer = setTimeout(() => {
      if (_view === 'pdf') _loadVisiblePages();
      else if (_view === 'doc') _applyZoomDoc();
      else if (['.jpg','.jpeg','.png','.bmp','.webp','.gif'].includes(_ext)) {
        const img = imgEl();
        img.style.transform = `scale(${_zoom})`;
      }
    }, 150);
  }

  function _applyZoomDoc() {
    const d = docEl();
    if (d) d.style.setProperty('--doc-zoom', _zoom);
  }

  function _updateZoomLabel() {
    const lbl = zoomLabel();
    if (lbl) lbl.textContent = Math.round(_zoom * 100) + '%';
    // Visual only: at ≤100% pages are capped to the pane width (style.css)
    const wrap = pdfWrap();
    if (wrap) wrap.classList.toggle('fit-width', _zoom <= 1.0001);
  }

  // ── PDF contextual toolbar ────────────────────────────────────────────────
  function _initPdfToolbar() {
    const wire = (id, fn) => { const el = $(id); if(el) el.addEventListener('click', fn); };
    const editable = () => _path && _pdfSrc && !_converted;

    wire('pdf-rotate-cw',  async () => {
      if (!editable()) return;
      await SFM.rotatePage(_pdfSrc, _pdfCurPage, 90);
      _loadVisiblePages();
    });
    wire('pdf-rotate-ccw', async () => {
      if (!editable()) return;
      await SFM.rotatePage(_pdfSrc, _pdfCurPage, -90);
      _loadVisiblePages();
    });
    wire('pdf-del-page', async () => {
      if (!editable()) return;
      if (!(await Dialogs.confirm({ title: 'Delete page', danger: true, icon: 'trash', okLabel: 'Delete page', okIcon: 'trash', message: `Delete page ${_pdfCurPage + 1} of ${_pdfCount} from this PDF?`, detail: 'This changes the PDF file itself.' }))) return;
      await SFM.deletePage(_pdfSrc, _pdfCurPage);
      _pdfCount--;
      _pdfCurPage = Math.min(_pdfCurPage, _pdfCount - 1);
      _loadVisiblePages();
      App.toast('Page deleted', 'success');
    });
    wire('pdf-arrange', () => {
      if (!editable()) return;
      Dialogs.openArrangePages(_pdfSrc, _pdfCount);
    });
    wire('pdf-split', () => {
      if (editable()) PdfTools.openSplit(_pdfSrc);
    });
    wire('pdf-extract', () => {
      if (editable()) PdfTools.openExtract(_pdfSrc, { page: _pdfCurPage });
    });
    wire('pdf-compress', () => {
      if (!editable()) return;
      Dialogs.openCompressPdf(_pdfSrc);
    });
    // PDF float nav
    wire('pdf-prev', goPrev);
    wire('pdf-next', goNext);
    const pageInput = $('pdf-page-input');
    if (pageInput) {
      pageInput.addEventListener('change', () => goToPage(+pageInput.value));
      pageInput.addEventListener('keydown', e => { if(e.key==='Enter') goToPage(+pageInput.value); });
    }
  }

  // ── Preview zoom buttons ──────────────────────────────────────────────────
  function _initZoomButtons() {
    const wire = (id, fn) => { const el = $(id); if(el) el.addEventListener('click', fn); };
    wire('preview-zoom-in',    zoomIn);
    wire('preview-zoom-out',   zoomOut);
    wire('preview-zoom-reset', zoomReset);
    wire('preview-fullscreen', () => {
      if (!_path) return;
      const name = _path.split(/[\\/]/).pop();
      if (_view === 'pdf' && _converted) Dialogs.openFullView(_pdfSrc, '.pdf', _pdfCurPage, { readOnly: true, title: name });
      else if (_view === 'doc' && _docFull) Dialogs.openFullView(_path, _ext, 0, { html: _docFull.html, htmlClass: _docFull.cls, title: name });
      else Dialogs.openFullView(_path, _ext, _pdfCurPage);
    });

    // Mouse wheel zoom on preview body
    const body = $('preview-body');
    if (body) {
      body.addEventListener('wheel', e => {
        if (!e.ctrlKey) return;
        e.preventDefault();
        if (e.deltaY < 0) zoomIn(); else zoomOut();
      }, { passive: false });
    }
  }

  // ── Arrow key navigation in PDF ───────────────────────────────────────────
  function _initPdfKeyNav() {
    document.addEventListener('keydown', e => {
      if (_view !== 'pdf') return;
      if (['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) return;
      if (document.querySelector('.modal-overlay')) return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); goNext(); }
      if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   { e.preventDefault(); goPrev(); }
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  function getCurrentPath() { return _path; }
  function getCurrentPage() { return _pdfCurPage; }
  function getPdfCount()    { return _pdfCount; }
  function getView()        { return { view: _view, pdfSrc: _pdfSrc, converted: _converted }; }

  // ── Init ──────────────────────────────────────────────────────────────────
  let _emptyDefault = null;   // the static "Nothing selected" markup from index.html
  function init() {
    if (emptyEl()) _emptyDefault = emptyEl().innerHTML;
    _initPdfToolbar();
    _initZoomButtons();
    _initPdfKeyNav();
    _initScrollPageTrack();
    _initTextSelection();
  }
  init();

  return {
    previewFile, clear,
    zoomIn, zoomOut, zoomReset,
    goPrev, goNext, goToPage,
    getCurrentPath, getCurrentPage, getPdfCount, getView,
  };
})();
