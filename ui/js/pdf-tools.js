/**
 * pdf-tools.js — PDF split / merge / arrange / extract UI.
 *
 *   PdfTools.openMerge(paths, folder)        reorderable file list → one PDF
 *   PdfTools.openSplit(path)                 every page / every N / ranges / extract
 *   PdfTools.openExtract(path, {page})       page ranges → new PDF
 *   PdfTools.extractPage(path, page)         one page (0-based) → new PDF
 *   PdfTools.openArrange(paths, {mode})      visual page-grid editor
 *   PdfTools.onDetails(entry|null, multi)    details-pane quick actions
 *
 * Backend: pdf_tools_bridge.py (SFM.pdf* wrappers in bridge.js). Outputs are
 * never overwritten (the backend picks a free name); only Arrange's explicit
 * "Overwrite original" replaces a file, after backing it up to _to_review/.
 */

const PdfTools = (() => {

  // ── small helpers ─────────────────────────────────────────────────────────
  const IMG_RE  = /\.(jpe?g|png|bmp|gif|tiff?|webp)$/i;
  const isPdf   = p => /\.pdf$/i.test(p || '');
  const isImg   = p => IMG_RE.test(p || '');
  const baseOf  = p => String(p || '').split(/[\\/]/).pop();
  const dirOf   = p => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const stemOf  = p => baseOf(p).replace(/\.[^.]+$/, '');
  const sepOf   = d => (String(d).includes('/') && !String(d).includes('\\')) ? '/' : '\\';
  const joinP   = (d, n) => String(d).replace(/[\\/]+$/, '') + sepOf(d) + n;
  const cleanName = n => String(n || '').trim().replace(/\.pdf$/i, '').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim();
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem('pdftools.' + key);
      localStorage.setItem('pdftools.' + key, String(val));
    } catch (_) { return null; }
  }

  let _jobSeq = 0;
  function newJob() { return 'pdfjob' + (++_jobSeq) + '_' + Date.now(); }

  // Progress bar wired to pdf_tools_progress events for one job.
  function trackProgress(wrapEl, jobId) {
    if (!wrapEl) return () => {};
    wrapEl.classList.add('on');
    const bar = wrapEl.querySelector('.progress-bar');
    const txt = wrapEl.querySelector('.pt-hint');
    if (bar) { bar.classList.add('indeterminate'); bar.style.width = '0%'; }
    if (txt) txt.textContent = 'Working…';
    const off = SFM.on('pdf_tools_progress', p => {
      if (!p || p.job !== jobId) return;
      if (bar && p.total) {
        bar.classList.remove('indeterminate');
        bar.style.width = Math.round(100 * p.done / p.total) + '%';
      }
      if (txt) txt.textContent = (p.msg || '') + (p.total ? `  (${p.done}/${p.total})` : '');
    });
    return () => { off(); wrapEl.classList.remove('on'); };
  }
  const progressHtml = () => `
    <div class="pt-progress progress-block"><div class="progress-wrap"><div class="progress-bar" style="width:0%"></div></div>
    <div class="pt-hint progress-label"></div></div>`;
  const ic = (name, size = 16, cls = '') => Icons.svg(name, size, cls);
  const setBtn = (b, label, icon) => Dialogs.setBtn(b, label, icon);

  // Shared success/failure reporting.
  function finish(r, okMsg, selectPath) {
    App.setStatus('Ready');
    if (!r || !r.ok) {
      App.toast(esc((r && r.error) || 'Operation failed'), 'error', 7000);
      return false;
    }
    App.toast(okMsg, 'success', 5000);
    if (selectPath && FileTree.refreshAndSelect) FileTree.refreshAndSelect(selectPath);
    else FileTree.refresh();
    return true;
  }

  // ── tiny modal (own stack; Escape closes the top one) ─────────────────────
  const _mstack = [];
  function modal({ title, icon, subtitle, body, footer, cls }) {
    const ov = document.createElement('div');
    ov.className = 'modal-overlay pt-overlay';
    ov.style.zIndex = 9500 + _mstack.length * 20;
    ov.innerHTML = `
      <div class="modal pt-modal ${cls || ''}" role="dialog">
        ${Dialogs.header(title, { icon, subtitle })}
        <div class="modal-body">${body}</div>
        ${footer ? `<div class="modal-footer">${footer}</div>` : ''}
      </div>`;
    document.body.appendChild(ov);
    const m = {
      el: ov,
      $: sel => ov.querySelector(sel),
      $$: sel => Array.from(ov.querySelectorAll(sel)),
      busy: false,
      close() {
        if (m.busy) return;
        const i = _mstack.indexOf(m);
        if (i >= 0) _mstack.splice(i, 1);
        ov.remove();
      },
    };
    ov.querySelector('.modal-close').addEventListener('click', () => m.close());
    ov.addEventListener('mousedown', e => { if (e.target === ov) m.close(); });
    _mstack.push(m);
    return m;
  }
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !_mstack.length) return;
    // A Dialogs.confirm/ask opened on top of our editor handles its own Escape.
    if (document.querySelector('.modal-overlay.dlg-top')) return;
    e.stopPropagation(); e.preventDefault();
    _mstack[_mstack.length - 1].close();
  }, true);

  // Disable the footer while a job runs; the primary button shows a spinner.
  function footBusy(m, on) {
    m.busy = on;
    m.$$('.modal-footer .btn').forEach(b => {
      b.disabled = on;
      if (b.classList.contains('btn-primary')) b.classList.toggle('is-loading', on);
    });
  }

  const checkMark = () => `<span class="option-card-check">${ic('check', 10)}</span>`;

  async function browseFolderInto(input) {
    const r = await SFM.call('browse_for_folder');
    if (r && r.ok && r.path) input.value = r.path;
  }

  // =========================================================================
  // MERGE
  // =========================================================================
  async function openMerge(paths, folder) {
    let items = [];                      // {path, count, error, isImg}
    const m = modal({
      title: 'Merge into one PDF',
      icon: 'merge',
      subtitle: 'Combine PDFs and images in the order below',
      cls: 'pt-wide',
      body: `
        <div class="field">
          <div class="section-head">
            <span class="field-label">Files in merge order <span class="text-muted">· drag to reorder</span></span>
            <span class="row">
              <span class="pill pill-neutral" id="pt-m-total"></span>
              <button class="btn btn-sm btn-ghost" id="pt-m-sort" title="Sort by file name">${ic('chevrons-up-down', 14)}Sort A–Z</button>
              <button class="btn btn-sm" id="pt-m-add">${ic('plus', 14)}Add files…</button>
            </span>
          </div>
          <div class="reorder-list pt-list" id="pt-m-list"></div>
        </div>
        <div class="field-grid">
          <div class="field">
            <label class="field-label" for="pt-m-name">Output file name</label>
            <div class="field-row"><input class="input-text" id="pt-m-name"><span class="field-suffix">.pdf</span></div>
          </div>
          <div class="field">
            <label class="field-label" for="pt-m-dir">Save in folder</label>
            <div class="field-row"><input class="input-text" id="pt-m-dir"><button class="btn" id="pt-m-browse">Browse…</button></div>
          </div>
        </div>
        <div class="col">
          <label class="switch"><input type="checkbox" id="pt-m-bm"><span class="switch-track"></span>Add a bookmark for each file</label>
          <label class="switch"><input type="checkbox" id="pt-m-move"><span class="switch-track"></span>Afterwards, move the source files to <code>_to_review/</code></label>
        </div>
        <div class="field-hint">Images become one page each. An existing file is never overwritten — a number is added to the new name.</div>
        ${progressHtml()}`,
      footer: `
        <button class="btn btn-ghost footer-left" id="pt-m-arrange" title="Open the page editor with all these files">${ic('layers')}Arrange pages…</button>
        <button class="btn" id="pt-m-cancel">Cancel</button>
        <button class="btn btn-primary" id="pt-m-go">${ic('merge')}Merge</button>`,
    });
    const list = m.$('#pt-m-list'), nameInp = m.$('#pt-m-name'), dirInp = m.$('#pt-m-dir');
    m.$('#pt-m-bm').checked   = store('mergeBookmarks') !== '0';
    m.$('#pt-m-move').checked = store('mergeMoveSources') === '1';
    dirInp.value = folder || (paths && paths[0] ? dirOf(paths[0]) : '');
    let nameTouched = false;
    nameInp.addEventListener('input', () => { nameTouched = true; });

    function syncName() {
      if (!nameTouched) nameInp.value = items.length ? stemOf(items[0].path) + '_merged' : '';
      if (!dirInp.value && items.length) dirInp.value = dirOf(items[0].path);
    }

    async function addPaths(ps) {
      const fresh = (ps || []).filter(p => (isPdf(p) || isImg(p)) &&
        !items.some(it => it.path.toLowerCase() === p.toLowerCase()));
      const skipped = (ps || []).length - fresh.length;
      if (!fresh.length) { if (skipped) App.toast('Only new PDF or image files can be added', 'warning'); return; }
      list.innerHTML = '<div class="pt-empty progress-label"><span class="spinner"></span>Reading files…</div>';
      const r = await SFM.pdfInfos(fresh);
      const infos = (r && r.items) || [];
      fresh.forEach((p, i) => {
        const inf = infos[i] || {};
        items.push({ path: p, count: inf.ok ? inf.page_count : 0, error: inf.ok ? '' : (inf.error || 'Unreadable'), isImg: isImg(p) });
      });
      syncName(); render();
    }

    function render() {
      if (!items.length) {
        list.innerHTML = `<div class="empty-state empty-state-sm pt-empty"><div class="empty-state-icon">${ic('files', 22)}</div>
          <div class="empty-state-title">No files yet</div><div class="empty-state-text">Add PDFs or images to merge them into one PDF.</div></div>`;
      } else {
        list.innerHTML = items.map((it, i) => `
          <div class="pt-item reorder-row" draggable="true" data-i="${i}">
            <span class="reorder-grip" title="Drag to reorder">${ic('grip-vertical', 14)}</span>
            <span class="reorder-num">${i + 1}</span>
            <span class="reorder-thumb">${Icons.file({ name: it.path }, 18)}</span>
            <div class="reorder-name"><span title="${esc(it.path)}">${esc(baseOf(it.path))}</span><small>${esc(dirOf(it.path))}</small></div>
            ${it.error ? `<span class="pill pill-red pt-pages-bad" title="${esc(it.error)}">${ic('alert-triangle', 12)}${esc(it.error)}</span>`
                       : `<span class="pill pill-neutral">${it.isImg ? 'Image · 1 page' : it.count + (it.count === 1 ? ' page' : ' pages')}</span>`}
            <span class="reorder-actions">
              <button class="icon-btn icon-btn-sm" data-act="up"   title="Move up" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>${ic('arrow-up', 14)}</button>
              <button class="icon-btn icon-btn-sm" data-act="down" title="Move down" aria-label="Move down" ${i === items.length - 1 ? 'disabled' : ''}>${ic('arrow-down', 14)}</button>
              <button class="icon-btn icon-btn-sm icon-btn-danger" data-act="rm" title="Remove from list" aria-label="Remove from list">${ic('x', 14)}</button>
            </span>
          </div>`).join('');
      }
      const pages = items.reduce((a, it) => a + (it.count || 0), 0);
      const bad = items.filter(it => it.error).length;
      m.$('#pt-m-total').textContent = items.length
        ? `${items.length} file${items.length === 1 ? '' : 's'} · ${pages} page${pages === 1 ? '' : 's'}` + (bad ? ` · ${bad} unreadable` : '') : '';
      m.$('#pt-m-total').classList.toggle('hidden', !items.length);
      setBtn(m.$('#pt-m-go'), pages ? `Merge ${pages} page${pages === 1 ? '' : 's'}` : 'Merge', 'merge');
      m.$('#pt-m-go').disabled = items.length < 1 || !!bad;
      m.$('#pt-m-arrange').disabled = items.length < 1 || !!bad;
    }

    list.addEventListener('click', e => {
      const b = e.target.closest('button[data-act]');
      if (!b) return;
      const i = +b.closest('.pt-item').dataset.i;
      if (b.dataset.act === 'up' && i > 0) [items[i - 1], items[i]] = [items[i], items[i - 1]];
      else if (b.dataset.act === 'down' && i < items.length - 1) [items[i + 1], items[i]] = [items[i], items[i + 1]];
      else if (b.dataset.act === 'rm') items.splice(i, 1);
      syncName(); render();
    });

    // HTML5 drag & drop reorder
    let dragI = null;
    list.addEventListener('dragstart', e => {
      const row = e.target.closest('.pt-item'); if (!row) return;
      dragI = +row.dataset.i; row.classList.add('pt-dragging');
      try { e.dataTransfer.setData('text/plain', String(dragI)); e.dataTransfer.effectAllowed = 'move'; } catch (_) {}
    });
    list.addEventListener('dragover', e => {
      if (dragI === null) return;
      e.preventDefault();
      list.querySelectorAll('.pt-drop-before,.pt-drop-after').forEach(x => x.classList.remove('pt-drop-before', 'pt-drop-after'));
      const row = e.target.closest('.pt-item'); if (!row) return;
      const r = row.getBoundingClientRect();
      row.classList.add(e.clientY < r.top + r.height / 2 ? 'pt-drop-before' : 'pt-drop-after');
    });
    list.addEventListener('drop', e => {
      e.preventDefault();
      const row = e.target.closest('.pt-item');
      if (dragI === null || !row) return;
      const r = row.getBoundingClientRect();
      let to = +row.dataset.i + (e.clientY < r.top + r.height / 2 ? 0 : 1);
      const [it] = items.splice(dragI, 1);
      if (to > dragI) to--;
      items.splice(to, 0, it);
      dragI = null; syncName(); render();
    });
    list.addEventListener('dragend', () => { dragI = null; render(); });

    m.$('#pt-m-add').addEventListener('click', async () => {
      const r = await SFM.browseForPdfsOrImages();
      if (r && r.ok && r.paths && r.paths.length) addPaths(r.paths);
    });
    m.$('#pt-m-sort').addEventListener('click', () => {
      items.sort((a, b) => baseOf(a.path).localeCompare(baseOf(b.path), undefined, { numeric: true, sensitivity: 'base' }));
      syncName(); render();
    });
    m.$('#pt-m-browse').addEventListener('click', () => browseFolderInto(dirInp));
    m.$('#pt-m-cancel').addEventListener('click', () => m.close());
    m.$('#pt-m-arrange').addEventListener('click', () => {
      const ps = items.map(it => it.path);
      m.close();
      openArrange(ps, { mode: 'merge', name: cleanName(nameInp.value), folder: dirInp.value.trim() });
    });

    async function go() {
      if (m.busy) return;
      const name = cleanName(nameInp.value);
      const dir  = dirInp.value.trim();
      if (!items.length) { App.toast('Add at least one file', 'warning'); return; }
      if (!name) { App.toast('Enter an output file name', 'warning'); nameInp.focus(); return; }
      const de = await SFM.pathExists(dir);
      if (!dir || !de.ok || !de.is_dir) { App.toast('Choose an existing output folder', 'warning'); return; }
      const bookmarks = m.$('#pt-m-bm').checked, moveSrc = m.$('#pt-m-move').checked;
      store('mergeBookmarks', bookmarks ? '1' : '0');
      store('mergeMoveSources', moveSrc ? '1' : '0');
      const job = newJob();
      const stop = trackProgress(m.$('.pt-progress'), job);
      footBusy(m, true);
      App.setStatus('Merging PDFs…', true);
      const r = await SFM.pdfMerge(items.map(it => it.path), joinP(dir, name + '.pdf'), bookmarks, job)
        .catch(e => ({ ok: false, error: String(e) }));
      stop(); footBusy(m, false);
      if (!r.ok) { finish(r); render(); return; }
      let extra = '';
      if (moveSrc) {
        const srcs = items.map(it => it.path).filter(p => p.toLowerCase() !== String(r.out_path).toLowerCase());
        const mv = await SFM.softDelete(srcs);
        const n = ((mv && mv.results) || []).filter(x => x.ok).length;
        extra = n ? ` · ${n} source file(s) moved to _to_review/` : '';
        if (!mv || !mv.ok) App.toast('Some source files could not be moved: ' + esc(mv && mv.error), 'warning', 6000);
      }
      m.close();
      finish(r, `Merged ${r.page_count} page(s) → <b>${esc(baseOf(r.out_path))}</b>${extra}`, r.out_path);
    }
    m.$('#pt-m-go').addEventListener('click', go);
    nameInp.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });

    render();
    if (paths && paths.length) await addPaths(paths);
    else { const r = await SFM.browseForPdfsOrImages(); if (r && r.ok && r.paths) addPaths(r.paths); }
  }

  // =========================================================================
  // SPLIT
  // =========================================================================
  async function openSplit(path) {
    if (!isPdf(path)) { App.toast('Select a PDF to split', 'warning'); return; }
    const info = await SFM.pdfInfo(path);
    if (!info.ok) { App.toast(esc(info.error), 'error', 7000); return; }
    const n = info.page_count, stem = stemOf(path);
    const mode0 = store('splitMode') || 'each';
    const m = modal({
      title: 'Split PDF',
      icon: 'split',
      subtitle: `${baseOf(path)} · ${n} page${n === 1 ? '' : 's'}`,
      body: `
        <div class="field">
          <span class="field-label">How to split</span>
          <div class="option-cards pt-modes">
            <label class="option-card pt-mode" data-mode="each"><input type="radio" name="pt-s-mode" value="each">
              <span class="option-card-icon">${ic('files')}</span>
              <span class="option-card-body"><span class="option-card-title">Every page</span>
                <span class="option-card-desc">One file per page (${n} files).</span></span>${checkMark()}</label>
            <label class="option-card pt-mode" data-mode="every"><input type="radio" name="pt-s-mode" value="every">
              <span class="option-card-icon">${ic('layers')}</span>
              <span class="option-card-body"><span class="option-card-title">Every N pages</span>
                <span class="option-card-row"><input class="input-text pt-num-input" id="pt-s-every" type="number" min="1" max="${n}" value="${esc(store('splitEvery') || 2)}"><span class="option-card-desc">pages per file</span></span></span>${checkMark()}</label>
            <label class="option-card pt-mode" data-mode="ranges"><input type="radio" name="pt-s-mode" value="ranges">
              <span class="option-card-icon">${ic('scissors')}</span>
              <span class="option-card-body"><span class="option-card-title">By page ranges</span>
                <span class="option-card-row"><input class="input-text" id="pt-s-ranges" placeholder="e.g. 1-3, 4-6, 7-end"></span></span>${checkMark()}</label>
            <label class="option-card pt-mode" data-mode="extract"><input type="radio" name="pt-s-mode" value="extract">
              <span class="option-card-icon">${ic('extract')}</span>
              <span class="option-card-body"><span class="option-card-title">Selected pages, one file</span>
                <span class="option-card-row"><input class="input-text" id="pt-s-pick" placeholder="e.g. 1, 3, 5-6"></span></span>${checkMark()}</label>
          </div>
        </div>
        <div class="field">
          <span class="field-label">Result</span>
          <div class="pt-result"><span class="pt-result-icon">${ic('files')}</span><div class="pt-preview" id="pt-s-prev"></div></div>
          <div class="pt-err" id="pt-s-err"></div>
        </div>
        <div class="field">
          <label class="field-label" for="pt-s-dir">Output folder</label>
          <div class="field-row"><input class="input-text" id="pt-s-dir" value="${esc(joinP(dirOf(path), stem + '_split'))}"><button class="btn" id="pt-s-browse">Browse…</button></div>
          <div class="field-hint">The folder is created if needed. Existing files are never overwritten.</div>
        </div>
        ${progressHtml()}`,
      footer: `<button class="btn" id="pt-s-cancel">Cancel</button><button class="btn btn-primary" id="pt-s-go">${ic('split')}Split</button>`,
    });
    const modeOf = () => (m.$('input[name="pt-s-mode"]:checked') || {}).value || 'each';
    const specOf = mode => mode === 'ranges' ? m.$('#pt-s-ranges').value : mode === 'extract' ? m.$('#pt-s-pick').value : '';
    let valid = false, seq = 0;

    function label(g) {
      const runs = []; let i = 0;
      while (i < g.length) { let j = i; while (j + 1 < g.length && g[j + 1] === g[j] + 1) j++; runs.push(i === j ? '' + g[i] : g[i] + '-' + g[j]); i = j + 1; }
      return runs.join('_');
    }
    async function preview() {
      const mode = modeOf(), my = ++seq;
      m.$$('.pt-mode').forEach(el => el.classList.toggle('on', el.dataset.mode === mode));
      const spec = specOf(mode);
      if ((mode === 'ranges' || mode === 'extract') && !spec.trim()) {
        valid = false; m.$('#pt-s-prev').textContent = ''; m.$('#pt-s-err').textContent = 'Type the pages to use.';
        m.$('#pt-s-go').disabled = true; return;
      }
      const r = await SFM.pdfParseRanges(spec, n, mode, +m.$('#pt-s-every').value || 0);
      if (my !== seq) return;
      valid = !!r.ok;
      m.$('#pt-s-go').disabled = !valid;
      m.$('#pt-s-err').textContent = r.ok ? '' : r.error;
      if (!r.ok) { m.$('#pt-s-prev').textContent = ''; return; }
      const names = r.groups.map(g => `${stem}_p${label(g)}.pdf`);
      const shown = names.slice(0, 4).join(', ') + (names.length > 4 ? `, … (${names.length - 4} more)` : '');
      m.$('#pt-s-prev').innerHTML = `<strong>${r.count} file${r.count === 1 ? '' : 's'}</strong><span class="text-muted">${esc(shown)}</span>`;
      setBtn(m.$('#pt-s-go'), r.count === 1 ? 'Create 1 file' : `Create ${r.count} files`, 'split');
    }
    let t = null;
    const later = () => { clearTimeout(t); t = setTimeout(preview, 180); };
    m.$$('input[name="pt-s-mode"]').forEach(rb => { rb.checked = rb.value === mode0; rb.addEventListener('change', preview); });
    ['#pt-s-every', '#pt-s-ranges', '#pt-s-pick'].forEach(sel => {
      const el = m.$(sel);
      el.addEventListener('input', later);
      el.addEventListener('focus', () => {
        const rb = el.closest('.pt-mode').querySelector('input[type=radio]');
        if (!rb.checked) { rb.checked = true; preview(); }
      });
    });
    m.$('#pt-s-browse').addEventListener('click', () => browseFolderInto(m.$('#pt-s-dir')));
    m.$('#pt-s-cancel').addEventListener('click', () => m.close());

    async function go() {
      if (m.busy || !valid) return;
      const mode = modeOf(), every = +m.$('#pt-s-every').value || 1;
      store('splitMode', mode); if (mode === 'every') store('splitEvery', every);
      const job = newJob(), stop = trackProgress(m.$('.pt-progress'), job);
      footBusy(m, true);
      App.setStatus('Splitting PDF…', true);
      const r = await SFM.pdfSplit(path, mode, every, specOf(mode), m.$('#pt-s-dir').value.trim(), job)
        .catch(e => ({ ok: false, error: String(e) }));
      stop(); footBusy(m, false);
      if (!r.ok) { finish(r); return; }
      m.close();
      finish(r, `Split into ${r.count} file(s) → <b>${esc(baseOf(r.out_dir))}</b>`, r.out_dir);
    }
    m.$('#pt-s-go').addEventListener('click', go);
    m.el.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') go(); });
    preview();
  }

  // =========================================================================
  // EXTRACT
  // =========================================================================
  async function openExtract(path, opts) {
    if (!isPdf(path)) { App.toast('Select a PDF first', 'warning'); return; }
    const info = await SFM.pdfInfo(path);
    if (!info.ok) { App.toast(esc(info.error), 'error', 7000); return; }
    const n = info.page_count;
    const start = (opts && Number.isInteger(opts.page)) ? String(opts.page + 1) : '';
    const m = modal({
      title: 'Extract pages',
      icon: 'extract',
      subtitle: `${baseOf(path)} · ${n} page${n === 1 ? '' : 's'}`,
      cls: 'modal-sm pt-narrow',
      body: `
        <div class="field">
          <label class="field-label" for="pt-e-spec">Pages to extract, in this order</label>
          <input class="input-text" id="pt-e-spec" value="${esc(start)}" placeholder="e.g. 1-3, 5, 8-end">
          <div class="pt-err" id="pt-e-err"></div>
        </div>
        <div class="field">
          <label class="field-label" for="pt-e-name">New file name</label>
          <div class="field-row"><input class="input-text" id="pt-e-name" placeholder="${esc(stemOf(path))}_p… (automatic)"><span class="field-suffix">.pdf</span></div>
          <div class="field-hint">Saved next to the original, which is not changed. Existing files are never overwritten.</div>
        </div>
        ${progressHtml()}`,
      footer: `<button class="btn" id="pt-e-cancel">Cancel</button><button class="btn btn-primary" id="pt-e-go">${ic('extract')}Extract</button>`,
    });
    const specInp = m.$('#pt-e-spec');
    let valid = false, seq = 0;
    async function check() {
      const my = ++seq, spec = specInp.value;
      if (!spec.trim()) { valid = false; m.$('#pt-e-err').textContent = ''; m.$('#pt-e-go').disabled = true; return; }
      const r = await SFM.pdfParseRanges(spec, n, 'extract');
      if (my !== seq) return;
      valid = !!r.ok;
      m.$('#pt-e-go').disabled = !valid;
      m.$('#pt-e-err').className = r.ok ? 'pt-ok' : 'pt-err';
      m.$('#pt-e-err').innerHTML = ic(r.ok ? 'check-circle' : 'alert-circle', 14) + esc(r.ok ? `${r.groups[0].length} page${r.groups[0].length === 1 ? '' : 's'}: ${r.groups[0].slice(0, 30).join(', ')}${r.groups[0].length > 30 ? ', …' : ''}` : r.error);
    }
    let t = null;
    specInp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(check, 180); });
    m.$('#pt-e-cancel').addEventListener('click', () => m.close());
    async function go() {
      if (m.busy) return;
      await check();
      if (!valid) return;
      const nm = cleanName(m.$('#pt-e-name').value);
      const job = newJob(), stop = trackProgress(m.$('.pt-progress'), job);
      footBusy(m, true);
      App.setStatus('Extracting pages…', true);
      const r = await SFM.pdfExtract(path, specInp.value, nm ? joinP(dirOf(path), nm + '.pdf') : '', job)
        .catch(e => ({ ok: false, error: String(e) }));
      stop(); footBusy(m, false);
      if (!r.ok) { finish(r); return; }
      m.close();
      finish(r, `Extracted ${r.page_count} page(s) → <b>${esc(baseOf(r.out_path))}</b>`, r.out_path);
    }
    m.$('#pt-e-go').addEventListener('click', go);
    m.el.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') go(); });
    specInp.focus(); specInp.select();
    check();
  }

  async function extractPage(path, page) {
    if (!isPdf(path) || !Number.isInteger(page) || page < 0) { openExtract(path); return; }
    App.setStatus('Extracting page…', true);
    const r = await SFM.pdfExtract(path, String(page + 1), '').catch(e => ({ ok: false, error: String(e) }));
    finish(r, r.ok ? `Extracted page ${page + 1} → <b>${esc(baseOf(r.out_path))}</b>` : '', r.out_path);
  }

  // Page currently shown in the preview for `path` (or null).
  function previewPageFor(path) {
    try {
      if (window.Preview && Preview.getCurrentPath && Preview.getCurrentPath() &&
          Preview.getCurrentPath().toLowerCase() === String(path).toLowerCase()) return Preview.getCurrentPage();
    } catch (_) {}
    return null;
  }

  // =========================================================================
  // ARRANGE — visual page-grid editor
  // =========================================================================
  async function openArrange(pathsIn, opts) {
    const paths = (Array.isArray(pathsIn) ? pathsIn : [pathsIn]).filter(Boolean);
    if (!paths.length) return;
    const mode = (opts && opts.mode) || (paths.length > 1 ? 'merge' : 'arrange');
    const primary = paths[0];
    const canOverwrite = mode === 'arrange' && isPdf(primary);

    App.setStatus('Reading pages…', true);
    const ir = await SFM.pdfInfos(paths);
    App.setStatus('Ready');
    const infos = (ir && ir.items) || [];
    const bad = infos.filter(i => !i.ok);
    if (bad.length) { App.toast(esc(bad[0].error), 'error', 7000); return; }

    let uid = 0;
    let slots = [];
    infos.forEach(inf => { for (let p = 0; p < inf.page_count; p++) slots.push({ id: ++uid, path: inf.path, page: p, rotate: 0 }); });
    const multiSource = () => new Set(slots.map(s => s.path.toLowerCase())).size > 1;
    const initialSig = sig();
    let sel = new Set(), anchor = null, focusId = slots[0] ? slots[0].id : null;
    let undoStack = [], redoStack = [];
    let tileW = +(store('arrangeTileW') || 150);
    let busy = false;

    function sig() { return JSON.stringify(slots.map(s => [s.path, s.page, s.rotate])); }
    const dirty = () => sig() !== initialSig;
    function snapshot() {
      undoStack.push(slots.map(s => ({ ...s })));
      if (undoStack.length > 200) undoStack.shift();
      redoStack = [];
    }
    function undo() { if (!undoStack.length) return; redoStack.push(slots.map(s => ({ ...s }))); slots = undoStack.pop(); pruneSel(); render(); }
    function redo() { if (!redoStack.length) return; undoStack.push(slots.map(s => ({ ...s }))); slots = redoStack.pop(); pruneSel(); render(); }
    function pruneSel() {
      const ids = new Set(slots.map(s => s.id));
      sel = new Set([...sel].filter(id => ids.has(id)));
      if (!ids.has(focusId)) focusId = slots[0] ? slots[0].id : null;
    }
    const idx = id => slots.findIndex(s => s.id === id);
    const selIdx = () => slots.map((s, i) => sel.has(s.id) ? i : -1).filter(i => i >= 0);
    function targets() {                 // selected pages, or the focused one
      const s = selIdx();
      if (s.length) return s;
      const f = idx(focusId);
      return f >= 0 ? [f] : [];
    }

    // ── DOM ─────────────────────────────────────────────────────────────────
    const ov = document.createElement('div');
    ov.className = 'modal-overlay pt-overlay';
    ov.style.zIndex = 9500 + _mstack.length * 20;
    const defName = (opts && opts.name) || stemOf(primary) + (mode === 'merge' ? '_merged' : '_arranged');
    const defDir  = (opts && opts.folder) || dirOf(primary);
    ov.innerHTML = `
      <div class="pt-org" role="dialog">
        <div class="pt-org-head">
          <span class="modal-head-icon">${ic(mode === 'merge' ? 'merge' : 'layers', 18)}</span>
          <div class="modal-heading">
            <h2 class="modal-title">${mode === 'merge' ? 'Merge & arrange pages' : 'Arrange pages'}</h2>
            <div class="modal-subtitle" title="${esc(paths.join('\n'))}">${esc(baseOf(primary))}${paths.length > 1 ? ` + ${paths.length - 1} more` : ''}</div>
          </div>
          <span class="pill pill-neutral" id="pt-a-status"></span>
          <button class="modal-close" id="pt-a-x" aria-label="Close" title="Close (Esc)">${ic('x')}</button>
        </div>
        <div class="pt-org-bar" role="toolbar">
          <div class="pt-tb-group">
            <button class="icon-btn" data-a="undo" data-tooltip="Undo · Ctrl+Z" aria-label="Undo">${ic('undo')}</button>
            <button class="icon-btn" data-a="redo" data-tooltip="Redo · Ctrl+Y" aria-label="Redo">${ic('redo')}</button>
          </div>
          <span class="toolbar-sep"></span>
          <div class="pt-tb-group">
            <button class="icon-btn" data-a="rotl" data-tooltip="Rotate left · L" aria-label="Rotate left">${ic('rotate-ccw')}</button>
            <button class="icon-btn" data-a="rotr" data-tooltip="Rotate right · R" aria-label="Rotate right">${ic('rotate-cw')}</button>
            <button class="icon-btn" data-a="dup"  data-tooltip="Duplicate · Ctrl+D" aria-label="Duplicate">${ic('copy')}</button>
            <button class="icon-btn icon-btn-danger" data-a="del" data-tooltip="Delete · Del" aria-label="Delete pages">${ic('trash')}</button>
          </div>
          <span class="toolbar-sep"></span>
          <div class="pt-tb-group">
            <button class="btn btn-sm btn-ghost" data-a="insert" title="Insert all pages of other PDFs or images after the selection">${ic('plus', 14)}Insert pages…</button>
            <button class="btn btn-sm btn-ghost" data-a="extract" title="Save the selected pages as a new PDF">${ic('extract', 14)}Extract selected</button>
            <button class="btn btn-sm btn-ghost" data-a="all" title="Select all (Ctrl+A)">${ic('list-checks', 14)}<span class="pt-all-label">Select all</span></button>
          </div>
          <span class="pt-spacer"></span>
          <span class="pt-tb-hint">Click, Ctrl+click or Shift+click to select · drag to move</span>
          <div class="pt-tb-group">
            <button class="icon-btn" data-a="zout" data-tooltip="Smaller thumbnails" aria-label="Smaller thumbnails">${ic('zoom-out')}</button>
            <button class="icon-btn" data-a="zin"  data-tooltip="Larger thumbnails" aria-label="Larger thumbnails">${ic('zoom-in')}</button>
          </div>
        </div>
        <div class="pt-grid" id="pt-a-grid" tabindex="0"><div class="pt-caret" id="pt-a-caret"></div></div>
        <div class="pt-org-foot">
          ${canOverwrite ? `
          <div class="segmented pt-out-seg" role="radiogroup" aria-label="Save mode">
            <label class="seg-btn"><input type="radio" name="pt-a-out" value="new" checked>${ic('file-pdf', 14)}Save as new file</label>
            <label class="seg-btn" title="The current version is copied to _to_review/ first"><input type="radio" name="pt-a-out" value="over">${ic('refresh', 14)}Overwrite original</label>
          </div>` : `<span class="field-label">Save as</span>`}
          <div class="pt-foot-fields">
            <div class="field-row"><input class="input-text" id="pt-a-name" value="${esc(defName)}" aria-label="File name"><span class="field-suffix">.pdf in</span></div>
            <div class="field-row pt-foot-dir"><input class="input-text" id="pt-a-dir" value="${esc(defDir)}" aria-label="Folder"><button class="btn" id="pt-a-browse">Browse…</button></div>
          </div>
          ${progressHtml()}
          <span class="pt-spacer"></span>
          <button class="btn" id="pt-a-cancel">Close</button>
          <button class="btn btn-primary" id="pt-a-save">${ic('check')}Save</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    const $ = s => ov.querySelector(s);
    const grid = $('#pt-a-grid'), caret = $('#pt-a-caret');
    const self = {
      busy: false,
      close() {                          // Escape (via the shared modal stack)
        if (drag) { cancelDrag(); return; }
        tryClose();
      },
    };
    _mstack.push(self);

    // ── thumbnails: lazy (IntersectionObserver) + batched + cached ──────────
    const cache = new Map();             // "path|page|w" -> data_url
    const queue = new Map();             // path -> Set(page)
    let pumping = false;
    const thumbWidth = () => (tileW <= 150 ? 160 : tileW <= 260 ? 280 : 440);
    const io = new IntersectionObserver(ents => {
      ents.forEach(en => {
        if (!en.isIntersecting) return;
        io.unobserve(en.target);
        const s = slots.find(x => x.id === +en.target.dataset.id);
        if (s) want(s.path, s.page);
      });
    }, { root: grid, rootMargin: '400px' });
    function want(path, page) {
      const key = `${path}|${page}|${thumbWidth()}`;
      if (cache.has(key)) { paint(path, page); return; }
      if (!queue.has(path)) queue.set(path, new Set());
      queue.get(path).add(page);
      pump();
    }
    async function pump() {
      if (pumping) return;
      pumping = true;
      try {
        while (queue.size && document.body.contains(ov)) {
          const [path, set] = queue.entries().next().value;
          const pages = [...set].slice(0, 8);
          pages.forEach(p => set.delete(p));
          if (!set.size) queue.delete(path);
          const w = thumbWidth();
          const r = await SFM.pdfThumbs(path, pages, w).catch(() => null);
          pages.forEach(p => {
            const du = r && r.ok && r.thumbs ? r.thumbs[String(p)] : null;
            cache.set(`${path}|${p}|${w}`, du || '');
            paint(path, p);
          });
        }
      } finally { pumping = false; }
    }
    function paint(path, page) {
      const w = thumbWidth(), du = cache.get(`${path}|${page}|${w}`);
      if (du === undefined) return;
      slots.forEach(s => {
        if (s.path !== path || s.page !== page) return;
        const img = grid.querySelector(`.pt-tile[data-id="${s.id}"] img`);
        if (!img) return;
        const ph = img.parentNode.querySelector('.pt-ph');
        if (du) { if (img.getAttribute('src') !== du) img.src = du; img.style.visibility = 'visible'; if (ph) ph.remove(); }
        else if (ph) ph.textContent = 'No preview';
      });
    }

    // ── render ──────────────────────────────────────────────────────────────
    function render() {
      const w = tileW, h = Math.round(w * 1.3), multi = multiSource();
      io.disconnect();
      const frag = document.createDocumentFragment(), lazy = [];
      slots.forEach((s, i) => {
        const t = document.createElement('div');
        t.className = 'pt-tile' + (sel.has(s.id) ? ' sel' : '') + (s.id === focusId ? ' focus' : '');
        t.dataset.id = s.id;
        t.style.width = w + 'px';
        const odd = s.rotate === 90 || s.rotate === 270;
        const tf = `rotate(${s.rotate}deg)` + (odd ? ` scale(${(w / h).toFixed(3)})` : '');
        const du = cache.get(`${s.path}|${s.page}|${thumbWidth()}`);
        t.innerHTML = `
          <div class="pt-thumb" style="width:${w}px;height:${h}px">
            <img draggable="false" alt="" style="transform:${tf};visibility:${du ? 'visible' : 'hidden'}" ${du ? `src="${du}"` : ''}>
            ${du ? '' : `<span class="pt-ph">${du === '' ? 'No preview' : '<span class="spinner"></span>'}</span>`}
            <span class="pt-num-badge">${i + 1}</span>
            <span class="pt-check-badge">${ic('check', 12)}</span>
            ${s.rotate ? `<span class="pt-rot-badge" title="Rotated ${s.rotate}°">${ic('rotate-cw', 11)}${s.rotate}°</span>` : ''}
            <div class="pt-tile-tools">
              <button data-t="rotl" title="Rotate left" aria-label="Rotate left">${ic('rotate-ccw', 14)}</button>
              <button data-t="rotr" title="Rotate right" aria-label="Rotate right">${ic('rotate-cw', 14)}</button>
              <button data-t="dup" title="Duplicate" aria-label="Duplicate">${ic('copy', 14)}</button>
              <button data-t="del" class="del" title="Delete" aria-label="Delete">${ic('trash', 14)}</button>
            </div>
          </div>
          <div class="pt-cap" title="${esc(baseOf(s.path))} — page ${s.page + 1}">${multi ? esc(stemOf(s.path).slice(0, 18)) + ' · p' + (s.page + 1) : (s.page !== i ? `Page ${i + 1} <span class="text-muted">· was ${s.page + 1}</span>` : `Page ${i + 1}`)}</div>`;
        frag.appendChild(t);
        if (du === undefined) lazy.push(t);
      });
      grid.querySelectorAll('.pt-tile').forEach(x => x.remove());
      grid.appendChild(frag);
      lazy.forEach(t => io.observe(t));
      status();
    }
    function status() {
      const n = sel.size;
      $('#pt-a-status').textContent = `${slots.length} page${slots.length === 1 ? '' : 's'}` + (n ? ` · ${n} selected` : '') + (dirty() ? ' · unsaved changes' : '');
      $('#pt-a-status').className = 'pill ' + (dirty() ? 'pill-yellow pill-dot' : 'pill-neutral');
      ov.querySelector('[data-a="undo"]').disabled = !undoStack.length;
      ov.querySelector('[data-a="redo"]').disabled = !redoStack.length;
      ov.querySelector('[data-a="extract"]').disabled = !n;
      ov.querySelector('[data-a="all"] .pt-all-label').textContent = (n && n === slots.length) ? 'Select none' : 'Select all';
      ['rotl', 'rotr', 'dup', 'del'].forEach(a => { ov.querySelector(`[data-a="${a}"]`).disabled = !targets().length; });
      $('#pt-a-save').disabled = !slots.length;
    }

    // ── edit operations ─────────────────────────────────────────────────────
    function rotate(ix, deg) { if (!ix.length) return; snapshot(); ix.forEach(i => { slots[i].rotate = (slots[i].rotate + deg + 360) % 360; }); render(); }
    function remove(ix) {
      if (!ix.length) return;
      if (ix.length >= slots.length) { App.toast('A PDF needs at least one page', 'warning'); return; }
      snapshot();
      const after = slots.find((s, i) => i > Math.max(...ix) && !ix.includes(i));
      const drop = new Set(ix.map(i => slots[i].id));
      slots = slots.filter(s => !drop.has(s.id));
      sel.clear(); focusId = after ? after.id : (slots[slots.length - 1] || {}).id;
      render();
    }
    function duplicate(ix) {
      if (!ix.length) return;
      snapshot();
      const copies = ix.map(i => ({ ...slots[i], id: ++uid }));
      slots.splice(Math.max(...ix) + 1, 0, ...copies);
      sel = new Set(copies.map(c => c.id)); focusId = copies[0].id;
      render();
    }
    function moveTo(ids, insertAt) {       // insertAt: index in the CURRENT array
      const set = new Set(ids);
      const moving = slots.filter(s => set.has(s.id));
      const before = slots.slice(0, insertAt).filter(s => !set.has(s.id)).length;
      const rest = slots.filter(s => !set.has(s.id));
      const next = rest.slice(0, before).concat(moving, rest.slice(before));
      if (next.every((s, i) => s.id === slots[i].id)) return;
      snapshot(); slots = next; render();
    }
    async function insertPages() {
      const r = await SFM.browseForPdfsOrImages();
      if (!r || !r.ok || !r.paths || !r.paths.length) return;
      const inf = await SFM.pdfInfos(r.paths);
      const items = ((inf && inf.items) || []);
      const fails = items.filter(i => !i.ok);
      fails.forEach(f => App.toast(esc(f.error), 'error', 7000));
      const add = [];
      items.filter(i => i.ok).forEach(i => { for (let p = 0; p < i.page_count; p++) add.push({ id: ++uid, path: i.path, page: p, rotate: 0 }); });
      if (!add.length) return;
      snapshot();
      const ix = selIdx(), f = idx(focusId);
      const at = ix.length ? Math.max(...ix) + 1 : (f >= 0 && sel.size ? f + 1 : slots.length);
      slots.splice(at, 0, ...add);
      sel = new Set(add.map(a => a.id)); focusId = add[0].id;
      render();
      grid.querySelector(`.pt-tile[data-id="${focusId}"]`)?.scrollIntoView({ block: 'nearest' });
      App.toast(`Inserted ${add.length} page(s)`, 'success');
    }

    function outPath() {
      const nm = cleanName($('#pt-a-name').value), dir = $('#pt-a-dir').value.trim();
      return nm && dir ? joinP(dir, nm + '.pdf') : '';
    }
    const overwriteChosen = () => canOverwrite && (ov.querySelector('input[name="pt-a-out"]:checked') || {}).value === 'over';
    const payload = ix => ix.map(i => ({ path: slots[i].path, page: slots[i].page, rotate: slots[i].rotate }));

    async function runBuild(pages, out, replace, label) {
      if (busy) return null;
      busy = true; self.busy = true;
      const job = newJob(), stop = trackProgress(ov.querySelector('.pt-progress'), job);
      const ctl = '.pt-org-foot .btn, .pt-org-bar .btn, .pt-org-bar .icon-btn';
      ov.querySelectorAll(ctl).forEach(b => b.disabled = true);
      $('#pt-a-save').classList.add('is-loading');
      App.setStatus(label, true);
      const r = await SFM.pdfBuild(pages, out, replace, job).catch(e => ({ ok: false, error: String(e) }));
      stop(); busy = false; self.busy = false;
      ov.querySelectorAll(ctl).forEach(b => b.disabled = false);
      $('#pt-a-save').classList.remove('is-loading');
      status();
      return r;
    }

    async function extractSelected() {
      const ix = selIdx();
      if (!ix.length) { App.toast('Select pages first (click, Ctrl+click, Shift+click)', 'warning'); return; }
      const dir = $('#pt-a-dir').value.trim() || dirOf(primary);
      const r = await runBuild(payload(ix), joinP(dir, stemOf(primary) + '_extract.pdf'), false, 'Extracting pages…');
      if (r) finish(r, r.ok ? `Extracted ${r.page_count} page(s) → <b>${esc(baseOf(r.out_path))}</b>` : '', r.out_path);
    }

    async function save() {
      if (busy || !slots.length) return;
      const all = payload(slots.map((_, i) => i));
      if (overwriteChosen()) {
        if (!(await Dialogs.confirm({ title: 'Overwrite original', icon: 'alert-triangle', tone: 'warning',
              okLabel: 'Overwrite', message: `Overwrite “${baseOf(primary)}” with the arranged pages?`,
              detail: 'The current version will be kept as a backup copy in “_to_review” next to it.' }))) return;
        const r = await runBuild(all, primary, true, 'Saving PDF…');
        if (!r) return;
        if (!r.ok) { finish(r); return; }
        forceClose();
        finish(r, `Saved <b>${esc(baseOf(primary))}</b> · backup in _to_review/`, primary);
        try { if (Preview.getCurrentPath && (Preview.getCurrentPath() || '').toLowerCase() === primary.toLowerCase()) Preview.previewFile(primary, '.pdf'); } catch (_) {}
        return;
      }
      const out = outPath();
      if (!out) { App.toast('Enter a file name and folder', 'warning'); return; }
      const de = await SFM.pathExists($('#pt-a-dir').value.trim());
      if (!de.ok || !de.is_dir) { App.toast('Choose an existing output folder', 'warning'); return; }
      const r = await runBuild(all, out, false, 'Saving PDF…');
      if (!r) return;
      if (!r.ok) { finish(r); return; }
      forceClose();
      finish(r, `Saved ${r.page_count} page(s) → <b>${esc(baseOf(r.out_path))}</b>`, r.out_path);
    }

    function forceClose() {
      io.disconnect();
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousemove', onMove, true);
      document.removeEventListener('mouseup', onUp, true);
      const i = _mstack.indexOf(self); if (i >= 0) _mstack.splice(i, 1);
      ov.remove();
    }
    async function tryClose() {
      if (busy) return;
      if (dirty() && !(await Dialogs.confirm({ title: 'Unsaved changes', icon: 'alert-triangle', tone: 'warning', okLabel: 'Discard changes', message: 'Discard your page changes?' }))) return;
      forceClose();
    }

    // ── toolbar ─────────────────────────────────────────────────────────────
    ov.querySelector('.pt-org-bar').addEventListener('click', e => {
      const b = e.target.closest('[data-a]'); if (!b || b.disabled) return;
      const a = b.dataset.a;
      if (a === 'rotl') rotate(targets(), -90);
      else if (a === 'rotr') rotate(targets(), 90);
      else if (a === 'dup') duplicate(targets());
      else if (a === 'del') remove(targets());
      else if (a === 'insert') insertPages();
      else if (a === 'extract') extractSelected();
      else if (a === 'undo') undo();
      else if (a === 'redo') redo();
      else if (a === 'all') { sel = (sel.size === slots.length) ? new Set() : new Set(slots.map(s => s.id)); render(); }
      else if (a === 'zin' || a === 'zout') setTile(tileW * (a === 'zin' ? 1.2 : 1 / 1.2));
      grid.focus();
    });
    function setTile(w) {
      tileW = Math.round(Math.max(90, Math.min(420, w)));
      store('arrangeTileW', tileW);
      render();
    }
    grid.addEventListener('wheel', e => {
      if (!e.ctrlKey) return;
      e.preventDefault(); setTile(tileW * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
    }, { passive: false });
    $('#pt-a-browse').addEventListener('click', () => browseFolderInto($('#pt-a-dir')));
    $('#pt-a-cancel').addEventListener('click', tryClose);
    $('#pt-a-x').addEventListener('click', tryClose);
    $('#pt-a-save').addEventListener('click', save);
    ov.querySelectorAll('input[name="pt-a-out"]').forEach(rb => rb.addEventListener('change', () => {
      const over = overwriteChosen();
      ['#pt-a-name', '#pt-a-dir', '#pt-a-browse'].forEach(s => { $(s).disabled = over; });
      ov.querySelector('.pt-foot-fields')?.classList.toggle('is-dim', over);
      setBtn($('#pt-a-save'), over ? 'Overwrite original…' : 'Save', over ? 'refresh' : 'check');
    }));

    // ── selection (click / ctrl / shift) + per-tile tools ───────────────────
    let suppressClick = false;
    grid.addEventListener('click', e => {
      if (suppressClick) { suppressClick = false; return; }
      const tool = e.target.closest('[data-t]');
      const tile = e.target.closest('.pt-tile');
      if (!tile) { if (!e.ctrlKey && !e.shiftKey && sel.size) { sel.clear(); render(); } return; }
      const id = +tile.dataset.id, i = idx(id);
      if (tool) {
        const ix = sel.has(id) ? selIdx() : [i];
        const t = tool.dataset.t;
        if (t === 'rotl') rotate(ix, -90); else if (t === 'rotr') rotate(ix, 90);
        else if (t === 'dup') duplicate(ix); else if (t === 'del') remove(ix);
        return;
      }
      if (e.shiftKey && anchor !== null && idx(anchor) >= 0) {
        const [a, b] = [Math.min(idx(anchor), i), Math.max(idx(anchor), i)];
        if (!e.ctrlKey) sel.clear();
        for (let k = a; k <= b; k++) sel.add(slots[k].id);
      } else if (e.ctrlKey || e.metaKey) {
        sel.has(id) ? sel.delete(id) : sel.add(id); anchor = id;
      } else {
        sel = new Set([id]); anchor = id;
      }
      focusId = id;
      grid.focus();
      render();
    });

    // ── drag & drop reorder (pointer based; moves the whole selection) ──────
    let drag = null;
    grid.addEventListener('mousedown', e => {
      if (e.button !== 0 || e.target.closest('[data-t]')) return;
      const tile = e.target.closest('.pt-tile'); if (!tile) return;
      drag = { id: +tile.dataset.id, sx: e.clientX, sy: e.clientY, on: false, at: null };
      e.preventDefault();
    });
    function dropIndex(x, y) {
      const tiles = Array.from(grid.querySelectorAll('.pt-tile'));
      if (!tiles.length) return { at: 0 };
      let best = null;
      for (const t of tiles) {
        const r = t.getBoundingClientRect();
        if (y < r.top - 10 || y > r.bottom + 10) continue;
        const d = Math.abs(x - (r.left + r.width / 2));
        if (!best || d < best.d) best = { t, r, d };
      }
      if (!best) {                         // above / below all rows
        const first = tiles[0].getBoundingClientRect();
        return y < first.top ? { at: 0, t: tiles[0], before: true } : { at: slots.length, t: tiles[tiles.length - 1], before: false };
      }
      const before = x < best.r.left + best.r.width / 2;
      const i = idx(+best.t.dataset.id);
      return { at: before ? i : i + 1, t: best.t, before };
    }
    let scrollTimer = null;
    function cancelDrag() {
      clearInterval(scrollTimer);
      drag = null; caret.style.display = 'none';
      grid.querySelectorAll('.pt-dragging').forEach(t => t.classList.remove('pt-dragging'));
    }
    function onMove(e) {
      if (!drag) return;
      if (!drag.on) {
        if (Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 6) return;
        drag.on = true;
        if (!sel.has(drag.id)) { sel = new Set([drag.id]); anchor = drag.id; render(); }
        drag.ids = slots.filter(s => sel.has(s.id)).map(s => s.id);
        drag.ids.forEach(id => grid.querySelector(`.pt-tile[data-id="${id}"]`)?.classList.add('pt-dragging'));
      }
      const d = dropIndex(e.clientX, e.clientY);
      drag.at = d.at;
      if (d.t) {
        const r = d.t.getBoundingClientRect(), g = grid.getBoundingClientRect();
        caret.style.display = 'block';
        caret.style.height = r.height + 'px';
        caret.style.top  = (r.top - g.top + grid.scrollTop) + 'px';
        caret.style.left = ((d.before ? r.left - 11 : r.right + 7) - g.left + grid.scrollLeft) + 'px';
      }
      // auto-scroll near the edges
      const g = grid.getBoundingClientRect();
      clearInterval(scrollTimer);
      const edge = e.clientY < g.top + 40 ? -1 : e.clientY > g.bottom - 40 ? 1 : 0;
      if (edge) scrollTimer = setInterval(() => { grid.scrollTop += edge * 18; }, 30);
    }
    function onUp() {
      clearInterval(scrollTimer);
      if (!drag) return;
      const d = drag; drag = null;
      caret.style.display = 'none';
      if (!d.on) return;
      suppressClick = true;
      setTimeout(() => { suppressClick = false; }, 0);
      grid.querySelectorAll('.pt-dragging').forEach(t => t.classList.remove('pt-dragging'));
      if (d.at !== null) moveTo(d.ids, d.at);
    }
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('mouseup', onUp, true);

    // ── keyboard ────────────────────────────────────────────────────────────
    function cols() {
      const tiles = grid.querySelectorAll('.pt-tile');
      if (tiles.length < 2) return 1;
      const top = tiles[0].getBoundingClientRect().top;
      let c = 0; for (const t of tiles) { if (Math.abs(t.getBoundingClientRect().top - top) > 4) break; c++; }
      return Math.max(1, c);
    }
    function onKey(e) {
      if (!document.body.contains(ov) || _mstack[_mstack.length - 1] !== self) return;
      const inInput = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
      const ctrl = e.ctrlKey || e.metaKey, k = e.key;
      if (ctrl && (k === 's' || k === 'S')) { e.preventDefault(); e.stopPropagation(); save(); return; }
      if (inInput) { if (k === 'Enter') { e.preventDefault(); save(); } return; }
      const handled = () => { e.preventDefault(); e.stopPropagation(); };
      if (ctrl && (k === 'z' || k === 'Z') && !e.shiftKey) { handled(); undo(); }
      else if (ctrl && (k === 'y' || k === 'Y' || ((k === 'z' || k === 'Z') && e.shiftKey))) { handled(); redo(); }
      else if (ctrl && (k === 'a' || k === 'A')) { handled(); sel = new Set(slots.map(s => s.id)); render(); }
      else if (ctrl && (k === 'd' || k === 'D')) { handled(); duplicate(targets()); }
      else if (k === 'Delete' || k === 'Backspace') { handled(); remove(targets()); }
      else if (k === 'r' || k === 'R' || k === ']') { handled(); rotate(targets(), 90); }
      else if (k === 'l' || k === 'L' || k === '[') { handled(); rotate(targets(), -90); }
      else if (/^Arrow/.test(k)) {
        handled();
        const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols(), ArrowDown: cols() }[k];
        const f = Math.max(0, idx(focusId));
        if (ctrl) {                        // move selected pages
          const ix = targets(); if (!ix.length) return;
          const ids = ix.map(i => slots[i].id);
          const at = step < 0 ? Math.max(0, Math.min(...ix) + step) : Math.min(slots.length, Math.max(...ix) + step + 1);
          if (!sel.size) sel = new Set(ids);
          moveTo(ids, at);
        } else {
          const n = Math.max(0, Math.min(slots.length - 1, f + step));
          focusId = slots[n].id;
          if (e.shiftKey) { if (anchor === null) anchor = slots[f].id; const [a, b] = [Math.min(idx(anchor), n), Math.max(idx(anchor), n)]; sel = new Set(slots.slice(a, b + 1).map(s => s.id)); }
          else { sel = new Set([focusId]); anchor = focusId; }
          render();
        }
        grid.querySelector(`.pt-tile[data-id="${focusId}"]`)?.scrollIntoView({ block: 'nearest' });
      }
    }
    document.addEventListener('keydown', onKey, true);

    render();
    grid.focus();
  }

  // =========================================================================
  // Details-pane quick actions
  // =========================================================================
  function onDetails(entry, multi) {
    const sec = document.getElementById('pdf-tools-section');
    if (!sec) return;
    const show = (id, on) => { const b = document.getElementById(id); if (b) b.classList.toggle('hidden', !on); };
    if (entry) {
      const pdf = !entry.is_dir && isPdf(entry.path);
      sec.classList.toggle('hidden', !pdf);
      ['qa-pdf-arrange', 'qa-pdf-split', 'qa-pdf-extract', 'qa-pdf-merge'].forEach(id => show(id, pdf));
      show('qa-pdf-merge-sel', false);
      sec.dataset.path = pdf ? entry.path : '';
      return;
    }
    const paths = (multi || []).map(x => typeof x === 'string' ? x : (x && x.path)).filter(p => isPdf(p) || isImg(p));
    const ok = paths.length >= 2 && paths.some(isPdf);
    sec.classList.toggle('hidden', !ok);
    ['qa-pdf-arrange', 'qa-pdf-split', 'qa-pdf-extract', 'qa-pdf-merge'].forEach(id => show(id, false));
    show('qa-pdf-merge-sel', ok);
    const mb = document.getElementById('qa-pdf-merge-sel');
    if (mb) mb.lastChild.textContent = ` Merge ${paths.length} selected…`;
    sec.dataset.paths = JSON.stringify(paths);
  }

  function _initDetails() {
    const sec = document.getElementById('pdf-tools-section');
    if (!sec) return;
    const cur = () => sec.dataset.path || '';
    const wire = (id, fn) => { const b = document.getElementById(id); if (b) b.addEventListener('click', fn); };
    wire('qa-pdf-arrange', () => cur() && openArrange([cur()]));
    wire('qa-pdf-split',   () => cur() && openSplit(cur()));
    wire('qa-pdf-extract', () => cur() && openExtract(cur(), { page: previewPageFor(cur()) }));
    wire('qa-pdf-merge',   () => cur() && openMerge([cur()], FileTree.getCurrentFolder()));
    wire('qa-pdf-merge-sel', () => {
      let ps = []; try { ps = JSON.parse(sec.dataset.paths || '[]'); } catch (_) {}
      if (ps.length) openMerge(ps, FileTree.getCurrentFolder());
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _initDetails);
  else _initDetails();

  return {
    openMerge, openSplit, openExtract, extractPage, openArrange,
    onDetails, previewPageFor,
  };
})();
