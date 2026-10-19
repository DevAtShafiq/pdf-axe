/**
 * recorder.js — screen recording + screenshots.
 *
 *   Recorder.open()          setup dialog (source, audio, webcam, quality, folder,
 *                            recent recordings, screenshots)
 *   Recorder.toggle()        Ctrl+Shift+R: start with the last settings / stop
 *   Recorder.screenshot(m)   'full' | 'region' → PNG in the recordings folder
 *
 * Capture runs in the page: getDisplayMedia (screen / window / this app, with
 * optional system sound) + getUserMedia (microphone, webcam) → MediaRecorder
 * (MP4/H.264 when supported, else WebM). Mic + system sound are mixed with Web
 * Audio. The webcam bubble is composited frame by frame with
 * MediaStreamTrackProcessor/Generator (keeps working while the app is
 * minimised — no timers involved).
 *
 * The encoded data is streamed to Python about once a second (rec_chunk) into a
 * .part file that is renamed on stop (recorder_bridge.py) — long recordings
 * never sit in memory. Ctrl+Shift+R is a system-wide hotkey while recording.
 */
const Recorder = (() => {
  const LS_KEY = 'oxRecPrefs';
  const LS_BAR = 'oxRecBarPos';
  const APP_TITLE = 'Office Axe';
  const FORMATS = [
    { ext: 'mp4',  label: 'MP4',  codec: 'H.264', mime: 'video/mp4;codecs=avc1.42E01E,mp4a.40.2' },
    { ext: 'mp4',  label: 'MP4',  codec: 'H.264', mime: 'video/mp4' },
    { ext: 'webm', label: 'WebM', codec: 'VP9',   mime: 'video/webm;codecs=vp9,opus' },
    { ext: 'webm', label: 'WebM', codec: 'VP8',   mime: 'video/webm;codecs=vp8,opus' },
    { ext: 'webm', label: 'WebM', codec: 'VP8',   mime: 'video/webm' },
  ];
  const QUALITY = { '720': [1280, 720], '1080': [1920, 1080], 'orig': null };
  const DEFAULTS = { source: 'screen', audio: 'mic', micId: '', webcam: false, camId: '',
                     corner: 'br', quality: '1080', fps: 30, countdown: true, format: '',
                     minimize: false, hideForShot: true };

  let _rec = null;          // active recording session
  let _setup = null;        // open setup dialog state

  const _esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const _base = p => String(p || '').split(/[\\/]/).pop();
  const _dir = p => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const _norm = p => String(p || '').replace(/[\\/]+/g, '/').replace(/\/+$/, '').toLowerCase();
  const _call = (m, ...a) => SFM.call(m, ...a).catch(e => ({ ok: false, error: String(e && e.message || e) }));

  function _fmtSize(n) {
    n = Number(n) || 0;
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return i ? n.toFixed(1) + ' ' + u[i] : n + ' B';
  }
  function _fmtTime(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = s % 60;
    const p = v => String(v).padStart(2, '0');
    return h ? `${h}:${p(m)}:${p(ss)}` : `${p(m)}:${p(ss)}`;
  }
  function _ago(t) {
    const d = (Date.now() / 1000) - t;
    if (d < 60) return 'just now';
    if (d < 3600) return Math.floor(d / 60) + ' min ago';
    if (d < 86400) return Math.floor(d / 3600) + ' h ago';
    return new Date(t * 1000).toLocaleDateString();
  }

  // ── prefs ────────────────────────────────────────────────────────────────
  function _prefs() {
    let p = {};
    try { p = JSON.parse(localStorage.getItem(LS_KEY) || '{}') || {}; } catch (_) { p = {}; }
    return { ...DEFAULTS, ...p };
  }
  function _savePrefs(p) { try { localStorage.setItem(LS_KEY, JSON.stringify(p)); } catch (_) {} }

  // ── capabilities ─────────────────────────────────────────────────────────
  function _supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia && window.MediaRecorder);
  }
  function _formats() {
    const out = { mp4: null, webm: null };
    if (!window.MediaRecorder) return out;
    for (const f of FORMATS) {
      try { if (!out[f.ext] && MediaRecorder.isTypeSupported(f.mime)) out[f.ext] = f; } catch (_) {}
    }
    return out;
  }
  function _pickFormat(pref) {
    const f = _formats();
    return (pref && f[pref]) || f.mp4 || f.webm || null;
  }
  const _canComposite = () => typeof window.MediaStreamTrackProcessor === 'function'
    && typeof window.MediaStreamTrackGenerator === 'function' && typeof window.OffscreenCanvas === 'function';

  async function _devices(kind) {
    try {
      return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === kind);
    } catch (_) { return []; }
  }

  // ── Setup dialog ─────────────────────────────────────────────────────────
  function _seg(name, opts, cur) {
    return `<div class="segmented segmented-block" data-seg="${name}" role="radiogroup">${opts.map(([v, label, icon]) =>
      `<button type="button" class="seg-btn ${String(v) === String(cur) ? 'active' : ''}" data-val="${v}">${icon ? Icons.svg(icon, 14) : ''}${label}</button>`).join('')}</div>`;
  }
  function _card(name, value, cur, icon, title, desc) {
    return `<label class="option-card"><input type="radio" name="${name}" value="${value}" ${value === cur ? 'checked' : ''}>
      <span class="option-card-icon">${Icons.svg(icon, 16)}</span>
      <span class="option-card-body"><span class="option-card-title">${title}</span>
        <span class="option-card-desc">${desc}</span></span>
      <span class="option-card-check">${Icons.svg('check', 10)}</span></label>`;
  }

  async function open() {
    if (_rec) { _focusBar(); return; }
    if (_setup) return;
    if (!_supported()) {
      App.toast('Screen recording is not supported by this version of the browser engine (WebView2). Update Microsoft Edge WebView2 Runtime.', 'error', 7000);
      return;
    }
    const p = _prefs();
    const fmts = _formats();
    const fmt = _pickFormat(p.format);
    const comp = _canComposite();
    const id = Dialogs.modal('recorder', {
      title: 'Screen recorder',
      icon: 'video',
      subtitle: 'Record your screen, a window or this app — or take a screenshot',
      width: '640px',
      cls: 'rec-modal',
      body: `
        <div class="field">
          <span class="field-label">What to record</span>
          <div class="option-cards rec-cards-3">
            ${_card('rec-src-__ID__', 'screen', p.source, 'monitor', 'Entire screen', 'A whole monitor')}
            ${_card('rec-src-__ID__', 'window', p.source, 'app-window', 'A window', 'One app window')}
            ${_card('rec-src-__ID__', 'app', p.source, 'sidebar', 'This app', 'Office Axe only')}
          </div>
          <div class="field-hint">You pick the exact screen or window in the next step.</div>
        </div>

        <div class="field-grid">
          <div class="field">
            <span class="field-label">Audio</span>
            ${_seg('audio', [['none', 'None'], ['mic', 'Mic'], ['system', 'System'], ['both', 'Both']], p.audio)}
          </div>
          <div class="field">
            <span class="field-label">Quality</span>
            <div class="rec-row">
              ${_seg('quality', [['720', '720p'], ['1080', '1080p'], ['orig', 'Original']], p.quality)}
              ${_seg('fps', [[30, '30 fps'], [15, '15 fps']], p.fps)}
            </div>
          </div>
        </div>

        <div class="field rec-mic-field" id="rec-micf-__ID__">
          <label class="field-label" for="rec-mic-__ID__">Microphone</label>
          <div class="field-row">
            <select id="rec-mic-__ID__" class="input-text"><option value="">Default microphone</option></select>
            <div class="rec-meter" title="Microphone level" aria-label="Microphone level"><div class="rec-meter-bar" id="rec-meter-__ID__"></div></div>
          </div>
          <div class="field-hint" id="rec-michint-__ID__">Speak to test the level.</div>
        </div>

        <div class="field">
          <div class="rec-row rec-row-between">
            <label class="switch" title="${comp ? '' : 'Not supported by this WebView2 version'}">
              <input type="checkbox" id="rec-cam-__ID__" ${p.webcam && comp ? 'checked' : ''} ${comp ? '' : 'disabled'}>
              <span class="switch-track"></span>Webcam bubble</label>
            <div class="rec-cam-opts" id="rec-camopts-__ID__">
              <select id="rec-camsel-__ID__" class="input-text rec-cam-select"><option value="">Default camera</option></select>
              <select id="rec-corner-__ID__" class="input-text rec-corner-select" aria-label="Webcam position">
                ${[['br', 'Bottom right'], ['bl', 'Bottom left'], ['tr', 'Top right'], ['tl', 'Top left']].map(([v, n]) =>
                  `<option value="${v}" ${p.corner === v ? 'selected' : ''}>${n}</option>`).join('')}
              </select>
            </div>
          </div>
          <div class="rec-cam-preview hidden" id="rec-campv-__ID__"><video muted playsinline autoplay></video></div>
        </div>

        <div class="field-grid">
          <div class="field">
            <span class="field-label">Format</span>
            <div class="segmented segmented-block" data-seg="format">
              <button type="button" class="seg-btn ${fmt && fmt.ext === 'mp4' ? 'active' : ''}" data-val="mp4" ${fmts.mp4 ? '' : 'disabled title="Not supported here"'}>MP4</button>
              <button type="button" class="seg-btn ${fmt && fmt.ext === 'webm' ? 'active' : ''}" data-val="webm" ${fmts.webm ? '' : 'disabled title="Not supported here"'}>WebM</button>
            </div>
            <div class="field-hint" id="rec-fmthint-__ID__"></div>
          </div>
          <div class="field">
            <span class="field-label">Options</span>
            <label class="switch"><input type="checkbox" id="rec-cd-__ID__" ${p.countdown ? 'checked' : ''}><span class="switch-track"></span>3-second countdown</label>
            <label class="switch"><input type="checkbox" id="rec-min-__ID__" ${p.minimize ? 'checked' : ''}><span class="switch-track"></span>Minimise Office Axe while recording</label>
          </div>
        </div>

        <div class="field">
          <label class="field-label" for="rec-dir-__ID__">Save to</label>
          <div class="field-row">
            <input id="rec-dir-__ID__" class="input-text" readonly>
            <button class="btn" id="rec-browse-__ID__">Browse&#x2026;</button>
            <button class="icon-btn" id="rec-opendir-__ID__" title="Open folder" aria-label="Open folder">${Icons.svg('folder-open', 16)}</button>
          </div>
        </div>

        <div class="rec-shot card">
          <div class="rec-shot-text">
            <span class="card-title">${Icons.svg('image', 16)}Screenshot</span>
            <label class="switch"><input type="checkbox" id="rec-hide-__ID__" ${p.hideForShot ? 'checked' : ''}><span class="switch-track"></span>Hide Office Axe while capturing</label>
          </div>
          <div class="rec-row">
            <button class="btn" id="rec-shotfull-__ID__">${Icons.svg('monitor', 16)}Full screen</button>
            <button class="btn" id="rec-shotreg-__ID__">${Icons.svg('crop', 16)}Region&#x2026;</button>
          </div>
        </div>

        <div class="field">
          <div class="section-head"><span class="section-title">Recent recordings</span>
            <button class="btn btn-sm btn-ghost" id="rec-refresh-__ID__">${Icons.svg('refresh', 14)}Refresh</button></div>
          <div class="rec-list" id="rec-list-__ID__"><div class="empty-state-sm text-muted">Loading&#x2026;</div></div>
        </div>`,
      footer: `<span class="footer-left text-muted text-sm rec-kbd"><kbd class="kbd">Ctrl</kbd>+<kbd class="kbd">Shift</kbd>+<kbd class="kbd">R</kbd> start / stop</span>
               <button class="btn" id="rec-close-__ID__">Close</button>
               <button class="btn btn-primary" id="rec-start-__ID__">${Icons.svg('record', 16)}Start recording</button>`,
    });
    const root = document.getElementById('mo-' + id);
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
    root.querySelectorAll('[name*="__ID__"]').forEach(el => { el.name = el.name.replace('__ID__', id); });
    const $ = s => document.getElementById(s + '-' + id);
    const st = _setup = { id, root, prefs: { ...p, format: fmt ? fmt.ext : '' }, micStream: null, camStream: null,
                          meterRaf: 0, audioCtx: null, dir: '' };

    // segmented controls
    root.querySelectorAll('[data-seg]').forEach(seg => seg.addEventListener('click', e => {
      const b = e.target.closest('.seg-btn');
      if (!b || b.disabled) return;
      seg.querySelectorAll('.seg-btn').forEach(x => x.classList.toggle('active', x === b));
      const k = seg.dataset.seg;
      st.prefs[k] = k === 'fps' ? Number(b.dataset.val) : b.dataset.val;
      _syncSetup();
    }));
    root.querySelectorAll(`input[name="rec-src-${id}"]`).forEach(r =>
      r.addEventListener('change', () => { st.prefs.source = r.value; _syncSetup(); }));
    $('rec-mic').addEventListener('change', () => { st.prefs.micId = $('rec-mic').value; _startMicPreview(); });
    $('rec-cam').addEventListener('change', () => { st.prefs.webcam = $('rec-cam').checked; _syncSetup(); });
    $('rec-camsel').addEventListener('change', () => { st.prefs.camId = $('rec-camsel').value; _startCamPreview(); });
    $('rec-corner').addEventListener('change', () => {
      st.prefs.corner = $('rec-corner').value;
      const pv = $('rec-campv'); if (pv) pv.dataset.corner = st.prefs.corner;
    });
    $('rec-cd').addEventListener('change', () => { st.prefs.countdown = $('rec-cd').checked; });
    $('rec-min').addEventListener('change', () => { st.prefs.minimize = $('rec-min').checked; });
    $('rec-hide').addEventListener('change', () => { st.prefs.hideForShot = $('rec-hide').checked; _savePrefs(st.prefs); });
    $('rec-close').addEventListener('click', () => Dialogs.closeModal(id));
    $('rec-start').addEventListener('click', () => { _savePrefs(st.prefs); start(st.prefs, id); });
    $('rec-browse').addEventListener('click', async () => {
      const r = await _call('browse_for_folder');
      if (!r || !r.ok || !r.path) return;
      const s = await _call('rec_set_dir', r.path);
      if (s && s.ok) { _showDir(s.path); _loadList(); } else App.toast(_esc((s && s.error) || 'Cannot use that folder'), 'error');
    });
    $('rec-opendir').addEventListener('click', () => st.dir && _call('open_folder_in_explorer', st.dir));
    $('rec-refresh').addEventListener('click', _loadList);
    $('rec-shotfull').addEventListener('click', () => screenshot('full'));
    $('rec-shotreg').addEventListener('click', () => screenshot('region'));

    // cleanup when the dialog goes away (Esc, ×, Close, Start)
    const mo = new MutationObserver(() => {
      if (!document.body.contains(root)) { mo.disconnect(); _teardownSetup(st); }
    });
    mo.observe(document.body, { childList: true });

    _syncSetup();
    const d = await _call('rec_default_dir');
    if (d && d.ok) _showDir(d.path);
    _loadList();
    _fillDevices();
  }

  function _showDir(path) {
    const st = _setup; if (!st) return;
    st.dir = path;
    const inp = document.getElementById('rec-dir-' + st.id);
    if (inp) { inp.value = path; inp.title = path; }
  }

  function _syncSetup() {
    const st = _setup; if (!st) return;
    const $ = s => document.getElementById(s + '-' + st.id);
    const p = st.prefs;
    const wantMic = p.audio === 'mic' || p.audio === 'both';
    $('rec-micf').classList.toggle('hidden', !wantMic);
    if (!wantMic) _stopMicPreview();
    else if (!st.micStream && !st.micGen) _startMicPreview();
    $('rec-camopts').classList.toggle('is-dim', !p.webcam);
    $('rec-camsel').disabled = !p.webcam;
    $('rec-corner').disabled = !p.webcam;
    if (!p.webcam) _stopCamPreview();
    else if (!st.camStream && !st.camGen) _startCamPreview();
    const f = _pickFormat(p.format);
    const hint = $('rec-fmthint');
    if (hint) hint.textContent = f ? `${f.label} (${f.codec}${f.mime.includes('opus') || f.mime.includes('mp4a') ? ' + audio' : ''})${f.ext === 'mp4' ? ' — plays everywhere' : ' — plays in browsers and VLC'}` : 'No supported format';
    const sys = p.audio === 'system' || p.audio === 'both';
    const mh = $('rec-michint');
    if (mh) mh.textContent = sys ? 'System sound is captured from the screen you choose (not available for single windows).' : 'Speak to test the level.';
  }

  async function _fillDevices() {
    const st = _setup; if (!st) return;
    const fill = async (selId, kind, cur, def) => {
      const sel = document.getElementById(selId + '-' + st.id);
      if (!sel) return;
      const list = await _devices(kind);
      sel.innerHTML = `<option value="">${def}</option>` + list.filter(d => d.deviceId && d.deviceId !== 'default' && d.deviceId !== 'communications')
        .map((d, i) => `<option value="${_esc(d.deviceId)}" ${d.deviceId === cur ? 'selected' : ''}>${_esc(d.label || (kind === 'audioinput' ? 'Microphone ' : 'Camera ') + (i + 1))}</option>`).join('');
    };
    await fill('rec-mic', 'audioinput', st.prefs.micId, 'Default microphone');
    await fill('rec-camsel', 'videoinput', st.prefs.camId, 'Default camera');
  }

  async function _startMicPreview() {
    const st = _setup; if (!st) return;
    _stopMicPreview();
    const myGen = st.micGen = (st.seq = (st.seq || 0) + 1);
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: st.prefs.micId ? { deviceId: { exact: st.prefs.micId } } : true });
      if (_setup !== st || st.micGen !== myGen) { s.getTracks().forEach(t => t.stop()); return; }
      st.micStream = s;
      _fillDevices();
      const ctx = st.audioCtx || (st.audioCtx = new AudioContext());
      const an = ctx.createAnalyser(); an.fftSize = 512;
      st.micNode = ctx.createMediaStreamSource(s); st.micNode.connect(an);
      const buf = new Uint8Array(an.fftSize);
      const bar = document.getElementById('rec-meter-' + st.id);
      const tick = () => {
        if (_setup !== st || st.micGen !== myGen) return;
        an.getByteTimeDomainData(buf);
        let peak = 0;
        for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128));
        if (bar) bar.style.width = Math.min(100, Math.round(peak / 128 * 160)) + '%';
        st.meterRaf = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) {
      const h = document.getElementById('rec-michint-' + st.id);
      if (h) h.textContent = 'Microphone not available: ' + (e && e.message || e);
    }
  }
  function _stopMicPreview() {
    const st = _setup; if (!st) return;
    cancelAnimationFrame(st.meterRaf);
    try { st.micNode && st.micNode.disconnect(); } catch (_) {}
    if (st.micStream) st.micStream.getTracks().forEach(t => t.stop());
    st.micStream = null; st.micNode = null; st.micGen = 0;
    const bar = document.getElementById('rec-meter-' + st.id);
    if (bar) bar.style.width = '0%';
  }
  async function _startCamPreview() {
    const st = _setup; if (!st) return;
    _stopCamPreview();
    const myGen = st.camGen = (st.seq = (st.seq || 0) + 1);
    const box = document.getElementById('rec-campv-' + st.id);
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { ...(st.prefs.camId ? { deviceId: { exact: st.prefs.camId } } : {}), width: { ideal: 480 }, height: { ideal: 480 } } });
      if (_setup !== st || st.camGen !== myGen) { s.getTracks().forEach(t => t.stop()); return; }
      st.camStream = s;
      _fillDevices();
      if (box) { box.querySelector('video').srcObject = s; box.classList.remove('hidden'); box.dataset.corner = st.prefs.corner; }
    } catch (e) {
      App.toast('Webcam not available: ' + _esc(e && e.message || e), 'warning', 4000);
      const cb = document.getElementById('rec-cam-' + st.id);
      if (cb) cb.checked = false;
      st.prefs.webcam = false;
      document.getElementById('rec-camopts-' + st.id)?.classList.add('is-dim');
    }
  }
  function _stopCamPreview() {
    const st = _setup; if (!st) return;
    if (st.camStream) st.camStream.getTracks().forEach(t => t.stop());
    st.camStream = null; st.camGen = 0;
    const box = document.getElementById('rec-campv-' + st.id);
    if (box) { box.classList.add('hidden'); const v = box.querySelector('video'); if (v) v.srcObject = null; }
  }
  function _teardownSetup(st) {
    if (_setup !== st) return;
    _stopMicPreview(); _stopCamPreview();
    try { st.audioCtx && st.audioCtx.close(); } catch (_) {}
    _setup = null;
  }

  // ── recent list ──────────────────────────────────────────────────────────
  async function _loadList() {
    const st = _setup; if (!st) return;
    const box = document.getElementById('rec-list-' + st.id);
    if (!box) return;
    const r = await _call('rec_list', 8);
    if (_setup !== st) return;
    if (!r || !r.ok) { box.innerHTML = `<div class="text-muted text-sm">${_esc((r && r.error) || 'Cannot read the folder')}</div>`; return; }
    if (r.dir) _showDir(r.dir);
    const rows = [];
    (r.unfinished || []).forEach(u => rows.push(`
      <div class="rec-item rec-item-warn" data-path="${_esc(u.path)}">
        <span class="rec-item-icon">${Icons.svg('alert-triangle', 16)}</span>
        <span class="rec-item-name"><span class="truncate">Unfinished recording</span><small>${_esc(u.size_str)} · ${_ago(u.mtime)} — the app closed while recording</small></span>
        <span class="rec-item-actions"><button class="btn btn-sm" data-act="recover">${Icons.svg('restore', 14)}Recover</button></span>
      </div>`));
    (r.items || []).forEach(it => rows.push(`
      <div class="rec-item" data-path="${_esc(it.path)}">
        <span class="rec-item-icon ${it.kind}">${Icons.svg(it.kind === 'image' ? 'file-image' : 'file-video', 16)}</span>
        <span class="rec-item-name"><span class="truncate" title="${_esc(it.name)}">${_esc(it.name)}</span><small>${_esc(it.size_str)} · ${_ago(it.mtime)}</small></span>
        <span class="rec-item-actions">
          <button class="icon-btn icon-btn-sm" data-act="open" title="${it.kind === 'image' ? 'Open' : 'Play'}" aria-label="${it.kind === 'image' ? 'Open' : 'Play'}">${Icons.svg(it.kind === 'image' ? 'external-link' : 'play', 14)}</button>
          <button class="icon-btn icon-btn-sm" data-act="rename" title="Rename" aria-label="Rename">${Icons.svg('text-cursor', 14)}</button>
          <button class="icon-btn icon-btn-sm" data-act="reveal" title="Show in folder" aria-label="Show in folder">${Icons.svg('folder-open', 14)}</button>
        </span>
      </div>`));
    box.innerHTML = rows.length ? rows.join('') : `<div class="rec-empty text-muted text-sm">${Icons.svg('video', 16)}No recordings yet.</div>`;
    box.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', async () => {
      const path = b.closest('.rec-item').dataset.path;
      const act = b.dataset.act;
      if (act === 'open') _call('open_native', path);
      else if (act === 'reveal') _call('rec_reveal', path);
      else if (act === 'recover') {
        const res = await _call('rec_recover', path);
        if (res && res.ok) App.toast('Recovered as ' + _esc(res.name), 'success'); else App.toast(_esc((res && res.error) || 'Recover failed'), 'error');
        _loadList();
      } else if (act === 'rename') {
        const name = _base(path);
        const nn = await Dialogs.ask({ title: 'Rename recording', label: 'New name', value: name, selectStem: true,
                                       icon: 'text-cursor', okLabel: 'Rename' });
        if (!nn || nn === name) return;
        const res = await _call('rename_file', path, nn);
        if (res && res.ok) _loadList(); else App.toast('Rename failed: ' + _esc((res && res.error) || ''), 'error');
      }
    }));
  }

  // ── screenshots ──────────────────────────────────────────────────────────
  let _shooting = false;
  async function screenshot(mode) {
    if (_shooting) return;
    _shooting = true;
    const p = _prefs();
    const hide = _setup ? !!document.getElementById('rec-hide-' + _setup.id)?.checked : p.hideForShot;
    App.setStatus(mode === 'region' ? 'Drag to select an area — Esc to cancel' : 'Taking a screenshot…', true);
    try {
      const r = await _call(mode === 'region' ? 'screenshot_region' : 'screenshot_full', hide);
      if (r && r.ok) {
        _savedToast(r, 'Saved screenshot');
        _revealInList(r.path);
        if (_setup) _loadList();
      } else if (r && r.reason !== 'cancelled') {
        App.toast('Screenshot failed: ' + _esc((r && (r.error || r.detail)) || 'unknown error'), 'error', 6000);
      }
    } finally {
      _shooting = false;
      App.setStatus('Ready');
    }
  }

  // ── toasts ───────────────────────────────────────────────────────────────
  function _savedToast(r, title) {
    const container = document.getElementById('toast-container');
    const label = `${title} ${_esc(r.name)} (${_esc(r.size_str || _fmtSize(r.size))})`;
    if (!container) { App.toast(label, 'success', 6000); return; }
    const el = document.createElement('div');
    el.className = 'toast success rec-toast';
    el.setAttribute('role', 'status');
    el.innerHTML = `<span class="icon">${Icons.svg('check-circle', 16)}</span>
      <span class="rec-toast-body"><span class="rec-toast-title">${_esc(title)}</span>
        <span class="rec-toast-name" title="${_esc(r.path)}">${_esc(r.name)} <span class="text-muted">(${_esc(r.size_str || _fmtSize(r.size))})</span></span>
        <span class="rec-toast-actions">
          <button class="btn btn-sm" data-act="open">${Icons.svg(/\.png$/i.test(r.name) ? 'external-link' : 'play', 14)}Open</button>
          <button class="btn btn-sm btn-ghost" data-act="reveal">${Icons.svg('folder-open', 14)}Show in folder</button>
        </span></span>`;
    let timer = null;
    const close = () => { el.classList.add('fade-out'); setTimeout(() => el.remove(), 300); };
    const arm = () => { clearTimeout(timer); timer = setTimeout(close, 9000); };
    el.querySelector('[data-act="open"]').addEventListener('click', () => { _call('open_native', r.path); close(); });
    el.querySelector('[data-act="reveal"]').addEventListener('click', () => { _call('rec_reveal', r.path); close(); });
    el.addEventListener('mouseenter', () => clearTimeout(timer));
    el.addEventListener('mouseleave', arm);
    container.appendChild(el);
    arm();
  }
  function _revealInList(path) {
    try {
      const cur = App.state && App.state.currentFolder;
      if (cur && _norm(cur) === _norm(_dir(path)) && FileTree.revealPath) FileTree.revealPath(path);
    } catch (_) {}
  }

  // ── recording ────────────────────────────────────────────────────────────
  function _displayConstraints(p) {
    const q = QUALITY[p.quality];
    const video = { frameRate: { ideal: p.fps, max: p.fps } };
    if (q) { video.width = { max: q[0] }; video.height = { max: q[1] }; }
    video.displaySurface = p.source === 'window' ? 'window' : p.source === 'app' ? 'browser' : 'monitor';
    const wantSys = p.audio === 'system' || p.audio === 'both';
    const c = { video, audio: wantSys ? { echoCancellation: false, noiseSuppression: false, autoGainControl: false } : false };
    if (wantSys) { c.systemAudio = 'include'; }
    if (p.source === 'app') { c.preferCurrentTab = true; c.selfBrowserSurface = 'include'; }
    else { c.selfBrowserSurface = 'exclude'; }
    if (p.source === 'window') c.monitorTypeSurfaces = 'exclude';
    c.surfaceSwitching = 'exclude';
    return c;
  }
  function _bitrate(p, w, h) {
    const px = (w || 1920) * (h || 1080);
    const base = px <= 1280 * 720 ? 5e6 : px <= 1920 * 1080 ? 8e6 : 12e6;
    return Math.round(base * (p.fps <= 15 ? 0.6 : 1));
  }

  async function start(prefs, setupId) {
    if (_rec) return;
    const p = { ...DEFAULTS, ...(prefs || _prefs()) };
    if (!_supported()) { App.toast('Screen recording is not supported here', 'error'); return; }
    const fmt = _pickFormat(p.format);
    if (!fmt) { App.toast('No supported video format (MP4/WebM) in this WebView2 version', 'error', 6000); return; }
    const startBtn = setupId && document.getElementById('rec-start-' + setupId);
    if (startBtn) startBtn.classList.add('is-loading');

    // getDisplayMedia must run first, while the click still counts as a user gesture.
    let display;
    try {
      display = await navigator.mediaDevices.getDisplayMedia(_displayConstraints(p));
    } catch (e) {
      if (startBtn) startBtn.classList.remove('is-loading');
      if (e && (e.name === 'NotAllowedError' || e.name === 'AbortError')) return;   // picker cancelled
      App.toast('Could not start screen capture: ' + _esc(e && e.message || e), 'error', 6000);
      return;
    }
    if (setupId) Dialogs.closeModal(setupId);

    const s = _rec = {
      p, fmt, display, streams: [display], id: '', recorder: null, chain: Promise.resolve(), bytes: 0,
      started: 0, pausedAt: 0, pausedTotal: 0, state: 'starting', muted: false, writeError: null,
      audioCtx: null, micGain: null, compositor: null, discard: false, minimized: false,
    };
    try {
      // microphone
      let mic = null;
      if (p.audio === 'mic' || p.audio === 'both') {
        try {
          mic = await navigator.mediaDevices.getUserMedia({ audio: { ...(p.micId ? { deviceId: { exact: p.micId } } : {}),
                                                                      echoCancellation: true, noiseSuppression: true } });
          s.streams.push(mic);
        } catch (e) {
          App.toast('Microphone not available — recording without it', 'warning', 5000);
        }
      }
      const sysTracks = display.getAudioTracks();
      if ((p.audio === 'system' || p.audio === 'both') && !sysTracks.length) {
        App.toast('System sound is not available for this source — recording without it', 'info', 5000);
      }
      // audio mix (always through Web Audio so the mic can be muted live)
      let audioTrack = null;
      if (mic || sysTracks.length) {
        const ctx = s.audioCtx = new AudioContext();
        const dest = ctx.createMediaStreamDestination();
        if (sysTracks.length) ctx.createMediaStreamSource(new MediaStream(sysTracks)).connect(dest);
        if (mic) {
          s.micGain = ctx.createGain();
          ctx.createMediaStreamSource(mic).connect(s.micGain).connect(dest);
        }
        if (ctx.state === 'suspended') { try { await ctx.resume(); } catch (_) {} }
        audioTrack = dest.stream.getAudioTracks()[0];
      }
      // video (+ webcam bubble)
      let videoTrack = display.getVideoTracks()[0];
      if (p.webcam && _canComposite()) {
        try {
          const cam = await navigator.mediaDevices.getUserMedia({ video: { ...(p.camId ? { deviceId: { exact: p.camId } } : {}),
                                                                           width: { ideal: 640 }, height: { ideal: 480 } } });
          s.streams.push(cam);
          s.compositor = _composite(videoTrack, cam.getVideoTracks()[0], p);
          videoTrack = s.compositor.track;
        } catch (e) {
          App.toast('Webcam not available — recording without it', 'warning', 5000);
        }
      }
      display.getVideoTracks()[0].addEventListener('ended', () => { if (_rec === s && s.state !== 'stopping') stop(); });

      const settings = display.getVideoTracks()[0].getSettings() || {};
      const out = new MediaStream(audioTrack ? [videoTrack, audioTrack] : [videoTrack]);

      if (p.countdown) {
        const go = await _countdown(3);
        if (!go) { _cleanup(s); _rec = null; App.toast('Recording cancelled', 'info', 2500); return; }
      }

      const b = await _call('rec_begin', fmt.ext, '');
      if (!b || !b.ok) throw new Error((b && b.error) || 'Cannot create the recording file');
      s.id = b.id; s.dir = b.dir;
      const rec = s.recorder = new MediaRecorder(out, {
        mimeType: fmt.mime, videoBitsPerSecond: _bitrate(p, settings.width, settings.height),
        audioBitsPerSecond: 128000,
      });
      rec.ondataavailable = e => {
        if (!e.data || !e.data.size || s.discard) return;
        s.chain = s.chain.then(() => _write(s, e.data));
        _tick(s);
      };
      rec.onerror = e => { App.toast('Recording error: ' + _esc(e && e.error && e.error.message || 'unknown'), 'error', 6000); stop(); };
      rec.start(1000);
      s.started = performance.now();
      s.state = 'recording';
      _showBar(s);
      _call('rec_hotkey', true);
      App.setStatus('Recording…', true);
      s.timer = setInterval(() => _tick(s), 500);
      _tick(s);
      if (p.minimize) { s.minimized = true; _call('rec_window', 'minimize'); }
    } catch (e) {
      _cleanup(s);
      if (s.id) _call('rec_discard', s.id);
      _rec = null;
      App.toast('Could not start recording: ' + _esc(e && e.message || e), 'error', 6000);
    }
  }

  function _blobToB64(blob) {
    return new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => {
        // The MIME type itself can contain commas (codecs=avc1…,mp4a…), so cut at ';base64,'.
        const u = String(fr.result || ''); const i = u.indexOf(';base64,');
        res(i >= 0 ? u.slice(i + 8) : u.slice(u.indexOf(',') + 1));
      };
      fr.onerror = () => rej(fr.error);
      fr.readAsDataURL(blob);
    });
  }
  async function _write(s, blob) {
    if (s.writeError || s.discard) return;
    try {
      const r = await _call('rec_chunk', s.id, await _blobToB64(blob));
      if (r && r.ok) { s.bytes = r.bytes; return; }
      s.writeError = (r && r.error) || 'write failed';
    } catch (e) { s.writeError = String(e && e.message || e); }
    App.toast('Recording stopped: ' + _esc(s.writeError), 'error', 8000);
    if (_rec === s && s.state !== 'stopping') stop();
  }

  function _elapsed(s) {
    if (!s.started) return 0;
    const now = s.state === 'paused' ? s.pausedAt : performance.now();
    return now - s.started - s.pausedTotal;
  }
  function _tick(s) {
    if (_rec !== s) return;
    const t = _fmtTime(_elapsed(s));
    const bar = s.bar;
    if (bar) {
      bar.querySelector('.rec-time').textContent = t;
      bar.querySelector('.rec-size').textContent = _fmtSize(s.bytes);
    }
    const title = `${s.state === 'paused' ? '❚❚ Paused' : '● Recording'} ${t} — ${APP_TITLE}`;
    if (title !== s.lastTitle) {
      s.lastTitle = title;
      document.title = title;
      _call('rec_set_title', title);
    }
  }

  // ── webcam compositing (frame driven; keeps running while minimised) ─────
  function _composite(screenTrack, camTrack, p) {
    const gen = new MediaStreamTrackGenerator({ kind: 'video' });
    const writer = gen.writable.getWriter();
    const out = new OffscreenCanvas(16, 16), octx = out.getContext('2d');
    const scr = new OffscreenCanvas(16, 16), sctx = scr.getContext('2d');
    const cam = new OffscreenCanvas(16, 16), cctx = cam.getContext('2d');
    let alive = true, haveScreen = false, haveCam = false, last = 0, writing = false;
    const minGap = 1000 / Math.max(5, p.fps || 30) * 0.9;

    function compose() {
      if (!haveScreen || writing) return;
      const now = performance.now();
      if (now - last < minGap) return;
      last = now;
      const W = scr.width, H = scr.height;
      if (out.width !== W || out.height !== H) { out.width = W; out.height = H; }
      octx.drawImage(scr, 0, 0);
      if (haveCam) {
        const d = Math.round(Math.min(W, H) * 0.24), m = Math.round(Math.min(W, H) * 0.03);
        const x = p.corner === 'tl' || p.corner === 'bl' ? m : W - d - m;
        const y = p.corner === 'tl' || p.corner === 'tr' ? m : H - d - m;
        octx.save();
        octx.beginPath(); octx.arc(x + d / 2, y + d / 2, d / 2, 0, Math.PI * 2); octx.closePath();
        octx.clip();
        const side = Math.min(cam.width, cam.height);
        octx.drawImage(cam, (cam.width - side) / 2, (cam.height - side) / 2, side, side, x, y, d, d);
        octx.restore();
        octx.lineWidth = Math.max(2, Math.round(d * 0.025));
        octx.strokeStyle = 'rgba(255,255,255,0.85)';
        octx.beginPath(); octx.arc(x + d / 2, y + d / 2, d / 2, 0, Math.PI * 2); octx.stroke();
      }
      if (!alive) return;
      let vf;
      try { vf = new VideoFrame(out, { timestamp: Math.round(now * 1000) }); } catch (_) { return; }
      writing = true;
      writer.write(vf).catch(() => { try { vf.close(); } catch (_) {} }).finally(() => { writing = false; });
    }
    async function pump(track, onFrame) {
      const reader = new MediaStreamTrackProcessor({ track }).readable.getReader();
      while (alive) {
        let r;
        try { r = await reader.read(); } catch (_) { break; }
        if (r.done) break;
        const f = r.value;
        try { onFrame(f); } finally { f.close(); }
        compose();
      }
      try { reader.releaseLock(); } catch (_) {}
    }
    pump(screenTrack, f => {
      const w = f.displayWidth, h = f.displayHeight;
      if (scr.width !== w || scr.height !== h) { scr.width = w; scr.height = h; }
      sctx.drawImage(f, 0, 0, w, h);
      haveScreen = true;
    });
    pump(camTrack, f => {
      const w = f.displayWidth, h = f.displayHeight;
      if (cam.width !== w || cam.height !== h) { cam.width = w; cam.height = h; }
      cctx.drawImage(f, 0, 0, w, h);
      haveCam = true;
    });
    return { track: gen, stop() { alive = false; try { const p = writer.close(); if (p && p.catch) p.catch(() => {}); } catch (_) {} try { gen.stop(); } catch (_) {} } };
  }

  // ── countdown ────────────────────────────────────────────────────────────
  function _countdown(n) {
    return new Promise(resolve => {
      const el = document.createElement('div');
      el.className = 'rec-countdown';
      el.innerHTML = `<div class="rec-countdown-box"><div class="rec-countdown-num">${n}</div>
        <div class="rec-countdown-text">Recording starts…</div><div class="rec-countdown-hint">Esc to cancel</div></div>`;
      document.body.appendChild(el);
      let left = n, done = false;
      const finish = ok => {
        if (done) return; done = true;
        clearInterval(iv); document.removeEventListener('keydown', onKey, true); el.remove();
        // let the compositor drop the overlay before the first frame is recorded
        setTimeout(() => resolve(ok), ok ? 120 : 0);
      };
      const onKey = e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); } };
      document.addEventListener('keydown', onKey, true);
      const iv = setInterval(() => {
        left -= 1;
        if (left <= 0) finish(true);
        else el.querySelector('.rec-countdown-num').textContent = left;
      }, 1000);
    });
  }

  // ── floating control bar ────────────────────────────────────────────────
  function _showBar(s) {
    const bar = document.createElement('div');
    bar.className = 'rec-bar';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Recording controls');
    const hasMic = !!s.micGain;
    bar.innerHTML = `
      <span class="rec-grip" title="Drag to move">${Icons.svg('grip-vertical', 14)}</span>
      <span class="rec-dot" aria-hidden="true"></span>
      <span class="rec-status"><span class="rec-time">00:00</span><span class="rec-size">0 B</span></span>
      <span class="rec-sep"></span>
      <button class="icon-btn" data-act="pause" title="Pause" aria-label="Pause">${Icons.svg('pause', 16)}</button>
      ${hasMic ? `<button class="icon-btn" data-act="mute" title="Mute microphone" aria-label="Mute microphone">${Icons.svg('mic', 16)}</button>` : ''}
      <button class="btn btn-sm btn-danger rec-stop" data-act="stop" title="Stop and save (Ctrl+Shift+R)">${Icons.svg('stop', 14)}Stop</button>
      <button class="icon-btn icon-btn-danger" data-act="discard" title="Discard recording" aria-label="Discard recording">${Icons.svg('trash', 16)}</button>`;
    document.body.appendChild(bar);
    s.bar = bar;
    try {
      const pos = JSON.parse(localStorage.getItem(LS_BAR) || 'null');
      if (pos) _placeBar(bar, pos.x, pos.y);
    } catch (_) {}
    bar.querySelector('[data-act="pause"]').addEventListener('click', () => togglePause());
    bar.querySelector('[data-act="mute"]')?.addEventListener('click', () => toggleMute());
    bar.querySelector('[data-act="stop"]').addEventListener('click', () => stop());
    bar.querySelector('[data-act="discard"]').addEventListener('click', () => discard());
    // drag (within the app window)
    const grip = bar.querySelector('.rec-grip');
    grip.addEventListener('pointerdown', e => {
      e.preventDefault();
      const r = bar.getBoundingClientRect();
      const dx = e.clientX - r.left, dy = e.clientY - r.top;
      grip.setPointerCapture(e.pointerId);
      const move = ev => _placeBar(bar, ev.clientX - dx, ev.clientY - dy);
      const up = () => {
        grip.removeEventListener('pointermove', move);
        grip.removeEventListener('pointerup', up);
        const rr = bar.getBoundingClientRect();
        try { localStorage.setItem(LS_BAR, JSON.stringify({ x: rr.left, y: rr.top })); } catch (_) {}
      };
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', up);
    });
  }
  function _placeBar(bar, x, y) {
    const w = bar.offsetWidth, h = bar.offsetHeight;
    x = Math.max(4, Math.min(window.innerWidth - w - 4, x));
    y = Math.max(4, Math.min(window.innerHeight - h - 4, y));
    bar.style.left = x + 'px'; bar.style.top = y + 'px';
    bar.style.bottom = 'auto'; bar.style.transform = 'none';
  }
  function _focusBar() {
    const b = _rec && _rec.bar;
    if (!b) return;
    b.classList.remove('rec-bar-flash'); void b.offsetWidth; b.classList.add('rec-bar-flash');
  }

  function togglePause() {
    const s = _rec; if (!s || !s.recorder) return;
    const btn = s.bar && s.bar.querySelector('[data-act="pause"]');
    if (s.state === 'recording') {
      try { s.recorder.pause(); } catch (_) { return; }
      s.state = 'paused'; s.pausedAt = performance.now();
      s.bar.classList.add('is-paused');
      if (btn) { btn.innerHTML = Icons.svg('play', 16); btn.title = 'Resume'; btn.setAttribute('aria-label', 'Resume'); }
      App.setStatus('Recording paused');
    } else if (s.state === 'paused') {
      try { s.recorder.resume(); } catch (_) { return; }
      s.pausedTotal += performance.now() - s.pausedAt;
      s.state = 'recording';
      s.bar.classList.remove('is-paused');
      if (btn) { btn.innerHTML = Icons.svg('pause', 16); btn.title = 'Pause'; btn.setAttribute('aria-label', 'Pause'); }
      App.setStatus('Recording…', true);
    }
    _tick(s);
  }
  function toggleMute() {
    const s = _rec; if (!s || !s.micGain) return;
    s.muted = !s.muted;
    s.micGain.gain.value = s.muted ? 0 : 1;
    const btn = s.bar && s.bar.querySelector('[data-act="mute"]');
    if (btn) {
      btn.innerHTML = Icons.svg(s.muted ? 'mic-off' : 'mic', 16);
      btn.classList.toggle('is-muted', s.muted);
      btn.title = s.muted ? 'Unmute microphone' : 'Mute microphone';
      btn.setAttribute('aria-label', btn.title);
    }
  }

  function _cleanup(s) {
    clearInterval(s.timer);
    try { s.compositor && s.compositor.stop(); } catch (_) {}
    (s.streams || []).forEach(st => st.getTracks().forEach(t => { try { t.stop(); } catch (_) {} }));
    try { s.audioCtx && s.audioCtx.close(); } catch (_) {}
    if (s.bar) { s.bar.remove(); s.bar = null; }
    document.title = APP_TITLE;
    _call('rec_set_title', '');
    _call('rec_hotkey', false);
    App.setStatus('Ready');
  }
  function _stopRecorder(s) {
    return new Promise(resolve => {
      const r = s.recorder;
      if (!r || r.state === 'inactive') { resolve(); return; }
      r.addEventListener('stop', () => resolve(), { once: true });
      try { r.stop(); } catch (_) { resolve(); }
    });
  }

  async function stop() {
    const s = _rec;
    if (!s || s.state === 'stopping' || s.state === 'starting') return;
    if (s.state === 'paused') s.pausedTotal += performance.now() - s.pausedAt;
    s.state = 'stopping';
    if (s.bar) s.bar.classList.add('is-saving');
    await _stopRecorder(s);
    await s.chain;
    _cleanup(s);
    _rec = null;
    if (s.minimized) _call('rec_window', 'restore');
    const r = await _call('rec_finish', s.id, '');
    if (r && r.ok) {
      _savedToast(r, 'Saved recording');
      _revealInList(r.path);
      SFM.emit && SFM.emit('rec_saved', r);
    } else if (r && r.code === 'empty') {
      App.toast('Nothing was recorded', 'warning');
    } else {
      App.toast('Could not save the recording: ' + _esc((r && r.error) || 'unknown error'), 'error', 8000);
    }
  }

  async function discard() {
    const s = _rec;
    if (!s || s.state === 'stopping') return;
    const wasRec = s.state === 'recording';
    if (wasRec) togglePause();
    const ok = await Dialogs.confirm({ title: 'Discard this recording?', message: 'The recording will not be saved.',
      detail: 'The partial file is moved to the _to_review folder inside the recordings folder.',
      okLabel: 'Discard', okIcon: 'trash', danger: true });
    if (_rec !== s) return;
    if (!ok) { if (wasRec && s.state === 'paused') togglePause(); return; }
    s.state = 'stopping';
    s.discard = true;
    await _stopRecorder(s);
    await s.chain;
    _cleanup(s);
    _rec = null;
    if (s.minimized) _call('rec_window', 'restore');
    await _call('rec_discard', s.id);
    App.toast('Recording discarded', 'info', 3000);
  }

  function toggle() {
    if (_rec) { if (_rec.state !== 'starting') stop(); return; }
    if (_setup) { document.getElementById('rec-start-' + _setup.id)?.click(); return; }
    start(_prefs());
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && !e.altKey && (e.key === 'R' || e.key === 'r')) {
      e.preventDefault(); e.stopPropagation();
      toggle();
    }
  }, true);
  if (typeof SFM !== 'undefined' && SFM.on) SFM.on('rec_hotkey', () => { if (_rec) toggle(); });

  function _init() {
    document.getElementById('btn-record')?.addEventListener('click', () => open());
    // "More actions" gets a Capture section; Settings gets the recordings folder.
    // (Injected on open so dialogs.js stays untouched.)
    new MutationObserver(() => {
      const more = document.getElementById('modal-more');
      if (more && !more.querySelector('.rec-more')) _injectMore(more);
      const set = document.getElementById('modal-settings');
      if (set && !set.querySelector('.rec-settings')) _injectSettings(set);
    }).observe(document.body, { childList: true });
  }

  function _injectMore(modal) {
    const list = modal.querySelector('.qa-list');
    if (!list) return;
    const item = (act, icon, label, hint) =>
      `<button class="qa-btn more-item" data-rec="${act}"><span class="icon">${Icons.svg(icon, 16)}</span>
         <span class="flex-1">${label}</span>${hint ? `<span class="hint">${hint}</span>` : ''}</button>`;
    const wrap = document.createElement('div');
    wrap.className = 'rec-more';
    wrap.innerHTML = `<div class="section-title rec-more-title">Capture</div>
      ${item('record', 'record', 'Record screen&#x2026;', 'Ctrl+Shift+R')}
      ${item('full', 'monitor', 'Screenshot — full screen')}
      ${item('region', 'crop', 'Screenshot — select area&#x2026;')}`;
    list.appendChild(wrap);
    wrap.querySelectorAll('[data-rec]').forEach(b => b.addEventListener('click', () => {
      Dialogs.closeModal();
      const act = b.dataset.rec;
      setTimeout(() => (act === 'record' ? open() : screenshot(act)), 50);
    }));
  }

  async function _injectSettings(modal) {
    const form = modal.querySelector('.settings-form');
    if (!form) return;
    const sec = document.createElement('section');
    sec.className = 'settings-group rec-settings';
    sec.innerHTML = `<div class="settings-group-head">${Icons.svg('video', 16)}Screen recording</div>
      <div class="settings-row">
        <label class="field-label" for="s-recfolder">Recordings folder</label>
        <div>
          <div class="field-row">
            <input id="s-recfolder" class="input-text" placeholder="Videos\\Office Axe">
            <button class="btn" id="s-recfolder-browse">Browse&#x2026;</button>
          </div>
          <div class="field-hint">Screen recordings and screenshots are saved here. Leave empty for Videos\\Office Axe.</div>
        </div>
      </div>`;
    const groups = form.querySelectorAll('.settings-group');
    form.insertBefore(sec, groups.length > 1 ? groups[1] : null);
    const inp = sec.querySelector('#s-recfolder');
    const d = await _call('rec_default_dir');
    let initial = '';
    if (d && d.ok) { initial = d.is_default ? '' : d.path; inp.value = initial; inp.placeholder = d.default || inp.placeholder; }
    sec.querySelector('#s-recfolder-browse').addEventListener('click', async () => {
      const r = await _call('browse_for_folder');
      if (r && r.ok && r.path) inp.value = r.path;
    });
    const save = modal.querySelector('[data-modal-btn="Save"]');
    if (save) save.addEventListener('click', async () => {
      const v = inp.value.trim();
      if (v === initial) return;
      const r = await _call('rec_set_dir', v);
      if (!r || !r.ok) App.toast('Recordings folder: ' + _esc((r && r.error) || 'cannot use that folder'), 'error', 6000);
    }, true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init); else _init();

  return { open, start, stop, discard, toggle, togglePause, toggleMute, screenshot,
           isRecording: () => !!_rec, formats: _formats };
})();
