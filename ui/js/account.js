/**
 * account.js — Sign-in overlay, Account panel, Cloud panel and status-bar chip.
 *
 * Talks to the PDF Axe account server through the bridge (cloud_bridge.py).
 * Python pushes:
 *   account_changed, cloud_event {event, data}, cloud_live {connected},
 *   cloud_upload_progress, cloud_upload_done, cloud_download_progress,
 *   cloud_download_done, cloud_sync_state, cloud_sync_done, cloud_sync_log,
 *   cloud_files_dropped {paths}
 *
 * If no server address is configured the app is never blocked; the Account
 * panel explains how to connect instead. Free features never need a server.
 */
const Account = (() => {

  const MIN_PW = 8;
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const DRAG_TYPE = 'application/x-pdfaxe-paths';

  const st = {
    configured: false, server_url: '', logged_in: false, user: null,
    offline: false, live: false, server: null,
    loaded: false,
    overlayDismissed: false,   // "Continue offline" for this session
    authTab: 'login',
    devices: null, devicesError: null,
    // cloud panel
    cloudView: 'files',        // 'files' | 'trash'
    cwd: '',                   // current cloud folder ('' = root)
    files: [],
    selected: new Set(),       // 'f:<id>' | 'd:<path>'
    usage: null,
    listError: null,
    listLoading: false,
    sync: { running: false, paused: false, state: 'stopped', folder: '', remote_root: '', last: null },
    syncLog: [],
    uploadProgress: null,
    downloadProgress: null,
    syncDraft: '',
    pollTimer: null,
    pollUntil: 0,
  };

  // ── helpers ──────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function toast(msg, type = 'info', ms) { App.toast(esc(msg), type, ms); }
  function sub() { return (st.user && st.user.subscription) || null; }
  function isActive() { const s = sub(); return !!(s && s.active); }
  function fmtDate(ts) {
    if (!ts) return '';
    try { return new Date(ts * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
    catch (e) { return ''; }
  }
  function fmtDateTime(ts) {
    if (!ts) return '';
    try { return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); }
    catch (e) { return new Date(ts * 1000).toLocaleString(); }
  }
  function fmtSize(n) {
    n = Number(n) || 0;
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return i === 0 ? `${n} B` : `${n.toFixed(1)} ${u[i]}`;
  }
  function planState() {
    const s = sub();
    if (!s) return { text: 'Not subscribed', cls: 'off', short: 'No plan' };
    if (s.free_plan) return { text: 'Included (free on this server)', cls: 'on', short: 'Free' };
    if (s.status === 'past_due') {
      return s.active
        ? { text: `Payment failed — update your card by ${fmtDate(s.grace_until)}`, cls: 'warn', short: 'Payment due' }
        : { text: 'Payment overdue — plan paused', cls: 'off', short: 'Payment overdue' };
    }
    if (s.active) return { text: s.current_period_end ? `Active · renews ${fmtDate(s.current_period_end)}` : 'Active', cls: 'on', short: 'Active' };
    if (s.status === 'canceled') return { text: 'Cancelled', cls: 'off', short: 'Cancelled' };
    return { text: 'Not subscribed', cls: 'off', short: 'No plan' };
  }
  function billingTip(s) {
    return s && s.billing_available === false ? 'Billing is not set up on this server — ask the administrator' : '';
  }
  function panelVisible(name) {
    const el = $(`panel-${name}`);
    return !!el && el.style.display !== 'none' && !el.classList.contains('hidden');
  }

  // Small prompt modal (uses the shared .modal-overlay / .modal styles)
  function promptModal({ title, label, value = '', hint = '', okLabel = 'OK', selectStem = false }) {
    return new Promise(resolve => {
      const ov = document.createElement('div');
      ov.className = 'modal-overlay';
      ov.style.zIndex = 9600;
      ov.innerHTML = `
        <div class="modal" style="width:440px;max-width:96vw" role="dialog">
          <div class="modal-header"><h2 class="modal-title">${esc(title)}</h2>
            <button class="modal-close" aria-label="Close">&#x2715;</button></div>
          <div class="modal-body">
            <label class="detail-label">${esc(label)}</label>
            <input class="input-text acct-prompt-input" style="width:100%" value="${esc(value)}">
            ${hint ? `<div class="acct-hint">${esc(hint)}</div>` : ''}
          </div>
          <div class="modal-footer">
            <button class="btn" data-act="cancel">Cancel</button>
            <button class="btn btn-primary" data-act="ok">${esc(okLabel)}</button>
          </div>
        </div>`;
      document.body.appendChild(ov);
      const input = ov.querySelector('input');
      const done = v => { document.removeEventListener('keydown', onKey, true); ov.remove(); resolve(v); };
      const onKey = e => {
        if (e.key === 'Escape') { e.stopPropagation(); done(null); }
        else if (e.key === 'Enter' && document.activeElement === input) { e.preventDefault(); done(input.value.trim()); }
      };
      document.addEventListener('keydown', onKey, true);
      ov.querySelector('.modal-close').onclick = () => done(null);
      ov.querySelector('[data-act=cancel]').onclick = () => done(null);
      ov.querySelector('[data-act=ok]').onclick = () => done(input.value.trim());
      setTimeout(() => {
        input.focus();
        const dot = value.lastIndexOf('.');
        if (selectStem && dot > 0) input.setSelectionRange(0, dot); else input.select();
      }, 30);
    });
  }

  // ── state ────────────────────────────────────────────────────────────────
  let _refreshing = null;
  async function refresh() {
    if (_refreshing) return _refreshing;
    _refreshing = (async () => {
      try {
        const r = await SFM.accountGetState();
        if (r && r.ok !== false) {
          Object.assign(st, {
            configured: !!r.configured, server_url: r.server_url || '',
            logged_in: !!r.logged_in, user: r.user || null,
            offline: !!r.offline, live: !!r.live, server: r.server || st.server,
          });
        } else if (r) {
          Object.assign(st, { configured: !!r.configured, server_url: r.server_url || st.server_url });
        }
      } catch (e) {
        console.error('[Account] state', e);
      }
      st.loaded = true;
      renderAll();
      if (st.logged_in && isActive()) {
        refreshSyncStatus();
        if (panelVisible('cloud')) loadCloud();
      }
      if (st.logged_in && panelVisible('account')) loadDevices();
    })();
    try { return await _refreshing; } finally { _refreshing = null; }
  }

  function renderAll() {
    renderOverlay();
    renderChip();
    renderAccountPanel();
    renderCloudPanel();
  }

  // ── login overlay ────────────────────────────────────────────────────────
  function buildOverlay() {
    if ($('acct-overlay')) return;
    const el = document.createElement('div');
    el.id = 'acct-overlay';
    el.className = 'acct-overlay hidden';
    el.innerHTML = `
      <div class="acct-card" role="dialog" aria-labelledby="acct-title">
        <div class="acct-brand">S</div>
        <h2 id="acct-title" class="acct-title">Welcome</h2>
        <p class="acct-sub" id="acct-sub">Sign in to your account to continue.</p>
        <div class="acct-tabs" role="tablist">
          <button type="button" class="acct-tab active" data-tab="login" role="tab">Sign in</button>
          <button type="button" class="acct-tab" data-tab="register" role="tab">Create account</button>
        </div>
        <form id="acct-form" class="acct-form" autocomplete="on" novalidate>
          <label class="detail-label" for="acct-email">Email</label>
          <input id="acct-email" class="input-text" type="email" autocomplete="username" placeholder="you@example.com" required>
          <div id="acct-email-err" class="acct-field-err hidden"></div>
          <label class="detail-label" for="acct-password">Password</label>
          <div class="acct-pw-wrap">
            <input id="acct-password" class="input-text" type="password" autocomplete="current-password" placeholder="Password" required>
            <button type="button" class="acct-pw-toggle" id="acct-pw-toggle" title="Show password" aria-label="Show password">👁</button>
          </div>
          <div id="acct-password-err" class="acct-field-err hidden"></div>
          <div id="acct-confirm-wrap" class="hidden">
            <label class="detail-label" for="acct-confirm">Confirm password</label>
            <input id="acct-confirm" class="input-text" type="password" autocomplete="new-password" placeholder="Repeat password">
            <div id="acct-confirm-err" class="acct-field-err hidden"></div>
            <div class="acct-hint" id="acct-pw-hint">At least ${MIN_PW} characters.</div>
          </div>
          <label class="acct-check"><input type="checkbox" id="acct-remember" checked> Keep me signed in on this computer</label>
          <div id="acct-error" class="acct-error hidden" role="alert"></div>
          <button id="acct-submit" class="btn btn-primary acct-submit" type="submit">Sign in</button>
          <div class="acct-hint acct-forgot" id="acct-forgot">Forgot your password? Contact your server administrator to reset it.</div>
          <div class="acct-hint acct-plan-hint hidden" id="acct-plan-hint"></div>
        </form>
        <div id="acct-offline" class="acct-offline hidden">
          <span>Can't reach the server.</span>
          <button type="button" class="btn btn-ghost" id="acct-retry">Retry</button>
          <button type="button" class="btn btn-ghost" id="acct-continue">Continue offline</button>
        </div>
        <button type="button" class="btn btn-ghost acct-skip" id="acct-skip" title="Free tools work without an account">Use free tools without signing in</button>
        <details class="acct-server" id="acct-server-details">
          <summary>Server settings</summary>
          <label class="detail-label" for="acct-server-input">Server address</label>
          <div class="acct-row">
            <input id="acct-server-input" class="input-text" placeholder="https://accounts.example.com">
            <button type="button" class="btn" id="acct-server-save">Save</button>
          </div>
          <div id="acct-server-msg" class="acct-hint"></div>
        </details>
      </div>`;
    document.body.appendChild(el);

    el.querySelectorAll('.acct-tab').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));
    $('acct-form').addEventListener('submit', e => { e.preventDefault(); submitAuth(); });
    $('acct-retry').addEventListener('click', () => refresh());
    $('acct-continue').addEventListener('click', () => { st.overlayDismissed = true; renderOverlay(); });
    $('acct-skip').addEventListener('click', () => { st.overlayDismissed = true; renderOverlay(); });
    $('acct-server-save').addEventListener('click', () => saveServer($('acct-server-input').value, $('acct-server-msg')));
    $('acct-server-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); $('acct-server-save').click(); }
    });
    $('acct-pw-toggle').addEventListener('click', () => {
      const show = $('acct-password').type === 'password';
      ['acct-password', 'acct-confirm'].forEach(id => { $(id).type = show ? 'text' : 'password'; });
      const t = $('acct-pw-toggle');
      t.textContent = show ? '🙈' : '👁';
      t.title = show ? 'Hide password' : 'Show password';
      t.setAttribute('aria-label', t.title);
    });
    $('acct-email').addEventListener('blur', () => {
      const v = $('acct-email').value.trim();
      fieldError('email', v && !EMAIL_RE.test(v) ? 'That doesn\'t look like an email address.' : '');
    });
    ['acct-email', 'acct-password', 'acct-confirm'].forEach(id => $(id).addEventListener('input', () => {
      fieldError(id.replace('acct-', ''), '');
      showAuthError('');
      if (id !== 'acct-email') updatePwHint();
    }));
  }

  function updatePwHint() {
    const h = $('acct-pw-hint');
    if (!h) return;
    const pw = $('acct-password').value, cf = $('acct-confirm').value;
    if (!pw) h.textContent = `At least ${MIN_PW} characters.`;
    else if (pw.length < MIN_PW) h.textContent = `${MIN_PW - pw.length} more character${MIN_PW - pw.length === 1 ? '' : 's'} needed.`;
    else if (cf && cf !== pw) h.textContent = 'Passwords do not match yet.';
    else h.textContent = '✓ Password length OK.';
  }

  function fieldError(field, msg) {
    const el = $(`acct-${field}-err`);
    const input = $(`acct-${field}`);
    if (el) { el.textContent = msg || ''; el.classList.toggle('hidden', !msg); }
    if (input) input.classList.toggle('acct-invalid', !!msg);
  }

  function setTab(tab) {
    st.authTab = tab;
    document.querySelectorAll('#acct-overlay .acct-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const reg = tab === 'register';
    $('acct-confirm-wrap').classList.toggle('hidden', !reg);
    $('acct-forgot').classList.toggle('hidden', reg);
    $('acct-submit').textContent = reg ? 'Create account' : 'Sign in';
    $('acct-title').textContent = reg ? 'Create your account' : 'Welcome';
    $('acct-sub').textContent = reg ? 'Sign up, then choose a monthly plan to unlock cloud storage.' : 'Sign in to your account to continue.';
    $('acct-password').setAttribute('autocomplete', reg ? 'new-password' : 'current-password');
    ['email', 'password', 'confirm'].forEach(f => fieldError(f, ''));
    showAuthError('');
    renderPlanHint();
    updatePwHint();
  }

  function renderPlanHint() {
    const el = $('acct-plan-hint');
    if (!el) return;
    const sv = st.server || {};
    let txt = '';
    if (st.authTab === 'register') {
      if (sv.free_plan) txt = 'Every feature is included on this server.';
      else if (sv.billing && sv.billing !== 'none') txt = `Cloud storage and AI photo tools: ${sv.plan_label || 'monthly plan'}. Cancel any time.`;
    }
    el.textContent = txt;
    el.classList.toggle('hidden', !txt);
  }

  function showAuthError(msg) {
    const el = $('acct-error');
    if (!el) return;
    el.textContent = msg || '';
    el.classList.toggle('hidden', !msg);
  }

  async function submitAuth() {
    const email = $('acct-email').value.trim();
    const pw = $('acct-password').value;
    const reg = st.authTab === 'register';
    let bad = false;
    if (!email) { fieldError('email', 'Enter your email address.'); bad = true; }
    else if (!EMAIL_RE.test(email)) { fieldError('email', 'That doesn\'t look like an email address.'); bad = true; }
    if (!pw) { fieldError('password', 'Enter your password.'); bad = true; }
    else if (reg && pw.length < MIN_PW) { fieldError('password', `Use at least ${MIN_PW} characters.`); bad = true; }
    if (reg && !bad && pw !== $('acct-confirm').value) { fieldError('confirm', 'Passwords do not match.'); bad = true; }
    if (bad) return;

    const remember = $('acct-remember').checked;
    const btn = $('acct-submit');
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = reg ? 'Creating account…' : 'Signing in…';
    showAuthError('');
    try {
      const r = reg ? await SFM.accountRegister(email, pw, remember) : await SFM.accountLogin(email, pw, remember);
      if (r && r.ok) {
        $('acct-password').value = '';
        $('acct-confirm').value = '';
        st.offline = false;
        toast(reg ? 'Account created' : `Signed in as ${email}`, 'success');
        await refresh();
        if (reg && !isActive()) App.switchPanel('account');
      } else {
        const msg = (r && r.error) || 'Sign-in failed.';
        if (r && (r.field === 'email' || r.field === 'password') && r.status !== 401) fieldError(r.field, msg);
        else showAuthError(msg);
        if (r && r.status === 401) { $('acct-password').select(); }
        if (r && r.offline) { st.offline = true; renderOverlay(); }
      }
    } catch (e) {
      showAuthError(String(e && e.message || e));
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  function renderOverlay() {
    const el = $('acct-overlay');
    if (!el) return;
    const show = st.loaded && st.configured && !st.logged_in && !st.overlayDismissed;
    el.classList.toggle('hidden', !show);
    if (!show) return;
    const si = $('acct-server-input');
    if (si && document.activeElement !== si) si.value = st.server_url || '';
    $('acct-offline').classList.toggle('hidden', !st.offline);
    renderPlanHint();
    const em = $('acct-email');
    if (em && !em.value && document.activeElement?.tagName !== 'INPUT') setTimeout(() => em.focus(), 50);
  }

  async function saveServer(url, msgEl) {
    if (msgEl) msgEl.textContent = 'Checking…';
    try {
      const r = await SFM.accountSetServer(String(url || '').trim());
      if (!r || !r.ok) {
        if (msgEl) msgEl.textContent = (r && r.error) || 'Could not save the address.';
        return;
      }
      if (msgEl) {
        msgEl.textContent = !r.server_url ? 'Server address cleared.'
          : r.reachable ? 'Connected.' : 'Saved, but the server could not be reached.';
      }
      st.overlayDismissed = false;
      await refresh();
    } catch (e) {
      if (msgEl) msgEl.textContent = String(e && e.message || e);
    }
  }

  // ── status-bar chip ──────────────────────────────────────────────────────
  const SYNC_LABEL = { syncing: 'Syncing…', idle: 'Synced', paused: 'Sync paused', offline: 'Sync offline', error: 'Sync issue' };
  function renderChip() {
    const chip = $('acct-chip');
    if (!chip) return;
    if (!st.configured) { chip.style.display = 'none'; return; }
    chip.style.display = 'flex';
    const dot = $('acct-chip-dot');
    const text = $('acct-chip-text');
    let label, cls;
    if (!st.logged_in) { label = 'Not signed in'; cls = 'off'; }
    else {
      const email = (st.user && st.user.email) || 'Signed in';
      const p = planState();
      label = `${email} · ${p.short}`;
      cls = st.offline ? 'off' : (p.cls === 'on' ? 'on' : 'warn');
      if (st.offline) label += ' · offline';
      if (st.sync.running && SYNC_LABEL[st.sync.state]) label += ` · ${SYNC_LABEL[st.sync.state]}`;
    }
    text.textContent = label;
    dot.className = 'acct-chip-dot ' + cls + (st.sync.running && st.sync.state === 'syncing' ? ' busy' : '');
    chip.title = st.logged_in ? `${label}\nClick to open Account` : 'Click to sign in';
  }

  // ── Account panel ────────────────────────────────────────────────────────
  function serverBlock(id) {
    return `
      <div class="acct-section">
        <div class="detail-section-title">Server address</div>
        <div class="acct-row">
          <input id="${id}-input" class="input-text" placeholder="https://accounts.example.com" value="${esc(st.server_url)}">
          <button class="btn" id="${id}-save">Save</button>
        </div>
        <div class="acct-hint" id="${id}-msg"></div>
      </div>`;
  }
  function wireServerBlock(id) {
    const save = $(`${id}-save`);
    if (save) save.addEventListener('click', () => saveServer($(`${id}-input`).value, $(`${id}-msg`)));
  }

  function devicesBlock() {
    let inner;
    if (st.devicesError) inner = `<div class="acct-hint">${esc(st.devicesError)}</div>`;
    else if (!st.devices) inner = '<div class="acct-hint">Loading…</div>';
    else if (!st.devices.length) inner = '<div class="acct-hint">No active sign-ins.</div>';
    else inner = st.devices.map(d => `
      <div class="acct-kv acct-device">
        <span><strong>${esc(d.device || 'Unknown device')}</strong>${d.current ? ' <span class="acct-badge on">This computer</span>' : ''}
          <br><span class="acct-hint">Signed in ${esc(fmtDate(d.created_at))} · last active ${esc(fmtDateTime(d.last_seen))}</span></span>
        <button class="btn btn-ghost" data-revoke="${Number(d.id)}" data-current="${d.current ? 1 : 0}">Sign out</button>
      </div>`).join('');
    return `<div class="acct-section"><div class="detail-section-title">Signed-in devices</div>
      <div class="acct-card-flat">${inner}</div></div>`;
  }

  async function loadDevices() {
    const r = await SFM.accountListDevices().catch(e => ({ ok: false, error: String(e) }));
    if (r && r.ok) { st.devices = r.devices || []; st.devicesError = null; }
    else { st.devicesError = (r && r.error) || 'Could not load devices'; }
    if (panelVisible('account')) renderAccountPanel();
  }

  function renderAccountPanel() {
    const body = $('acct-panel-body');
    if (!body) return;
    let html = '<h2 class="acct-h2">👤 Account</h2>';

    if (!st.loaded) {
      html += '<p class="acct-muted">Loading…</p>';
    } else if (!st.configured) {
      html += `
        <p class="acct-muted">Accounts, the monthly plan and cloud storage need a PDF Axe account server.
        Everything else in the app works without one.</p>
        <ol class="acct-steps">
          <li>Ask your administrator for the server address (for example <code>https://accounts.example.com</code>),
              or run your own with <code>uvicorn server.app:app --port 8000</code>.</li>
          <li>Enter it below and click <strong>Save</strong>.</li>
          <li>Sign in or create an account in the window that appears.</li>
        </ol>
        ${serverBlock('acct-srv')}`;
    } else if (!st.logged_in) {
      html += `
        <p class="acct-muted">You are not signed in. Free tools keep working; sign in for cloud storage and AI photo tools.</p>
        <div class="acct-actions"><button class="btn btn-primary" id="acct-show-login">Sign in…</button></div>
        ${serverBlock('acct-srv')}`;
    } else {
      const s = sub() || {};
      const active = isActive();
      const p = planState();
      const polling = !!st.pollTimer;
      const tip = billingTip(s);
      const hasSub = s.status && !['none', '', 'canceled'].includes(s.status);
      html += `
        <div class="acct-card-flat">
          <div class="acct-kv"><span>Email</span><strong>${esc(st.user && st.user.email)}</strong></div>
          <div class="acct-kv"><span>Plan</span>
            <span><span class="acct-badge ${p.cls === 'warn' ? 'warn' : p.cls}">${esc(p.text)}</span>
            ${s.plan_label && !s.free_plan ? `<span class="acct-muted"> · ${esc(s.plan_label)}</span>` : ''}</span></div>
          <div class="acct-kv"><span>Connection</span><span>${st.offline ? 'Offline — showing the last known plan' : (st.live ? '<span class="cloud-live on"></span> Live' : '<span class="cloud-live"></span> Connecting…')}</span></div>
        </div>
        ${s.status === 'past_due' ? `<div class="acct-callout warn">Your last payment didn't go through.
            ${s.active ? `Paid features keep working until <strong>${esc(fmtDate(s.grace_until))}</strong>.` : 'Paid features are paused.'}
            Update your payment method to keep your plan.</div>` : ''}
        ${!active ? `<p class="acct-muted">Subscribe to ${esc(s.plan_label || 'the monthly plan')} to unlock cloud storage, auto-sync and AI photo tools.</p>` : ''}
        ${polling ? '<p class="acct-hint">Waiting for payment to complete in your browser… this page updates automatically.</p>' : ''}
        <div class="acct-actions">
          ${!active && !hasSub && !s.free_plan ? `<button class="btn btn-primary" id="acct-subscribe" ${tip ? `disabled title="${esc(tip)}"` : ''}>Subscribe${s.plan_label ? ' — ' + esc(s.plan_label) : ''}</button>` : ''}
          ${hasSub && !s.free_plan ? `<button class="btn ${s.status === 'past_due' ? 'btn-primary' : ''}" id="acct-portal" ${tip ? `disabled title="${esc(tip)}"` : ''}>${s.status === 'past_due' ? 'Update payment method' : 'Manage billing'}</button>` : ''}
          <button class="btn" id="acct-refresh">Refresh</button>
          <button class="btn btn-danger" id="acct-signout">Sign out</button>
        </div>
        ${tip && !s.free_plan ? `<div class="acct-hint">${esc(tip)}</div>` : ''}
        ${devicesBlock()}
        ${serverBlock('acct-srv')}`;
    }
    body.innerHTML = html;

    wireServerBlock('acct-srv');
    const w = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };
    w('acct-show-login', () => { st.overlayDismissed = false; renderOverlay(); });
    w('acct-subscribe', subscribe);
    w('acct-portal', openPortal);
    w('acct-refresh', () => refresh());
    w('acct-signout', signOut);
    body.querySelectorAll('[data-revoke]').forEach(b => b.addEventListener('click', async () => {
      const current = b.dataset.current === '1';
      if (!confirm(current ? 'Sign out of this computer?' : 'Sign out that device? It will need to sign in again.')) return;
      const r = await SFM.accountRevokeDevice(Number(b.dataset.revoke));
      if (!r || !r.ok) { toast('Could not sign out the device: ' + ((r && r.error) || 'unknown error'), 'error'); return; }
      toast(current ? 'Signed out' : 'Device signed out', 'success');
      if (current) await refresh(); else loadDevices();
    }));
  }

  async function subscribe() {
    const r = await SFM.billingOpenCheckout();
    if (!r || !r.ok) { toast('Could not open checkout: ' + ((r && r.error) || 'unknown error'), 'error', 5000); return; }
    toast('Checkout opened in your browser', 'info');
    startPlanPolling();
  }

  async function openPortal() {
    const r = await SFM.billingOpenPortal();
    if (!r || !r.ok) toast('Could not open billing: ' + ((r && r.error) || 'unknown error'), 'error', 5000);
    else toast('Billing page opened in your browser', 'info');
  }

  // Fallback in case the live event is missed; the webhook normally updates us via SSE
  function startPlanPolling() {
    stopPlanPolling();
    st.pollUntil = Date.now() + 10 * 60 * 1000;
    st.pollTimer = setInterval(async () => {
      if (Date.now() > st.pollUntil || !st.logged_in) { stopPlanPolling(); renderAccountPanel(); return; }
      await refresh();
      if (isActive()) { stopPlanPolling(); toast('Subscription active — thank you!', 'success', 5000); renderAll(); }
    }, 15000);
    renderAccountPanel();
    renderCloudPanel();
  }
  function stopPlanPolling() {
    if (st.pollTimer) clearInterval(st.pollTimer);
    st.pollTimer = null;
  }

  async function signOut() {
    if (!confirm('Sign out of this account on this computer?')) return;
    stopPlanPolling();
    await SFM.accountLogout();
    st.files = []; st.selected.clear(); st.usage = null; st.devices = null; st.cwd = '';
    st.overlayDismissed = false;
    toast('Signed out', 'info');
    await refresh();
  }

  // ── Cloud panel: data ────────────────────────────────────────────────────
  async function loadCloud() {
    if (!st.logged_in || !isActive()) { renderCloudPanel(); return; }
    st.listLoading = true;
    const view = st.cloudView;
    try {
      const [list, usage] = await Promise.all([SFM.cloudList(view === 'trash'), SFM.cloudUsage()]);
      if (view !== st.cloudView) return;   // view changed meanwhile
      if (list && list.ok) {
        st.files = list.files || [];
        st.listError = null;
        pruneSelection();
      } else {
        st.listError = (list && list.error) || 'Could not load files';
        if (list && list.need_subscription) refresh();
      }
      if (usage && usage.ok) st.usage = usage;
    } catch (e) {
      st.listError = String(e && e.message || e);
    } finally {
      st.listLoading = false;
      renderCloudPanel();
    }
  }

  // Items (folders + files) directly inside st.cwd
  function cwdItems() {
    const pre = st.cwd ? st.cwd + '/' : '';
    const folders = new Map();
    const files = [];
    for (const f of st.files) {
      if (pre && !f.path.startsWith(pre)) continue;
      const rest = f.path.slice(pre.length);
      const slash = rest.indexOf('/');
      if (slash === -1) { files.push(f); continue; }
      const name = rest.slice(0, slash);
      const d = folders.get(name) || { name, path: pre + name, size: 0, count: 0, updated_at: 0 };
      d.size += Number(f.size) || 0;
      d.count += 1;
      d.updated_at = Math.max(d.updated_at, f.updated_at || 0);
      folders.set(name, d);
    }
    const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    files.forEach(f => { f.name = f.path.slice(pre.length); });
    return { folders: [...folders.values()].sort(byName), files: files.sort(byName) };
  }

  function pruneSelection() {
    const valid = new Set();
    if (st.cloudView === 'trash') st.files.forEach(f => valid.add('f:' + f.id));
    else {
      const { folders, files } = cwdItems();
      folders.forEach(d => valid.add('d:' + d.path));
      files.forEach(f => valid.add('f:' + f.id));
    }
    [...st.selected].forEach(k => { if (!valid.has(k)) st.selected.delete(k); });
  }

  function selection() {
    const ids = [], dirs = [];
    st.selected.forEach(k => { if (k.startsWith('f:')) ids.push(Number(k.slice(2))); else dirs.push(k.slice(2)); });
    return { ids, dirs };
  }

  let _reloadTimer = null;
  function scheduleReload() {
    clearTimeout(_reloadTimer);
    _reloadTimer = setTimeout(() => { if (panelVisible('cloud')) loadCloud(); }, 400);
  }

  async function refreshSyncStatus() {
    try {
      const r = await SFM.cloudSyncStatus();
      if (r && r.ok) {
        st.sync = { running: !!r.running, paused: !!r.paused, state: r.state || 'stopped',
                    folder: r.folder || '', remote_root: r.remote_root || '', last: r.last || st.sync.last };
        if (typeof r.live === 'boolean') st.live = r.live;
        renderSyncSection();
        renderChip();
      }
    } catch (e) {}
  }

  // ── Cloud panel: render ──────────────────────────────────────────────────
  function renderCloudPanel() {
    const body = $('cloud-panel-body');
    if (!body) return;
    const head = `<div class="cloud-head"><h2 class="acct-h2">☁️ Cloud</h2>
      ${st.logged_in ? `<span class="cloud-live-wrap" title="${st.live ? 'Changes from your other devices appear instantly' : 'Live updates not connected — reconnecting automatically'}"><span class="cloud-live ${st.live ? 'on' : ''}"></span>${st.live ? 'Live' : (st.offline ? 'Offline' : 'Reconnecting…')}</span>` : ''}</div>`;

    if (!st.loaded) { body.innerHTML = head + '<p class="acct-muted">Loading…</p>'; return; }
    if (!st.configured || !st.logged_in) {
      body.innerHTML = head + `
        <div class="cloud-cta">
          <div class="cloud-cta-icon">☁️</div>
          <p>${st.configured ? 'Sign in to use cloud storage.' : 'Connect to an account server to use cloud storage.'}</p>
          <button class="btn btn-primary" id="cloud-go-account">Open Account</button>
        </div>`;
      $('cloud-go-account').addEventListener('click', () => {
        App.switchPanel('account');
        if (st.configured) { st.overlayDismissed = false; renderOverlay(); }
      });
      return;
    }
    if (!isActive()) {
      const s = sub() || {};
      const tip = billingTip(s);
      const pastDue = s.status === 'past_due';
      body.innerHTML = head + `
        <div class="cloud-cta">
          <div class="cloud-cta-icon">🔒</div>
          <h3>${pastDue ? 'Your plan is paused — payment overdue' : 'Cloud storage is part of the monthly plan'}</h3>
          <p class="acct-muted">Keep your files safe online, open them on every computer, and auto-sync a folder in real time.</p>
          ${pastDue
            ? `<button class="btn btn-primary" id="cloud-subscribe" ${tip ? `disabled title="${esc(tip)}"` : ''}>Update payment method</button>`
            : `<button class="btn btn-primary" id="cloud-subscribe" ${tip ? `disabled title="${esc(tip)}"` : ''}>Subscribe${s.plan_label ? ' — ' + esc(s.plan_label) : ''}</button>`}
          ${tip ? `<p class="acct-hint">${esc(tip)}</p>` : ''}
          ${st.pollTimer ? '<p class="acct-hint">Waiting for payment to complete…</p>' : ''}
        </div>`;
      $('cloud-subscribe').addEventListener('click', pastDue ? openPortal : subscribe);
      return;
    }

    const u = st.usage;
    const pct = u && u.quota ? Math.min(100, (u.used / u.quota) * 100) : 0;
    const trash = st.cloudView === 'trash';
    const nSel = st.selected.size;
    const { ids: selIds, dirs: selDirs } = selection();

    let rows, total = 0;
    if (st.listError) rows = `<div class="cloud-empty acct-error">${esc(st.listError)}</div>`;
    else if (trash) {
      total = st.files.length;
      rows = !st.files.length
        ? `<div class="cloud-empty">${st.listLoading ? 'Loading…' : 'Cloud trash is empty.'}</div>`
        : st.files.map(f => rowHtml('f:' + f.id, '🗑️', f.path, f.size_str || fmtSize(f.size), f.updated_at)).join('');
    } else {
      const { folders, files } = cwdItems();
      total = folders.length + files.length;
      if (!total) rows = `<div class="cloud-empty">${st.listLoading ? 'Loading…'
        : (st.cwd ? 'This folder is empty.' : 'No files in the cloud yet. Drop files here, use “Upload files…”, or start auto-sync below.')}</div>`;
      else rows = folders.map(d => rowHtml('d:' + d.path, '📁', d.name, `${fmtSize(d.size)} · ${d.count} file${d.count === 1 ? '' : 's'}`, d.updated_at, true)).join('')
        + files.map(f => rowHtml('f:' + f.id, '📄', f.name, f.size_str || fmtSize(f.size), f.updated_at)).join('');
    }
    const allSel = total > 0 && nSel === total;

    const crumbs = ['<button class="cloud-crumb" data-cd="">Cloud</button>'];
    if (!trash && st.cwd) {
      let acc = '';
      st.cwd.split('/').forEach(part => {
        acc = acc ? acc + '/' + part : part;
        crumbs.push(`<span class="cloud-crumb-sep">›</span><button class="cloud-crumb" data-cd="${esc(acc)}">${esc(part)}</button>`);
      });
    }

    body.innerHTML = head + `
      <div class="cloud-usage" title="Files in the cloud trash do not count">
        <div class="cloud-usage-bar"><div class="cloud-usage-fill ${pct > 90 ? 'full' : ''}" style="width:${pct.toFixed(1)}%"></div></div>
        <div class="acct-hint">${u ? `${esc(u.used_str)} of ${esc(u.quota_str)} used${u.max_upload ? ` · files up to ${esc(u.max_upload_str)}` : ''}` : 'Usage unavailable'}</div>
      </div>

      <div class="cloud-toolbar">
        <div class="cloud-seg">
          <button class="cloud-seg-btn ${!trash ? 'active' : ''}" data-view="files">Files</button>
          <button class="cloud-seg-btn ${trash ? 'active' : ''}" data-view="trash">Trash</button>
        </div>
        ${!trash ? `
          <button class="btn" id="cloud-upload-browse" title="Upload into the folder shown below">⬆ Upload files…</button>
          <button class="btn" id="cloud-upload-sel" title="Upload the files selected in the Files workspace">⬆ Upload selected</button>
          <button class="btn" id="cloud-download" ${nSel ? '' : 'disabled'}>⬇ Download${nSel ? ` (${nSel})` : ''}</button>
          <button class="btn" id="cloud-rename" ${nSel === 1 ? '' : 'disabled'} title="Rename, or type a path with / to move">✏ Rename</button>
          <button class="btn btn-danger" id="cloud-trash" ${nSel ? '' : 'disabled'} title="Restorable from the Trash tab">Move to cloud trash</button>
        ` : `
          <button class="btn btn-primary" id="cloud-restore" ${selIds.length ? '' : 'disabled'}>↩ Restore${selIds.length ? ` (${selIds.length})` : ''}</button>
        `}
        <span class="flex-1"></span>
        <button class="btn btn-ghost" id="cloud-refresh" title="Refresh">🔄</button>
      </div>
      ${!trash ? `<div class="cloud-crumbs">${crumbs.join('')}</div>` : ''}
      <div id="cloud-transfer"></div>

      <div class="cloud-list" id="cloud-drop">
        <div class="cloud-row cloud-row-head">
          <input type="checkbox" id="cloud-check-all" ${allSel ? 'checked' : ''} ${total ? '' : 'disabled'}>
          <span class="cloud-path">${trash ? 'Path' : 'Name'}</span><span class="cloud-size">Size</span><span class="cloud-date">Updated</span>
        </div>
        <div class="cloud-rows">${rows}</div>
        <div class="cloud-drop-hint">Drop files here to upload to <strong>${esc(st.cwd || 'Cloud')}</strong></div>
      </div>

      <div class="acct-section" id="cloud-sync-section"></div>`;

    renderTransfer();
    renderSyncSection();
    wireCloud(selIds, selDirs);
  }

  function rowHtml(key, icon, name, size, ts, isDir) {
    const sel = st.selected.has(key);
    return `
      <div class="cloud-row ${sel ? 'sel' : ''} ${isDir ? 'cloud-dir' : ''}" data-key="${esc(key)}">
        <input type="checkbox" class="cloud-check" data-key="${esc(key)}" ${sel ? 'checked' : ''}>
        <span class="cloud-path" title="${esc(name)}"><span class="cloud-icon">${icon}</span>${esc(name)}</span>
        <span class="cloud-size">${esc(size)}</span>
        <span class="cloud-date">${esc(fmtDateTime(ts))}</span>
      </div>`;
  }

  function renderTransfer() {
    const el = $('cloud-transfer');
    if (!el) return;
    const up = st.uploadProgress, dn = st.downloadProgress;
    let html = '';
    if (up) {
      const pct = up.overall_total ? Math.min(100, (up.overall_bytes / up.overall_total) * 100) : 0;
      html += `<div class="cloud-progress">
        <div class="cloud-progress-text">Uploading ${up.index + 1} of ${up.total}: ${esc(up.name)}
          <span class="acct-hint">${esc(fmtSize(up.bytes))} / ${esc(fmtSize(up.bytes_total))}</span></div>
        <div class="cloud-usage-bar"><div class="cloud-usage-fill" style="width:${pct.toFixed(1)}%"></div></div>
        <button class="btn btn-ghost" id="cloud-upload-cancel">Cancel</button></div>`;
    }
    if (dn) {
      const pct = dn.bytes_total ? Math.min(100, (dn.bytes / dn.bytes_total) * 100) : 0;
      html += `<div class="cloud-progress">
        <div class="cloud-progress-text">Downloading ${dn.index + 1} of ${dn.total}: ${esc(dn.name)}</div>
        <div class="cloud-usage-bar"><div class="cloud-usage-fill" style="width:${pct.toFixed(1)}%"></div></div></div>`;
    }
    el.innerHTML = html;
    const c = $('cloud-upload-cancel');
    if (c) c.addEventListener('click', () => { SFM.cloudUploadCancel(); c.disabled = true; c.textContent = 'Cancelling…'; });
  }

  const SYNC_STATE_TEXT = {
    syncing: ['busy', 'Syncing…'], idle: ['on', 'Up to date'], paused: ['warn', 'Paused'],
    offline: ['warn', 'Offline — will retry'], error: ['err', 'Problem — see below'], stopped: ['', 'Off'],
  };
  function renderSyncSection() {
    const el = $('cloud-sync-section');
    if (!el) return;
    const s = st.sync;
    const last = s.last;
    let lastTxt = s.running ? 'Waiting for the first sync…' : 'Not running.';
    if (last) {
      if (last.error) lastTxt = `Last sync failed: ${esc(last.error)}`;
      else {
        const bits = [`↑ ${last.uploaded || 0} uploaded`, `↓ ${last.downloaded || 0} downloaded`];
        if (last.conflicts) bits.push(`${last.conflicts} conflict${last.conflicts > 1 ? 's' : ''} (kept both)`);
        if (last.moved_to_review) bits.push(`${last.moved_to_review} removed in the cloud → moved to _to_review`);
        if (last.trashed_remote) bits.push(`${last.trashed_remote} moved to cloud trash`);
        if (last.errors) bits.push(`${last.errors} error${last.errors > 1 ? 's' : ''}`);
        lastTxt = `Last sync ${esc(fmtDateTime(last.time))}: ${bits.join(', ')}`;
        if (last.held_deletions) lastTxt += `<br><span class="acct-warn-text">⚠ ${last.held_deletions} synced files are missing from this folder, so they were <strong>not</strong> removed from the cloud.</span>`;
      }
    }
    const [cls, label] = SYNC_STATE_TEXT[s.running ? s.state : 'stopped'] || ['', s.state];
    el.innerHTML = `
      <div class="detail-section-title">Auto-sync folder
        <span class="cloud-sync-badge ${cls}">${esc(label)}</span></div>
      <p class="acct-muted">Two-way sync between a folder on this computer and a cloud folder of the same name, live across your devices.
        Files removed in the cloud are moved to a <code>_to_review</code> folder here — never deleted. If a file changed on both sides, both copies are kept.</p>
      <div class="acct-row">
        <input id="cloud-sync-folder" class="input-text" placeholder="D:\\Documents\\Work" value="${esc(s.running ? s.folder : (st.syncDraft || s.folder))}" ${s.running ? 'disabled' : ''}>
        <button class="btn" id="cloud-sync-browse" ${s.running ? 'disabled' : ''}>Browse…</button>
        ${s.running ? `
          ${s.paused ? '<button class="btn btn-primary" id="cloud-sync-resume">▶ Resume</button>'
                     : '<button class="btn" id="cloud-sync-pause">⏸ Pause</button>'}
          <button class="btn" id="cloud-sync-now" ${s.paused ? 'disabled' : ''} title="Sync now">⟳</button>
          <button class="btn" id="cloud-sync-stop">⏹ Stop</button>`
        : '<button class="btn btn-primary" id="cloud-sync-start">▶ Start</button>'}
      </div>
      ${s.running && s.remote_root ? `<div class="acct-hint">Cloud folder: ${esc(s.remote_root)}/</div>` : ''}
      <div class="acct-hint" id="cloud-sync-last">${lastTxt}</div>
      ${st.syncLog.length ? `<pre class="cloud-sync-log">${esc(st.syncLog.slice(-8).join('\n'))}</pre>` : ''}`;

    const w = (id, fn) => { const b = $(id); if (b) b.addEventListener('click', fn); };
    const fi = $('cloud-sync-folder');
    if (fi) fi.addEventListener('input', () => { st.syncDraft = fi.value; });
    w('cloud-sync-browse', async () => {
      const r = await SFM.call('browse_for_folder');
      if (r && r.ok && r.path) { $('cloud-sync-folder').value = r.path; st.syncDraft = r.path; }
    });
    w('cloud-sync-start', async () => {
      const folder = $('cloud-sync-folder').value.trim();
      if (!folder) { toast('Choose a folder to sync', 'warning'); return; }
      const r = await SFM.cloudSyncStart(folder);
      if (!r || !r.ok) { toast('Sync: ' + ((r && r.error) || 'could not start'), 'error', 5000); return; }
      st.syncLog = [];
      toast(`Auto-sync started for ${folder}`, 'success');
      refreshSyncStatus();
    });
    w('cloud-sync-stop', async () => {
      if (!confirm('Stop auto-sync? Files stay where they are on both sides.')) return;
      await SFM.cloudSyncStop();
      toast('Auto-sync stopped', 'info');
      refreshSyncStatus();
    });
    w('cloud-sync-pause', async () => { await SFM.cloudSyncPause(); refreshSyncStatus(); });
    w('cloud-sync-resume', async () => { await SFM.cloudSyncResume(); refreshSyncStatus(); });
    w('cloud-sync-now', async () => { await SFM.cloudSyncNow(); });
  }

  // ── Cloud panel: actions ─────────────────────────────────────────────────
  function wireCloud(selIds, selDirs) {
    const body = $('cloud-panel-body');
    body.querySelectorAll('.cloud-seg-btn').forEach(b => b.addEventListener('click', () => {
      if (st.cloudView === b.dataset.view) return;
      st.cloudView = b.dataset.view;
      st.files = []; st.selected.clear(); st.listError = null;
      renderCloudPanel();
      loadCloud();
    }));
    body.querySelectorAll('.cloud-crumb').forEach(b => b.addEventListener('click', () => cd(b.dataset.cd || '')));
    body.querySelectorAll('.cloud-check').forEach(cb => cb.addEventListener('click', e => e.stopPropagation()));
    body.querySelectorAll('.cloud-check').forEach(cb => cb.addEventListener('change', () => {
      if (cb.checked) st.selected.add(cb.dataset.key); else st.selected.delete(cb.dataset.key);
      renderCloudPanel();
    }));
    body.querySelectorAll('.cloud-row[data-key]').forEach(row => {
      row.addEventListener('click', e => {
        const k = row.dataset.key;
        if (e.ctrlKey || e.metaKey) { if (st.selected.has(k)) st.selected.delete(k); else st.selected.add(k); }
        else st.selected = new Set([k]);
        renderCloudPanel();
      });
      row.addEventListener('dblclick', () => {
        const k = row.dataset.key;
        if (k.startsWith('d:')) cd(k.slice(2));
      });
    });
    const all = $('cloud-check-all');
    if (all) all.addEventListener('change', () => {
      if (!all.checked) st.selected = new Set();
      else if (st.cloudView === 'trash') st.selected = new Set(st.files.map(f => 'f:' + f.id));
      else {
        const { folders, files } = cwdItems();
        st.selected = new Set([...folders.map(d => 'd:' + d.path), ...files.map(f => 'f:' + f.id)]);
      }
      renderCloudPanel();
    });

    const w = (id, fn) => { const b = $(id); if (b) b.addEventListener('click', fn); };
    w('cloud-refresh', () => loadCloud());
    w('cloud-upload-sel', () => {
      const paths = (App.state.selectedPaths || []).slice();
      if (!paths.length && App.state.focusedPath) paths.push(App.state.focusedPath);
      if (!paths.length) { toast('Select files in the Files workspace first, or use "Upload files…"', 'warning', 4500); return; }
      startUpload(paths);
    });
    w('cloud-upload-browse', async () => {
      const r = await SFM.cloudPickFiles();
      if (r && r.ok && r.paths && r.paths.length) startUpload(r.paths);
      else if (r && !r.ok) toast(r.error || 'Could not open file picker', 'error');
    });
    w('cloud-download', async () => {
      const ids = new Set(selIds);
      selDirs.forEach(d => st.files.forEach(f => { if (f.path.startsWith(d + '/')) ids.add(f.id); }));
      if (!ids.size) return;
      const r = await SFM.call('browse_for_folder');
      if (!r || !r.ok || !r.path) return;
      const s = await SFM.cloudDownload([...ids], r.path, st.cwd ? st.cwd + '/' : '');
      if (!s || !s.ok) { toast('Download: ' + ((s && s.error) || 'failed'), 'error'); return; }
      App.setStatus(`Downloading ${ids.size} file${ids.size > 1 ? 's' : ''}…`, true);
    });
    w('cloud-rename', renameSelected);
    w('cloud-trash', async () => {
      if (!selIds.length && !selDirs.length) return;
      const n = selIds.length + selDirs.length;
      if (selDirs.length && !confirm(`Move ${n} item${n > 1 ? 's' : ''} (including everything inside the selected folders) to the cloud trash? You can restore them from the Trash tab.`)) return;
      let done = 0; const errs = [];
      if (selIds.length) {
        const r = await SFM.cloudTrash(selIds);
        if (r && r.ok) done += r.done; else errs.push((r && r.error) || 'failed');
      }
      for (const d of selDirs) {
        const r = await SFM.cloudTrashFolder(d);
        if (r && r.ok) done += Number(r.result) || 0; else errs.push((r && r.error) || 'failed');
      }
      if (errs.length) toast('Trash failed: ' + errs[0], 'error');
      else toast(`Moved ${done} file${done === 1 ? '' : 's'} to cloud trash (restorable)`, 'success');
      st.selected.clear();
      loadCloud();
    });
    w('cloud-restore', async () => {
      if (!selIds.length) return;
      const r = await SFM.cloudRestore(selIds);
      if (r && r.ok) {
        toast(`Restored ${r.done} file${r.done === 1 ? '' : 's'}`, r.errors && r.errors.length ? 'warning' : 'success');
        if (r.errors && r.errors.length) toast(r.errors[0].error, 'error', 5000);
        st.selected.clear();
      } else toast('Restore failed: ' + ((r && r.error) || 'unknown error'), 'error', 5000);
      loadCloud();
    });
  }

  function cd(path) {
    st.cwd = path || '';
    st.selected.clear();
    renderCloudPanel();
  }

  async function renameSelected() {
    const { ids, dirs } = selection();
    const pre = st.cwd ? st.cwd + '/' : '';
    if (ids.length === 1) {
      const f = st.files.find(x => x.id === ids[0]);
      if (!f) return;
      const cur = f.path.slice(pre.length);
      const name = await promptModal({ title: 'Rename', label: 'New name', value: cur, selectStem: true,
        hint: 'Type a path with / to move it into a folder, e.g. Archive/report.pdf' });
      if (!name || name === cur) return;
      const target = name.startsWith('/') ? name.replace(/^\/+/, '') : pre + name;
      const r = await SFM.cloudMove(f.id, target);
      if (!r || !r.ok) { toast('Rename failed: ' + ((r && r.error) || 'unknown error'), 'error', 5000); return; }
      toast('Renamed', 'success');
    } else if (dirs.length === 1) {
      const d = dirs[0];
      const cur = d.slice(pre.length);
      const name = await promptModal({ title: 'Rename folder', label: 'New name', value: cur,
        hint: 'Everything inside the folder moves with it.' });
      if (!name || name === cur) return;
      const target = name.startsWith('/') ? name.replace(/^\/+/, '') : pre + name;
      const r = await SFM.cloudMoveFolder(d, target);
      if (!r || !r.ok) { toast('Rename failed: ' + ((r && r.error) || 'unknown error'), 'error', 5000); return; }
      toast(`Renamed folder (${r.result} file${r.result === 1 ? '' : 's'})`, 'success');
    } else return;
    st.selected.clear();
    loadCloud();
  }

  async function startUpload(paths) {
    if (!paths || !paths.length) return;
    if (st.uploadProgress) { toast('An upload is already running', 'warning'); return; }
    const r = await SFM.cloudUpload(paths, st.cloudView === 'files' ? st.cwd : '');
    if (!r || !r.ok) { toast('Upload: ' + ((r && r.error) || 'failed'), 'error'); return; }
    st.uploadProgress = { index: 0, total: paths.length, name: '', bytes: 0, bytes_total: 0, overall_bytes: 0, overall_total: 0 };
    renderTransfer();
    App.setStatus(`Uploading ${paths.length} item${paths.length > 1 ? 's' : ''} to the cloud…`, true);
  }

  // ── drag & drop ──────────────────────────────────────────────────────────
  // Sources: rows of the Files workspace (made draggable on pointer-down) and
  // Explorer (paths come from Python via cloud_files_dropped). Targets: the
  // Cloud panel list and the sidebar Cloud button.
  function initDragDrop() {
    document.addEventListener('pointerdown', e => {
      const row = e.target.closest && e.target.closest('.file-item[data-path], .thumb-tile[data-path]');
      if (row && !row.draggable) row.draggable = true;
    }, true);
    document.addEventListener('dragstart', e => {
      const row = e.target.closest && e.target.closest('.file-item[data-path], .thumb-tile[data-path]');
      if (!row || !e.dataTransfer) return;
      const sel = App.state.selectedPaths || [];
      const paths = sel.includes(row.dataset.path) ? sel.slice() : [row.dataset.path];
      e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(paths));
      e.dataTransfer.effectAllowed = 'copy';
    }, true);

    const accepts = e => !!(e.dataTransfer && [...e.dataTransfer.types].some(t => t === DRAG_TYPE || t === 'Files'));
    const internalPaths = e => {
      try { return JSON.parse(e.dataTransfer.getData(DRAG_TYPE) || '[]'); } catch (_) { return []; }
    };
    const canUploadHere = () => st.logged_in && isActive() && st.cloudView === 'files';

    const panel = $('panel-cloud');
    if (panel) {
      let depth = 0;
      panel.addEventListener('dragenter', e => { if (accepts(e)) { depth++; panel.classList.toggle('cloud-dragging', canUploadHere()); } });
      panel.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) panel.classList.remove('cloud-dragging'); });
      panel.addEventListener('dragover', e => {
        if (!accepts(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = canUploadHere() ? 'copy' : 'none';
      });
      panel.addEventListener('drop', e => {
        depth = 0;
        panel.classList.remove('cloud-dragging');
        if (!accepts(e)) return;
        e.preventDefault();
        if (!canUploadHere()) { toast('Open the Files tab of an active cloud plan to upload', 'warning'); return; }
        const paths = internalPaths(e);
        if (paths.length) startUpload(paths);
        // Explorer drops: Python sends the full paths as cloud_files_dropped
      });
    }
    document.querySelectorAll('.sidebar-btn[data-panel="cloud"]').forEach(btn => {
      btn.addEventListener('dragover', e => {
        if (!internalPaths(e).length && ![...(e.dataTransfer?.types || [])].includes(DRAG_TYPE)) return;
        e.preventDefault();
        btn.classList.add('cloud-drop-target');
      });
      btn.addEventListener('dragleave', () => btn.classList.remove('cloud-drop-target'));
      btn.addEventListener('drop', e => {
        btn.classList.remove('cloud-drop-target');
        const paths = internalPaths(e);
        if (!paths.length) return;
        e.preventDefault();
        App.switchPanel('cloud');
        renderCloudPanel();
        if (!st.logged_in || !isActive()) { toast('Sign in with an active plan to upload to the cloud', 'warning'); return; }
        startUpload(paths);
      });
    });
    SFM.cloudEnableDrop().catch(() => {});
  }

  // ── Python → JS events ───────────────────────────────────────────────────
  function initEvents() {
    SFM.on('account_changed', p => {
      if (p && p.reason === 'session_expired') toast('You were signed out (session expired or signed out from another device)', 'warning', 6000);
      if (p && p.reason === 'subscription_updated' && p.subscription && st.user) {
        const was = isActive();
        st.user = { ...st.user, subscription: p.subscription };
        if (p.subscription.active) stopPlanPolling();
        if (!was && p.subscription.active) toast('Subscription active — thank you!', 'success', 5000);
        if (p.subscription.status === 'past_due') toast('Payment failed — please update your payment method', 'warning', 7000);
        renderAll();
      }
      refresh();
    });
    SFM.on('cloud_live', p => {
      const was = st.live;
      st.live = !!(p && p.connected);
      if (st.live && !was) { st.offline = false; scheduleReload(); }
      renderChip();
      if (panelVisible('cloud')) renderCloudPanel();
      if (panelVisible('account')) renderAccountPanel();
    });
    SFM.on('cloud_event', p => {
      const ev = p && p.event;
      if (/^(file|folder)_/.test(ev || '')) scheduleReload();
      if (ev === 'session_revoked' && panelVisible('account')) loadDevices();
    });
    SFM.on('cloud_files_dropped', p => {
      const paths = (p && p.paths) || [];
      if (!paths.length) return;
      if (!st.logged_in || !isActive() || st.cloudView !== 'files') {
        toast('Sign in with an active plan and open the Files tab to upload', 'warning'); return;
      }
      startUpload(paths);
    });
    SFM.on('cloud_upload_progress', p => { st.uploadProgress = p; renderTransfer(); });
    SFM.on('cloud_upload_done', r => {
      st.uploadProgress = null;
      renderTransfer();
      App.setStatus('Ready');
      if (r.ok) toast(`Uploaded ${r.uploaded} file${r.uploaded === 1 ? '' : 's'} to the cloud`, 'success');
      else if (r.cancelled) toast(`Upload cancelled after ${r.uploaded} file${r.uploaded === 1 ? '' : 's'}`, 'info');
      else {
        const first = (r.errors && r.errors[0] && r.errors[0].error) || r.error || 'unknown error';
        toast(`Uploaded ${r.uploaded || 0}, ${r.failed || 0} failed: ${first}`, 'error', 7000);
      }
      scheduleReload();
    });
    SFM.on('cloud_download_progress', p => { st.downloadProgress = p; renderTransfer(); });
    SFM.on('cloud_download_done', r => {
      st.downloadProgress = null;
      renderTransfer();
      App.setStatus('Ready');
      if (r.ok) toast(`Downloaded ${r.downloaded} file${r.downloaded === 1 ? '' : 's'} to ${r.dest_dir}`, 'success', 4500);
      else {
        const first = (r.errors && r.errors[0] && r.errors[0].error) || r.error || 'unknown error';
        toast(`Downloaded ${r.downloaded || 0}, ${r.failed || 0} failed: ${first}`, 'error', 6000);
      }
      if (App.state.currentFolder && r.dest_dir && App.state.currentFolder === r.dest_dir) {
        try { FileTree.refresh(); } catch (e) {}
      }
    });
    SFM.on('cloud_sync_log', p => {
      if (!p || !p.log) return;
      st.syncLog.push(p.log);
      if (st.syncLog.length > 50) st.syncLog = st.syncLog.slice(-50);
    });
    SFM.on('cloud_sync_state', p => {
      if (!p) return;
      if (p.state === 'stopped') st.sync.running = false;
      else { st.sync.state = p.state; st.sync.paused = p.state === 'paused'; }
      renderChip();
      renderSyncSection();
    });
    SFM.on('cloud_sync_done', p => {
      st.sync.last = p;
      refreshSyncStatus();
      if ((p.uploaded || p.downloaded || p.trashed_remote) && panelVisible('cloud')) scheduleReload();
      if (p.held_deletions) toast(`Auto-sync: ${p.held_deletions} files are missing locally — not removed from the cloud`, 'warning', 7000);
      if ((p.downloaded || p.moved_to_review || p.conflicts) && App.state.currentFolder && p.folder &&
          App.state.currentFolder.toLowerCase().startsWith(p.folder.toLowerCase())) {
        try { FileTree.refresh(); } catch (e) {}
      }
    });
  }

  // ── boot ─────────────────────────────────────────────────────────────────
  function init() {
    buildOverlay();
    initEvents();
    initDragDrop();
    const chip = $('acct-chip');
    if (chip) chip.addEventListener('click', () => {
      if (!st.logged_in && st.configured) { st.overlayDismissed = false; renderOverlay(); }
      else App.switchPanel('account');
    });
    document.querySelectorAll('.sidebar-btn[data-panel="cloud"]').forEach(b =>
      b.addEventListener('click', () => { renderCloudPanel(); loadCloud(); refreshSyncStatus(); }));
    document.querySelectorAll('.sidebar-btn[data-panel="account"]').forEach(b =>
      b.addEventListener('click', () => { refresh(); if (st.logged_in) loadDevices(); }));
    renderAll();
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { refresh, isActive, state: st };
})();
