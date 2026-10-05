/**
 * account.js — Sign-in overlay, Account panel, Cloud panel and status-bar chip.
 *
 * Talks to the PDF Axe account server through the bridge (sfm_bridge.py,
 * "ACCOUNT / CLOUD" section). Python pushes:
 *   account_changed, cloud_event {event, data}, cloud_live {connected},
 *   cloud_upload_progress, cloud_upload_done, cloud_download_done,
 *   cloud_sync_done, cloud_sync_log
 *
 * If no server address is configured the app is never blocked; the Account
 * panel explains how to connect instead.
 */
const Account = (() => {

  const st = {
    configured: false, server_url: '', logged_in: false, user: null,
    offline: false, live: false,
    loaded: false,
    overlayDismissed: false,   // "Continue offline" for this session
    authTab: 'login',
    // cloud panel
    cloudView: 'files',        // 'files' | 'trash'
    files: [],
    selected: new Set(),
    usage: null,
    listError: null,
    listLoading: false,
    sync: { running: false, folder: '', remote_root: '', last: null },
    syncLog: [],
    uploadProgress: null,
    remoteDir: '',             // cloud folder for uploads (kept across re-renders)
    syncDraft: '',             // folder typed but not yet started
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
  function planText() {
    const s = sub();
    if (!s) return 'Not subscribed';
    if (s.active) return s.current_period_end ? `Active until ${fmtDate(s.current_period_end)}` : 'Active';
    if (s.status === 'past_due') return 'Payment overdue';
    if (s.status === 'canceled') return 'Cancelled';
    return 'Not subscribed';
  }
  function panelVisible(name) {
    const el = $(`panel-${name}`);
    return !!el && el.style.display !== 'none';
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
            offline: !!r.offline, live: !!r.live,
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
          <label class="detail-label" for="acct-password">Password</label>
          <input id="acct-password" class="input-text" type="password" autocomplete="current-password" placeholder="Password" required>
          <div id="acct-confirm-wrap" class="hidden">
            <label class="detail-label" for="acct-confirm">Confirm password</label>
            <input id="acct-confirm" class="input-text" type="password" autocomplete="new-password" placeholder="Repeat password">
            <div class="acct-hint">At least 8 characters.</div>
          </div>
          <div id="acct-error" class="acct-error hidden" role="alert"></div>
          <button id="acct-submit" class="btn btn-primary acct-submit" type="submit">Sign in</button>
        </form>
        <div id="acct-offline" class="acct-offline hidden">
          <span>Cannot reach the server.</span>
          <button type="button" class="btn btn-ghost" id="acct-retry">Retry</button>
          <button type="button" class="btn btn-ghost" id="acct-continue">Continue offline</button>
        </div>
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
    $('acct-server-save').addEventListener('click', () => saveServer($('acct-server-input').value, $('acct-server-msg')));
    $('acct-server-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); $('acct-server-save').click(); }
    });
  }

  function setTab(tab) {
    st.authTab = tab;
    document.querySelectorAll('#acct-overlay .acct-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const reg = tab === 'register';
    $('acct-confirm-wrap').classList.toggle('hidden', !reg);
    $('acct-submit').textContent = reg ? 'Create account' : 'Sign in';
    $('acct-title').textContent = reg ? 'Create your account' : 'Welcome';
    $('acct-sub').textContent = reg ? 'Sign up, then choose a monthly plan to unlock cloud storage.' : 'Sign in to your account to continue.';
    $('acct-password').setAttribute('autocomplete', reg ? 'new-password' : 'current-password');
    showAuthError('');
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
    if (!email || !pw) { showAuthError('Enter your email and password.'); return; }
    if (reg) {
      if (pw.length < 8) { showAuthError('Password must be at least 8 characters.'); return; }
      if (pw !== $('acct-confirm').value) { showAuthError('Passwords do not match.'); return; }
    }
    const btn = $('acct-submit');
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = reg ? 'Creating account…' : 'Signing in…';
    showAuthError('');
    try {
      const r = reg ? await SFM.accountRegister(email, pw) : await SFM.accountLogin(email, pw);
      if (r && r.ok) {
        $('acct-password').value = '';
        $('acct-confirm').value = '';
        toast(reg ? 'Account created' : `Signed in as ${email}`, 'success');
        await refresh();
        if (reg && !isActive()) App.switchPanel('account');
      } else {
        showAuthError((r && r.error) || 'Sign-in failed.');
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
      label = `${email} · ${isActive() ? 'Active' : 'No plan'}`;
      cls = st.offline ? 'off' : (isActive() ? 'on' : 'warn');
      if (st.offline) label += ' · offline';
    }
    text.textContent = label;
    dot.className = 'acct-chip-dot ' + cls;
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
        <p class="acct-muted">You are not signed in.</p>
        <div class="acct-actions"><button class="btn btn-primary" id="acct-show-login">Sign in…</button></div>
        ${serverBlock('acct-srv')}`;
    } else {
      const s = sub() || {};
      const active = isActive();
      const polling = !!st.pollTimer;
      html += `
        <div class="acct-card-flat">
          <div class="acct-kv"><span>Email</span><strong>${esc(st.user && st.user.email)}</strong></div>
          <div class="acct-kv"><span>Plan</span>
            <span><span class="acct-badge ${active ? 'on' : 'off'}">${esc(planText())}</span>
            ${s.plan_label ? `<span class="acct-muted"> · ${esc(s.plan_label)}</span>` : ''}</span></div>
          <div class="acct-kv"><span>Connection</span><span>${st.offline ? 'Offline' : (st.live ? '<span class="cloud-live on"></span> Live' : '<span class="cloud-live"></span> Connecting…')}</span></div>
        </div>
        ${!active ? `<p class="acct-muted">Subscribe to ${esc(s.plan_label || 'the monthly plan')} to unlock cloud storage, auto-sync and AI photo tools.</p>` : ''}
        ${polling ? '<p class="acct-hint">Waiting for payment to complete in your browser… this page updates automatically.</p>' : ''}
        <div class="acct-actions">
          ${!active ? `<button class="btn btn-primary" id="acct-subscribe" ${s.billing_available === false ? 'disabled title="Billing is not set up on this server"' : ''}>Subscribe</button>` : ''}
          ${(active || (s.status && s.status !== 'none' && s.status !== '')) ? '<button class="btn" id="acct-portal">Manage billing</button>' : ''}
          <button class="btn" id="acct-refresh">Refresh</button>
          <button class="btn btn-danger" id="acct-signout">Sign out</button>
        </div>
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

  function startPlanPolling() {
    stopPlanPolling();
    st.pollUntil = Date.now() + 10 * 60 * 1000;
    st.pollTimer = setInterval(async () => {
      if (Date.now() > st.pollUntil || !st.logged_in) { stopPlanPolling(); renderAccountPanel(); return; }
      await refresh();
      if (isActive()) { stopPlanPolling(); toast('Subscription active — thank you!', 'success', 5000); renderAll(); }
    }, 5000);
    renderAccountPanel();
  }
  function stopPlanPolling() {
    if (st.pollTimer) clearInterval(st.pollTimer);
    st.pollTimer = null;
  }

  async function signOut() {
    if (!confirm('Sign out of this account on this computer?')) return;
    stopPlanPolling();
    await SFM.accountLogout();
    st.files = []; st.selected.clear(); st.usage = null;
    st.overlayDismissed = false;
    toast('Signed out', 'info');
    await refresh();
  }

  // ── Cloud panel ──────────────────────────────────────────────────────────
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
        const ids = new Set(st.files.map(f => f.id));
        [...st.selected].forEach(id => { if (!ids.has(id)) st.selected.delete(id); });
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

  let _reloadTimer = null;
  function scheduleReload() {
    clearTimeout(_reloadTimer);
    _reloadTimer = setTimeout(() => { if (panelVisible('cloud')) loadCloud(); }, 400);
  }

  async function refreshSyncStatus() {
    try {
      const r = await SFM.cloudSyncStatus();
      if (r && r.ok) {
        st.sync = { running: !!r.running, folder: r.folder || '', remote_root: r.remote_root || '', last: r.last || null };
        if (typeof r.live === 'boolean') st.live = r.live;
        renderSyncSection();
      }
    } catch (e) {}
  }

  function renderCloudPanel() {
    const body = $('cloud-panel-body');
    if (!body) return;
    const head = `<div class="cloud-head"><h2 class="acct-h2">☁️ Cloud</h2>
      ${st.logged_in ? `<span class="cloud-live-wrap" title="${st.live ? 'Live updates connected' : 'Live updates not connected'}"><span class="cloud-live ${st.live ? 'on' : ''}"></span>${st.live ? 'Live' : 'Offline'}</span>` : ''}</div>`;

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
      body.innerHTML = head + `
        <div class="cloud-cta">
          <div class="cloud-cta-icon">🔒</div>
          <h3>Cloud storage is part of the monthly plan</h3>
          <p class="acct-muted">Keep your files safe online, open them on every computer, and auto-sync a folder in real time.</p>
          <button class="btn btn-primary" id="cloud-subscribe" ${s.billing_available === false ? 'disabled title="Billing is not set up on this server"' : ''}>Subscribe${s.plan_label ? ' — ' + esc(s.plan_label) : ''}</button>
          ${st.pollTimer ? '<p class="acct-hint">Waiting for payment to complete…</p>' : ''}
        </div>`;
      $('cloud-subscribe').addEventListener('click', subscribe);
      return;
    }

    const u = st.usage;
    const pct = u && u.quota ? Math.min(100, (u.used / u.quota) * 100) : 0;
    const trash = st.cloudView === 'trash';
    const nSel = st.selected.size;
    const allSel = st.files.length > 0 && nSel === st.files.length;

    let rows;
    if (st.listError) rows = `<div class="cloud-empty acct-error">${esc(st.listError)}</div>`;
    else if (!st.files.length) rows = `<div class="cloud-empty">${st.listLoading ? 'Loading…' : (trash ? 'Cloud trash is empty.' : 'No files in the cloud yet. Upload some, or start auto-sync below.')}</div>`;
    else {
      rows = st.files.map(f => `
        <label class="cloud-row ${st.selected.has(f.id) ? 'sel' : ''}" data-id="${f.id}">
          <input type="checkbox" class="cloud-check" data-id="${f.id}" ${st.selected.has(f.id) ? 'checked' : ''}>
          <span class="cloud-path" title="${esc(f.path)}">${esc(f.path)}</span>
          <span class="cloud-size">${esc(f.size_str || fmtSize(f.size))}</span>
          <span class="cloud-date">${esc(fmtDateTime(f.updated_at))}</span>
        </label>`).join('');
    }

    const up = st.uploadProgress;
    body.innerHTML = head + `
      <div class="cloud-usage">
        <div class="cloud-usage-bar"><div class="cloud-usage-fill ${pct > 90 ? 'full' : ''}" style="width:${pct.toFixed(1)}%"></div></div>
        <div class="acct-hint">${u ? `${esc(u.used_str)} of ${esc(u.quota_str)} used` : 'Usage unavailable'}</div>
      </div>

      <div class="cloud-toolbar">
        <div class="cloud-seg">
          <button class="cloud-seg-btn ${!trash ? 'active' : ''}" data-view="files">Files</button>
          <button class="cloud-seg-btn ${trash ? 'active' : ''}" data-view="trash">Trash</button>
        </div>
        ${!trash ? `
          <button class="btn" id="cloud-upload-sel" title="Upload the files selected in the Files workspace">⬆ Upload selected</button>
          <button class="btn" id="cloud-upload-browse">⬆ Upload files…</button>
          <input id="cloud-remote-dir" class="input-text cloud-remote" placeholder="Cloud folder (optional)" title="Uploads go into this cloud folder" value="${esc(st.remoteDir)}">
          <button class="btn" id="cloud-download" ${nSel ? '' : 'disabled'}>⬇ Download${nSel ? ` (${nSel})` : ''}</button>
          <button class="btn btn-danger" id="cloud-trash" ${nSel ? '' : 'disabled'}>Move to cloud trash</button>
        ` : `
          <button class="btn btn-primary" id="cloud-restore" ${nSel ? '' : 'disabled'}>↩ Restore${nSel ? ` (${nSel})` : ''}</button>
        `}
        <span class="flex-1"></span>
        <button class="btn btn-ghost" id="cloud-refresh" title="Refresh">🔄</button>
      </div>
      ${up ? `<div class="acct-hint cloud-progress">Uploading ${up.done + 1} of ${up.total}: ${esc(up.name)}</div>` : ''}

      <div class="cloud-list">
        <div class="cloud-row cloud-row-head">
          <input type="checkbox" id="cloud-check-all" ${allSel ? 'checked' : ''} ${st.files.length ? '' : 'disabled'}>
          <span class="cloud-path">Path</span><span class="cloud-size">Size</span><span class="cloud-date">Updated</span>
        </div>
        <div class="cloud-rows">${rows}</div>
      </div>

      <div class="acct-section" id="cloud-sync-section"></div>`;

    renderSyncSection();
    wireCloud();
  }

  function renderSyncSection() {
    const el = $('cloud-sync-section');
    if (!el) return;
    const s = st.sync;
    const last = s.last;
    let lastTxt = 'No sync yet.';
    if (last) {
      if (last.error) lastTxt = `Last sync failed: ${esc(last.error)}`;
      else lastTxt = `Last sync ${esc(fmtDateTime(last.time))}: ↑ ${last.uploaded || 0} uploaded, ↓ ${last.downloaded || 0} downloaded` +
        (last.conflicts ? `, ${last.conflicts} conflict${last.conflicts > 1 ? 's' : ''} (kept both)` : '') +
        (last.errors ? `, ${last.errors} error${last.errors > 1 ? 's' : ''}` : '');
    }
    el.innerHTML = `
      <div class="detail-section-title">Auto-sync folder
        ${s.running ? `<span class="cloud-live-wrap"><span class="cloud-live ${st.live ? 'on' : ''}"></span>${st.live ? 'Syncing live' : 'Syncing'}</span>` : ''}</div>
      <p class="acct-muted">Keeps a folder on this computer and a cloud folder of the same name in step. Nothing is ever removed on either side.</p>
      <div class="acct-row">
        <input id="cloud-sync-folder" class="input-text" placeholder="D:\\Documents\\Work" value="${esc(s.running ? s.folder : (st.syncDraft || s.folder))}" ${s.running ? 'disabled' : ''}>
        <button class="btn" id="cloud-sync-browse" ${s.running ? 'disabled' : ''}>Browse…</button>
        ${s.running ? '<button class="btn" id="cloud-sync-stop">⏹ Stop</button>'
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
      await SFM.cloudSyncStop();
      toast('Auto-sync stopped', 'info');
      refreshSyncStatus();
    });
  }

  function wireCloud() {
    const body = $('cloud-panel-body');
    body.querySelectorAll('.cloud-seg-btn').forEach(b => b.addEventListener('click', () => {
      if (st.cloudView === b.dataset.view) return;
      st.cloudView = b.dataset.view;
      st.files = []; st.selected.clear(); st.listError = null;
      renderCloudPanel();
      loadCloud();
    }));
    body.querySelectorAll('.cloud-check').forEach(cb => cb.addEventListener('change', () => {
      const id = Number(cb.dataset.id);
      if (cb.checked) st.selected.add(id); else st.selected.delete(id);
      renderCloudPanel();
    }));
    const all = $('cloud-check-all');
    if (all) all.addEventListener('change', () => {
      st.selected = all.checked ? new Set(st.files.map(f => f.id)) : new Set();
      renderCloudPanel();
    });

    const rd = $('cloud-remote-dir');
    if (rd) rd.addEventListener('input', () => { st.remoteDir = rd.value; });
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
      const ids = [...st.selected];
      if (!ids.length) return;
      const r = await SFM.call('browse_for_folder');
      if (!r || !r.ok || !r.path) return;
      const s = await SFM.cloudDownload(ids, r.path);
      if (!s || !s.ok) { toast('Download: ' + ((s && s.error) || 'failed'), 'error'); return; }
      App.setStatus(`Downloading ${ids.length} file${ids.length > 1 ? 's' : ''}…`, true);
    });
    w('cloud-trash', async () => {
      const ids = [...st.selected];
      if (!ids.length) return;
      const r = await SFM.cloudTrash(ids);
      if (r && r.ok) {
        toast(`Moved ${r.done} file${r.done === 1 ? '' : 's'} to cloud trash (restorable)`, 'success');
        st.selected.clear();
      } else toast('Trash failed: ' + ((r && r.error) || 'unknown error'), 'error');
      loadCloud();
    });
    w('cloud-restore', async () => {
      const ids = [...st.selected];
      if (!ids.length) return;
      const r = await SFM.cloudRestore(ids);
      if (r && r.ok) {
        toast(`Restored ${r.done} file${r.done === 1 ? '' : 's'}`, 'success');
        st.selected.clear();
      } else toast('Restore failed: ' + ((r && r.error) || 'unknown error'), 'error');
      loadCloud();
    });
  }

  async function startUpload(paths) {
    const dirEl = $('cloud-remote-dir');
    const remoteDir = dirEl ? dirEl.value.trim() : '';
    const r = await SFM.cloudUpload(paths, remoteDir);
    if (!r || !r.ok) { toast('Upload: ' + ((r && r.error) || 'failed'), 'error'); return; }
    App.setStatus(`Uploading ${paths.length} item${paths.length > 1 ? 's' : ''} to the cloud…`, true);
  }

  // ── Python → JS events ───────────────────────────────────────────────────
  function initEvents() {
    SFM.on('account_changed', p => {
      if (p && p.reason === 'session_expired') toast('Your session expired — please sign in again', 'warning', 5000);
      if (p && p.reason === 'subscription_updated' && p.subscription && st.user) {
        st.user = { ...st.user, subscription: p.subscription };
        if (p.subscription.active) stopPlanPolling();
      }
      refresh();
    });
    SFM.on('cloud_live', p => {
      st.live = !!(p && p.connected);
      renderChip();
      if (panelVisible('cloud')) renderCloudPanel();
      if (panelVisible('account')) renderAccountPanel();
    });
    SFM.on('cloud_event', p => {
      const ev = p && p.event;
      if (ev === 'file_updated' || ev === 'file_trashed') scheduleReload();
    });
    SFM.on('cloud_upload_progress', p => {
      st.uploadProgress = p;
      if (panelVisible('cloud')) {
        const el = document.querySelector('#cloud-panel-body .cloud-progress');
        if (el) el.textContent = `Uploading ${p.done + 1} of ${p.total}: ${p.name}`;
        else renderCloudPanel();
      }
    });
    SFM.on('cloud_upload_done', r => {
      st.uploadProgress = null;
      App.setStatus('Ready');
      if (r.ok) toast(`Uploaded ${r.uploaded} file${r.uploaded === 1 ? '' : 's'} to the cloud`, 'success');
      else {
        const first = (r.errors && r.errors[0] && r.errors[0].error) || r.error || 'unknown error';
        toast(`Uploaded ${r.uploaded || 0}, ${r.failed || 0} failed: ${first}`, 'error', 6000);
      }
      scheduleReload();
    });
    SFM.on('cloud_download_done', r => {
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
    SFM.on('cloud_sync_done', p => {
      st.sync.last = p;
      refreshSyncStatus();
      if ((p.uploaded || p.downloaded) && panelVisible('cloud')) scheduleReload();
      if (p.downloaded && App.state.currentFolder && p.folder &&
          App.state.currentFolder.toLowerCase().startsWith(p.folder.toLowerCase())) {
        try { FileTree.refresh(); } catch (e) {}
      }
    });
  }

  // ── boot ─────────────────────────────────────────────────────────────────
  function init() {
    buildOverlay();
    initEvents();
    const chip = $('acct-chip');
    if (chip) chip.addEventListener('click', () => {
      if (!st.logged_in && st.configured) { st.overlayDismissed = false; renderOverlay(); }
      else App.switchPanel('account');
    });
    document.querySelectorAll('.sidebar-btn[data-panel="cloud"]').forEach(b =>
      b.addEventListener('click', () => { renderCloudPanel(); loadCloud(); refreshSyncStatus(); }));
    document.querySelectorAll('.sidebar-btn[data-panel="account"]').forEach(b =>
      b.addEventListener('click', () => refresh()));
    renderAll();
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { refresh, isActive, state: st };
})();
