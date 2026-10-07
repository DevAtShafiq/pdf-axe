/**
 * qr.js — QR codes: pick one on screen, or check files.
 *
 *   QrScan.pick()            toolbar "QR": native overlay over the whole desktop
 *                            (qr_pick.py helper) — click a code or drag a box;
 *                            links open immediately, plain text shows in a dialog
 *   QrScan.scanPaths(paths)  one results dialog for one or many images / PDFs
 *
 * Backend: sfm_bridge qr_pick_start (→ event qr_pick_result) / scan_qr_files /
 * qr_open_url (qr_scan.py). Only http(s) links are ever opened (checked in Python).
 */
const QrScan = (() => {
  const EXTS = ['.jpg', '.jpeg', '.jfif', '.png', '.bmp', '.webp', '.gif', '.tif', '.tiff', '.pdf'];
  const TYPE_ICON = { url: 'link', email: 'mail', phone: 'user-circle', sms: 'mail', wifi: 'globe', vcard: 'user',
                      contact: 'user', geo: 'globe', event: 'clock', text: 'file-text', other: 'alert-triangle' };
  const tIcon = (type, size = 12) => Icons.svg(TYPE_ICON[type] || 'qr-code', size);

  function _esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  const _ext = p => { const m = /\.[^.\\/]+$/.exec(p || ''); return m ? m[0].toLowerCase() : ''; };
  const _base = p => String(p || '').split(/[\\/]/).pop();
  function isScannable(p) { return EXTS.includes(_ext(p)); }

  async function _copy(text) {
    if (!(await SFM.copyText(text))) { App.toast('Copy failed', 'error'); return; }
    App.toast('Copied to clipboard', 'success', 1800);
  }

  // ── Open link — immediately, like the old app (http/https only, enforced in Python)
  async function openLink(res) {
    const url = (res && (res.url || res.text)) || '';
    const r = await SFM.qrOpenUrl(url).catch(e => ({ ok: false, error: String(e) }));
    if (!r || !r.ok) App.toast('Cannot open link: ' + _esc((r && r.error) || 'unknown error'), 'error');
  }

  // _modal() picks the id; buttons in body/footer are written with __ID__ and patched here.
  function _fixIds(id) {
    const root = document.getElementById('mo-' + id);
    if (!root) return;
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
  }

  // Inline notice (tone: '' | 'warning' | 'error') → element
  function _note(tone, title, text) {
    const d = document.createElement('div');
    d.className = 'callout qr-none ' + tone;
    d.innerHTML = `${Icons.svg(tone === 'error' ? 'alert-circle' : tone === 'warning' ? 'alert-triangle' : 'scan', 16)}
      <div class="callout-body"><span class="callout-title">${_esc(title)}</span>${text ? `<span>${_esc(text)}</span>` : ''}</div>`;
    return d;
  }

  // ── One decoded code → card ──────────────────────────────────────────────
  function renderItem(res, showPage) {
    const el = document.createElement('div');
    el.className = 'qr-item';
    const thumb = res.thumb
      ? `<img class="qr-thumb" src="${res.thumb}" alt="QR code">`
      : `<div class="qr-thumb empty">${tIcon(res.type, 26)}</div>`;
    const isUrl = res.type === 'url';
    const warns = res.warnings || [], notes = res.notes || [];
    let head = '';
    if (isUrl && res.host) {
      const dom = res.domain || res.host;
      const i = res.host.lastIndexOf(dom);
      const hostHtml = i >= 0
        ? `<span class="sub">${_esc(res.host.slice(0, i))}</span><span class="dom">${_esc(dom)}</span>${_esc(res.host.slice(i + dom.length))}`
        : `<span class="dom">${_esc(res.host)}</span>`;
      const secure = res.scheme === 'https';
      head = `<div class="qr-host" title="Website the link points to">
                <span class="qr-lock ${secure ? 'ok' : 'bad'}" title="${secure ? 'Encrypted (https)' : 'Not encrypted (http)'}">${Icons.svg(secure ? 'lock' : 'alert-circle', 14)}</span>
                <span class="qr-host-name">${hostHtml}</span></div>`;
    }
    const pageBadge = (showPage && res.page) ? `<span class="pill pill-neutral">Page ${res.page}</span>` : '';
    const sym = res.symbology && res.symbology !== 'QRCODE' ? `<span class="pill pill-neutral">${_esc(res.symbology)}</span>` : '';
    const tone = isUrl ? (warns.length ? 'pill-yellow' : 'pill-green') : (res.type === 'other' ? 'pill-red' : 'pill-blue');
    el.innerHTML = `${thumb}
      <div class="qr-body">
        <div class="qr-meta">
          <span class="pill ${tone}">${tIcon(res.type)}${_esc(res.label || res.type)}</span>
          ${pageBadge}${sym}
          ${notes.map(n => `<span class="pill pill-green">${Icons.svg('check', 12)}${_esc(n)}</span>`).join('')}
        </div>
        ${head}
        <div class="qr-text">${_esc(isUrl ? (res.url || res.text) : res.text)}</div>
        ${warns.length ? `<div class="qr-warn-pills">${warns.map(w =>
          `<span class="pill ${res.type === 'other' ? 'pill-red' : 'pill-yellow'}" title="${_esc(w)}">${Icons.svg('alert-triangle', 12)}<span class="truncate">${_esc(w)}</span></span>`).join('')}</div>` : ''}
        <div class="qr-actions">
          ${isUrl && res.openable ? `<button class="btn btn-sm btn-primary" data-act="open">${Icons.svg('external-link', 14)}Open link</button>` : ''}
          <button class="btn btn-sm" data-act="copy">${Icons.svg('copy', 14)}Copy${isUrl ? ' text' : ''}</button>
          ${isUrl ? `<button class="btn btn-sm btn-ghost" data-act="copyurl">${Icons.svg('link', 14)}Copy link</button>` : ''}
        </div>
      </div>`;
    el.querySelector('[data-act="copy"]').addEventListener('click', () => _copy(res.text));
    el.querySelector('[data-act="open"]')?.addEventListener('click', () => openLink(res));
    el.querySelector('[data-act="copyurl"]')?.addEventListener('click', () => _copy(res.url || res.text));
    return el;
  }

  // ── Files → results dialog ───────────────────────────────────────────────
  async function scanPaths(paths) {
    paths = (paths || []).filter(isScannable);
    if (!paths.length) { App.toast('Select an image or PDF to scan for QR codes', 'warning'); return; }
    const batch = paths.length > 1;
    const jobId = 'qr' + Date.now();
    const id = Dialogs.modal('qr-results', {
      title: 'QR code check',
      icon: 'qr-code',
      subtitle: batch ? `${paths.length} files` : _base(paths[0]),
      width: '680px',
      body: `<div class="progress-block">
               <div class="progress-label qr-summary" id="qrr-sum-__ID__"><span class="mt-spinner"></span>
                 <span id="qrr-sumtxt-__ID__">Scanning…</span></div>
               <div class="progress-wrap qr-progress"><div class="progress-bar" id="qrr-bar-__ID__" style="width:0%"></div></div>
             </div>
             <div class="qr-list" id="qrr-list-__ID__"></div>`,
      footer: `<button class="btn btn-ghost footer-left" id="qrr-copyall-__ID__" disabled>${Icons.svg('clipboard', 16)}Copy all results</button>
               <button class="btn" id="qrr-cancel-__ID__">${Icons.svg('stop', 16)}Stop</button>
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
        h.innerHTML = `${Icons.file({ name: f.name }, 16)}<span class="qr-file-name">${_esc(f.name)}</span>
          <span class="pill ${f.ok ? (n ? 'pill-blue' : 'pill-neutral') : 'pill-red'}">${f.ok ? (n ? n + ' code' + (n > 1 ? 's' : '') : 'No QR') : 'Error'}</span>`;
        list.appendChild(h);
      }
      if (!f.ok) {
        list.appendChild(_note('error', 'Could not scan this file', f.error || ''));
      } else if (!(f.results || []).length) {
        const pages = f.kind === 'pdf' ? ` (${f.pages_scanned} page${f.pages_scanned === 1 ? '' : 's'} checked)` : '';
        list.appendChild(_note('', `No QR code found${pages}`,
          'If the code is small or blurred, try a sharper scan — or open the file, press QR and click the code.'));
      } else {
        const multiPage = f.kind === 'pdf' || (f.page_count || 0) > 1;
        f.results.forEach(r => list.appendChild(renderItem(r, multiPage)));
      }
      if (f.truncated) {
        list.appendChild(_note('warning', 'Partly checked', `Only the first ${f.pages_scanned} of ${f.page_count} pages were checked.`));
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
      if (done) {
        sum.querySelector('.mt-spinner')?.remove();
        if (!sum.querySelector('svg')) sum.insertAdjacentHTML('afterbegin', Icons.svg(errs ? 'alert-circle' : warn ? 'alert-triangle' : 'check-circle', 16, errs ? 'text-red' : warn ? 'text-yellow' : 'text-green'));
        bar.parentElement.classList.add('hidden');
        cancelBtn.style.display = 'none'; copyAll.disabled = !codes;
      }
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
      if (!p.ok) list.appendChild(_note('error', 'Scan failed', p.error || ''));
      summary(true, p.cancelled);
    });
    cancelBtn.addEventListener('click', () => { SFM.qrScanCancel(jobId); cancelBtn.disabled = true; cancelBtn.classList.add('is-loading'); });
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
      list.innerHTML = '';
      list.appendChild(_note('error', 'Could not start the scan', (r && r.error) || ''));
      summary(true);
      sumTxt.textContent = 'QR scanning is not available';
    }
  }

  // ── Pick a QR code on screen (native overlay, qr_pick.py) ───────────────
  // The overlay covers every monitor, this window included, so a certificate
  // open in the preview pane can be clicked directly. Python opens http(s)
  // links itself (no confirmation) and reports back with `qr_pick_result`.
  let _picking = false;

  async function pick() {
    if (_picking) { App.toast('The QR picker is already open — click a code, or press Esc', 'info', 3000); return; }
    let r;
    try { r = await SFM.qrPickStart(); } catch (e) { r = { ok: false, error: String(e) }; }
    if (!r || !r.ok) {
      if (r && r.busy) App.toast('The QR picker is already open — click a code, or press Esc', 'info', 3000);
      else App.toast('Could not start the QR picker: ' + _esc((r && r.error) || 'unknown error'), 'error', 5000);
      return;
    }
    _picking = true;
    App.setStatus('Click a QR code anywhere on screen — drag a box around it, or press Esc to cancel', true);
  }

  function _hostHtml(res) {
    const host = res.host || '';
    if (!host) return _esc(res.url || res.text || '');
    const dom = res.domain || host;
    const i = host.lastIndexOf(dom);
    return i >= 0
      ? `<span class="sub">${_esc(host.slice(0, i))}</span><span class="dom">${_esc(dom)}</span>${_esc(host.slice(i + dom.length))}`
      : `<span class="dom">${_esc(host)}</span>`;
  }

  // Toast with a Copy action (App.toast has no actions).
  function _linkToast(res) {
    const container = document.getElementById('toast-container');
    if (!container) { App.toast('Opened link: ' + _esc(res.url), 'success', 6000); return; }
    const el = document.createElement('div');
    el.className = 'toast success qr-toast';
    el.setAttribute('role', 'status');
    el.innerHTML = `<span class="icon">${Icons.svg('external-link', 16)}</span>
      <span class="qr-toast-body">
        <span class="qr-toast-title">Opened link</span>
        <span class="qr-host-name qr-toast-host">${_hostHtml(res)}</span>
        <span class="qr-toast-url" title="${_esc(res.url)}">${_esc(res.url)}</span>
      </span>
      <button class="btn btn-sm btn-ghost qr-toast-copy" title="Copy link" aria-label="Copy link">${Icons.svg('copy', 14)}Copy</button>`;
    let timer = null;
    const close = () => { el.classList.add('fade-out'); setTimeout(() => el.remove(), 300); };
    const arm = () => { clearTimeout(timer); timer = setTimeout(close, 7000); };
    el.querySelector('.qr-toast-copy').addEventListener('click', () => _copy(res.url));
    el.addEventListener('mouseenter', () => clearTimeout(timer));
    el.addEventListener('mouseleave', arm);
    container.appendChild(el);
    arm();
  }

  function _useAsFileName(text) {
    const name = String(text || '').replace(/[\\/:*?"<>|\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180);
    if (!name) return false;
    if (typeof FileTree !== 'undefined' && FileTree.startRename) { FileTree.startRename(undefined, name); return true; }
    const ri = document.getElementById('rename-input');
    const st = App.state || {};
    if (!ri || !(st.focusedPath || (st.selectedPaths || []).length)) {
      App.toast('Select a file first, then use the QR text as its name', 'warning', 4000);
      return false;
    }
    ri.value = name;
    ri.dispatchEvent(new Event('input', { bubbles: true }));
    ri.focus();
    ri.select();
    return true;
  }

  // Plain text (or a link that could not be opened) → small dialog
  function _textDialog(res) {
    const isUrl = !!res.is_url;
    const text = isUrl ? res.url : (res.text || '');
    const id = Dialogs.modal('qr-pick-text', {
      title: isUrl ? 'QR code link' : 'QR code text',
      icon: 'qr-code',
      subtitle: res.label && res.label !== 'Text' ? res.label : 'Decoded from the screen',
      width: '520px',
      body: `${isUrl && res.open_error ? `<div class="callout warning">${Icons.svg('alert-triangle', 16)}<div class="callout-body">
               <span class="callout-title">The link could not be opened</span><span>${_esc(res.open_error)}</span></div></div>` : ''}
             <div class="mono-box selectable qr-pick-text">${_esc(text)}</div>
             <div class="field-hint">${text.length} character${text.length === 1 ? '' : 's'}</div>`,
      footer: `<button class="btn btn-ghost footer-left" id="qpt-name-__ID__">${Icons.svg('text-cursor', 16)}Use as file name</button>
               <button class="btn" id="qpt-close-__ID__">Close</button>
               ${isUrl ? `<button class="btn" id="qpt-copy-__ID__">${Icons.svg('copy', 16)}Copy</button>
                          <button class="btn btn-primary" id="qpt-open-__ID__">${Icons.svg('external-link', 16)}Open link</button>`
                       : `<button class="btn btn-primary" id="qpt-copy-__ID__">${Icons.svg('copy', 16)}Copy</button>`}`,
    });
    _fixIds(id);
    const $ = s => document.getElementById(s + '-' + id);
    $('qpt-close').addEventListener('click', () => Dialogs.closeModal(id));
    $('qpt-copy').addEventListener('click', () => _copy(text));
    $('qpt-name').addEventListener('click', () => { if (_useAsFileName(res.text)) Dialogs.closeModal(id); });
    $('qpt-open')?.addEventListener('click', () => { Dialogs.closeModal(id); openLink(res); });
  }

  function _unavailableDialog(res) {
    Dialogs.openModal('qr-pick-missing', 'QR picking is not available',
      `<div class="callout warning">${Icons.svg('alert-triangle', 16)}<div class="callout-body">
         <span class="callout-title">Screen capture or the QR decoder is missing</span>
         <span>${_esc(res.detail || '')}</span></div></div>
       <div class="field">
         <span class="field-label">Install the components (in the app's Python environment)</span>
         <div class="mono-box selectable">pip install mss pyzbar pillow</div>
         <div class="field-hint">On Windows the pyzbar wheel already includes the zbar DLL. If it still fails,
           install the Microsoft Visual C++ Redistributable, then restart Office Axe.</div>
       </div>`,
      [{ label: 'Close', primary: true, onClick: () => {} }],
      { icon: 'qr-code', subtitle: 'Needs mss, pyzbar and Pillow' });
  }

  function _onPickResult(res) {
    _picking = false;
    App.setStatus('Ready');
    res = res || { ok: false, reason: 'error' };
    if (res.ok) {
      if (res.is_url && res.opened) {
        App.setStatus('QR link opened: ' + (res.url || ''));
        _linkToast(res);
      } else {
        _textDialog(res);
      }
      return;
    }
    switch (res.reason) {
      case 'cancelled':   return;
      case 'not_found':   App.toast('No QR code found', 'info', 3500); return;
      case 'unavailable': _unavailableDialog(res); return;
      default:            App.toast('QR picker failed: ' + _esc(res.detail || res.error || 'unknown error'), 'error', 6000);
    }
  }

  if (typeof SFM !== 'undefined' && SFM.on) SFM.on('qr_pick_result', _onPickResult);

  return { pick, scanPaths, isScannable, renderItem, openLink };
})();
