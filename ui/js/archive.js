/**
 * archive.js — ZIP / unzip and ZIP + PDF passwords.
 *
 *   Archive.openZip(paths)            Compress to ZIP… (name, compression, password)
 *   Archive.quickZip(paths)           zip with defaults, no dialog
 *   Archive.zipEachSubfolder(folder)  one ZIP per sub-folder of a folder
 *   Archive.zipEach(paths)            one ZIP per selected item
 *   Archive.extract(path, {mode, dest, members})   mode 'folder' (default) | 'here'
 *   Archive.extractTo(path)           folder picker, then extract into a new sub-folder
 *   Archive.extractAll(paths)
 *   Archive.viewContents(path)        list entries, extract all / selected
 *   Archive.zipPassword(path)         add / change a ZIP password → name_protected.zip
 *   Archive.zipRemovePassword(path)   → name_unlocked.zip
 *   Archive.pdfProtect(path)          PDF open password + permissions → name_protected.pdf
 *   Archive.pdfRemovePassword(path)   → name_unlocked.pdf
 *   Archive.ensurePdfUnlocked(path)   prompt for the password of a locked PDF (preview only)
 *
 * Hooks used by shared files: menuItems (context-menu.js), onDetails (details.js),
 * previewHook (preview.js), onOpen (file-tree.js double-click).
 *
 * Backend: archive_bridge.py (SFM.zip* / SFM.pdf*Password wrappers in bridge.js).
 * Nothing is overwritten or deleted: every output is a new file or folder.
 */

