/**
 * preview.js — Center preview panel
 *
 * Handles:
 *   - PDF: page-by-page canvas render via bridge base64, zoom, pan, page nav
 *   - Image: <img> display, zoom
 *   - Word/Excel: HTML injected into iframe
 *   - Text: contenteditable div
 *   - PDF contextual toolbar (rotate, delete page, split, arrange, print)
 */

const Preview = (() => {

  // ── State ─────────────────────────────────────────────────────────────────
  let _path        = null;
  let _ext         = '';
  let _pdfPages    = [];   // array of {page, canvas, loaded}
  let _pdfCount    = 0;
  let _pdfCurPage  = 0;
  let _zoom        = 1.0;
  let _pdfDpi      = 150;
  let _renderQueue = [];
  let _renderBusy  = false;
  let _zoomTimer   = null;
  let _docPdfPath  = null;  // temp PDF path when rendering Office/HWP doc as PDF

  // file types routed through PDF renderer (Office + HWP)
  const OFFICE_EXTS = new Set(['.docx','.doc','.docm','.dotx','.rtf','.odt',
                                '.xlsx','.xls','.xlsm','.xlsb',
                                '.hwp','.hwpx']);

  const ZOOM_STEP  = 0.15;
  const ZOOM_MIN   = 0.2;
  const ZOOM_MAX   = 4.0;

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const $  = id  => document.getElementById(id);
  const el = sel => document.querySelector(sel);

  const pdfWrap     = () => $('pdf-canvas-wrap');
  const imgEl       = () => $('img-preview');
  const htmlFrame   = () => $('html-preview');
  const textEl      = () => $('text-preview');
  const emptyEl     = () => $('preview-empty');
  const floatNav    = () => $('pdf-float-nav');
  const ctxToolbar  = () => $('pdf-ctx-toolbar');
  const previewBody = () => $('preview-body');
  const zoomLabel   = () => $('preview-zoom-label');
  const fileNameEl  = () => $('preview-file-name');

  // ── Show / Hide panes ────────────────────────────────────────────────────
  function _showOnly(which) {
    pdfWrap().classList.add('hidden');
    imgEl().classList.add('hidden');
    htmlFrame().classList.add('hidden');
    textEl().classList.add('hidden');
    emptyEl().classList.add('hidden');
    floatNav().classList.add('hidden');
    ctxToolbar().classList.add('hidden');

    if (which === 'pdf')  { pdfWrap().classList.remove('hidden');  floatNav().classList.remove('hidden'); ctxToolbar().classList.remove('hidden'); }
    if (which === 'img')  { imgEl().classList.remove('hidden'); }
    if (which === 'html') { htmlFrame().classList.remove('hidden'); }
    if (which === 'text') { textEl().classList.remove('hidden'); }
    if (which === 'empty'){ emptyEl().classList.remove('hidden'); }
  }

  // ── Main entry ────────────────────────────────────────────────────────────
  async function previewFile(path, ext) {
    if (_path === path) return;   // already showing
    _path = path;
    _docPdfPath = null;
    _ext  = (ext || '').toLowerCase();
    _zoom = 1.0;
    _updateZoomLabel();

    const name = path.split(/[\\/]/).pop();
    fileNameEl().textContent = name;

    if (_ext === '.pdf') {
      await _openPdf(path);
    } else if (['.jpg','.jpeg','.png','.bmp','.webp','.gif','.tiff'].includes(_ext)) {
      await _openImage(path);
    } else if (OFFICE_EXTS.has(_ext)) {
      await _openOfficeDoc(path, _ext);
    } else if (['.csv'].includes(_ext)) {
      await _openExcelHtml(path);
    } else if (['.txt','.md','.log','.py','.js','.json','.xml','.html','.ini','.bat'].includes(_ext)) {
      await _openText(path);
    } else {
      _showOnly('empty');
      emptyEl().innerHTML = `<span class="big-icon">📄</span><p>${_esc(name)}</p><p class="text-muted" style="font-size:11px">No preview available</p>`;
    }
  }

  function clear() {
    _path = null; _pdfCount = 0; _pdfCurPage = 0; _docPdfPath = null;
    _showOnly('empty');
    fileNameEl().textContent = 'No file selected';
  }

  // ── PDF ──────────────────────────────────────────────────────────────────
  async function _openPdf(path) {
    _showOnly('pdf');
    pdfWrap().innerHTML = '<div class="loading-overlay" style="position:relative;height:300px"><div class="loading-spinner"></div></div>';

    try {
      const r = await SFM.getPdfPageCount(path);
      if (!r.ok) { _showError(r.error); return; }
      _pdfCount   = r.count;
      _pdfCurPage = 0;
      _docPdfPath = null;  // native PDF — no temp conversion needed
      await _loadVisiblePages();
    } catch(e) {
      _showError(String(e));
    }
  }

  async function _renderPdfPage(page) {
    if (!_path) return;
    try {
      const dpi = Math.round(_pdfDpi * _zoom);
      const r   = await SFM.getPdfPage(_path, page, Math.min(dpi, 300));
      if (!r.ok) return;

      // Find or create canvas/img for this page
      let pageEl = pdfWrap().querySelector(`[data-page="${page}"]`);
      if (!pageEl) {
        pageEl = document.createElement('img');
        pageEl.className = 'pdf-page-canvas';
        pageEl.dataset.page = page;
        pdfWrap().appendChild(pageEl);
      }
      pageEl.src   = r.data_url;
      pageEl.width = Math.round(r.width * _zoom);
      pageEl.style.width = Math.round(r.width * _zoom) + 'px';
    } catch(e) {}
  }

  async function _loadVisiblePages() {
    if (!_path || (!OFFICE_EXTS.has(_ext) && _ext !== '.pdf')) return;
    const pdfSrc = _docPdfPath || _path;  // use temp PDF for Office docs
    // Render all pages lazily via IntersectionObserver
    pdfWrap().innerHTML = '';
    for (let i = 0; i < _pdfCount; i++) {
      const placeholder = document.createElement('div');
      placeholder.style.cssText = `width:595px;height:842px;background:var(--bg-surface);margin:0 auto;border-radius:2px;box-shadow:var(--shadow-lg)`;
      placeholder.dataset.pageSlot = i;
      pdfWrap().appendChild(placeholder);
    }
    const obs = new IntersectionObserver(async entries => {
      for (const ent of entries) {
        if (!ent.isIntersecting) continue;
        const slot = ent.target;
        const page = +slot.dataset.pageSlot;
        obs.unobserve(slot);
        const dpi = Math.round(150 * _zoom);
        try {
          const r = await SFM.getPdfPage(pdfSrc, page, Math.min(dpi, 300));
          if (r.ok) {
            const img = document.createElement('img');
            img.src = r.data_url;
            img.className = 'pdf-page-canvas';
            img.dataset.page = page;
            img.style.width = Math.round(r.width * _zoom) + 'px';
            slot.replaceWith(img);
          }
        } catch(e) {}
      }
    }, { rootMargin: '400px' });
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

  function _scrollToPage(page) {
    const img = pdfWrap().querySelector(`[data-page="${page}"]`);
    if (img) img.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // Track current page from scroll position
  function _initScrollPageTrack() {
    pdfWrap().addEventListener('scroll', () => {
      const wrap = pdfWrap();
      const imgs = wrap.querySelectorAll('[data-page]');
      for (const img of imgs) {
        const rect = img.getBoundingClientRect();
        const wrapRect = wrap.getBoundingClientRect();
        if (rect.top >= wrapRect.top - 40) {
          const p = +img.dataset.page;
          if (p !== _pdfCurPage) {
            _pdfCurPage = p; _updatePageNav();
          }
          break;
        }
      }
    });
  }

  // ── Image ────────────────────────────────────────────────────────────────
  async function _openImage(path) {
    _showOnly('img');
    const img = imgEl();
    img.src = '';
    img.style.opacity = '0.3';
    try {
      const r = await SFM.getImagePreview(path, 1600);
      if (!r.ok) { _showError(r.error); return; }
      img.src = r.data_url;
      img.style.opacity = '1';
      img.style.transform = `scale(${_zoom})`;
      img.style.transformOrigin = 'center center';
    } catch(e) { _showError(String(e)); }
  }

  // ── Office / HWP → PDF renderer (native-quality) ─────────────────────────
  async function _openOfficeDoc(path, ext) {
    _showOnly('pdf');
    pdfWrap().innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-muted);font-family:var(--font-ui)">
      <div class="loading-spinner" style="margin:0 auto 12px"></div>
      <div>Converting to PDF for native preview…</div>
    </div>`;

    try {
      const r = await SFM.getDocAsPdf(path);
      if (!r.ok) {
        // PDF conversion failed — fall back to HTML (Excel) or text (Word)
        if (['.xlsx','.xls','.xlsm','.xlsb','.csv'].includes(ext)) {
          await _openExcelHtml(path);
        } else {
          // Word fallback: show plain text
          _showOnly('text');
          textEl().textContent = 'Converting…';
          const rt = await SFM.getDocxPreview(path);
          textEl().textContent = rt.ok ? (rt.text || '(Empty document)') : (r.error || 'Preview unavailable');
        }
        return;
      }
      _docPdfPath = r.pdf_path;
      _pdfCount   = r.page_count || 1;
      _pdfCurPage = 0;
      await _loadVisiblePages();
    } catch(e) { _showError(String(e)); }
  }

  // ── Excel HTML fallback (rich openpyxl styling, sheet tabs) ─────────────
  async function _openExcelHtml(path) {
    _showOnly('html');
    htmlFrame().srcdoc = '<p style="padding:20px;font-family:sans-serif;color:#666">Loading spreadsheet…</p>';

    // Sheet tab bar
    let _tabBar = document.getElementById('excel-sheet-tabs');
    if (!_tabBar) {
      _tabBar = document.createElement('div');
      _tabBar.id = 'excel-sheet-tabs';
      _tabBar.style.cssText = 'display:none;gap:2px;padding:4px 8px;background:var(--bg-surface);border-bottom:1px solid var(--border);flex-shrink:0;overflow-x:auto;white-space:nowrap;';
      htmlFrame().parentNode.insertBefore(_tabBar, htmlFrame());
    }
    _tabBar.style.display = 'none';
    _tabBar.innerHTML = '';

    async function _loadSheet(sheet) {
      htmlFrame().srcdoc = '<p style="padding:20px;font-family:sans-serif;color:#666">Loading…</p>';
      try {
        const r = await SFM.getExcelHtml(path, sheet);
        if (!r.ok) { _showError(r.error); return; }
        // r.html is a fully self-contained HTML document from doc_to_html_excel()
        htmlFrame().srcdoc = r.html;
        // Rebuild sheet tabs from returned sheet list
        const sheets = r.sheets || [];
        if (sheets.length > 1 && _tabBar.innerHTML === '') {
          _tabBar.style.display = 'flex';
          sheets.forEach((name, i) => {
            const btn = document.createElement('button');
            btn.className = 'btn';
            btn.style.cssText = 'font-size:11px;padding:2px 10px;border-radius:4px 4px 0 0;';
            btn.textContent = name;
            if (i === 0) btn.style.background = 'var(--accent)';
            btn.addEventListener('click', () => {
              _tabBar.querySelectorAll('button').forEach(b => b.style.background = '');
              btn.style.background = 'var(--accent)';
              _loadSheet(name);
            });
            _tabBar.appendChild(btn);
          });
        }
      } catch(e) { _showError(String(e)); }
    }

    try {
      await _loadSheet('');
    } catch(e) { _showError(String(e)); }
  }

  // ── Plain text ────────────────────────────────────────────────────────────
  async function _openText(path) {
    _showOnly('text');
    textEl().textContent = 'Loading…';
    try {
      const r = await SFM.getTextPreview(path);
      textEl().textContent = r.ok ? r.text : r.error;
    } catch(e) { textEl().textContent = String(e); }
  }

  // ── Error state ───────────────────────────────────────────────────────────
  function _showError(msg) {
    _showOnly('empty');
    emptyEl().innerHTML = `<span class="big-icon">⚠️</span><p class="text-red">${_esc(msg)}</p>`;
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
      if (_ext === '.pdf' || OFFICE_EXTS.has(_ext)) _loadVisiblePages();
      else if (['.jpg','.jpeg','.png','.bmp','.webp','.gif'].includes(_ext)) {
        const img = imgEl();
        img.style.transform = `scale(${_zoom})`;
      }
    }, 150);
  }

  function _updateZoomLabel() {
    const lbl = zoomLabel();
    if (lbl) lbl.textContent = Math.round(_zoom * 100) + '%';
  }

  // ── PDF contextual toolbar ────────────────────────────────────────────────
  function _initPdfToolbar() {
    const wire = (id, fn) => { const el = $(id); if(el) el.addEventListener('click', fn); };

    wire('pdf-rotate-cw',  async () => {
      if (!_path) return;
      await SFM.rotatePage(_path, _pdfCurPage, 90);
      _loadVisiblePages();
    });
    wire('pdf-rotate-ccw', async () => {
      if (!_path) return;
      await SFM.rotatePage(_path, _pdfCurPage, -90);
      _loadVisiblePages();
    });
    wire('pdf-del-page', async () => {
      if (!_path) return;
      if (!confirm(`Delete page ${_pdfCurPage + 1}?`)) return;
      await SFM.deletePage(_path, _pdfCurPage);
      _pdfCount--;
      _pdfCurPage = Math.min(_pdfCurPage, _pdfCount - 1);
      _loadVisiblePages();
      App.toast('Page deleted', 'success');
    });
    wire('pdf-arrange', () => {
      if (!_path) return;
      Dialogs.openArrangePages(_path, _pdfCount);
    });
    wire('pdf-split', async () => {
      if (!_path) return;
      App.setStatus('Splitting…', true);
      const outDir = _path.replace(/[\\/][^\\/]+$/, '');
      const r = await SFM.splitPdf(_path, outDir);
      App.setStatus('Ready');
      if (r.ok) { App.toast(`Split into ${r.files?.length || '?'} pages`, 'success'); FileTree.refresh(); }
      else       { App.toast('Split failed: ' + r.error, 'error'); }
    });
    wire('pdf-compress', () => {
      if (!_path) return;
      Dialogs.openCompressPdf(_path);
    });
    wire('pdf-ocr', async () => {
      if (!_path) return;
      App.setStatus('Running OCR…', true);
      App.toast('Making PDF searchable…', 'info');
      SFM.call('make_ocr_searchable', _path);
    });
    wire('pdf-print', () => {
      if (!_path) return;
      Dialogs.openPrint(_path);
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
      if (_path) Dialogs.openFullView(_path, _ext, _pdfCurPage);
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
      if (_ext !== '.pdf' && !OFFICE_EXTS.has(_ext)) return;
      if (['INPUT','TEXTAREA'].includes(document.activeElement?.tagName)) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); goNext(); }
      if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   { e.preventDefault(); goPrev(); }
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function getCurrentPath() { return _path; }
  function getCurrentPage() { return _pdfCurPage; }
  function getPdfCount()    { return _pdfCount; }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    _initPdfToolbar();
    _initZoomButtons();
    _initPdfKeyNav();
    _initScrollPageTrack();
  }
  init();

  return {
    previewFile, clear,
    zoomIn, zoomOut, zoomReset,
    goPrev, goNext, goToPage,
    getCurrentPath, getCurrentPage, getPdfCount,
  };
})();
