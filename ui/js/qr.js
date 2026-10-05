/**
 * qr.js — QR code checking (files, batches and the screen).
 *
 *   QrScan.scanSelection()   toolbar "QR": scan selected images/PDFs, else screen
 *   QrScan.scanPaths(paths)  one results dialog for one or many files
 *   QrScan.openScreen()      screenshot overlay: click a code or drag a box
 *
 * Backend: sfm_bridge scan_qr_files / decode_qr_at_point / decode_qr_in_region
 * / qr_open_url (qr_scan.py). Links are opened ONLY when the user clicks
 * "Open link" (with a confirmation when the link looks unusual).
 */
const QrScan = (() => {
  const EXTS = ['.jpg', '.jpeg', '.jfif', '.png', '.bmp', '.webp', '.gif', '.tif', '.tiff', '.pdf'];
  const TYPE_ICON = { url: '🔗', email: '✉️', phone: '📞', sms: '💬', wifi: '📶', vcard: '👤',
                      contact: '👤', geo: '📍', event: '📅', text: '📝', other: '⚠️' };

  function _esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  const _ext = p => { const m = /\.[^.\\/]+$/.exec(p || ''); return m ? m[0].toLowerCase() : ''; };
  const _base = p => String(p || '').split(/[\\/]/).pop();
  function isScannable(p) { return EXTS.includes(_ext(p)); }

  async function _copy(text) {
    try { await navigator.clipboard.writeText(text); }
    catch (_) { try { await SFM.setClipboard(text); } catch (e) { App.toast('Copy failed', 'error'); return; } }
    App.toast('Copied to clipboard', 'success', 1800);
  }

  // ── Open link (only on click; confirm when flagged) ───────────────────────
  async function _doOpen(url) {
    const r = await SFM.qrOpenUrl(url).catch(e => ({ ok: false, error: String(e) }));
    if (!r || !r.ok) App.toast('Cannot open link: ' + ((r && r.error) || 'unknown error'), 'error');
  }

  function openLink(res) {
    const url = res.url || res.text;
    const warns = res.warnings || [];
    if (!warns.length) { _doOpen(url); return; }
    const id = Dialogs.modal('qr-open-confirm', {
      title: '⚠️ Check this link before opening',
      width: '520px',
      body: `<div style="font-size:12.5px;color:var(--text-secondary)">This QR code points to
               <b style="color:var(--text-primary)">${_esc(res.host || '')}</b>:</div>
             <div class="qr-confirm-url">${_esc(url)}</div>
             <div class="qr-flags">${warns.map(w => `<div class="qr-flag warn">⚠ ${_esc(w)}</div>`).join('')}</div>
             <div style="font-size:11.5px;color:var(--text-muted);margin-top:10px">
               Genuine verification links normally use https and the issuing authority's own domain.</div>`,
      footer: `<button class="btn" id="qoc-cancel-__ID__">Cancel</button>
               <button class="btn btn-danger" id="qoc-open-__ID__">Open anyway</button>`,
    });
    _fixIds(id);
    document.getElementById('qoc-cancel-' + id).addEventListener('click', () => Dialogs.closeModal(id));
    document.getElementById('qoc-open-' + id).addEventListener('click', () => { Dialogs.closeModal(id); _doOpen(url); });
  }

  // _modal() picks the id; buttons in body/footer are written with __ID__ and patched here.
  function _fixIds(id) {
    const root = document.getElementById('mo-' + id);
    if (!root) return;
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
  }

  // ── One decoded code → card ──────────────────────────────────────────────
  function renderItem(res, showPage) {
    const el = document.createElement('div');
    el.className = 'qr-item';
    const thumb = res.thumb
      ? `<img class="qr-thumb" src="${res.thumb}" alt="QR code">`
      : `<div class="qr-thumb empty">${TYPE_ICON[res.type] || '▦'}</div>`;
    const isUrl = res.type === 'url';
    const warns = res.warnings || [], notes = res.notes || [];
    let head = '';
    if (isUrl && res.host) {
      const dom = res.domain || res.host;
      const i = res.host.lastIndexOf(dom);
      const hostHtml = i >= 0
        ? _esc(res.host.slice(0, i)) + `<span class="dom">${_esc(dom)}</span>` + _esc(res.host.slice(i + dom.length))
        : _esc(res.host);
      head = `<div class="qr-host" title="Website the link points to">${res.scheme === 'https' ? '🔒' : '🔓'} ${hostHtml}</div>`;
    }
    const pageBadge = (showPage && res.page) ? `<span class="badge badge-blue">Page ${res.page}</span>` : '';
    const sym = res.symbology && res.symbology !== 'QRCODE' ? `<span>${_esc(res.symbology)}</span>` : '';
    el.innerHTML = `${thumb}
      <div style="min-width:0">
        <div class="qr-meta">
          <span class="badge ${isUrl ? (warns.length ? 'badge-yellow' : 'badge-green') : 'badge-blue'}">${TYPE_ICON[res.type] || ''} ${_esc(res.label || res.type)}</span>
          ${pageBadge}${sym}
        </div>
        ${head}
        <div class="qr-text">${_esc(isUrl ? (res.url || res.text) : res.text)}</div>
        ${(warns.length || notes.length) ? `<div class="qr-flags">
          ${warns.map(w => `<div class="qr-flag ${res.type === 'other' ? 'bad' : 'warn'}">⚠ ${_esc(w)}</div>`).join('')}
          ${notes.map(n => `<div class="qr-flag note">✓ ${_esc(n)}</div>`).join('')}</div>` : ''}
        <div class="qr-actions">
          <button class="btn btn-sm" data-act="copy">📋 Copy</button>
          ${isUrl && res.openable ? `<button class="btn btn-sm btn-primary" data-act="open">🌐 Open link</button>` : ''}
          ${isUrl ? `<button class="btn btn-sm" data-act="copyurl">Copy link</button>` : ''}
        </div>
      </div>`;
    el.querySelector('[data-act="copy"]').addEventListener('click', () => _copy(res.text));
    el.querySelector('[data-act="open"]')?.addEventListener('click', () => openLink(res));
    el.querySelector('[data-act="copyurl"]')?.addEventListener('click', () => _copy(res.url || res.text));
    return el;
  }

  // ── Files → results dialog ───────────────────────────────────────────────
  function scanSelection() {
    const st = App.state;
    let paths = (st.selectedPaths || []).filter(isScannable);
    if (!paths.length && st.focusedPath && isScannable(st.focusedPath)) paths = [st.focusedPath];
    if (paths.length) scanPaths(paths);
    else openScreen();
  }

  async function scanPaths(paths) {
    paths = (paths || []).filter(isScannable);
    if (!paths.length) { App.toast('Select an image or PDF to scan for QR codes', 'warning'); return; }
    const batch = paths.length > 1;
    const jobId = 'qr' + Date.now();
    const id = Dialogs.modal('qr-results', {
      title: batch ? `📷 QR check — ${paths.length} files` : `📷 QR check — ${_base(paths[0])}`,
      width: '680px',
      body: `<div class="qr-summary" id="qrr-sum-__ID__"><span class="mt-spinner"></span>
               <span id="qrr-sumtxt-__ID__">Scanning…</span></div>
             <div class="qr-progress"><div id="qrr-bar-__ID__"></div></div>
             <div class="qr-list" id="qrr-list-__ID__"></div>`,
      footer: `<button class="btn" id="qrr-copyall-__ID__" disabled>Copy all results</button>
               <button class="btn" id="qrr-cancel-__ID__">Stop</button>
               <button class="btn btn-primary" id="qrr-close-__ID__">Close</button>`,
    });
    _fixIds(id);
    const $ = s => document.getElementById(s + '-' + id);
    const list = $('qrr-list'), sumTxt = $('qrr-sumtxt'), bar = $('qrr-bar'), sum = $('qrr-sum');
    const cancelBtn = $('qrr-cancel'), copyAll = $('qrr-copyall');
    $('qrr-close').addEventListener('click', () => Dialogs.closeModal(id));
    const files = [];
    let finished = false;

    function addFile(f) {
      files.push(f);
      if (batch) {
        const h = document.createElement('div');
        h.className = 'qr-file-head';
        const n = (f.results || []).length;
        h.innerHTML = `📄 ${_esc(f.name)} <span class="muted">— ${f.ok ? (n ? n + ' code' + (n > 1 ? 's' : '') : 'no QR') : 'error'}</span>`;
        list.appendChild(h);
      }
      if (!f.ok) {
        const d = document.createElement('div');
        d.className = 'qr-none err';
        d.textContent = '❌ ' + (f.error || 'Could not scan this file');
        list.appendChild(d);
      } else if (!(f.results || []).length) {
        const d = document.createElement('div');
        d.className = 'qr-none';
        const pages = f.kind === 'pdf' ? ` (${f.pages_scanned} page${f.pages_scanned === 1 ? '' : 's'} checked)` : '';
        d.textContent = `No QR code found${pages}. If the code is small or blurred, try a sharper scan, or use "Scan from screen" and drag a box around it.`;
        list.appendChild(d);
      } else {
        const multiPage = f.kind === 'pdf' || (f.page_count || 0) > 1;
        f.results.forEach(r => list.appendChild(renderItem(r, multiPage)));
      }
      if (f.truncated) {
        const d = document.createElement('div');
        d.className = 'qr-none';
        d.textContent = `Only the first ${f.pages_scanned} of ${f.page_count} pages were checked.`;
        list.appendChild(d);
      }
    }

    function summary(done, cancelled) {
      const codes = files.reduce((a, f) => a + (f.results || []).length, 0);
      const none = files.filter(f => f.ok && !(f.results || []).length).length;
      const errs = files.filter(f => !f.ok).length;
      const warn = files.reduce((a, f) => a + (f.results || []).filter(r => (r.warnings || []).length).length, 0);
      let t = `${codes} QR code${codes === 1 ? '' : 's'} found`;
      if (batch) t += ` in ${files.length}/${paths.length} files`;
      if (none) t += ` · ${none} without QR`;
      if (errs) t += ` · ${errs} error${errs === 1 ? '' : 's'}`;
      if (warn) t += ` · ${warn} link${warn === 1 ? '' : 's'} to double-check`;
      if (cancelled) t += ' · stopped';
      sumTxt.textContent = done ? t : `Scanning ${Math.min(files.length + 1, paths.length)} of ${paths.length}… ${t}`;
      bar.style.width = Math.round(files.length * 100 / paths.length) + '%';
      if (done) { sum.querySelector('.mt-spinner')?.remove(); cancelBtn.style.display = 'none'; copyAll.disabled = !codes; }
    }

    copyAll.addEventListener('click', () => {
      const rows = [['File', 'Page', 'Type', 'Content', 'Warnings'].join('\t')];
      files.forEach(f => (f.results || []).forEach(r => rows.push(
        [f.name, r.page || '', r.label || r.type, String(r.text).replace(/\s+/g, ' '), (r.warnings || []).join('; ')].join('\t'))));
      _copy(rows.join('\n'));
    });

    const offP = SFM.on('qr_scan_progress', p => {
      if (!p || p.job_id !== jobId) return;
      if (!document.getElementById('mo-' + id)) return;
      addFile(p.file); summary(false);
    });
    const offD = SFM.on('qr_scan_done', p => {
      if (!p || p.job_id !== jobId) return;
      offP(); offD(); finished = true;
      App.setStatus('Ready');
      if (!document.getElementById('mo-' + id)) return;
      if (!p.ok) {
        const d = document.createElement('div');
        d.className = 'qr-none err'; d.textContent = '❌ ' + (p.error || 'Scan failed');
        list.appendChild(d);
      }
      summary(true, p.cancelled);
    });
    cancelBtn.addEventListener('click', () => { SFM.qrScanCancel(jobId); cancelBtn.disabled = true; cancelBtn.textContent = 'Stopping…'; });
    // closing the dialog stops a running batch
    const obs = new MutationObserver(() => {
      if (!document.getElementById('mo-' + id)) {
        obs.disconnect();
        if (!finished) SFM.qrScanCancel(jobId).catch(() => {});
      }
    });
    obs.observe(document.body, { childList: true });

    App.setStatus('Scanning for QR codes…', true);
    summary(false);
    let r;
    try { r = await SFM.scanQrFiles(paths, jobId); }
    catch (e) { r = { ok: false, error: String(e) }; }
    if (!r || !r.ok) {
      offP(); offD(); finished = true;
      App.setStatus('Ready');
      list.innerHTML = `<div class="qr-none err">❌ ${_esc((r && r.error) || 'Could not start the scan')}</div>`;
      summary(true);
      sumTxt.textContent = 'QR scanning is not available';
    }
  }

  // ── Screen overlay: click a code, drag a box, or scan everything ─────────
  function openScreen() {
    const id = Dialogs.modal('qr-overlay', {
      title: '📷 Scan QR from screen',
      width: '96vw',
      extraStyle: 'max-width:none;',
      body: `<div class="qrs-toolbar">
               <div class="qrs-status" id="qrs-status-__ID__"><span class="mt-spinner"></span> Minimising window and taking a screenshot…</div>
               <button class="btn btn-sm" id="qrs-all-__ID__" disabled>Scan whole screen</button>
               <button class="btn btn-sm" id="qrs-retake-__ID__">Retake screenshot</button>
             </div>
             <div class="qrs-stage" id="qrs-stage-__ID__" style="display:none">
               <img id="qrs-img-__ID__" alt="Screen capture" draggable="false">
               <canvas id="qrs-canvas-__ID__"></canvas>
             </div>
             <div class="qrs-results" id="qrs-results-__ID__"></div>`,
      footer: `<button class="btn btn-primary" id="qrs-close-__ID__">Close</button>`,
    });
    _fixIds(id);
    const $ = s => document.getElementById(s + '-' + id);
    const statusEl = $('qrs-status'), stage = $('qrs-stage'), img = $('qrs-img'),
          canvas = $('qrs-canvas'), resultsEl = $('qrs-results'), allBtn = $('qrs-all');
    const ctx = canvas.getContext('2d');
    $('qrs-close').addEventListener('click', () => Dialogs.closeModal(id));
    let screenW = 0, screenH = 0, drag = null, boxes = [], busy = false;

    function status(html, err) { statusEl.innerHTML = html; statusEl.classList.toggle('err', !!err); }
    const scale = () => (canvas.width ? screenW / canvas.width : 1);

    function sizeCanvas() {
      canvas.width = img.clientWidth; canvas.height = img.clientHeight;
      canvas.style.width = img.clientWidth + 'px'; canvas.style.height = img.clientHeight + 'px';
      draw();
    }
    function draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const s = scale();
      ctx.lineWidth = 2;
      boxes.forEach(b => {
        ctx.strokeStyle = '#3fb950';
        ctx.strokeRect(b[0] / s - 3, b[1] / s - 3, b[2] / s + 6, b[3] / s + 6);
      });
      if (drag && drag.moved) {
        const x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1);
        const w = Math.abs(drag.x1 - drag.x0), h = Math.abs(drag.y1 - drag.y0);
        ctx.fillStyle = 'rgba(0,0,0,.45)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.clearRect(x, y, w, h);
        ctx.strokeStyle = '#58a6ff'; ctx.setLineDash([6, 4]);
        ctx.strokeRect(x + 1, y + 1, w - 2, h - 2); ctx.setLineDash([]);
      }
    }

    function showResults(r) {
      resultsEl.innerHTML = '';
      if (r && r.ok) {
        boxes = r.results.map(x => x.bbox);
        r.results.forEach(x => resultsEl.appendChild(renderItem(x, false)));
        const n = r.results.length;
        status(`✅ ${n} QR code${n === 1 ? '' : 's'} decoded — click another code or drag a box to scan again`);
      } else {
        boxes = [];
        status('❌ ' + _esc((r && r.error) || 'No QR code found') +
               ' — drag a box around the code, or try "Scan whole screen"', true);
      }
      draw();
    }

    async function decode(fn) {
      if (busy) return;
      busy = true;
      status('<span class="mt-spinner"></span> Decoding…');
      let r;
      try { r = await fn(); } catch (e) { r = { ok: false, error: String(e) }; }
      busy = false;
      if (document.getElementById('mo-' + id)) showResults(r);
    }

    function pos(e) {
      const rc = canvas.getBoundingClientRect();
      return { x: Math.max(0, Math.min(canvas.width, e.clientX - rc.left)),
               y: Math.max(0, Math.min(canvas.height, e.clientY - rc.top)) };
    }
    canvas.addEventListener('pointerdown', e => {
      if (e.button !== 0 || busy) return;
      const p = pos(e);
      drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y, moved: false };
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', e => {
      if (!drag) return;
      const p = pos(e);
      drag.x1 = p.x; drag.y1 = p.y;
      if (Math.abs(p.x - drag.x0) > 6 || Math.abs(p.y - drag.y0) > 6) drag.moved = true;
      draw();
    });
    canvas.addEventListener('pointerup', () => {
      if (!drag) return;
      const d = drag; drag = null;
      const s = scale();
      if (d.moved) {
        const x = Math.min(d.x0, d.x1) * s, y = Math.min(d.y0, d.y1) * s;
        const w = Math.abs(d.x1 - d.x0) * s, h = Math.abs(d.y1 - d.y0) * s;
        draw();
        decode(() => SFM.decodeQrInRegion(Math.round(x), Math.round(y), Math.round(w), Math.round(h)));
      } else {
        decode(() => SFM.decodeQrAtPoint(Math.round(d.x0 * s), Math.round(d.y0 * s)));
      }
    });
    allBtn.addEventListener('click', () => decode(() => SFM.decodeQrInRegion(0, 0, 0, 0)));

    const onResize = () => { if (stage.style.display !== 'none') sizeCanvas(); };
    window.addEventListener('resize', onResize);
    let unsub = null;
    const obs = new MutationObserver(() => {
      if (!document.getElementById('mo-' + id)) {
        obs.disconnect(); window.removeEventListener('resize', onResize); if (unsub) unsub();
      }
    });
    obs.observe(document.body, { childList: true });

    function capture() {
      if (unsub) unsub();
      stage.style.display = 'none'; resultsEl.innerHTML = ''; boxes = []; allBtn.disabled = true;
      status('<span class="mt-spinner"></span> Minimising window and taking a screenshot…');
      unsub = SFM.on('screen_capture_ready', r => {
        unsub(); unsub = null;
        if (!document.getElementById('mo-' + id)) return;
        if (!r || !r.ok) { status('❌ Screenshot failed: ' + _esc((r && r.error) || 'unknown error'), true); return; }
        screenW = r.width; screenH = r.height;
        img.onload = () => {
          stage.style.display = 'block'; allBtn.disabled = false; sizeCanvas();
          status('👆 Click a QR code, or drag a box around it. (Links open only when you click “Open link”.)');
        };
        img.src = r.data_url;
      });
      SFM.getScreenCapture().catch(err => status('❌ Failed to start capture: ' + _esc(err), true));
    }
    $('qrs-retake').addEventListener('click', capture);
    capture();
  }

  return { scanSelection, scanPaths, openScreen, isScannable, renderItem, openLink };
})();