const Archive = (() => {

  // ── helpers ───────────────────────────────────────────────────────────────
  const baseOf = p => String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop();
  const dirOf  = p => String(p || '').replace(/[\\/]+$/, '').replace(/[\\/][^\\/]*$/, '');
  const stemOf = p => baseOf(p).replace(/\.[^.]+$/, '');
  const extOf  = p => { const m = /\.[^.\\/]+$/.exec(baseOf(p)); return m ? m[0].toLowerCase() : ''; };
  const isZip  = p => extOf(p) === '.zip';
  const isPdf  = p => extOf(p) === '.pdf';
  const ic = (name, size = 16, cls = '') => Icons.svg(name, size, cls);
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtSize(b) {
    b = +b || 0;
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
    return (b / 1073741824).toFixed(2) + ' GB';
  }
  const plural = (n, one, many) => n + ' ' + (n === 1 ? one : (many || one + 's'));
  const cleanName = n => String(n || '').trim().replace(/\.zip$/i, '').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[ .]+$/, '').trim();
  const stripSuffix = s => s.replace(/_(protected|unlocked)$/i, '') || s;
  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem('archive.' + key);
      localStorage.setItem('archive.' + key, String(val));
    } catch (_) { return null; }
  }
  const selection = () => {
    const sel = (App.state.selectedPaths || []).slice();
    if (sel.length) return sel;
    return App.state.focusedPath ? [App.state.focusedPath] : [];
  };
  const isDirPath = p => {
    try {
      const e = (FileTree.getFocusedEntry && FileTree.getFocusedEntry());
      if (e && e.path === p) return !!e.is_dir;
    } catch (_) {}
    return !extOf(p);
  };
  function defaultZipName(paths) {
    if (paths.length === 1) return isDirPath(paths[0]) ? baseOf(paths[0]) : (stemOf(paths[0]) || baseOf(paths[0]));
    return baseOf(dirOf(paths[0])) || 'Archive';
  }
  function done(msg, selectPath) {
    App.setStatus('Ready');
    App.toast(msg, 'success', 5000);
    if (selectPath && FileTree.refreshAndSelect) FileTree.refreshAndSelect(selectPath);
    else FileTree.refresh();
  }
  function fail(r, prefix) {
    App.setStatus('Ready');
    if (r && r.code === 'cancelled') { App.toast(esc(r.error || 'Cancelled'), 'info', 5000); FileTree.refresh(); return; }
    App.toast(esc((prefix ? prefix + ': ' : '') + ((r && r.error) || 'Operation failed')), 'error', 8000);
  }

  // Session-only memory of passwords that worked (never written anywhere).
  const _pw = new Map();
  const _locked = new Set();         // PDFs known to need a password in the preview

  // ── capabilities ──────────────────────────────────────────────────────────
  let _caps = null;
  function caps() {
    if (!_caps) {
      _caps = SFM.archiveCapabilities()
        .then(r => (r && r.ok) ? r : { zip_password: false })
        .catch(() => { _caps = null; return { zip_password: false }; });
    }
    return _caps;
  }
  const NO_PYZIPPER = 'Password-protected ZIPs need the “pyzipper” component, which isn’t installed on this computer. Ask your administrator to install it. Plain ZIP and unzip work normally.';

  // ── password field + strength meter ───────────────────────────────────────
  function pwField(id, label, opts = {}) {
    return `
      <div class="field ax-pw-field${opts.cls ? ' ' + opts.cls : ''}">
        <label class="field-label" for="${id}">${esc(label)}</label>
        <div class="input-group ax-pw">
          <input id="${id}" class="input-text" type="password" autocomplete="off" spellcheck="false"
                 placeholder="${esc(opts.placeholder || '')}"${opts.disabled ? ' disabled' : ''}>
          <button type="button" class="icon-btn icon-btn-sm input-action ax-eye" data-for="${id}"
                  title="Show password" aria-label="Show password"${opts.disabled ? ' disabled' : ''}><i data-icon="eye" data-size="14"></i></button>
        </div>
        ${opts.strength ? `<div class="ax-strength" id="${id}-strength" data-score="0"><span></span><span></span><span></span><span></span><em></em></div>` : ''}
        <div class="field-error hidden" id="${id}-err"></div>
        ${opts.hint ? `<div class="field-hint">${opts.hint}</div>` : ''}
      </div>`;
  }
  function wirePw(root) {
    root.querySelectorAll('.ax-eye').forEach(btn => {
      btn.addEventListener('click', () => {
        const inp = root.querySelector('#' + btn.dataset.for);
        if (!inp) return;
        const show = inp.type === 'password';
        inp.type = show ? 'text' : 'password';
        btn.innerHTML = ic(show ? 'eye-off' : 'eye', 14);
        btn.title = show ? 'Hide password' : 'Show password';
        btn.setAttribute('aria-label', btn.title);
        inp.focus();
      });
    });
    root.querySelectorAll('.ax-strength').forEach(meter => {
      const inp = root.querySelector('#' + meter.id.replace(/-strength$/, ''));
      if (!inp) return;
      const upd = () => {
        const s = strength(inp.value);
        meter.dataset.score = inp.value ? s.score : 0;
        meter.querySelector('em').textContent = inp.value ? s.label : '';
      };
      inp.addEventListener('input', upd);
      upd();
    });
    root.querySelectorAll('.ax-pw-field input').forEach(inp => {
      inp.addEventListener('input', () => setErr(root, inp.id, ''));
    });
  }
  function strength(pw) {
    pw = String(pw || '');
    if (pw.length < 6) return { score: 1, label: 'Too short' };
    let s = 0;
    if (pw.length >= 8) s++;
    if (pw.length >= 12) s++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
    if (/\d/.test(pw)) s++;
    if (/[^A-Za-z0-9]/.test(pw)) s++;
    const score = Math.max(1, Math.min(4, s));
    return { score, label: ['', 'Weak', 'Fair', 'Good', 'Strong'][score] };
  }
  function setErr(root, id, msg) {
    const el = root.querySelector('#' + id + '-err');
    const inp = root.querySelector('#' + id);
    if (el) { el.textContent = msg || ''; el.classList.toggle('hidden', !msg); }
    if (inp) inp.classList.toggle('is-invalid', !!msg);
    if (msg && inp) { inp.focus(); inp.select(); }
  }
  const val = (root, id) => (root.querySelector('#' + id) || {}).value || '';

  // Footer button helpers for Dialogs.openModal dialogs.
  const footBtn = (ov, label) => ov.querySelector(`.modal-footer [data-modal-btn="${label}"]`);
  function busy(btn, on) {
    if (!btn) return;
    btn.classList.toggle('is-loading', !!on);
    btn.disabled = !!on;
  }
  function onEnter(ov, fn) {
    ov.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && e.target && e.target.tagName === 'INPUT') { e.preventDefault(); fn(); }
    });
  }

  // ── password prompt (Dialogs.ask-style, masked, with retry) ───────────────
  // submit(pw) → result; {ok:true} closes and resolves with the result,
  // wrong/need_password shows "Wrong password" inline and keeps it open, any
  // other failure closes and resolves with it. Cancel → null.
  function askPassword(o) {
    return new Promise(resolve => {
      let settled = false;
      const finish = v => { if (settled) return; settled = true; resolve(v); };
      const id = 'axask' + Date.now();
      const body = `
        ${o.message ? `<p class="ax-msg">${esc(o.message)}</p>` : ''}
        ${pwField(id, o.label || 'Password')}
        ${o.hint ? `<div class="field-hint ax-hint">${ic('info', 14)}<span>${esc(o.hint)}</span></div>` : ''}`;
      const okLabel = o.okLabel || 'OK';
      const submit = async () => {
        const pw = val(ov, id);
        if (!pw) { setErr(ov, id, 'Enter the password.'); return; }
        const btn = footBtn(ov, okLabel);
        busy(btn, true);
        let r;
        try { r = await o.submit(pw); } catch (e) { r = { ok: false, error: String(e) }; }
        busy(btn, false);
        if (r && r.ok) { finish({ ...r, password: pw }); ov._close(); return; }
        if (r && (r.code === 'wrong_password' || r.code === 'need_password')) {
          setErr(ov, id, 'Wrong password — try again.');
          return;
        }
        finish(r || { ok: false, error: 'Failed' });
        ov._close();
      };
      const ov = Dialogs.openModal(id, o.title || 'Password required', body, [
        { label: 'Cancel', onClick: () => ov._close() },
        { label: okLabel, primary: true, icon: o.okIcon || 'lock-open', onClick: submit },
      ], { icon: o.icon || 'lock', subtitle: o.subtitle, size: 'sm', cls: 'ax-modal ax-ask', onClose: () => finish(null) });
      ov.classList.add('dlg-top');
      ov.style.zIndex = 10050;
      wirePw(ov);
      onEnter(ov, submit);
      if (o.error) setTimeout(() => setErr(ov, id, o.error), 40);
      setTimeout(() => { const i = ov.querySelector('#' + id); if (i) i.focus(); }, 40);
    });
  }

  // ── background jobs + progress card ───────────────────────────────────────
  let _seq = 0;
  const newJob = () => 'ax' + (++_seq) + '_' + Date.now();

  // start(jobId) → Promise<{ok, job_id}|{ok:false, code, error}>. Resolves with the
  // archive_done payload, or with the start error (e.g. need_password).
  function runJob(start, { title, name, icon = 'package', status } = {}) {
    const job = newJob();
    return new Promise(resolve => {
      let card = null, timer = null, settled = false;
      const showCard = () => {
        if (card || settled) return;
        card = document.createElement('div');
        card.className = 'toast ax-job';
        card.setAttribute('role', 'status');
        card.innerHTML = `
          <span class="icon">${ic(icon, 16)}</span>
          <div class="ax-job-body">
            <div class="ax-job-head"><strong class="truncate">${esc(title || 'Working…')}</strong>
              <button class="btn btn-sm btn-ghost ax-job-cancel" type="button">Cancel</button></div>
            <div class="ax-job-file truncate">${esc(name || '')}</div>
            <div class="progress-wrap"><div class="progress-bar indeterminate" style="width:0%"></div></div>
            <div class="ax-job-pct"></div>
          </div>`;
        card.querySelector('.ax-job-cancel').addEventListener('click', e => {
          e.currentTarget.disabled = true;
          e.currentTarget.textContent = 'Cancelling…';
          SFM.archiveCancel(job).catch(() => {});
        });
        const box = document.getElementById('toast-container') || document.body;
        box.insertBefore(card, box.firstChild);
      };
      const cleanup = () => {
        settled = true;
        clearTimeout(timer);
        offP(); offD();
        if (card) { card.classList.add('fade-out'); const c = card; setTimeout(() => c.remove(), 300); }
      };
      const offP = SFM.on('archive_progress', p => {
        if (!p || p.job_id !== job) return;
        if (!card) return;
        const bar = card.querySelector('.progress-bar');
        if (p.total) {
          bar.classList.remove('indeterminate');
          bar.style.width = Math.min(100, Math.round(100 * p.done / p.total)) + '%';
          card.querySelector('.ax-job-pct').textContent = `${fmtSize(p.done)} of ${fmtSize(p.total)}`;
        }
        if (p.file) card.querySelector('.ax-job-file').textContent = p.file;
      });
      const offD = SFM.on('archive_done', d => {
        if (!d || d.job_id !== job) return;
        cleanup();
        resolve(d);
      });
      App.setStatus(status || title || 'Working…', true);
      timer = setTimeout(showCard, 700);
      Promise.resolve().then(() => start(job)).then(r => {
        if (!r || !r.ok) { cleanup(); App.setStatus('Ready'); resolve(r || { ok: false, error: 'Failed' }); }
      }).catch(e => { cleanup(); App.setStatus('Ready'); resolve({ ok: false, error: String(e) }); });
    });
  }

  // ── Compress to ZIP… ──────────────────────────────────────────────────────
  const LEVELS = { store: ['store', 0, 'Store', 'No compression — fastest'], fast: ['deflate', 1, 'Fast', 'Light compression'],
                   normal: ['deflate', 6, 'Normal', 'Good balance (recommended)'], max: ['deflate', 9, 'Maximum', 'Smallest file, slower'] };

  async function openZip(paths) {
    paths = (paths || []).filter(Boolean);
    if (!paths.length) { App.toast('Select files or folders to zip', 'warning'); return; }
    const cp = await caps();
    const canPw = !!cp.zip_password;
    let lvl = store('level') || 'normal';
    if (!LEVELS[lvl]) lvl = 'normal';
    const folder = dirOf(paths[0]);
    const body = `
      <div class="field">
        <label class="field-label" for="ax-zip-name">Archive name</label>
        <div class="field-row"><input id="ax-zip-name" class="input-text" spellcheck="false" autocomplete="off"
             value="${esc(defaultZipName(paths))}"><span class="field-suffix">.zip</span></div>
        <div class="field-error hidden" id="ax-zip-name-err"></div>
        <div class="field-hint">Saved in <strong>${esc(baseOf(folder) || folder)}</strong>. An existing file is never replaced.</div>
      </div>
      <div class="field">
        <span class="field-label">Compression</span>
        <div class="segmented segmented-block" id="ax-level" role="group" aria-label="Compression">
          ${Object.entries(LEVELS).map(([k, v]) => `<button type="button" class="seg-btn${k === lvl ? ' active' : ''}" data-level="${k}" title="${esc(v[3])}">${esc(v[2])}</button>`).join('')}
        </div>
        <div class="field-hint" id="ax-level-hint">${esc(LEVELS[lvl][3])}</div>
      </div>
      <div class="ax-pw-section${canPw ? '' : ' is-disabled'}">
        <div class="section-head">
          <label class="switch"><input type="checkbox" id="ax-pw-on"${canPw ? '' : ' disabled'}><span class="switch-track"></span>Protect with password</label>
          <span class="pill pill-neutral">${ic('shield-check', 12)}AES-256</span>
        </div>
        ${canPw ? '' : `<div class="callout warning ax-callout">${ic('alert-triangle', 16)}<div class="callout-body"><div class="callout-title">Password protection unavailable</div><div>${esc(NO_PYZIPPER)}</div></div></div>`}
        <div class="ax-pw-box hidden" id="ax-pw-box">
          <div class="field-grid">
            ${pwField('ax-zip-pw', 'Password', { strength: true })}
            ${pwField('ax-zip-pw2', 'Confirm password')}
          </div>
          <div class="field-hint ax-hint">${ic('info', 14)}<span>Anyone opening the ZIP will need this password. It can’t be recovered if you forget it.</span></div>
        </div>
      </div>`;
    const ov = Dialogs.openModal('ax-zip', 'Compress to ZIP', body, [
      { label: 'Cancel', onClick: () => ov._close() },
      { label: 'Create ZIP', primary: true, icon: 'package', onClick: () => submit() },
    ], { icon: 'package', subtitle: paths.length === 1 ? baseOf(paths[0]) : plural(paths.length, 'item'), cls: 'ax-modal' });
    wirePw(ov);
    const nameIn = ov.querySelector('#ax-zip-name');
    setTimeout(() => { nameIn.focus(); nameIn.select(); }, 40);
    nameIn.addEventListener('input', () => setErr(ov, 'ax-zip-name', ''));
    ov.querySelectorAll('#ax-level .seg-btn').forEach(b => b.addEventListener('click', () => {
      ov.querySelectorAll('#ax-level .seg-btn').forEach(x => x.classList.toggle('active', x === b));
      lvl = b.dataset.level;
      ov.querySelector('#ax-level-hint').textContent = LEVELS[lvl][3];
    }));
    const pwOn = ov.querySelector('#ax-pw-on');
    pwOn.addEventListener('change', () => {
      ov.querySelector('#ax-pw-box').classList.toggle('hidden', !pwOn.checked);
      if (pwOn.checked) setTimeout(() => ov.querySelector('#ax-zip-pw').focus(), 20);
    });
    onEnter(ov, () => submit());

    function submit() {
      const name = cleanName(nameIn.value);
      if (!name) { setErr(ov, 'ax-zip-name', 'Enter a name for the ZIP.'); return; }
      let pw = '';
      if (pwOn.checked) {
        pw = val(ov, 'ax-zip-pw');
        if (!pw) { setErr(ov, 'ax-zip-pw', 'Enter a password.'); return; }
        if (pw.length < 4) { setErr(ov, 'ax-zip-pw', 'Use at least 4 characters.'); return; }
        if (pw !== val(ov, 'ax-zip-pw2')) { setErr(ov, 'ax-zip-pw2', 'The passwords don’t match.'); return; }
      }
      store('level', lvl);
      ov._close();
      zipNow(paths, name + '.zip', pw, LEVELS[lvl]);
    }
  }

  async function zipNow(paths, outName = '', pw = '', level = LEVELS.normal) {
    const n = paths.length;
    const d = await runJob(job => SFM.zipPaths(paths, outName, pw, job, level[0], level[1]), {
      title: pw ? 'Creating protected ZIP…' : 'Creating ZIP…',
      name: outName || defaultZipName(paths) + '.zip', icon: 'package', status: 'Zipping…',
    });
    if (!d || !d.ok) return fail(d, 'Zip failed');
    done(`Created <strong>${esc(baseOf(d.out_path))}</strong> — ${plural(d.files || 0, 'file')}, ${fmtSize(d.size)}` +
         (d.encrypted ? ' · password protected' : ''), d.out_path);
    return d;
  }

  function quickZip(paths) {
    paths = (paths || []).filter(Boolean);
    if (!paths.length) { App.toast('Select files or folders to zip', 'warning'); return; }
    return zipNow(paths);
  }

  async function zipEach(paths, title) {
    paths = (paths || []).filter(Boolean);
    if (!paths.length) return;
    const d = await runJob(job => SFM.zipEach(paths, '', job), {
      title: title || `Zipping ${plural(paths.length, 'item')}…`, icon: 'package', status: 'Zipping…',
    });
    if (!d || !d.ok) return fail(d, 'Zip failed');
    const outs = d.outputs || [];
    const errs = d.errors || [];
    if (errs.length) App.toast(esc(`${errs.length} could not be zipped: ${errs[0].error}`), 'warning', 8000);
    done(`Created ${plural(outs.length, 'ZIP file')}`, outs.length === 1 ? outs[0] : null);
  }

  async function zipEachSubfolder(folder) {
    folder = folder || FileTree.getCurrentFolder();
    if (!folder) return;
    const r = await SFM.listFolder(folder);
    if (!r || !r.ok) { fail(r); return; }
    const dirs = (r.entries || []).filter(e => e.is_dir && e.name !== '_to_review').map(e => e.path);
    if (!dirs.length) { App.toast('This folder has no sub-folders to zip', 'info'); return; }
    const ok = await Dialogs.confirm({
      title: 'Zip each sub-folder', icon: 'package', okLabel: `Create ${plural(dirs.length, 'ZIP')}`, okIcon: 'package',
      message: `Zip each of the ${plural(dirs.length, 'sub-folder')} in “${baseOf(folder)}” into its own ZIP?`,
      detail: 'Each ZIP is saved next to its folder (e.g. “Name.zip”). Existing files are never replaced and the folders are kept.',
    });
    if (ok) zipEach(dirs, `Zipping ${plural(dirs.length, 'sub-folder')}…`);
  }

  // ── extract ───────────────────────────────────────────────────────────────
  // Resolves with the archive_done payload (or null when cancelled at the prompt).
  async function extract(path, o = {}) {
    const name = baseOf(path);
    const mode = o.mode === 'here' ? 'here' : 'folder';
    const start = pw => runJob(job => SFM.zipExtract(path, o.dest || '', pw, o.members || null, job, mode), {
      title: 'Extracting…', name, icon: 'package-open', status: 'Extracting ' + name + '…',
    });
    let pw = o.password || _pw.get(path) || '';
    let d = await start(pw);
    if (d && !d.ok && (d.code === 'need_password' || d.code === 'wrong_password')) {
      if (pw) _pw.delete(path);
      const r = await askPassword({
        title: 'Password required', subtitle: name, icon: 'lock', okLabel: 'Extract', okIcon: 'package-open',
        message: `“${name}” is password-protected. Enter its password to extract it.`,
        error: d.code === 'wrong_password' && pw ? 'Wrong password — try again.' : '',
        submit: p => SFM.zipList(path, p).then(x => (x && x.ok)
          ? (x.password_ok === false ? { ok: false, code: 'wrong_password' } : { ok: true }) : x),
      });
      if (!r) { App.setStatus('Ready'); return null; }
      if (!r.ok) { fail(r, 'Extract failed'); return r; }
      pw = r.password;
      _pw.set(path, pw);
      d = await start(pw);
    }
    if (!d || !d.ok) { if (!o.quiet) fail(d, 'Extract failed'); return d; }
    if (pw) _pw.set(path, pw);
    if (!o.quiet) {
      const where = mode === 'here' ? 'here' : `to “${baseOf(d.out_dir)}”`;
      done(`Extracted ${plural(d.files || 0, 'file')} ${esc(where)}`, d.select || d.out_path);
      if (d.renamed && d.renamed.length) {
        const f = d.renamed[0];
        App.toast(esc(`Some names already existed, so nothing was replaced — e.g. “${f.from}” was saved as “${f.to}”.`), 'info', 7000);
      }
    }
    return d;
  }

  async function extractTo(path) {
    const r = await SFM.call('browse_for_folder');
    if (!r || !r.ok || !r.path) return;
    return extract(path, { dest: r.path });
  }

  async function extractAll(paths) {
    paths = (paths || []).filter(isZip);
    if (!paths.length) { App.toast('Select ZIP files to extract', 'warning'); return; }
    if (paths.length === 1) return extract(paths[0]);
    let ok = 0, files = 0, last = null;
    const errors = [];
    for (const p of paths) {
      const d = await extract(p, { quiet: true });
      if (d && d.ok) { ok++; files += d.files || 0; last = d.out_dir; }
      else if (d) errors.push(baseOf(p) + ': ' + (d.error || 'failed'));
    }
    App.setStatus('Ready');
    if (errors.length) App.toast(esc(`${errors.length} not extracted — ${errors[0]}`), 'error', 8000);
    if (ok) done(`Extracted ${plural(ok, 'ZIP')} (${plural(files, 'file')}) into new folders`, ok === 1 ? last : null);
  }

  // ── View contents… ────────────────────────────────────────────────────────
  const MAX_ROWS = 2000;
  async function viewContents(path) {
    App.setStatus('Reading ZIP…', true);
    const info = await SFM.zipList(path);
    App.setStatus('Ready');
    if (!info || !info.ok) { fail(info, 'Could not read the ZIP'); return; }
    const entries = info.entries || [];
    const saved = info.total_size ? Math.max(0, Math.round(100 - 100 * info.compressed_size / info.total_size)) : 0;
    const lockPill = info.encrypted ? `<span class="pill pill-yellow">${ic('lock', 12)}Password protected${info.aes ? ' · AES' : ''}</span>` : '';
    const rows = entries.slice(0, MAX_ROWS).map(e => {
      const parts = e.name.replace(/\/$/, '').split('/');
      const depth = parts.length - 1;
      const leaf = parts[parts.length - 1] || e.name;
      return `<tr data-i="${e.index}" data-name="${esc(e.name.toLowerCase())}" data-dir="${e.is_dir ? 1 : 0}" data-path="${esc(e.name)}">
        <td class="ax-c-check"><input type="checkbox" aria-label="Select ${esc(leaf)}"></td>
        <td class="ax-c-name"><span class="ax-name" style="--depth:${depth}">${Icons.file({ is_dir: e.is_dir, name: leaf, ext: extOf(leaf) }, 16)}
          <span class="truncate" title="${esc(e.name)}">${esc(leaf)}</span>${e.encrypted ? `<span class="ax-lock" title="Encrypted">${ic('lock', 12)}</span>` : ''}</span></td>
        <td class="ax-c-num">${e.is_dir ? '' : fmtSize(e.size)}</td>
        <td class="ax-c-num ax-hide-sm">${e.is_dir ? '' : fmtSize(e.compressed)}</td>
        <td class="ax-c-date ax-hide-sm">${esc(e.date || '')}</td></tr>`;
    }).join('');
    const body = `
      <div class="ax-summary">
        <span>${ic('files', 14)}<strong>${info.files}</strong> ${info.files === 1 ? 'file' : 'files'}${info.dirs ? ` · ${plural(info.dirs, 'folder')}` : ''}</span>
        <span>${fmtSize(info.total_size)} unpacked${saved > 0 ? ` · ${saved}% smaller zipped` : ''}</span>
        ${lockPill}
      </div>
      <div class="input-group">
        <span class="input-icon">${ic('search', 14)}</span>
        <input class="input-text" id="ax-view-filter" placeholder="Filter by name" autocomplete="off" spellcheck="false">
      </div>
      <div class="ax-table-wrap">
        ${entries.length ? `<table class="ax-table">
          <thead><tr><th class="ax-c-check"><input type="checkbox" id="ax-view-all" aria-label="Select all"></th>
            <th>Name</th><th class="ax-c-num">Size</th><th class="ax-c-num ax-hide-sm">Packed</th><th class="ax-c-date ax-hide-sm">Modified</th></tr></thead>
          <tbody>${rows}</tbody></table>`
        : `<div class="empty-state empty-state-sm"><p class="empty-state-text">This ZIP is empty.</p></div>`}
      </div>
      ${entries.length > MAX_ROWS ? `<div class="field-hint">Showing the first ${MAX_ROWS.toLocaleString()} of ${entries.length.toLocaleString()} entries. “Extract all” extracts everything.</div>` : ''}`;
    const ov = Dialogs.openModal('ax-view', 'ZIP contents', body, [
      { label: 'Close', onClick: () => ov._close() },
      { label: 'Extract selected', icon: 'extract', onClick: () => go(true) },
      { label: 'Extract all', primary: true, icon: 'package-open', onClick: () => go(false) },
    ], { icon: 'file-archive', subtitle: baseOf(path), size: 'lg', cls: 'ax-modal ax-view' });
    const selBtn = footBtn(ov, 'Extract selected');
    const boxes = () => Array.from(ov.querySelectorAll('tbody input[type=checkbox]'));
    const sync = () => {
      const n = boxes().filter(b => b.checked).length;
      selBtn.disabled = !n;
      Dialogs.setBtn(selBtn, n ? `Extract selected (${n})` : 'Extract selected', 'extract');
      ov.querySelectorAll('tbody tr').forEach(tr => tr.classList.toggle('is-selected', tr.querySelector('input').checked));
    };
    sync();
    ov.querySelectorAll('tbody tr').forEach(tr => {
      const cb = tr.querySelector('input');
      cb.addEventListener('change', () => {
        if (tr.dataset.dir === '1') {               // a folder ticks everything inside it
          const pre = tr.dataset.path.replace(/\/?$/, '/');
          ov.querySelectorAll('tbody tr').forEach(r2 => {
            if (r2.dataset.path.startsWith(pre)) r2.querySelector('input').checked = cb.checked;
          });
        }
        sync();
      });
      tr.addEventListener('click', e => { if (e.target.tagName !== 'INPUT') { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); } });
    });
    const all = ov.querySelector('#ax-view-all');
    if (all) all.addEventListener('change', () => {
      ov.querySelectorAll('tbody tr').forEach(tr => { if (!tr.classList.contains('hidden')) tr.querySelector('input').checked = all.checked; });
      sync();
    });
    ov.querySelector('#ax-view-filter').addEventListener('input', e => {
      const q = e.target.value.trim().toLowerCase();
      ov.querySelectorAll('tbody tr').forEach(tr => tr.classList.toggle('hidden', !!q && !tr.dataset.name.includes(q)));
    });
    function go(selectedOnly) {
      let members = null;
      if (selectedOnly) {
        members = Array.from(ov.querySelectorAll('tbody tr')).filter(tr => tr.querySelector('input').checked).map(tr => +tr.dataset.i);
        if (!members.length) return;
      }
      ov._close();
      extract(path, { members });
    }
  }

  // ── ZIP passwords ─────────────────────────────────────────────────────────
  async function zipPassword(path) {
    const name = baseOf(path);
    const [cp, info] = await Promise.all([caps(), SFM.zipList(path)]);
    if (!info || !info.ok) { fail(info, 'Could not read the ZIP'); return; }
    const has = !!info.encrypted;
    const canPw = !!cp.zip_password;
    const outName = stripSuffix(stemOf(path)) + '_protected.zip';
    const body = `
      ${canPw ? '' : `<div class="callout warning ax-callout">${ic('alert-triangle', 16)}<div class="callout-body"><div class="callout-title">Password protection unavailable</div><div>${esc(NO_PYZIPPER)}</div></div></div>`}
      ${has ? pwField('ax-zp-cur', 'Current password', { disabled: !canPw }) : ''}
      <div class="field-grid">
        ${pwField('ax-zp-new', has ? 'New password' : 'Password', { strength: true, disabled: !canPw })}
        ${pwField('ax-zp-new2', 'Confirm password', { disabled: !canPw })}
      </div>
      <div class="field-hint ax-hint">${ic('info', 14)}<span>Saves a protected copy (AES-256) as <strong>${esc(outName)}</strong>. The original ZIP is kept unchanged.</span></div>`;
    const okLabel = has ? 'Change password' : 'Add password';
    const ov = Dialogs.openModal('ax-zp', has ? 'Change ZIP password' : 'Add a password to the ZIP', body, [
      { label: 'Cancel', onClick: () => ov._close() },
      { label: okLabel, primary: true, icon: 'lock', onClick: () => submit() },
    ], { icon: 'lock', subtitle: name, cls: 'ax-modal' });
    wirePw(ov);
    const okBtn = footBtn(ov, okLabel);
    if (!canPw) okBtn.disabled = true;
    else setTimeout(() => ov.querySelector(has ? '#ax-zp-cur' : '#ax-zp-new').focus(), 40);
    onEnter(ov, () => submit());

    async function submit() {
      if (!canPw) return;
      const cur = has ? val(ov, 'ax-zp-cur') : '';
      const pw = val(ov, 'ax-zp-new');
      if (has && !cur) { setErr(ov, 'ax-zp-cur', 'Enter the current password.'); return; }
      if (!pw) { setErr(ov, 'ax-zp-new', 'Enter a password.'); return; }
      if (pw.length < 4) { setErr(ov, 'ax-zp-new', 'Use at least 4 characters.'); return; }
      if (pw !== val(ov, 'ax-zp-new2')) { setErr(ov, 'ax-zp-new2', 'The passwords don’t match.'); return; }
      busy(okBtn, true);
      App.setStatus('Protecting ZIP…', true);
      const r = await SFM.zipSetPassword(path, pw, cur).catch(e => ({ ok: false, error: String(e) }));
      busy(okBtn, false);
      App.setStatus('Ready');
      if (r && (r.code === 'wrong_password' || r.code === 'need_password') && has) {
        setErr(ov, 'ax-zp-cur', 'Wrong password — try again.'); return;
      }
      ov._close();
      if (!r || !r.ok) { fail(r, 'Could not protect the ZIP'); return; }
      done(`Saved <strong>${esc(baseOf(r.out_path))}</strong> — password protected`, r.out_path);
    }
  }

  async function zipRemovePassword(path) {
    const name = baseOf(path);
    const outName = stripSuffix(stemOf(path)) + '_unlocked.zip';
    const r = await askPassword({
      title: 'Remove ZIP password', subtitle: name, icon: 'lock-open', okLabel: 'Remove password', okIcon: 'lock-open',
      message: `Enter the password of “${name}”.`,
      hint: `Saves an unlocked copy as ${outName}. The original stays protected.`,
      submit: p => SFM.zipRemovePassword(path, p),
    });
    if (!r) return;
    if (!r.ok) {
      if (r.code === 'not_encrypted') { App.toast('This ZIP has no password', 'info'); return; }
      fail(r, 'Could not remove the password'); return;
    }
    done(`Saved <strong>${esc(baseOf(r.out_path))}</strong> — no password`, r.out_path);
  }

  // ── PDF passwords ─────────────────────────────────────────────────────────
  const PERMS = [
    ['print', 'Allow printing'],
    ['copy', 'Allow copying text and images'],
    ['edit', 'Allow editing pages'],
    ['annotate', 'Allow comments and form filling'],
  ];
  async function pdfProtect(path) {
    const name = baseOf(path);
    const st = await SFM.pdfIsEncrypted(path).catch(() => null);
    if (st && !st.ok) { fail(st, 'Could not read the PDF'); return; }
    const locked = !!(st && st.needs_password);
    const outName = stripSuffix(stemOf(path)) + '_protected.pdf';
    const body = `
      ${locked ? `<div class="callout info ax-callout">${ic('lock', 16)}<div class="callout-body">This PDF already has a password. Enter it to set a new one.</div></div>
        ${pwField('ax-pp-cur', 'Current password')}` : ''}
      <div class="field-grid">
        ${pwField('ax-pp-user', 'Password to open', { strength: true })}
        ${pwField('ax-pp-user2', 'Confirm password')}
      </div>
      <div class="ax-perms">
        <div class="section-title">Permissions</div>
        <div class="ax-perm-grid">
          ${PERMS.map(([k, label]) => `<label class="check"><input type="checkbox" data-perm="${k}" checked> ${esc(label)}</label>`).join('')}
        </div>
        ${pwField('ax-pp-owner', 'Owner password (optional)', {
          hint: 'Unlocks full rights. If you leave it empty and turn off a permission, a random owner password is used so the restriction really applies.',
        })}
      </div>
      <div class="field-hint ax-hint">${ic('info', 14)}<span>Saves an encrypted copy (AES-256) as <strong>${esc(outName)}</strong>. The original PDF is kept unchanged.</span></div>`;
    const ov = Dialogs.openModal('ax-pp', 'Protect PDF with password', body, [
      { label: 'Cancel', onClick: () => ov._close() },
      { label: 'Protect PDF', primary: true, icon: 'lock', onClick: () => submit() },
    ], { icon: 'lock', subtitle: name, cls: 'ax-modal' });
    wirePw(ov);
    const okBtn = footBtn(ov, 'Protect PDF');
    setTimeout(() => ov.querySelector(locked ? '#ax-pp-cur' : '#ax-pp-user').focus(), 40);
    onEnter(ov, () => submit());

    async function submit() {
      const cur = locked ? val(ov, 'ax-pp-cur') : '';
      const user = val(ov, 'ax-pp-user');
      const owner = val(ov, 'ax-pp-owner');
      if (locked && !cur) { setErr(ov, 'ax-pp-cur', 'Enter the current password.'); return; }
      if (!user) { setErr(ov, 'ax-pp-user', 'Enter a password.'); return; }
      if (user.length < 4) { setErr(ov, 'ax-pp-user', 'Use at least 4 characters.'); return; }
      if (user !== val(ov, 'ax-pp-user2')) { setErr(ov, 'ax-pp-user2', 'The passwords don’t match.'); return; }
      if (owner && owner === user) { setErr(ov, 'ax-pp-owner', 'Use a different owner password, or leave it empty.'); return; }
      const perms = {};
      ov.querySelectorAll('[data-perm]').forEach(c => { perms[c.dataset.perm] = c.checked; });
      busy(okBtn, true);
      App.setStatus('Protecting PDF…', true);
      const r = await SFM.pdfSetPassword(path, user, owner, perms, cur).catch(e => ({ ok: false, error: String(e) }));
      busy(okBtn, false);
      App.setStatus('Ready');
      if (r && (r.code === 'wrong_password' || r.code === 'need_password') && locked) {
        setErr(ov, 'ax-pp-cur', 'Wrong password — try again.'); return;
      }
      ov._close();
      if (!r || !r.ok) { fail(r, 'Could not protect the PDF'); return; }
      done(`Saved <strong>${esc(baseOf(r.out_path))}</strong> — password protected`, r.out_path);
    }
  }

  async function pdfRemovePassword(path) {
    const name = baseOf(path);
    const st = await SFM.pdfIsEncrypted(path).catch(() => null);
    if (st && st.ok && !st.encrypted) { App.toast('This PDF has no password', 'info'); return; }
    const restrictOnly = st && st.ok && !st.needs_password;
    const outName = stripSuffix(stemOf(path)) + '_unlocked.pdf';
    const r = await askPassword({
      title: 'Remove PDF password', subtitle: name, icon: 'lock-open', okLabel: 'Remove password', okIcon: 'lock-open',
      label: restrictOnly ? 'Owner password' : 'Password',
      message: restrictOnly ? `“${name}” opens without a password but has restrictions. Enter its owner password to remove them.`
                            : `Enter the password of “${name}”.`,
      hint: `Saves an unlocked copy as ${outName}. The original stays protected.`,
      submit: p => SFM.pdfRemovePassword(path, p),
    });
    if (!r) return;
    if (!r.ok) {
      if (r.code === 'not_encrypted') { App.toast('This PDF has no password', 'info'); return; }
      fail(r, 'Could not remove the password'); return;
    }
    done(`Saved <strong>${esc(baseOf(r.out_path))}</strong> — no password`, r.out_path);
  }

  // Preview-only unlock (the file is not changed). → true when the PDF can be shown.
  async function ensurePdfUnlocked(path) {
    const st = await SFM.pdfPreviewState(path).catch(() => null);
    if (!st || !st.ok || !st.locked) { _locked.delete(path); return true; }
    _locked.add(path);
    const r = await askPassword({
      title: 'Password-protected PDF', subtitle: baseOf(path), icon: 'lock', okLabel: 'Open', okIcon: 'lock-open',
      message: `Enter the password to view “${baseOf(path)}”.`,
      hint: 'The file itself is not changed. Use “Remove password…” to save an unlocked copy.',
      submit: p => SFM.pdfUnlockPreview(path, p),
    });
    if (r && r.ok) { _locked.delete(path); return true; }
    if (r && !r.ok) fail(r);
    return false;
  }

  // ── hooks: context menu ───────────────────────────────────────────────────
  // ctx: { entries, paths, item(menu, icon, label, shortcut, fn), sep(menu), hide() }
  function menuItems(menu, ctx) {
    const { entries, item, sep, hide } = ctx;
    if (!entries || !entries.length) return;
    const paths = entries.map(e => e.path);
    const single = entries.length === 1 ? entries[0] : null;
    const zips = entries.filter(e => !e.is_dir && isZip(e.path));
    const allZip = zips.length === entries.length;
    const run = fn => () => { hide(); fn(); };
    const last = () => menu.lastElementChild;

    if (allZip && single) {
      const p = single.path;
      item(menu, 'package-open', 'Extract here', '', run(() => extract(p, { mode: 'here' })));
      item(menu, 'folder-archive', `Extract to “${stemOf(p)}/”`, 'Ctrl+Shift+E', run(() => extract(p)));
      item(menu, 'folder-input', 'Extract to…', '', run(() => extractTo(p)));
      item(menu, 'list', 'View contents…', '', run(() => viewContents(p)));
      item(menu, 'lock', 'Add password…', '', run(() => zipPassword(p)));
      const addEl = last();
      item(menu, 'lock-open', 'Remove password…', '', run(() => zipRemovePassword(p)));
      const remEl = last();
      remEl.classList.add('hidden');
      SFM.zipList(p).then(r => {
        if (!r || !r.ok || !r.encrypted) return;
        const lab = addEl.querySelector('.ctx-label');
        if (lab) lab.textContent = 'Change password…';
        remEl.classList.remove('hidden');
      }).catch(() => {});
      sep(menu);
      return;
    }
    if (allZip) {
      item(menu, 'package-open', `Extract all (${zips.length})`, 'Ctrl+Shift+E', run(() => extractAll(zips.map(z => z.path))));
      sep(menu);
      return;
    }
    item(menu, 'package', 'Compress to ZIP…', 'Ctrl+Shift+Z', run(() => openZip(paths)));
    item(menu, 'archive', 'Quick zip', '', run(() => quickZip(paths)));
    if (single && single.is_dir) {
      item(menu, 'folder-archive', 'Zip each sub-folder…', '', run(() => zipEachSubfolder(single.path)));
    } else if (entries.length > 1 && entries.every(e => e.is_dir)) {
      item(menu, 'folder-archive', `Zip each separately (${entries.length})`, '', run(() => zipEach(paths)));
    }
    if (single && !single.is_dir && isPdf(single.path)) {
      const p = single.path;
      item(menu, 'lock', 'Protect with password…', '', run(() => pdfProtect(p)));
      item(menu, 'lock-open', 'Remove password…', '', run(() => pdfRemovePassword(p)));
      const remEl = last();
      remEl.classList.add('hidden');
      SFM.pdfPreviewState(p).then(r => { if (r && r.ok && r.encrypted) remEl.classList.remove('hidden'); }).catch(() => {});
    }
    sep(menu);
  }

  // ── hooks: details pane ───────────────────────────────────────────────────
  let _detTok = 0;
  function _section() {
    let sec = document.getElementById('archive-section');
    if (sec) return sec;
    sec = document.createElement('div');
    sec.className = 'detail-section hidden';
    sec.id = 'archive-section';
    const after = document.getElementById('pdf-tools-section') || document.getElementById('quick-actions');
    if (after && after.parentNode) after.parentNode.insertBefore(sec, after.nextSibling);
    else (document.getElementById('details-content') || document.body).appendChild(sec);
    return sec;
  }
  const qa = (act, icon, label, extra = '') =>
    `<button class="qa-btn${extra}" data-ax="${act}"><span class="icon">${ic(icon, 16)}</span> ${esc(label)}</button>`;
  function _addRow(label, value) {
    const c = document.getElementById('detail-rows');
    if (!c) return;
    const row = document.createElement('div');
    row.className = 'detail-row ax-row';
    row.innerHTML = `<span class="detail-label">${esc(label)}</span><span class="detail-value">${value}</span>`;
    c.appendChild(row);
  }

  function onDetails(entry, multi) {
    const tok = ++_detTok;
    _updateToolbar();
    const sec = _section();
    const render = (title, html, pill) => {
      sec.innerHTML = `<div class="detail-section-title">${esc(title)}${pill || ''}</div><div class="qa-list">${html}</div>`;
      sec.classList.remove('hidden');
    };
    sec.classList.add('hidden');
    sec.onclick = null;
    const bind = map => {
      sec.onclick = e => {
        const b = e.target.closest('[data-ax]');
        if (b && map[b.dataset.ax]) map[b.dataset.ax]();
      };
    };

    if (!entry && (!multi || !multi.length)) return;
    if (!entry) {                                       // multi-select
      const paths = multi.map(x => typeof x === 'string' ? x : x.path).filter(Boolean);
      const zips = paths.filter(isZip);
      if (zips.length === paths.length) {
        render('Archive', qa('xall', 'package-open', `Extract all (${zips.length})…`, ' primary'));
        bind({ xall: () => extractAll(zips) });
      } else {
        render('Archive', qa('zip', 'package', `Compress ${paths.length} items to ZIP…`)
          + (multi.every(x => x.is_dir) ? qa('each', 'folder-archive', 'Zip each separately') : ''));
        bind({ zip: () => openZip(paths), each: () => zipEach(paths) });
      }
      return;
    }

    const p = entry.path;
    if (!entry.is_dir && isZip(p)) {
      SFM.zipList(p).then(info => {
        if (tok !== _detTok || !info || !info.ok) return;
        const saved = info.total_size ? Math.max(0, Math.round(100 - 100 * info.compressed_size / info.total_size)) : 0;
        _addRow('Contents', esc(`${plural(info.files, 'file')}${info.dirs ? ', ' + plural(info.dirs, 'folder') : ''}`));
        _addRow('Unpacked', esc(fmtSize(info.total_size) + (saved > 0 ? ` (${saved}% smaller zipped)` : '')));
        const pill = info.encrypted ? `<span class="pill pill-yellow ax-pill">${ic('lock', 12)}Password protected</span>` : '';
        render('Archive',
          qa('here', 'package-open', 'Extract here', ' primary')
          + qa('folder', 'folder-archive', `Extract to “${stemOf(p)}”`)
          + qa('to', 'folder-input', 'Extract to…')
          + qa('view', 'list', 'View contents…')
          + qa('pw', 'lock', info.encrypted ? 'Change password…' : 'Add password…')
          + (info.encrypted ? qa('unpw', 'lock-open', 'Remove password…') : ''), pill);
        bind({
          here: () => extract(p, { mode: 'here' }), folder: () => extract(p), to: () => extractTo(p),
          view: () => viewContents(p), pw: () => zipPassword(p), unpw: () => zipRemovePassword(p),
        });
      }).catch(() => {});
      return;
    }
    if (!entry.is_dir && isPdf(p)) {
      SFM.pdfPreviewState(p).then(st => {
        if (tok !== _detTok || !st || !st.ok) return;
        const zipBtn = qa('zip', 'package', 'Compress to ZIP…');
        if (st.encrypted) {
          _addRow('Security', esc(st.needs_password ? 'Password to open' : 'Restrictions (owner password)'));
          render('Security', qa('unpw', 'lock-open', 'Remove password…') + qa('pw', 'lock', 'Change password…') + zipBtn,
            `<span class="pill pill-yellow ax-pill">${ic('lock', 12)}Password protected</span>`);
        } else {
          render('Security', qa('pw', 'lock', 'Protect with password…') + zipBtn);
        }
        bind({ pw: () => pdfProtect(p), unpw: () => pdfRemovePassword(p), zip: () => openZip([p]) });
      }).catch(() => {});
      return;
    }
    render('Archive', qa('zip', 'package', 'Compress to ZIP…')
      + (entry.is_dir ? qa('each', 'folder-archive', 'Zip each sub-folder…') : ''));
    bind({ zip: () => openZip([p]), each: () => zipEachSubfolder(p) });
  }

  // ── hooks: preview pane ───────────────────────────────────────────────────
  // api: { el (empty-state element), show() (show only it), openPdf(), isCurrent() }
  async function previewHook(path, ext, api) {
    if (ext === '.zip') {
      api.show();
      api.el.innerHTML = `<div class="ax-pv"><div class="loading-spinner"></div></div>`;
      const info = await SFM.zipList(path).catch(e => ({ ok: false, error: String(e) }));
      if (!api.isCurrent()) return true;
      if (!info || !info.ok) {
        api.el.innerHTML = `<div class="empty-state-icon">${ic('alert-triangle', 28)}</div>
          <p class="empty-state-title">${esc(baseOf(path))}</p><p class="empty-state-text">${esc((info && info.error) || 'Could not read the ZIP')}</p>`;
        return true;
      }
      const list = (info.entries || []).filter(e => !e.is_dir).slice(0, 200).map(e =>
        `<li>${Icons.file({ name: e.name, ext: extOf(e.name) }, 14)}<span class="truncate" title="${esc(e.name)}">${esc(e.name)}</span>`
        + `${e.encrypted ? `<span class="ax-lock">${ic('lock', 12)}</span>` : ''}<span class="ax-pv-size">${fmtSize(e.size)}</span></li>`).join('');
      const more = info.files > 200 ? `<li class="ax-pv-more">+ ${(info.files - 200).toLocaleString()} more…</li>` : '';
      api.el.innerHTML = `
        <div class="ax-pv">
          <div class="ax-pv-head">
            <span class="ax-pv-icon">${ic('file-archive', 24)}</span>
            <div class="ax-pv-title"><strong class="truncate">${esc(baseOf(path))}</strong>
              <span>${esc(plural(info.files, 'file'))} · ${esc(fmtSize(info.total_size))} unpacked</span></div>
            ${info.encrypted ? `<span class="pill pill-yellow">${ic('lock', 12)}Password protected</span>` : ''}
          </div>
          <ul class="ax-pv-list">${list || '<li class="ax-pv-more">This ZIP is empty.</li>'}${more}</ul>
          <div class="ax-pv-actions">
            <button class="btn" data-ax="view">${ic('list', 16)}View contents…</button>
            <button class="btn btn-primary" data-ax="x">${ic('package-open', 16)}Extract</button>
          </div>
        </div>`;
      api.el.querySelector('[data-ax="view"]').addEventListener('click', () => viewContents(path));
      api.el.querySelector('[data-ax="x"]').addEventListener('click', () => extract(path));
      return true;
    }
    if (ext === '.pdf') {
      const st = await SFM.pdfPreviewState(path).catch(() => null);
      if (!api.isCurrent()) return true;
      if (!st || !st.ok || !st.locked) { _locked.delete(path); return false; }
      _locked.add(path);
      api.show();
      api.el.innerHTML = `
        <div class="ax-locked">
          <div class="empty-state-icon">${ic('lock', 28)}</div>
          <p class="empty-state-title">${esc(baseOf(path))}</p>
          <p class="empty-state-text">This PDF is password-protected.</p>
          <div class="ax-pv-actions">
            <button class="btn" data-ax="rm">${ic('lock-open', 16)}Remove password…</button>
            <button class="btn btn-primary" data-ax="unlock">${ic('key', 16)}Enter password…</button>
          </div>
        </div>`;
      api.el.querySelector('[data-ax="unlock"]').addEventListener('click', async () => {
        if (await ensurePdfUnlocked(path) && api.isCurrent()) api.openPdf();
      });
      api.el.querySelector('[data-ax="rm"]').addEventListener('click', () => pdfRemovePassword(path));
      return true;
    }
    return false;
  }

  // ── hooks: double-click in the file list ──────────────────────────────────
  // → true when handled (ZIP → View contents; locked PDF → ask, then full view).
  function onOpen(entry) {
    if (!entry || entry.is_dir) return false;
    if (isZip(entry.path)) { viewContents(entry.path); return true; }
    if (isPdf(entry.path) && _locked.has(entry.path)) {
      ensurePdfUnlocked(entry.path).then(ok => {
        if (!ok) return;
        Preview.clear && Preview.clear();
        Preview.previewFile(entry.path, '.pdf');
        Dialogs.openFullView(entry.path, '.pdf');
      });
      return true;
    }
    return false;
  }

  // ── toolbar + keyboard ────────────────────────────────────────────────────
  function _updateToolbar() {
    const b = document.getElementById('btn-unzip');
    if (!b) return;
    const sel = selection();
    b.classList.toggle('hidden', !(sel.length && sel.every(isZip)));
  }
  function zipSelection() {
    const sel = selection();
    if (!sel.length) { App.toast('Select files or folders to zip', 'warning'); return; }
    openZip(sel);
  }
  function extractSelection() {
    const zips = selection().filter(isZip);
    if (!zips.length) { App.toast('Select a ZIP file to extract', 'warning'); return; }
    zips.length === 1 ? extract(zips[0]) : extractAll(zips);
  }
  function _init() {
    const wire = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('click', fn); };
    wire('btn-zip', zipSelection);
    wire('btn-unzip', extractSelection);
    document.addEventListener('keydown', e => {
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (!(e.ctrlKey || e.metaKey) || !e.shiftKey || e.altKey) return;
      if (document.querySelector('.modal-overlay')) return;
      const k = (e.key || '').toLowerCase();
      if (k === 'z') { e.preventDefault(); e.stopPropagation(); zipSelection(); }
      else if (k === 'e') { e.preventDefault(); e.stopPropagation(); extractSelection(); }
    }, true);
    _updateToolbar();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();

  return {
    openZip, quickZip, zipEach, zipEachSubfolder, zipSelection,
    extract, extractTo, extractAll, extractSelection, viewContents,
    zipPassword, zipRemovePassword, pdfProtect, pdfRemovePassword, ensurePdfUnlocked,
    askPassword, capabilities: caps,
    menuItems, onDetails, previewHook, onOpen,
  };
})();
