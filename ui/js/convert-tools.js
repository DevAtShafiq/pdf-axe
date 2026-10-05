/**
 * convert-tools.js — Conversion & compression dialogs
 *
 *   ConvertTools.openImagesToPdf(paths, {mode})   images → PDF (combine / one per image)
 *   ConvertTools.quickImageToPdf(path)            one image → PDF with defaults
 *   ConvertTools.openPdfToImages(path)            PDF pages → PNG/JPG/WEBP
 *   ConvertTools.openConvertImage(paths)          image format conversion
 *   ConvertTools.openCompressPdf(paths)           PDF compression (single / batch)
 *   ConvertTools.openCompressImages(paths)        image compression (single / batch)
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

  function _openModal(id, title, body, buttons) {
    return Dialogs.openModal(id, title, body, buttons);
  }
  function _btn(overlay, label) {
    return overlay && overlay.querySelector(`[data-modal-btn="${label}"]`);
  }
  function _busy(overlay, labels, on) {
    labels.forEach(l => { const b = _btn(overlay, l); if (b) b.disabled = on; });
  }
  function _close(overlay) { if (overlay && overlay._close) overlay._close(); }

  const _progressHtml = id => `
    <div class="cvt-progress hidden" id="${id}">
      <div class="progress-wrap"><div class="progress-bar" id="${id}-bar" style="width:0%"></div></div>
      <div class="cvt-progress-label" id="${id}-label"></div>
    </div>`;
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

  function _select(id, opts, value) {
    return `<select id="${id}" class="input-text cvt-select">` +
      opts.map(([v, l]) => `<option value="${_esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${_esc(l)}</option>`).join('') +
      '</select>';
  }
  const _field = (label, html, extra = '') =>
    `<div class="cvt-field" ${extra}><label class="detail-label">${label}</label>${html}</div>`;

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
      <div class="cvt-seg" id="i2p-mode">
        <button type="button" data-mode="combine">One combined PDF</button>
        <button type="button" data-mode="separate">One PDF per image</button>
      </div>
      <div class="cvt-list-head">
        <span class="detail-label">Page order — drag or use ↑ ↓</span>
        <button type="button" class="btn btn-ghost cvt-mini" id="i2p-sort">Sort by name</button>
      </div>
      <div class="cvt-list" id="i2p-list"></div>` : `
      <p class="text-muted cvt-sub">Convert <strong>${_esc(_name(items[0]))}</strong> to PDF.</p>`}
      <div class="cvt-grid">
        ${_field('Page size', _select('i2p-size', [['fit', 'Fit to image'], ['a4', 'A4'], ['letter', 'Letter'], ['legal', 'Legal']], o.page_size))}
        ${_field('Orientation', _select('i2p-orient', [['auto', 'Auto (follow image)'], ['portrait', 'Portrait'], ['landscape', 'Landscape']], o.orientation))}
        ${_field('Margin', _select('i2p-margin', [[0, 'None'], [5, 'Small (5 mm)'], [10, 'Normal (10 mm)'], [20, 'Large (20 mm)']], o.margin_mm))}
        ${_field('Image quality', _select('i2p-quality', [[100, 'Original (no recompression)'], [92, 'High'], [85, 'Good (default)'], [70, 'Medium'], [55, 'Small file']], o.quality))}
      </div>
      ${_field('Output file', `<input id="i2p-out" class="input-text cvt-input" value="${_esc(_stem(items[0]) + (multi ? '_combined' : '') + '.pdf')}">`, 'id="i2p-out-row"')}
      <p class="text-muted cvt-note" id="i2p-note">Originals are kept. An existing file is never replaced — a number is added instead.</p>
      ${_progressHtml('i2p-prog')}`;

    const overlay = _openModal('img2pdf', multi ? `Images → PDF (${items.length})` : 'Image → PDF', body, [
      { label: 'Cancel', onClick: () => _close(overlay) },
      { label: 'Convert', primary: true, onClick: run },
    ]);
    overlay.querySelector('.modal')?.classList.add('cvt-modal');

    // ── list (multi) ──
    const thumbs = {};
    function renderList() {
      const list = $('i2p-list'); if (!list) return;
      list.innerHTML = items.map((p, i) => `
        <div class="cvt-row" draggable="true" data-idx="${i}">
          <span class="cvt-num">${i + 1}</span>
          <span class="cvt-thumb">${thumbs[p] ? `<img src="${thumbs[p]}" alt="">` : '🖼'}</span>
          <span class="cvt-name" title="${_esc(p)}">${_esc(_name(p))}</span>
          <button type="button" class="cvt-icon-btn" data-act="up" title="Move up" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button type="button" class="cvt-icon-btn" data-act="down" title="Move down" ${i === items.length - 1 ? 'disabled' : ''}>↓</button>
          <button type="button" class="cvt-icon-btn" data-act="rm" title="Remove from list">✕</button>
        </div>`).join('');
      const title = overlay.querySelector('.modal-title');
      if (title) title.textContent = `Images → PDF (${items.length})`;
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
        dragIdx = +row.dataset.idx; row.classList.add('dragging');
        try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(dragIdx)); } catch (err) {}
      });
      list.addEventListener('dragover', e => {
        const row = e.target.closest('.cvt-row'); if (!row) return;
        e.preventDefault();
        list.querySelectorAll('.drop-target').forEach(r => r.classList.remove('drop-target'));
        row.classList.add('drop-target');
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
        const btn = _btn(overlay, 'Convert');
        if (btn) btn.textContent = mode === 'combine' ? 'Create PDF' : `Create ${items.length} PDFs`;
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
        const note = $('i2p-note'); if (note) { note.textContent = '❌ ' + r.error; note.classList.add('cvt-error'); }
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
      <p class="text-muted cvt-sub">Save pages of <strong>${_esc(_name(path))}</strong> as images
        <span id="p2i-count"></span>.</p>
      <div class="cvt-grid">
        ${_field('Format', _select('p2i-fmt', [['png', 'PNG (lossless)'], ['jpg', 'JPG (small)'], ['webp', 'WEBP (smallest)']], o.fmt))}
        ${_field('Resolution', _select('p2i-dpi', [[72, '72 dpi (screen)'], [150, '150 dpi (balanced)'], [200, '200 dpi (sharp)'], [300, '300 dpi (print)']], o.dpi))}
        ${_field('Pages', _select('p2i-which', [['all', 'All pages'], ['range', 'Page range…']], 'all'))}
        ${_field('JPG / WEBP quality', _select('p2i-quality', [[95, 'Best'], [90, 'High'], [80, 'Good'], [65, 'Small']], o.quality), 'id="p2i-q-row"')}
      </div>
      ${_field('Page range', '<input id="p2i-range" class="input-text cvt-input" placeholder="e.g. 1-3, 5, 8-10">', 'id="p2i-range-row"')}
      <p class="text-muted cvt-note" id="p2i-note">Images are saved in a new folder <strong>${_esc(_stem(path))}_images</strong> next to the PDF.</p>
      ${_progressHtml('p2i-prog')}`;

    const overlay = _openModal('pdf2img', 'PDF → Images', body, [
      { label: 'Cancel', onClick: () => _close(overlay) },
      { label: 'Convert', primary: true, onClick: run },
    ]);
    overlay.querySelector('.modal')?.classList.add('cvt-modal');

    let pageCount = 0;
    SFM.getPdfPageCount(path).then(r => {
      if (r.ok) { pageCount = r.count; const c = $('p2i-count'); if (c) c.textContent = '(' + r.count + ' page' + (r.count === 1 ? '' : 's') + ')'; }
    }).catch(() => {});
    const sync = () => {
      $('p2i-range-row')?.classList.toggle('hidden', $('p2i-which')?.value !== 'range');
      $('p2i-q-row')?.classList.toggle('cvt-dim', $('p2i-fmt')?.value === 'png');
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
        const note = $('p2i-note'); if (note) { note.textContent = '❌ ' + r.error; note.classList.add('cvt-error'); }
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
    const label = items.length === 1 ? `<strong>${_esc(_name(items[0]))}</strong>` : `<strong>${items.length}</strong> images`;
    const body = `
      <p class="text-muted cvt-sub">Convert ${label}. Originals are kept.</p>
      <div class="cvt-grid">
        ${_field('Convert to', _select('ci2-fmt', [['jpg', 'JPG'], ['png', 'PNG'], ['webp', 'WEBP'], ['bmp', 'BMP'], ['tiff', 'TIFF']], o.fmt))}
        ${_field('Quality (JPG / WEBP)', _select('ci2-q', [[95, 'Best (95)'], [92, 'High (92)'], [85, 'Good (85)'], [75, 'Medium (75)'], [60, 'Small (60)']], o.quality), 'id="ci2-q-row"')}
      </div>
      <p class="text-muted cvt-note" id="ci2-note"></p>
      ${_progressHtml('ci2-prog')}`;
    const overlay = _openModal('convimg', items.length === 1 ? 'Convert Image' : `Convert ${items.length} Images`, body, [
      { label: 'Cancel', onClick: () => _close(overlay) },
      { label: 'Convert', primary: true, onClick: run },
    ]);
    overlay.querySelector('.modal')?.classList.add('cvt-modal');
    const sync = () => {
      const f = $('ci2-fmt')?.value;
      $('ci2-q-row')?.classList.toggle('cvt-dim', !['jpg', 'webp'].includes(f));
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
        const note = $('ci2-note'); if (note) { note.textContent = '❌ ' + errs.join('; '); note.classList.add('cvt-error'); }
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

  // ── 4. Compress PDF(s) ────────────────────────────────────────────────────
  const PDF_PRESETS = [
    ['screen',   'Smallest',     'Images 72 dpi — email, WhatsApp, upload portals'],
    ['ebook',    'Balanced',     'Images 150 dpi — good on screen and for printing text'],
    ['printer',  'High quality', 'Images 300 dpi — print; smaller savings'],
    ['lossless', 'Lossless',     'Only removes waste; images untouched'],
  ];

  function _resultRow(name, r) {
    if (!r.ok) return `<div class="cvt-res err"><span class="cvt-name">${_esc(name)}</span><span>❌ ${_esc(r.error)}</span></div>`;
    if (r.kept_original) return `<div class="cvt-res kept"><span class="cvt-name">${_esc(name)}</span><span>${_fmtBytes(r.before)} · kept original — ${_esc((r.note || 'already optimized').replace(/ — .*$/, ''))}</span></div>`;
    const extra = r.target_met === false ? ' ⚠ target not reached' : '';
    return `<div class="cvt-res ok"><span class="cvt-name" title="${_esc(r.out)}">${_esc(_name(r.out))}</span>` +
      `<span>${_fmtBytes(r.before)} → <strong>${_fmtBytes(r.after)}</strong> (${_pct(r.before, r.after)})${extra}</span></div>`;
  }
  function _summary(results) {
    const good = results.filter(r => r.ok && !r.kept_original);
    const before = good.reduce((a, r) => a + r.before, 0);
    const after = good.reduce((a, r) => a + r.after, 0);
    return { good, before, after, kept: results.filter(r => r.ok && r.kept_original).length,
             failed: results.filter(r => !r.ok).length };
  }

  function openCompressPdf(paths) {
    const items = (Array.isArray(paths) ? paths : [paths]).filter(isPdf);
    if (!items.length) { App.toast('Select PDF file(s) first', 'warning'); return; }
    const single = items.length === 1;
    const o = _load('pdfcmp', { preset: 'ebook' });
    const body = `
      <p class="text-muted cvt-sub">Compress ${single ? `<strong>${_esc(_name(items[0]))}</strong>` : `<strong>${items.length}</strong> PDFs`}.
        Originals are kept; output is <em>name_compressed.pdf</em>.</p>
      <div class="cvt-presets" id="pc-presets">
        ${PDF_PRESETS.map(([k, t, d]) => `
          <label class="cvt-preset"><input type="radio" name="pc-preset" value="${k}" ${k === o.preset ? 'checked' : ''}>
            <span><strong>${t}</strong><small>${d}</small></span></label>`).join('')}
      </div>
      ${single ? _field('Output file', `<input id="pc-out" class="input-text cvt-input" value="${_esc(_stem(items[0]) + '_compressed.pdf')}">`) : ''}
      ${_progressHtml('pc-prog')}
      <div class="cvt-results hidden" id="pc-results"></div>`;
    const overlay = _openModal('pdfcmp', single ? 'Compress PDF' : `Compress ${items.length} PDFs`, body, [
      { label: 'Close', onClick: () => _close(overlay) },
      { label: 'Compress', primary: true, onClick: run },
    ]);
    overlay.querySelector('.modal')?.classList.add('cvt-modal');

    let running = false;
    async function run() {
      if (running) return;
      const preset = overlay.querySelector('input[name="pc-preset"]:checked')?.value || 'ebook';
      _save('pdfcmp', { preset });
      let outName = single ? ($('pc-out')?.value || '').trim() : '';
      if (outName && !/\.pdf$/i.test(outName)) outName += '.pdf';
      if (/[\\/:*?"<>|]/.test(outName)) { App.toast('The file name contains characters Windows does not allow', 'warning'); return; }
      running = true;
      _busy(overlay, ['Compress', 'Close'], true);
      App.setStatus('Compressing PDF…', true);
      const resBox = $('pc-results');
      resBox.innerHTML = ''; resBox.classList.remove('hidden');
      const results = [];
      for (let i = 0; i < items.length; i++) {
        const p = items[i];
        _progress('pc-prog', i, items.length, `Compressing ${i + 1} of ${items.length}: ${_name(p)}…`);
        const out = outName ? p.replace(/[\\/][^\\/]+$/, '') + '\\' + outName : '';
        let r;
        try { r = await SFM.compressPdfQuality(p, preset, out); } catch (e) { r = { ok: false, error: String(e) }; }
        results.push(r);
        resBox.insertAdjacentHTML('beforeend', _resultRow(_name(p), r));
      }
      running = false;
      App.setStatus('Ready');
      _busy(overlay, ['Compress', 'Close'], false);
      const s = _summary(results);
      _progress('pc-prog', items.length, items.length,
        s.good.length ? `Done — ${s.good.length} compressed: ${_fmtBytes(s.before)} → ${_fmtBytes(s.after)} (${_pct(s.before, s.after)})` : 'Done');
      if (s.good.length) {
        const last = s.good[s.good.length - 1];
        App.toast(single
          ? `Compressed → <strong>${_esc(_name(last.out))}</strong>: ${_fmtBytes(last.before)} → ${_fmtBytes(last.after)} (${_pct(last.before, last.after)})`
          : `Compressed ${s.good.length} PDF(s): ${_fmtBytes(s.before)} → ${_fmtBytes(s.after)} (${_pct(s.before, s.after)})`, 'success', 6000);
        _reveal(last.out);
      }
      if (s.kept) App.toast(s.kept === 1 && single
        ? 'This PDF is already well compressed — the original was kept, no new file.'
        : `${s.kept} PDF(s) were already well compressed — originals kept`, 'info', 6000);
      if (s.failed) App.toast(`${s.failed} PDF(s) failed — see the dialog`, 'error', 6000);
    }
  }

  // ── 5. Compress image(s) ──────────────────────────────────────────────────
  function openCompressImages(paths) {
    const items = (Array.isArray(paths) ? paths : [paths]).filter(isImage);
    if (!items.length) { App.toast('Select image(s) first', 'warning'); return; }
    const o = _load('imgcmp', { quality: 70, edge: 1600, fmt: '', target: 0 });
    const label = items.length === 1 ? `<strong>${_esc(_name(items[0]))}</strong>` : `<strong>${items.length}</strong> images`;
    const body = `
      <p class="text-muted cvt-sub">Compress ${label}. Originals are kept; output is <em>name_compressed.ext</em>.</p>
      ${_field('Target file size', _select('ic-target', [[0, 'No target — use quality below'], [100, 'Under 100 KB'], [200, 'Under 200 KB'], [500, 'Under 500 KB'], [1024, 'Under 1 MB'], [2048, 'Under 2 MB'], ['custom', 'Custom…']], [0, 100, 200, 500, 1024, 2048].includes(o.target) ? o.target : 'custom'))}
      <div class="cvt-field ${[0, 100, 200, 500, 1024, 2048].includes(o.target) ? 'hidden' : ''}" id="ic-custom-row">
        <label class="detail-label">Custom target (KB)</label>
        <input id="ic-custom" type="number" min="10" step="10" class="input-text cvt-input" value="${o.target || 300}">
      </div>
      <div class="cvt-field" id="ic-q-row">
        <label class="detail-label">Quality: <span id="ic-q-val">${o.quality}</span></label>
        <input id="ic-q" type="range" min="10" max="95" step="1" value="${o.quality}" class="cvt-range">
      </div>
      <div class="cvt-grid">
        ${_field('Max size (longest edge)', _select('ic-edge', [[0, 'Original'], [3000, '3000 px'], [2000, '2000 px'], [1600, '1600 px'], [1200, '1200 px'], [1024, '1024 px'], [800, '800 px']], o.edge))}
        ${_field('Output format', _select('ic-fmt', [['', 'Same as original'], ['jpg', 'JPG'], ['webp', 'WEBP'], ['png', 'PNG']], o.fmt))}
      </div>
      <p class="text-muted cvt-note" id="ic-note"></p>
      ${_progressHtml('ic-prog')}
      <div class="cvt-results hidden" id="ic-results"></div>`;
    const overlay = _openModal('imgcmp', items.length === 1 ? '🗜 Compress Image' : `🗜 Compress ${items.length} Images`, body, [
      { label: 'Close', onClick: () => _close(overlay) },
      { label: 'Compress', primary: true, onClick: run },
    ]);
    overlay.querySelector('.modal')?.classList.add('cvt-modal');

    const targetKb = () => {
      const v = $('ic-target')?.value;
      if (v === 'custom') return Math.max(10, parseInt($('ic-custom')?.value, 10) || 0);
      return parseInt(v, 10) || 0;
    };
    const sync = () => {
      const t = $('ic-target')?.value;
      $('ic-custom-row')?.classList.toggle('hidden', t !== 'custom');
      const hasTarget = t !== '0';
      $('ic-q-row')?.classList.toggle('cvt-dim', hasTarget);
      const q = $('ic-q'); if (q) q.disabled = hasTarget;
      const note = $('ic-note');
      if (note) note.textContent = hasTarget
        ? 'Finds the best quality that fits under the target (shrinks the image only if needed). PNG output is saved as JPG.'
        : '';
    };
    $('ic-target')?.addEventListener('change', sync);
    $('ic-q')?.addEventListener('input', () => { const v = $('ic-q-val'); if (v) v.textContent = $('ic-q').value; });
    sync();

    let running = false;
    async function run() {
      if (running) return;
      const q = parseInt($('ic-q')?.value, 10) || 70;
      const edge = parseInt($('ic-edge')?.value, 10) || 0;
      const fmt = $('ic-fmt')?.value || '';
      const tkb = targetKb();
      _save('imgcmp', { quality: q, edge, fmt, target: tkb });
      running = true;
      _busy(overlay, ['Compress', 'Close'], true);
      App.setStatus('Compressing image(s)…', true);
      const resBox = $('ic-results');
      resBox.innerHTML = ''; resBox.classList.remove('hidden');
      const results = [];
      for (let i = 0; i < items.length; i++) {
        _progress('ic-prog', i, items.length, `Compressing ${i + 1} of ${items.length}: ${_name(items[i])}…`);
        let r;
        try { r = await SFM.compressImage(items[i], q, edge, fmt, '', tkb); } catch (e) { r = { ok: false, error: String(e) }; }
        results.push(r);
        resBox.insertAdjacentHTML('beforeend', _resultRow(_name(items[i]), r));
      }
      running = false;
      App.setStatus('Ready');
      _busy(overlay, ['Compress', 'Close'], false);
      const s = _summary(results);
      const line = s.good.length ? `${_fmtBytes(s.before)} → ${_fmtBytes(s.after)} (${_pct(s.before, s.after)})` : '';
      _progress('ic-prog', items.length, items.length,
        s.good.length ? `Done — ${s.good.length} of ${items.length} compressed: ${line}` : 'Done');
      if (s.good.length) {
        const last = s.good[s.good.length - 1];
        App.toast(items.length === 1
          ? `Compressed → <strong>${_esc(_name(last.out))}</strong>: ${line}`
          : `Compressed ${s.good.length} image(s): ${line}`, 'success', 6000);
        if (results.some(r => r.ok && r.target_met === false)) App.toast('Some images could not reach the target size — the smallest version was saved', 'warning', 6000);
        _reveal(last.out);
      }
      if (s.kept) App.toast(`${s.kept} image(s) already well compressed — originals kept`, 'info', 5000);
      if (s.failed) App.toast(`${s.failed} image(s) failed — see the dialog`, 'error', 6000);
    }
  }

  return {
    isImage, isPdf, IMAGE_EXTS,
    openImagesToPdf, quickImageToPdf, openPdfToImages, openConvertImage,
    openCompressPdf, openCompressImages,
  };
})();
