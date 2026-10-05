/**
 * convert-tools.js — Conversion & compression dialogs
 *
 *   ConvertTools.openImagesToPdf(paths, {mode})   images → PDF (combine / one per image)
 *   ConvertTools.quickImageToPdf(path)            one image → PDF with defaults
 *   ConvertTools.openPdfToImages(path)            PDF pages → PNG/JPG/WEBP
 *   ConvertTools.openConvertImage(paths)          image format conversion
 *   ConvertTools.openCompress(paths)              compress PDFs, images or a mix, optionally
 *                                                 to a target size ("Under 100 KB")
 *   ConvertTools.openCompressPdf(paths)           thin wrapper → openCompress (PDFs only)
 *   ConvertTools.openCompressImages(paths)        thin wrapper → openCompress (images only)
 *
 * Backend: media_convert.py via sfm_bridge (SFM.* wrappers in bridge.js).
 * Every operation keeps the originals and never overwrites (name-2, name-3 …).
 * Long jobs use the media_progress / media_done events, tagged with a job id.
 */

const ConvertTools = (() => {

  // ── helpers ───────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const _esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const _name = p => String(p || '').split(/[\\/]/).pop();
  const _stem = p => _name(p).replace(/\.[^.]+$/, '');
  const _ext  = p => (String(p || '').match(/\.[^.\\/]+$/) || [''])[0].toLowerCase();

  const IMAGE_EXTS = ['.jpg', '.jpeg', '.jfif', '.png', '.bmp', '.webp', '.gif',
                      '.tif', '.tiff', '.heic', '.heif'];
  const isImage = p => IMAGE_EXTS.includes(_ext(p));
  const isPdf   = p => _ext(p) === '.pdf';

  function _fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }
  function _pct(before, after) {
    if (!before) return '';
    const p = Math.round((before - after) * 1000 / before) / 10;
    return p >= 0 ? p + '% smaller' : Math.abs(p) + '% larger';
  }

  // Remembered dialog options (per viewer, best effort).
  function _load(key, dflt) {
    try { const v = JSON.parse(localStorage.getItem('sfm.cvt.' + key)); return v == null ? dflt : { ...dflt, ...v }; }
    catch (e) { return dflt; }
  }
  function _save(key, val) {
    try { localStorage.setItem('sfm.cvt.' + key, JSON.stringify(val)); } catch (e) {}
  }

  let _seq = 0;
  const _newJob = kind => kind + '-' + Date.now() + '-' + (++_seq);

  // Start an async bridge job and resolve with its media_done payload.
  function _runJob(job, start, onProgress) {
    return new Promise(resolve => {
      const offP = SFM.on('media_progress', p => { if (p && p.job === job && onProgress) onProgress(p); });
      const offD = SFM.on('media_done', r => {
        if (!r || r.job !== job) return;
        offP(); offD(); resolve(r);
      });
      Promise.resolve().then(start).then(r => {
        if (r && r.ok === false) { offP(); offD(); resolve(r); }
      }).catch(e => { offP(); offD(); resolve({ ok: false, error: String(e) }); });
    });
  }

  function _openModal(id, title, body, buttons, opts) {
    return Dialogs.openModal(id, title, body, buttons, opts);
  }
  function _btn(overlay, label) {
    return overlay && overlay.querySelector(`[data-modal-btn="${label}"]`);
  }
  // labels[0] is the primary action: it shows a spinner while busy.
  function _busy(overlay, labels, on) {
    labels.forEach((l, i) => {
      const b = _btn(overlay, l); if (!b) return;
      b.disabled = on;
      if (i === 0) b.classList.toggle('is-loading', on);
    });
  }
  function _close(overlay) { if (overlay && overlay._close) overlay._close(); }
  function _subtitle(overlay, text) {
    const s = overlay && overlay.querySelector('.modal-subtitle');
    if (s) { s.textContent = text; s.title = text; }
  }

  const _progressHtml = id => `
    <div class="progress-block hidden" id="${id}">
      <div class="progress-wrap"><div class="progress-bar" id="${id}-bar" style="width:0%"></div></div>
      <div class="progress-label" id="${id}-label"></div>
    </div>`;
  // Hint / error line: <div class="callout cvt-note"><svg/><span id=...></span></div>
  const _noteHtml = (id, text = '', tone = 'info') =>
    `<div class="callout ${tone} cvt-note">${Icons.svg(tone === 'error' ? 'alert-circle' : 'info', 16)}<span id="${id}">${text}</span></div>`;
  function _noteError(id, msg) {
    const span = $(id); if (!span) return;
    span.textContent = msg;
    const box = span.parentElement;
    box.className = 'callout error cvt-note';
    const ic = box.querySelector('svg'); if (ic) ic.outerHTML = Icons.svg('alert-circle', 16);
  }
  const _sizeChange = (a, b) =>
    `<span class="size-change">${_fmtBytes(a)}${Icons.svg('arrow-right', 12)}<strong>${_fmtBytes(b)}</strong></span>`;
  function _pctPill(before, after) {
    if (!before) return '';
    const smaller = after <= before;
    return `<span class="pill ${smaller ? 'pill-green' : 'pill-yellow'}">${_esc(_pct(before, after))}</span>`;
  }
  function _progress(id, done, total, label) {
    const wrap = $(id); if (!wrap) return;
    wrap.classList.remove('hidden');
    const bar = $(id + '-bar');
    if (bar) {
      if (total > 0) { bar.classList.remove('indeterminate'); bar.style.width = Math.round(done * 100 / total) + '%'; }
      else bar.classList.add('indeterminate');
    }
    const lab = $(id + '-label');
    if (lab) lab.textContent = label || '';
  }

  // Refresh the list and select the new file when it is in the open folder.
  function _reveal(path) {
    if (path && FileTree.revealPath) FileTree.revealPath(path);
    else FileTree.refresh();
  }

  function _select(id, opts, value, cls = '') {
    return `<select id="${id}" class="input-text ${cls}">` +
      opts.map(([v, l]) => `<option value="${_esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${_esc(l)}</option>`).join('') +
      '</select>';
  }
  // Segmented control that mirrors a hidden <select id> (so .value / 'change' keep working).
  function _segSelect(id, opts, value) {
    const cur = opts.some(([v]) => String(v) === String(value)) ? String(value) : String(opts[0][0]);
    return `<div class="segmented segmented-block" data-seg-for="${id}" role="radiogroup">` +
      opts.map(([v, l]) => `<button type="button" class="seg-btn${String(v) === cur ? ' active' : ''}" data-v="${_esc(v)}">${_esc(l)}</button>`).join('') +
      `</div>` + _select(id, opts, cur, 'hidden');
  }
  function _wireSegs(root) {
    (root || document).querySelectorAll('[data-seg-for]').forEach(seg => {
      const sel = $(seg.dataset.segFor);
      seg.addEventListener('click', e => {
        const b = e.target.closest('.seg-btn'); if (!b || !sel) return;
        seg.querySelectorAll('.seg-btn').forEach(x => x.classList.toggle('active', x === b));
        sel.value = b.dataset.v;
        sel.dispatchEvent(new Event('change'));
      });
    });
  }
  const _field = (label, html, extra = '') =>
    `<div class="field" ${extra}><label class="field-label">${label}</label>${html}</div>`;

  // ── 1. Images → PDF ───────────────────────────────────────────────────────
  const IMG2PDF_DEFAULTS = { page_size: 'fit', orientation: 'auto', margin_mm: 0, quality: 85 };

  function quickImageToPdf(path) {
    if (!path) return;
    App.setStatus('Converting to PDF…', true);
    return SFM.convertToPdf(path, '', _load('img2pdf', IMG2PDF_DEFAULTS)).then(r => {
      App.setStatus('Ready');
      if (r.ok) {
        App.toast('Created <strong>' + _esc(_name(r.out_path)) + '</strong>', 'success');
        _reveal(r.out_path);
      } else {
        App.toast('Convert to PDF failed: ' + _esc(r.error), 'error', 6000);
      }
      return r;
    });
  }

  function openImagesToPdf(paths, options = {}) {
    let items = (Array.isArray(paths) ? paths : [paths]).filter(Boolean);
    const bad = items.filter(p => !isImage(p));
    items = items.filter(isImage);
    if (!items.length) { App.toast('Select one or more images first', 'warning'); return; }
    if (bad.length) App.toast(bad.length + ' non-image file(s) left out', 'info');

    const o = _load('img2pdf', IMG2PDF_DEFAULTS);
    let mode = items.length > 1 ? (options.mode || 'combine') : 'combine';
    const multi = items.length > 1;

    const body = `
      ${multi ? `
      <div class="segmented segmented-block" id="i2p-mode" role="radiogroup" aria-label="Output">
        <button type="button" class="seg-btn" data-mode="combine">${Icons.svg('combine', 14)}One combined PDF</button>
        <button type="button" class="seg-btn" data-mode="separate">${Icons.svg('files', 14)}One PDF per image</button>
      </div>
      <div class="field">
        <div class="section-head">
          <span class="field-label">Page order <span class="text-muted">· drag to reorder</span></span>
          <button type="button" class="btn btn-sm btn-ghost" id="i2p-sort">${Icons.svg('chevrons-up-down', 14)}Sort by name</button>
        </div>
        <div class="reorder-list cvt-list" id="i2p-list"></div>
      </div>` : ''}
      <div class="field-grid">
        ${_field('Page size', _select('i2p-size', [['fit', 'Fit to image'], ['a4', 'A4'], ['letter', 'Letter'], ['legal', 'Legal']], o.page_size))}
        ${_field('Orientation', _select('i2p-orient', [['auto', 'Auto (follow image)'], ['portrait', 'Portrait'], ['landscape', 'Landscape']], o.orientation))}
        ${_field('Margin', _select('i2p-margin', [[0, 'None'], [5, 'Small (5 mm)'], [10, 'Normal (10 mm)'], [20, 'Large (20 mm)']], o.margin_mm))}
        ${_field('Image quality', _select('i2p-quality', [[100, 'Original (no recompression)'], [92, 'High'], [85, 'Good (default)'], [70, 'Medium'], [55, 'Small file']], o.quality))}
      </div>
      ${_field('Output file', `<div class="input-group"><span class="input-icon">${Icons.svg('file-pdf', 14)}</span><input id="i2p-out" class="input-text" value="${_esc(_stem(items[0]) + (multi ? '_combined' : '') + '.pdf')}"></div>`, 'id="i2p-out-row"')}
      ${_noteHtml('i2p-note', 'Originals are kept. An existing file is never replaced — a number is added instead.')}
      ${_progressHtml('i2p-prog')}`;

    const i2pTitle = () => multi ? 'Images to PDF' : 'Image to PDF';
    const i2pSub = () => multi ? `${items.length} images` : _name(items[0]);
    const overlay = _openModal('img2pdf', i2pTitle(), body, [
      { label: 'Cancel', onClick: () => _close(overlay) },
      { label: 'Convert', primary: true, icon: 'file-pdf', onClick: run },
    ], { icon: 'file-pdf', subtitle: i2pSub() });
    overlay.querySelector('.modal')?.classList.add('cvt-modal');

    // ── list (multi) ──
    const thumbs = {};
    function renderList() {
      const list = $('i2p-list'); if (!list) return;
      list.innerHTML = items.map((p, i) => `
        <div class="cvt-row reorder-row" draggable="true" data-idx="${i}">
          <span class="reorder-grip" title="Drag to reorder">${Icons.svg('grip-vertical', 14)}</span>
          <span class="reorder-num">${i + 1}</span>
          <span class="reorder-thumb">${thumbs[p] ? `<img src="${thumbs[p]}" alt="">` : `<span class="ft ft-img">${Icons.svg('file-image', 16)}</span>`}</span>
          <span class="reorder-name"><span title="${_esc(p)}">${_esc(_name(p))}</span><small>${_esc(_ext(p).replace('.', '').toUpperCase())} image</small></span>
          <span class="reorder-actions">
            <button type="button" class="icon-btn icon-btn-sm" data-act="up" title="Move up" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>${Icons.svg('arrow-up', 14)}</button>
            <button type="button" class="icon-btn icon-btn-sm" data-act="down" title="Move down" aria-label="Move down" ${i === items.length - 1 ? 'disabled' : ''}>${Icons.svg('arrow-down', 14)}</button>
            <button type="button" class="icon-btn icon-btn-sm icon-btn-danger" data-act="rm" title="Remove from list" aria-label="Remove from list">${Icons.svg('x', 14)}</button>
          </span>
        </div>`).join('');
      _subtitle(overlay, i2pSub());
    }
    async function loadThumbs() {
      for (const p of items.slice(0, 80)) {
        if (!overlay.isConnected) return;
        if (thumbs[p]) continue;
        try {
          const r = await SFM.getImagePreview(p, 64);
          if (r.ok) { thumbs[p] = r.data_url; renderList(); }
        } catch (e) {}
      }
    }
    if (multi) {
      renderList();
      loadThumbs();
      const list = $('i2p-list');
      let dragIdx = -1;
      list.addEventListener('click', e => {
        const b = e.target.closest('[data-act]'); if (!b) return;
        const i = +b.closest('.cvt-row').dataset.idx;
        if (b.dataset.act === 'up' && i > 0) [items[i - 1], items[i]] = [items[i], items[i - 1]];
        if (b.dataset.act === 'down' && i < items.length - 1) [items[i + 1], items[i]] = [items[i], items[i + 1]];
        if (b.dataset.act === 'rm') {
          if (items.length <= 1) { App.toast('Keep at least one image', 'warning'); return; }
          items.splice(i, 1);
        }
        renderList();
      });
      list.addEventListener('dragstart', e => {
        const row = e.target.closest('.cvt-row'); if (!row) return;
        dragIdx = +row.dataset.idx; row.classList.add('is-dragging');
        try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(dragIdx)); } catch (err) {}
      });
      list.addEventListener('dragover', e => {
        const row = e.target.closest('.cvt-row'); if (!row) return;
        e.preventDefault();
        list.querySelectorAll('.drop-before').forEach(r => r.classList.remove('drop-before'));
        row.classList.add('drop-before');
      });
      list.addEventListener('drop', e => {
        const row = e.target.closest('.cvt-row'); if (!row || dragIdx < 0) return;
        e.preventDefault();
        const to = +row.dataset.idx;
        const [m] = items.splice(dragIdx, 1);
        items.splice(to, 0, m);
        dragIdx = -1;
        renderList();
      });
      list.addEventListener('dragend', () => { dragIdx = -1; renderList(); });
      $('i2p-sort')?.addEventListener('click', () => {
        items.sort((a, b) => _name(a).localeCompare(_name(b), undefined, { numeric: true, sensitivity: 'base' }));
        renderList();
      });
      const seg = $('i2p-mode');
      const syncMode = () => {
        seg.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
        $('i2p-out-row')?.classList.toggle('hidden', mode !== 'combine');
        Dialogs.setBtn(_btn(overlay, 'Convert'), mode === 'combine' ? 'Create PDF' : `Create ${items.length} PDFs`, 'file-pdf');
      };
      seg.addEventListener('click', e => {
        const b = e.target.closest('[data-mode]'); if (!b) return;
        mode = b.dataset.mode; syncMode();
      });
      syncMode();
    }
    const syncOrient = () => { const s = $('i2p-orient'); if (s) s.disabled = $('i2p-size')?.value === 'fit'; };
    $('i2p-size')?.addEventListener('change', syncOrient);
    syncOrient();

    let running = false;
    async function run() {
      if (running) return;
      const opts = {
        page_size:   $('i2p-size')?.value || 'fit',
        orientation: $('i2p-orient')?.value || 'auto',
        margin_mm:   +($('i2p-margin')?.value || 0),
        quality:     +($('i2p-quality')?.value || 85),
      };
      _save('img2pdf', opts);
      let outName = ($('i2p-out')?.value || '').trim();
      if (outName && !/\.pdf$/i.test(outName)) outName += '.pdf';
      if (/[\\/:*?"<>|]/.test(outName)) { App.toast('The file name contains characters Windows does not allow', 'warning'); return; }
      const dir = items[0].replace(/[\\/][^\\/]+$/, '');
      const job = _newJob('i2p');
      running = true;
      _busy(overlay, ['Convert', 'Cancel'], true);
      App.setStatus('Converting to PDF…', true);
      _progress('i2p-prog', 0, items.length, 'Starting…');
      const r = await _runJob(job,
        () => SFM.imagesToPdfAsync(items.slice(), { ...opts, mode, out_path: outName ? dir + '\\' + outName : '' }, job),
        p => _progress('i2p-prog', p.done, p.total, p.done < p.total ? `Adding ${p.done + 1} of ${p.total}: ${p.label}` : p.label));
      running = false;
      App.setStatus('Ready');
      _busy(overlay, ['Convert', 'Cancel'], false);
      if (!r.ok) {
        _progress('i2p-prog', 0, 1, '');
        App.toast('Convert to PDF failed: ' + _esc(r.error), 'error', 7000);
        _noteError('i2p-note', r.error);
        return;
      }
      _close(overlay);
      if (r.mode === 'separate') {
        const fails = (r.results || []).filter(x => !x.ok);
        App.toast(`Created ${r.files.length} PDF(s)` + (fails.length ? ` — ${fails.length} failed` : ''), fails.length ? 'warning' : 'success', 5000);
        fails.slice(0, 3).forEach(f => App.toast(_esc(_name(f.path)) + ': ' + _esc(f.error), 'error', 7000));
        _reveal(r.files[r.files.length - 1]);
      } else {
        const skipped = r.skipped || [];
        App.toast(`Created <strong>${_esc(_name(r.out_path))}</strong> — ${r.pages} page(s), ${_fmtBytes(r.size)}`, 'success', 5000);
        if (skipped.length) App.toast(`Skipped ${skipped.length}: ` + skipped.slice(0, 3).map(s => _esc(_name(s.path)) + ' (' + _esc(s.error) + ')').join('; '), 'warning', 8000);
        _reveal(r.out_path);
      }
    }
  }

  // ── 2. PDF → Images ───────────────────────────────────────────────────────
  async function openPdfToImages(path) {
    if (!path) return;
    const o = _load('pdf2img', { fmt: 'png', dpi: 150, quality: 90 });
    const body = `
      ${_field('Format', _segSelect('p2i-fmt', [['png', 'PNG · lossless'], ['jpg', 'JPG · small'], ['webp', 'WEBP · smallest']], o.fmt))}
      <div class="field-grid">
        ${_field('Resolution', _select('p2i-dpi', [[72, '72 dpi (screen)'], [150, '150 dpi (balanced)'], [200, '200 dpi (sharp)'], [300, '300 dpi (print)']], o.dpi))}
        ${_field('JPG / WEBP quality', _select('p2i-quality', [[95, 'Best'], [90, 'High'], [80, 'Good'], [65, 'Small']], o.quality), 'id="p2i-q-row"')}
      </div>
      ${_field('Pages', _segSelect('p2i-which', [['all', 'All pages'], ['range', 'Page range']], 'all'))}
      ${_field('Page range', '<input id="p2i-range" class="input-text" placeholder="e.g. 1-3, 5, 8-10"><div class="field-hint">Separate pages and ranges with commas.</div>', 'id="p2i-range-row"')}
      ${_noteHtml('p2i-note', `Images are saved in a new folder <strong>${_esc(_stem(path))}_images</strong> next to the PDF.`)}
      ${_progressHtml('p2i-prog')}`;

    const overlay = _openModal('pdf2img', 'PDF to images', body, [
      { label: 'Cancel', onClick: () => _close(overlay) },
      { label: 'Convert', primary: true, icon: 'images', onClick: run },
    ], { icon: 'images', subtitle: _name(path) });
    overlay.querySelector('.modal')?.classList.add('cvt-modal');
    _wireSegs(overlay);

    let pageCount = 0;
    SFM.getPdfPageCount(path).then(r => {
      if (r.ok) { pageCount = r.count; _subtitle(overlay, `${_name(path)} · ${r.count} page${r.count === 1 ? '' : 's'}`); }
    }).catch(() => {});
    const sync = () => {
      $('p2i-range-row')?.classList.toggle('hidden', $('p2i-which')?.value !== 'range');
      $('p2i-q-row')?.classList.toggle('is-dim', $('p2i-fmt')?.value === 'png');
    };
    $('p2i-which')?.addEventListener('change', () => { sync(); if ($('p2i-which').value === 'range') $('p2i-range')?.focus(); });
    $('p2i-fmt')?.addEventListener('change', sync);
    sync();

    let running = false;
    async function run() {
      if (running) return;
      const opts = {
        fmt: $('p2i-fmt')?.value || 'png',
        dpi: +($('p2i-dpi')?.value || 150),
        quality: +($('p2i-quality')?.value || 90),
        pages: $('p2i-which')?.value === 'range' ? ($('p2i-range')?.value || '').trim() : '',
      };
      if ($('p2i-which')?.value === 'range' && !opts.pages) { App.toast('Enter a page range, e.g. 1-3,5', 'warning'); $('p2i-range')?.focus(); return; }
      _save('pdf2img', { fmt: opts.fmt, dpi: opts.dpi, quality: opts.quality });
      if (opts.dpi >= 300 && pageCount > 50 && !confirm(`Render ${pageCount} pages at ${opts.dpi} dpi? This can take a while and use a lot of disk space.`)) return;
      const job = _newJob('p2i');
      running = true;
      _busy(overlay, ['Convert', 'Cancel'], true);
      App.setStatus('Converting PDF to images…', true);
      _progress('p2i-prog', 0, 0, 'Starting…');
      const r = await _runJob(job, () => SFM.pdfToImagesAsync(path, opts, job),
        p => _progress('p2i-prog', p.done, p.total, p.done < p.total ? `Rendering ${p.label} (${p.done + 1} of ${p.total})` : 'Finishing…'));
      running = false;
      App.setStatus('Ready');
      _busy(overlay, ['Convert', 'Cancel'], false);
      if (!r.ok) {
        _progress('p2i-prog', 0, 1, '');
        _noteError('p2i-note', r.error);
        App.toast('PDF → Images failed: ' + _esc(r.error), 'error', 7000);
        return;
      }
      _close(overlay);
      App.toast(`Saved ${r.files.length} ${opts.fmt.toUpperCase()} image(s) in <strong>${_esc(_name(r.out_dir))}</strong>`, 'success', 5000);
      _reveal(r.out_dir);
    }
  }

  // ── 3. Convert image format ───────────────────────────────────────────────
  function openConvertImage(paths) {
    const items = (Array.isArray(paths) ? paths : [paths]).filter(isImage);
    if (!items.length) { App.toast('Select image(s) first', 'warning'); return; }
    const o = _load('convimg', { fmt: 'jpg', quality: 92 });
    const body = `
      ${_field('Convert to', _segSelect('ci2-fmt', [['jpg', 'JPG'], ['png', 'PNG'], ['webp', 'WEBP'], ['bmp', 'BMP'], ['tiff', 'TIFF']], o.fmt))}
      ${_field('Quality (JPG / WEBP)', _select('ci2-q', [[95, 'Best (95)'], [92, 'High (92)'], [85, 'Good (85)'], [75, 'Medium (75)'], [60, 'Small (60)']], o.quality), 'id="ci2-q-row"')}
      ${_noteHtml('ci2-note')}
      ${_progressHtml('ci2-prog')}`;
    const overlay = _openModal('convimg', items.length === 1 ? 'Convert image' : 'Convert images', body, [
      { label: 'Cancel', onClick: () => _close(overlay) },
      { label: 'Convert', primary: true, icon: 'convert', onClick: run },
    ], { icon: 'convert', subtitle: items.length === 1 ? _name(items[0]) + ' · original is kept' : `${items.length} images · originals are kept`, size: 'sm' });
    overlay.querySelector('.modal')?.classList.add('cvt-modal');
    _wireSegs(overlay);
    const sync = () => {
      const f = $('ci2-fmt')?.value;
      $('ci2-q-row')?.classList.toggle('is-dim', !['jpg', 'webp'].includes(f));
      const note = $('ci2-note');
      if (note) note.textContent = ['jpg', 'bmp'].includes(f)
        ? 'Transparent areas become white (JPG/BMP have no transparency).'
        : 'Saved next to the original; an existing file is never replaced.';
    };
    $('ci2-fmt')?.addEventListener('change', sync);
    sync();

    let running = false;
    async function run() {
      if (running) return;
      const fmt = $('ci2-fmt')?.value || 'jpg';
      const q = +($('ci2-q')?.value || 92);
      _save('convimg', { fmt, quality: q });
      running = true;
      _busy(overlay, ['Convert', 'Cancel'], true);
      App.setStatus('Converting images…', true);
      const outs = [], errs = [];
      for (let i = 0; i < items.length; i++) {
        _progress('ci2-prog', i, items.length, `Converting ${i + 1} of ${items.length}: ${_name(items[i])}`);
        let r;
        try { r = await SFM.convertImage(items[i], fmt, '', q); } catch (e) { r = { ok: false, error: String(e) }; }
        if (r.ok) outs.push(r.out); else errs.push(_name(items[i]) + ': ' + r.error);
      }
      running = false;
      App.setStatus('Ready');
      _busy(overlay, ['Convert', 'Cancel'], false);
      if (!outs.length) {
        _progress('ci2-prog', 0, 1, '');
        _noteError('ci2-note', errs.join('; '));
        App.toast('Convert failed: ' + _esc(errs[0] || ''), 'error', 7000);
        return;
      }
      _close(overlay);
      App.toast(outs.length === 1 && items.length === 1
        ? 'Converted → <strong>' + _esc(_name(outs[0])) + '</strong>'
        : `Converted ${outs.length} of ${items.length} image(s) to ${fmt.toUpperCase()}`, 'success');
      if (errs.length) App.toast('Failed: ' + _esc(errs.slice(0, 3).join('; ')), 'error', 7000);
      _reveal(outs[outs.length - 1]);
    }
  }

  // ── 4. Compress (PDFs, images or a mix) ───────────────────────────────────
  // One dialog for any selection. A target size ("Under 100 KB") applies to
  // every file; "No limit" shows the PDF presets / image quality options.
  // [key, title, description, expected effect, effect tone, icon]
  const PDF_PRESETS = [
    ['screen',   'Smallest',     'Images at 72 dpi. For email, WhatsApp and upload portals.', 'Largest saving', 'pill-green', 'compress'],
    ['ebook',    'Balanced',     'Images at 150 dpi. Sharp on screen, fine for printing text.', 'Good saving', 'pill-blue', 'file-text'],
    ['printer',  'High quality', 'Images at 300 dpi. Best for printing.', 'Smaller saving', 'pill-neutral', 'file-image'],
    ['lossless', 'Lossless',     'Only removes waste; images are untouched.', 'No quality loss', 'pill-neutral', 'shield-check'],
  ];
  // [value (KB, '0' = none, 'custom'), title, hint]
  const TARGETS = [
    ['0', 'No limit', 'Pick quality'],
    ['100', '100 KB', 'Forms, portals'],
    ['200', '200 KB', 'Email'],
    ['500', '500 KB', 'Uploads'],
    ['1024', '1 MB', 'Sharper'],
    ['2048', '2 MB', 'Print-ready'],
    ['custom', 'Custom', 'Any size'],
  ];
  // Mirror of media_convert.PDF_TARGET_LADDER (index 0 = clean-up only): the
  // "Custom level" slider picks a step; slider value s -> ladder index (length - s).
  const PDF_LADDER = [null, [300, 85], [300, 80], [250, 80], [200, 80], [200, 70], [170, 70],
    [150, 70], [150, 60], [135, 60], [120, 55], [110, 55], [96, 55], [96, 45], [85, 45],
    [72, 40], [72, 35], [60, 35], [60, 30], [50, 30]];
  const LEVEL_MAX = PDF_LADDER.length - 1;
  const _levelIdx = s => PDF_LADDER.length - Math.max(1, Math.min(LEVEL_MAX, s | 0));
  const _levelText = s => { const st = PDF_LADDER[_levelIdx(s)]; return `Images ${st[0]} dpi · quality ${st[1]}`; };
  const CMP_DEFAULTS = { target: '0', custom: 300, unit: 'KB', preset: 'ebook', level: 12, quality: 70, edge: 1600, fmt: '' };
  const PREVIEW_DEBOUNCE = 250;
  const _check = () => `<span class="option-card-check">${Icons.svg('check', 10)}</span>`;

  // "100 KB", "1 MB", "1.5 MB" for a size given in KB.
  function _kbLabel(kb) {
    kb = Number(kb) || 0;
    if (kb >= 1024) return (Math.round(kb / 1024 * 10) / 10) + ' MB';
    return Math.round(kb) + ' KB';
  }
  // Short size for pills: "340 KB", "1.2 MB".
  function _short(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
    return (Math.round(n / 1024 / 1024 * 10) / 10) + ' MB';
  }

  function openCompress(paths) {
    const all = (Array.isArray(paths) ? paths : [paths]).filter(Boolean);
    const items = all.filter(p => isPdf(p) || isImage(p));
    if (!items.length) { App.toast('Compress works on PDFs and images', 'warning'); return; }
    if (items.length < all.length) App.toast(`${all.length - items.length} file(s) left out — only PDFs and images can be compressed`, 'info');
    const pdfs = items.filter(isPdf), imgs = items.filter(isImage);
    const single = items.length === 1;
    const o = _load('compress', CMP_DEFAULTS);
    const knownTarget = TARGETS.some(([v]) => v === String(o.target));
    const curTarget = knownTarget ? String(o.target) : '0';

    const title = single ? (pdfs.length ? 'Compress PDF' : 'Compress image')
      : !imgs.length ? 'Compress PDFs' : !pdfs.length ? 'Compress images' : 'Compress files';
    const countText = [pdfs.length && `${pdfs.length} PDF${pdfs.length === 1 ? '' : 's'}`,
                       imgs.length && `${imgs.length} image${imgs.length === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
    const subtitle = single ? _name(items[0]) : countText;

    const body = `
      <div class="field">
        <div class="section-head"><span class="field-label">Target size</span><span class="cmp-target-hint" id="cmp-target-hint"></span></div>
        <div class="cmp-targets" id="cmp-targets" role="radiogroup" aria-label="Target size">
          ${TARGETS.map(([v, t, d]) => `
            <label class="cmp-target"><input type="radio" name="cmp-target" value="${v}" ${v === curTarget ? 'checked' : ''}>
              <span class="cmp-target-title">${t}</span><span class="cmp-target-desc">${d}</span></label>`).join('')}
        </div>
        <div class="cmp-custom hidden" id="cmp-custom-row">
          <label class="field-label" for="cmp-custom">Custom target</label>
          <input id="cmp-custom" type="number" min="1" step="any" class="input-text" value="${_esc(o.custom)}">
          ${_segSelect('cmp-unit', [['KB', 'KB'], ['MB', 'MB']], o.unit)}
        </div>
      </div>
      <div class="cmp-quality" id="cmp-quality">
        ${pdfs.length ? `
        <div class="field">
          <span class="field-label">${imgs.length ? 'PDF quality' : 'Compression level'}</span>
          <div class="option-cards" id="pc-presets" role="radiogroup">
            ${PDF_PRESETS.map(([k, t, d, fx, tone, ic]) => `
              <label class="option-card"><input type="radio" name="pc-preset" value="${k}" ${k === o.preset ? 'checked' : ''}>
                <span class="option-card-icon">${Icons.svg(ic, 16)}</span>
                <span class="option-card-body"><span class="option-card-title">${t}</span>
                  <span class="option-card-desc">${d}</span>
                  <span class="option-card-meta"><span class="pill ${tone}">${fx}</span><span class="pill pill-neutral cmp-est" id="pc-est-${k}"></span></span></span>
                ${_check()}</label>`).join('')}
            <label class="option-card cmp-level-card"><input type="radio" name="pc-preset" value="level" ${o.preset === 'level' ? 'checked' : ''}>
              <span class="option-card-icon">${Icons.svg('settings', 16)}</span>
              <span class="option-card-body"><span class="option-card-title">Custom level</span>
                <span class="option-card-desc">Drag the slider until the size looks right.</span>
                <span class="option-card-meta"><span class="pill pill-neutral cmp-est" id="pc-est-level"></span></span></span>
              ${_check()}</label>
          </div>
          <div class="cmp-level hidden" id="pc-level-row">
            <div class="section-head"><label class="field-label" for="pc-level" id="pc-level-text">${_esc(_levelText(o.level))}</label><span class="cmp-readout" id="pc-level-est"></span></div>
            <input id="pc-level" type="range" min="1" max="${LEVEL_MAX}" step="1" value="${_esc(o.level)}" class="cvt-range">
            <div class="cvt-range-scale"><span>Smaller file</span><span>Better quality</span></div>
          </div>
        </div>` : ''}
        ${imgs.length ? `
        <div class="field" id="ic-q-row">
          <div class="section-head"><label class="field-label" for="ic-q">${pdfs.length ? 'Image quality' : 'Quality'} <span class="pill pill-neutral" id="ic-q-val">${_esc(o.quality)}</span></label><span class="cmp-readout" id="ic-est"></span></div>
          <input id="ic-q" type="range" min="10" max="95" step="1" value="${_esc(o.quality)}" class="cvt-range">
          <div class="cvt-range-scale"><span>Smaller file</span><span>Better quality</span></div>
        </div>
        <div class="field-grid">
          ${_field('Max size (longest edge)', _select('ic-edge', [[0, 'Original'], [3000, '3000 px'], [2000, '2000 px'], [1600, '1600 px'], [1200, '1200 px'], [1024, '1024 px'], [800, '800 px']], o.edge))}
          ${_field('Output format', _select('ic-fmt', [['', 'Same as original'], ['jpg', 'JPG'], ['webp', 'WEBP'], ['png', 'PNG']], o.fmt))}
        </div>` : ''}
      </div>
      ${_noteHtml('cmp-note')}
      <div class="field">
        <div class="section-head"><span class="field-label">${single ? 'File' : `Files <span class="text-muted">· ${_esc(countText)}</span>`}</span><span class="cmp-total" id="cmp-total"></span></div>
        <div class="result-list cmp-files" id="cmp-files"></div>
      </div>
      ${single && pdfs.length ? _field('Output file', `<div class="input-group"><span class="input-icon">${Icons.svg('file-pdf', 14)}</span><input id="pc-out" class="input-text" value="${_esc(_stem(items[0]) + '_compressed.pdf')}"></div><div class="field-hint">The original is kept. An existing file is never replaced.</div>`)
        : `<div class="field-hint">${single ? 'The original is kept; the copy is' : 'Originals are kept; each copy is'} saved as <em>name_compressed</em> next to it. Existing files are never replaced.</div>`}
      ${_progressHtml('cmp-prog')}
      <div id="cmp-summary" class="hidden"></div>`;

    const SAVE = 'Save smallest anyway';
    const overlay = _openModal('compress', title, body, [
      { label: SAVE, left: true, icon: 'download', onClick: saveSmallest },
      { label: 'Close', onClick: () => _close(overlay) },
      { label: 'Compress', primary: true, icon: 'compress', onClick: run },
    ], { icon: 'compress', subtitle });
    overlay.querySelector('.modal')?.classList.add('cvt-modal', 'cmp-modal');
    _btn(overlay, SAVE)?.classList.add('hidden');
    _wireSegs(overlay);

    const sizes = {};          // path -> bytes (current size)
    const results = {};        // path -> last bridge result
    const busy = {};           // path -> status line while running
    // Size previews (computed in memory by the bridge; nothing is written)
    const pvPdf = {};          // path -> {sizes: {key: bytes}, smallest, estimate, error}
    const pvImg = {};          // path -> {[imgKey]: {after, kept}, smallest, error}
    const jobKeys = {};        // preview job -> image settings key it measured
    const jobPrefix = _newJob('cpv');
    let pvSeq = 0;
    const timers = {};

    const pdfMode = () => overlay.querySelector('input[name="pc-preset"]:checked')?.value || 'ebook';
    const levelVal = () => parseInt($('pc-level')?.value, 10) || o.level || 12;
    const pdfKey = () => pdfMode() === 'level' ? 'level-' + _levelIdx(levelVal()) : pdfMode();
    const imgOpts = () => ({ quality: parseInt($('ic-q')?.value, 10) || 70,
                             edge: $('ic-edge') ? (parseInt($('ic-edge').value, 10) || 0) : 0,
                             fmt: $('ic-fmt') ? ($('ic-fmt').value || '') : '' });
    const imgKey = () => { const x = imgOpts(); return `${x.quality}|${x.edge}|${x.fmt}`; };

    // Predicted size of one PDF for a preset/level key ({bytes, kept, estimate} or null).
    function pdfPred(p, key) {
      const v = pvPdf[p], b = v && v.sizes && v.sizes[key];
      if (b == null) return null;
      const before = sizes[p] || v.before || 0;
      const kept = b >= before * 0.99;
      return { bytes: kept ? before : b, kept, estimate: !!v.estimate };
    }
    // Predicted size of one file for the current "No limit" settings.
    function predicted(p) {
      if (isPdf(p)) return pdfPred(p, pdfKey());
      const v = pvImg[p] && pvImg[p][imgKey()];
      return v ? { bytes: v.kept ? (sizes[p] || v.after) : v.after, kept: !!v.kept, estimate: false } : null;
    }
    function smallestOf(p) {
      const v = isPdf(p) ? pvPdf[p] : pvImg[p];
      return v && v.smallest != null ? { bytes: v.smallest, estimate: !!v.estimate } : null;
    }
    const pvError = p => (isPdf(p) ? pvPdf[p] : pvImg[p])?.error;

    function requestPdf() {
      if (!pdfs.length) return;
      const keys = [...PDF_PRESETS.map(x => x[0]), pdfKey()]
        .filter((k, i, a) => a.indexOf(k) === i)
        .filter(k => pdfs.some(p => !pvError(p) && !(pvPdf[p] && pvPdf[p].sizes && k in pvPdf[p].sizes)));
      const needSmall = pdfs.some(p => !pvError(p) && !(pvPdf[p] && pvPdf[p].smallest != null));
      if (keys.length || needSmall) {
        const job = `${jobPrefix}-pdf-${++pvSeq}`;
        SFM.compressPreview(pdfs.filter(p => !pvError(p)), { channel: 'pdf', pdf_keys: keys, smallest: needSmall }, job).catch(() => {});
      }
      renderEstimates();
    }
    function requestImg() {
      if (!imgs.length) return;
      const key = imgKey(), x = imgOpts();
      const need = imgs.filter(p => !pvError(p) && !(pvImg[p] && pvImg[p][key]));
      const needSmall = imgs.some(p => !pvError(p) && !(pvImg[p] && pvImg[p].smallest != null));
      if (need.length || needSmall) {
        const job = `${jobPrefix}-img-${++pvSeq}`;
        jobKeys[job] = key;
        SFM.compressPreview(need.length ? need : imgs.filter(p => !pvError(p)),
          { channel: 'img', img_quality: x.quality, img_edge: x.edge, img_fmt: x.fmt, smallest: needSmall }, job).catch(() => {});
      }
      renderEstimates();
    }
    function debounce(name, fn) {
      clearTimeout(timers[name]);
      timers[name] = setTimeout(fn, PREVIEW_DEBOUNCE);
    }
    const offPreview = SFM.on('compress_preview', ev => {
      if (!overlay.isConnected) { offPreview(); return; }
      if (!ev || !String(ev.job || '').startsWith(jobPrefix) || ev.done) return;
      const p = ev.path;
      if (isPdf(p)) {
        const cur = pvPdf[p] || (pvPdf[p] = { sizes: {} });
        if (!ev.ok) cur.error = ev.error;
        else {
          Object.assign(cur.sizes, ev.sizes || {});
          if (ev.smallest != null) cur.smallest = ev.smallest;
          cur.estimate = !!ev.estimate; cur.before = ev.before;
        }
      } else {
        const cur = pvImg[p] || (pvImg[p] = {});
        if (!ev.ok) cur.error = ev.error;
        else {
          const key = jobKeys[ev.job];
          if (key) cur[key] = { after: ev.after, kept: ev.kept_original };
          if (ev.smallest != null) cur.smallest = ev.smallest;
        }
      }
      if (ev.before != null && sizes[p] == null) sizes[p] = ev.before;
      renderEstimates();
    });

    const _delta = (before, after) => before ? ` ${after <= before ? '−' : '+'}${Math.abs(Math.round((before - after) * 100 / before))}%` : '';
    const measuring = `<span class="spinner cmp-spin"></span>Measuring…`;
    // Total predicted size for a set of files, or null while any is unknown.
    function totalFor(list, fn) {
      let before = 0, after = 0, est = false;
      for (const p of list) {
        if (pvError(p)) continue;
        const v = fn(p); if (!v) return null;
        before += sizes[p] || 0; after += v.bytes; est = est || v.estimate;
      }
      return { before, after, est };
    }
    function renderEstimates() {
      if (!overlay.isConnected) return;
      // PDF preset cards + custom level: "≈ 340 KB −62%"
      if (pdfs.length) {
        [...PDF_PRESETS.map(x => x[0]), 'level'].forEach(k => {
          const el = $('pc-est-' + k); if (!el) return;
          if (k === 'level' && pdfMode() !== 'level') { el.innerHTML = ''; return; }
          const key = k === 'level' ? 'level-' + _levelIdx(levelVal()) : k;
          const t = totalFor(pdfs, p => pdfPred(p, key));
          el.innerHTML = t ? _esc(`≈ ${_short(t.after)}${_delta(t.before, t.after)}`) + (t.est ? ' <span class="cmp-est-tag">estimate</span>' : '') : measuring;
          el.title = t && t.est ? 'Estimated from sample pages (large PDF)' : 'Predicted size, measured in memory';
          el.classList.toggle('pill-green', !!t && t.after < t.before * 0.99);
        });
        const le = $('pc-level-est'), src = $('pc-est-level');
        if (le && src) le.innerHTML = src.innerHTML;
      }
      // Image quality readout: "≈ 128 KB −55%"
      const ie = $('ic-est');
      if (ie) {
        const t = totalFor(imgs, predicted);
        ie.innerHTML = t ? `≈ ${_esc(_short(t.after))}<span class="text-muted">${_esc(_delta(t.before, t.after))}</span>` : measuring;
      }
      // Target cards: how many files can reach each size
      const allSmall = items.map(p => pvError(p) ? { bytes: 0 } : smallestOf(p));
      overlay.querySelectorAll('.cmp-target').forEach(card => {
        const v = card.querySelector('input').value, d = card.querySelector('.cmp-target-desc');
        if (!/^\d+$/.test(v) || v === '0') return;
        const tb = parseInt(v, 10) * 1024;
        if (allSmall.some(x => !x)) { d.textContent = TARGETS.find(t => t[0] === v)[2]; card.classList.remove('is-unreachable'); return; }
        const ok = items.filter((p, i) => (sizes[p] || 0) <= tb || allSmall[i].bytes <= tb).length;
        d.textContent = ok === items.length ? (single ? ((sizes[items[0]] || 0) <= tb ? 'Already under' : '✓ Reachable') : '✓ All files') : single ? 'min ≈ ' + _short(allSmall[0].bytes) : `${ok} of ${items.length} files`;
        card.classList.toggle('is-unreachable', ok < items.length);
      });
      if (!Object.keys(results).length) renderRows();
    }

    // Target in KB (0 = no limit).
    function targetKb() {
      const v = overlay.querySelector('input[name="cmp-target"]:checked')?.value || '0';
      if (v !== 'custom') return parseInt(v, 10) || 0;
      const n = parseFloat($('cmp-custom')?.value);
      if (!(n > 0)) return 0;
      return ($('cmp-unit')?.value === 'MB') ? n * 1024 : n;
    }

    function rowHtml(p) {
      const r = results[p], size = sizes[p], tkb = targetKb();
      const tb = tkb * 1024;
      const icon = Icons.file({ name: _name(p), ext: _ext(p) }, 16);
      let cls = '', lead = `<span class="result-icon cmp-ft">${icon}</span>`, sub = '', detail = '';
      if (busy[p]) {
        lead = `<span class="result-icon"><span class="spinner"></span></span>`;
        sub = busy[p];
        detail = size != null ? _fmtBytes(size) : '';
      } else if (!r) {
        sub = isPdf(p) ? 'PDF' : _ext(p).replace('.', '').toUpperCase() + ' image';
        detail = size == null ? '<span class="text-muted">…</span>' : _fmtBytes(size);
        if (pvError(p)) { sub += ' · ' + pvError(p); }
        else if (tb) {
          const sm = smallestOf(p);
          if (size != null && size <= tb) detail += `<span class="pill pill-neutral">Already under ${_kbLabel(tkb)}</span>`;
          else if (!sm) detail += `<span class="pill pill-neutral">${measuring}</span>`;
          else if (sm.bytes <= tb) detail += `<span class="pill pill-green">${Icons.svg('check', 12)}Reachable</span>`;
          else detail += `<span class="pill pill-yellow" title="Even the strongest setting stays above ${_kbLabel(tkb)}">min ≈ ${_short(sm.bytes)}</span>`;
          if (sm && sm.estimate) sub += ' · estimate from sample pages';
        } else if (size != null) {
          const pr = predicted(p);
          if (!pr) detail += `<span class="pill pill-neutral">${measuring}</span>`;
          else if (pr.kept) detail += `<span class="pill pill-neutral">No smaller copy possible</span>`;
          else detail = `<span class="size-change">${_fmtBytes(size)}${Icons.svg('arrow-right', 12)}<strong>≈ ${_fmtBytes(pr.bytes)}</strong></span>${_pctPill(size, pr.bytes)}`;
          if (pr && pr.estimate) sub += ' · estimate from sample pages';
        }
      } else if (!r.ok) {
        cls = 'err';
        lead = `<span class="result-icon">${Icons.svg('x-circle', 16)}</span>`;
        sub = r.error || 'Failed';
      } else {
        const rt = r.target_bytes ? _kbLabel(r.target_bytes / 1024) : '';
        const missed = r.target_met === false;
        const written = !r.kept_original;
        cls = missed ? 'warn' : written ? 'ok' : 'kept';
        lead = `<span class="result-icon">${Icons.svg(missed ? 'alert-triangle' : written ? 'check-circle' : 'check', 16)}</span>`;
        sub = written ? 'Saved as ' + _name(r.out) : '';
        if (r.format_changed && written && r.target_bytes) sub += ' · saved as JPG to reach the target';
        else if (r.settings && written) sub += ' · ' + String(r.settings).toLowerCase();
        if (missed && !written) sub = r.can_save_smallest ? 'Not saved — use “Save smallest anyway” to keep the smallest version' : 'No smaller copy is possible — the original was kept';
        else if (!written) sub = (r.note || 'Already well compressed').replace(/ — .*$/, '') + ' — original kept';
        if (written) detail = _sizeChange(r.before, r.after) + _pctPill(r.before, r.after);
        else if (missed && r.can_save_smallest) detail = `<span class="size-change">${_fmtBytes(r.before)}${Icons.svg('arrow-right', 12)}<span class="text-muted">${_fmtBytes(r.smallest)}</span></span>`;
        else detail = _fmtBytes(r.before);
        if (r.target_met === true) detail += `<span class="pill pill-green">${Icons.svg('check', 12)}Under ${rt}</span>`;
        if (missed) detail += `<span class="pill pill-yellow" title="${_esc(r.note || '')}">${_short(r.smallest || r.after)} — target not reachable</span>`;
      }
      return `<div class="result-row ${cls}" data-path="${_esc(p)}">${lead}
        <span class="result-name cmp-name"><span title="${_esc(p)}">${_esc(_name(p))}</span>${sub ? `<small title="${_esc(sub)}">${_esc(sub)}</small>` : ''}</span>
        <span class="result-detail">${detail}</span></div>`;
    }
    function renderRows() {
      const box = $('cmp-files'); if (!box) return;
      box.innerHTML = items.map(rowHtml).join('');
      const total = items.reduce((a, p) => a + (sizes[p] || 0), 0);
      const t = $('cmp-total');
      if (t) {
        let txt = Object.keys(sizes).length && !single ? 'Total ' + _fmtBytes(total) : '';
        if (txt && !targetKb() && !Object.keys(results).length) {
          const pt = totalFor(items, predicted);
          if (pt) txt += ' → ≈ ' + _fmtBytes(pt.after + items.filter(pvError).reduce((a, p) => a + (sizes[p] || 0), 0));
        }
        t.textContent = txt;
      }
    }
    function renderRow(p) {
      const row = $('cmp-files')?.querySelector(`[data-path="${CSS.escape(p)}"]`);
      if (row) row.outerHTML = rowHtml(p); else renderRows();
    }

    function sync() {
      const v = overlay.querySelector('input[name="cmp-target"]:checked')?.value || '0';
      const tkb = targetKb();
      $('cmp-custom-row')?.classList.toggle('hidden', v !== 'custom');
      $('cmp-quality')?.classList.toggle('hidden', v !== '0');
      $('pc-level-row')?.classList.toggle('hidden', pdfMode() !== 'level');
      const lt = $('pc-level-text'); if (lt && $('pc-level')) lt.textContent = _levelText(levelVal());
      const hint = $('cmp-target-hint');
      if (hint) hint.textContent = tkb ? `${single ? 'The copy' : 'Every copy'} at or under ${_kbLabel(tkb)}` : '';
      const note = $('cmp-note');
      if (note) {
        const bits = [];
        if (tkb) {
          bits.push('Finds the best quality that fits the target.');
          if (pdfs.length) bits.push('PDF images are downsampled step by step; text stays sharp text.');
          if (imgs.length) bits.push('Images shrink only if needed; PNG is saved as JPG.');
        }
        note.textContent = bits.join(' ');
      }
      renderEstimates();
    }
    // Changing a setting after a run goes back to showing predictions.
    function changed() {
      if (running) return;
      if (Object.keys(results).length) {
        items.forEach(p => delete results[p]);
        $('cmp-summary')?.classList.add('hidden');
        _btn(overlay, SAVE)?.classList.add('hidden');
        Dialogs.setBtn(_btn(overlay, 'Compress'), 'Compress', 'compress');
      }
      sync();
    }
    overlay.querySelectorAll('input[name="cmp-target"]').forEach(r => r.addEventListener('change', () => {
      changed();
      if (r.value === 'custom' && r.checked) $('cmp-custom')?.focus();
    }));
    $('cmp-custom')?.addEventListener('input', changed);
    $('cmp-unit')?.addEventListener('change', changed);
    overlay.querySelectorAll('input[name="pc-preset"]').forEach(r => r.addEventListener('change', () => { changed(); requestPdf(); }));
    $('pc-level')?.addEventListener('input', () => { changed(); debounce('pdf', requestPdf); });
    $('ic-q')?.addEventListener('input', () => {
      const v = $('ic-q-val'); if (v) v.textContent = $('ic-q').value;
      changed(); debounce('img', requestImg);
    });
    $('ic-edge')?.addEventListener('change', () => { changed(); requestImg(); });
    $('ic-fmt')?.addEventListener('change', () => { changed(); requestImg(); });
    sync();
    SFM.fileSizes(items).then(r => {
      if (r && r.ok) { Object.assign(sizes, r.sizes || {}); renderEstimates(); }
    }).catch(() => {});
    requestPdf();
    requestImg();

    async function compressOne(p, i, opts, saveSmallestFlag) {
      const prefix = items.length > 1 ? `Compressing ${i + 1} of ${items.length}: ${_name(p)}` : `Compressing ${_name(p)}`;
      busy[p] = opts.tkb ? `Looking for the best quality under ${_kbLabel(opts.tkb)}…` : 'Compressing…';
      renderRow(p);
      _progress('cmp-prog', i, items.length, prefix + '…');
      let r;
      try {
        if (isPdf(p)) {
          const job = _newJob('cmp');
          r = await _runJob(job,
            () => SFM.compressPdfAsync(p, opts.out, opts.preset, opts.tkb, job, saveSmallestFlag),
            pr => {
              busy[p] = pr.label || busy[p];
              renderRow(p);
              _progress('cmp-prog', i + (pr.total ? Math.min(pr.done / pr.total, 0.95) : 0), items.length, `${prefix} — ${pr.label || ''}`);
            });
        } else {
          r = await SFM.compressImage(p, opts.quality, opts.tkb ? 0 : opts.edge, opts.tkb ? '' : opts.fmt, '', opts.tkb, saveSmallestFlag);
        }
      } catch (e) { r = { ok: false, error: String(e) }; }
      delete busy[p];
      results[p] = r;
      if (r && r.ok && r.out) sizes[r.out] = r.after;
      renderRow(p);
      return r;
    }

    function finish(list) {
      const tkb = targetKb();
      const vals = items.map(p => results[p]).filter(Boolean);
      const good = vals.filter(r => r.ok && !r.kept_original);
      const failed = vals.filter(r => !r.ok).length;
      const misses = vals.filter(r => r.ok && r.target_met === false);
      const pending = misses.filter(r => r.kept_original && r.can_save_smallest);
      const met = vals.filter(r => r.ok && r.target_met === true).length;
      const before = good.reduce((a, r) => a + r.before, 0), after = good.reduce((a, r) => a + r.after, 0);
      $('cmp-prog')?.classList.add('hidden');
      let html;
      const tl = _kbLabel(tkb);
      if (failed === vals.length) {
        html = `<div class="callout error">${Icons.svg('alert-circle', 16)}<div class="callout-body"><span class="callout-title">Compression failed</span><span>See the details above.</span></div></div>`;
      } else if (tkb && misses.length) {
        const n = misses.length;
        html = `<div class="callout warning">${Icons.svg('alert-triangle', 16)}<div class="callout-body">
          <span class="callout-title">${n === vals.length ? `Could not reach ${tl}` : `${met} of ${vals.length} under ${tl} — ${n} could not reach it`}</span>
          <span>${pending.length ? (n === 1 && misses[0].note ? _esc(misses[0].note) + '. ' : '') + 'Nothing was saved for ' + (pending.length === 1 ? 'that file' : 'those files') + '. Use <strong>Save smallest anyway</strong> to keep the smallest version, or close to leave ' + (pending.length === 1 ? 'it' : 'them') + ' as ' + (pending.length === 1 ? 'it is' : 'they are') + '.'
            : misses.some(r => !r.kept_original) ? `The smallest possible version was saved${n > 1 ? ' for each' : ''} — still over ${tl}.`
            : 'No smaller copy is possible (text and vector content cannot be compressed further), so the original was kept.'}</span>
          ${good.length ? `<span class="row">${_sizeChange(before, after)}${_pctPill(before, after)}</span>` : ''}</div></div>`;
      } else if (good.length) {
        html = `<div class="callout success">${Icons.svg('check-circle', 16)}<div class="callout-body">
          <span class="callout-title">${tkb ? (met === vals.length ? (vals.length === 1 ? `Under ${tl}` : `All ${vals.length} files under ${tl}`) : `${met} of ${vals.length} under ${tl}`)
            : good.length === vals.length ? 'Done' : `${good.length} of ${vals.length} files compressed`}</span>
          <span class="row">${_sizeChange(before, after)}${_pctPill(before, after)}</span></div></div>`;
      } else {
        html = `<div class="callout ${tkb ? 'success' : 'warning'}">${Icons.svg(tkb ? 'check-circle' : 'info', 16)}<div class="callout-body">
          <span class="callout-title">${tkb ? `Already under ${tl}` : 'Already well compressed'}</span><span>No new file was needed, so the original${vals.length > 1 ? 's were' : ' was'} kept.</span></div></div>`;
      }
      const sumBox = $('cmp-summary');
      sumBox.innerHTML = html; sumBox.classList.remove('hidden');
      const sb = _btn(overlay, SAVE);
      if (sb) {
        sb.classList.toggle('hidden', !pending.length);
        Dialogs.setBtn(sb, pending.length > 1 ? `${SAVE} (${pending.length})` : SAVE, 'download');
      }
      Dialogs.setBtn(_btn(overlay, 'Compress'), 'Compress again', 'compress');
      const fresh = (list || []).filter(r => r && r.ok && !r.kept_original);
      if (fresh.length) {
        const last = fresh[fresh.length - 1];
        App.toast(fresh.length === 1
          ? `Compressed → <strong>${_esc(_name(last.out))}</strong>: ${_fmtBytes(last.before)} → ${_fmtBytes(last.after)} (${_pct(last.before, last.after)})`
          : `Compressed ${fresh.length} files: ${_fmtBytes(before)} → ${_fmtBytes(after)} (${_pct(before, after)})`, 'success', 6000);
        _reveal(last.out);
      }
      if (failed) App.toast(`${failed} file(s) failed — see the dialog`, 'error', 6000);
    }

    let running = false;
    let lastOpts = null;       // options of the last run (reused by "Save smallest anyway")
    function readOpts() {
      const v = overlay.querySelector('input[name="cmp-target"]:checked')?.value || '0';
      const opts = {
        tkb: targetKb(),
        preset: pdfKey(),
        quality: parseInt($('ic-q')?.value, 10) || o.quality || 70,
        edge: $('ic-edge') ? (parseInt($('ic-edge').value, 10) || 0) : o.edge,
        fmt: $('ic-fmt') ? ($('ic-fmt').value || '') : o.fmt,
        out: '',
      };
      _save('compress', { target: v, custom: parseFloat($('cmp-custom')?.value) || o.custom,
                          unit: $('cmp-unit')?.value || 'KB', preset: pdfMode(), level: levelVal(),
                          quality: opts.quality, edge: opts.edge, fmt: opts.fmt });
      return opts;
    }

    async function runList(list, opts, saveFlag) {
      running = true;
      _busy(overlay, ['Compress', 'Close', SAVE], true);
      App.setStatus('Compressing…', true);
      $('cmp-summary').classList.add('hidden');
      const out = [];
      for (let i = 0; i < list.length; i++) out.push(await compressOne(list[i], items.indexOf(list[i]), opts, saveFlag));
      running = false;
      App.setStatus('Ready');
      _busy(overlay, ['Compress', 'Close', SAVE], false);
      finish(out);
    }

    async function run() {
      if (running) return;
      const v = overlay.querySelector('input[name="cmp-target"]:checked')?.value || '0';
      if (v === 'custom' && !targetKb()) { App.toast('Enter a target size, e.g. 300 KB', 'warning'); $('cmp-custom')?.focus(); return; }
      const opts = readOpts();
      if (single && pdfs.length) {
        let outName = ($('pc-out')?.value || '').trim();
        if (outName && !/\.pdf$/i.test(outName)) outName += '.pdf';
        if (/[\\/:*?"<>|]/.test(outName)) { App.toast('The file name contains characters Windows does not allow', 'warning'); return; }
        opts.out = outName ? items[0].replace(/[\\/][^\\/]+$/, '') + '\\' + outName : '';
      }
      items.forEach(p => delete results[p]);
      lastOpts = opts;
      await runList(items.slice(), opts, false);
    }

    async function saveSmallest() {
      if (running || !lastOpts) return;
      const list = items.filter(p => { const r = results[p]; return r && r.ok && r.target_met === false && r.kept_original && r.can_save_smallest; });
      if (!list.length) return;
      await runList(list, lastOpts, true);
    }
    return overlay;
  }

  // Thin wrappers kept for existing callers.
  function openCompressPdf(paths) {
    const items = (Array.isArray(paths) ? paths : [paths]).filter(isPdf);
    if (!items.length) { App.toast('Select PDF file(s) first', 'warning'); return; }
    return openCompress(items);
  }
  function openCompressImages(paths) {
    const items = (Array.isArray(paths) ? paths : [paths]).filter(isImage);
    if (!items.length) { App.toast('Select image(s) first', 'warning'); return; }
    return openCompress(items);
  }

  return {
    isImage, isPdf, IMAGE_EXTS,
    openImagesToPdf, quickImageToPdf, openPdfToImages, openConvertImage,
    openCompress, openCompressPdf, openCompressImages,
  };
})();
