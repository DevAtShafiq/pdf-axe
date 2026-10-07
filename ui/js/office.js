/**
 * office.js — Office drive: one shared Country → Program → Student drive for
 * every staff member of an office, plus People & settings.
 *
 * Rendered inside the Cloud panel (account.js owns the panel and the
 * "Office drive | My files" switch and calls Office.render(body)) and as a
 * card in the Account panel (Office.accountCardHtml / wireAccountCard).
 *
 * Bridge (bridge.js → office_* / shared_*), events:
 *   office_changed {reason}, shared_changed {paths, actor, action},
 *   shared_upload_progress / shared_upload_done,
 *   shared_download_progress / shared_download_done.
 * Permissions are enforced by the server; the UI hides/disables what the
 * current role may not do (staff + enforce_hierarchy: no country/program
 * changes and no uploads above the student level).
 */
const Office = (() => {

  const LOCAL_DRAG = 'application/x-pdfaxe-paths';
  const SHARED_DRAG = 'application/x-officeaxe-shared';
  const LEVEL = {
    country: { icon: 'globe', label: 'Country', plural: 'countries' },
    program: { icon: 'graduation-cap', label: 'Program', plural: 'programs' },
    student: { icon: 'user', label: 'Student', plural: 'students' },
  };
  const DEPTH_LEVEL = [null, 'country', 'program', 'student'];
  const BAD_CHARS = /[<>:"/\\|?*\x00-\x1f]/;
  const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;
  const CODE_RE = /^OFX-[A-Z0-9]{4}-[A-Z0-9]{4}$/;
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const DEFAULT_SUBFOLDERS = ['Passport', 'Academic', 'Visa', 'Financial'];

  const st = {
    loaded: false, office: null, invites: [], error: null,
    view: 'drive',              // 'drive' | 'trash' | 'manage'
    cwd: '',
    lists: {},                  // path → { items, level, breadcrumb, error, loading, stale }
    expanded: new Set(['']),
    selected: new Set(), anchor: null,
    query: '', results: null, searching: false,
    activityOpen: false, activity: null, activityError: null,
    trash: null, trashError: null, trashSel: new Set(),
    usage: null,
    jobs: new Map(),            // job_id → progress
    members: null, membersError: null, pending: null, lastInvite: null,
    draft: null, draftDirty: false, progCountry: '',
    renaming: null,
    pendingDrop: null,          // {dir, t} — target of an Explorer drop
    lastToast: 0,
  };

  // ── helpers ──────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function toast(msg, type = 'info', ms) { App.toast(esc(msg), type, ms); }
  function err(r, fallback) { return (r && r.error) || fallback || 'Something went wrong'; }
  const depth = p => (p ? p.split('/').length : 0);
  const parentOf = p => (p && p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');
  const baseName = p => (p ? p.slice(p.lastIndexOf('/') + 1) : '');
  const join = (a, b) => (a ? a + '/' + b : b);
  const crumbText = p => (p ? p.split('/').join(' › ') : 'Office');
  const byName = (a, b) => (b.is_dir - a.is_dir) || a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
  const uniq = arr => { const seen = new Set(); return arr.filter(x => { const k = String(x).toLowerCase(); if (!x || seen.has(k)) return false; seen.add(k); return true; }); };
  const sortNames = arr => arr.slice().sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));

  function fmtSize(n) {
    n = Number(n) || 0;
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return i === 0 ? `${n} B` : `${n.toFixed(1)} ${u[i]}`;
  }
  function toSec(ts) {
    if (!ts) return 0;
    if (typeof ts === 'string') { const n = Number(ts); if (!isNaN(n)) ts = n; else return Date.parse(ts) / 1000 || 0; }
    return ts > 1e12 ? ts / 1000 : ts;
  }
  function fmtDateTime(ts) {
    const s = toSec(ts);
    if (!s) return '';
    try { return new Date(s * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); }
    catch (e) { return new Date(s * 1000).toLocaleString(); }
  }
  function fmtDate(ts) {
    const s = toSec(ts);
    if (!s) return '';
    try { return new Date(s * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
    catch (e) { return ''; }
  }
  function fmtRel(ts) {
    const s = toSec(ts);
    if (!s) return '';
    const d = Date.now() / 1000 - s;
    if (d < 45) return 'just now';
    if (d < 3600) return `${Math.max(1, Math.round(d / 60))} min ago`;
    if (d < 86400) return `${Math.round(d / 3600)} h ago`;
    if (d < 172800) return 'yesterday';
    if (d < 86400 * 7) return `${Math.floor(d / 86400)} days ago`;
    return fmtDate(s);
  }
  function personName(who) {
    const s = String(who || '').trim();
    if (!s) return '';
    if (!s.includes('@')) return s;
    return s.split('@')[0].split(/[._-]+/).filter(Boolean).map(w => w[0].toUpperCase() + w.slice(1)).join(' ');
  }
  function initialOf(who) { const n = personName(who); return n ? n[0].toUpperCase() : '?'; }
  function myEmail() { try { return ((Account.state.user || {}).email || '').toLowerCase(); } catch (e) { return ''; } }
  function isMe(who) { const m = myEmail(); return !!m && String(who || '').toLowerCase() === m; }

  function validateName(v, what = 'a name') {
    const s = String(v || '');
    if (!s.trim()) return `Enter ${what}.`;
    if (BAD_CHARS.test(s)) return 'A name can\'t contain \\ / : * ? " < > |';
    if (/[. ]$/.test(s)) return 'A name can\'t end with a dot or a space.';
    if (s === '.' || s === '..' || RESERVED.test(s.trim())) return 'That name is reserved by Windows — choose another.';
    if (s.length > 120) return 'Keep the name under 120 characters.';
    return '';
  }

  // ── roles & permissions (mirrors the server rules) ───────────────────────
  const role = () => (st.office && st.office.role) || 'staff';
  const isOwner = () => role() === 'owner';
  const isAdmin = () => role() === 'owner' || role() === 'admin';
  const settings = () => (st.office && st.office.settings) || {};
  const enforce = () => settings().enforce_hierarchy !== false;
  const canStructure = () => isAdmin() || !enforce();           // countries & programs
  const canModify = path => depth(path) >= 3 || canStructure();  // rename / move / trash
  const canUploadInto = dir => depth(dir) >= 3 || (canStructure() && depth(dir) >= 1);
  const canMkdirIn = dir => depth(dir) >= 3 || canStructure();
  function locked() { const p = st.office && st.office.plan; return !!(p && p.active === false); }
  function levelOf(item) {
    if (!item || !item.is_dir) return null;
    return item.level || DEPTH_LEVEL[depth(item.path)] || null;
  }
  function programsFor(country, s = settings()) {
    const p = s.programs;
    if (Array.isArray(p)) return p;
    if (p && typeof p === 'object') return p[country] || [];
    return [];
  }

  // ── data ─────────────────────────────────────────────────────────────────
  function rerender() {
    try { if (panelVisible('cloud') && Account.cloudArea() === 'office') Account.renderCloud(); } catch (e) {}
    try { if (panelVisible('account')) Account.renderAccount(); } catch (e) {}
  }
  function panelVisible(name) {
    const el = $(`panel-${name}`);
    return !!el && el.style.display !== 'none' && !el.classList.contains('hidden');
  }
  function driveVisible() {
    try { return panelVisible('cloud') && Account.cloudArea() === 'office' && !!$('ofx-root'); } catch (e) { return false; }
  }

  function resetDrive() {
    st.cwd = ''; st.lists = {}; st.expanded = new Set(['']); st.selected.clear();
    st.query = ''; st.results = null; st.activity = null; st.trash = null; st.trashSel.clear();
    st.usage = null; st.members = null; st.pending = null; st.draft = null; st.draftDirty = false;
    st.lastInvite = null; st.view = 'drive';
  }

  let _stateP = null;
  async function loadState() {
    if (_stateP) return _stateP;
    _stateP = (async () => {
      try {
        const r = await SFM.officeGetState();
        if (r && r.ok !== false) {
          const prev = st.office && st.office.id;
          st.office = r.office || null;
          st.invites = r.pending_invites || [];
          st.error = null;
          if (!st.office || st.office.id !== prev) resetDrive();
        } else st.error = err(r, 'Could not load your office');
      } catch (e) { st.error = String(e && e.message || e); }
      st.loaded = true;
      rerender();
      if (driveVisible()) enter();
    })();
    try { return await _stateP; } finally { _stateP = null; }
  }

  function reset() {
    st.loaded = false; st.office = null; st.invites = []; st.error = null;
    st.jobs.clear(); resetDrive();
  }

  async function load(path, quiet) {
    const prev = st.lists[path];
    st.lists[path] = Object.assign({ items: [], level: null }, prev || {}, { loading: true, stale: false });
    if (!quiet) renderParts();
    let r;
    try { r = await SFM.sharedList(path); } catch (e) { r = { ok: false, error: String(e && e.message || e) }; }
    if (r && r.ok !== false) {
      st.lists[path] = { items: (r.items || []).slice().sort(byName), level: r.level || DEPTH_LEVEL[depth(path)] || null,
                         breadcrumb: r.breadcrumb || null, loading: false };
    } else {
      st.lists[path] = Object.assign({ items: [] }, prev || {}, { loading: false, error: err(r, 'Could not load this folder') });
    }
    if (path === st.cwd) pruneSelection();
    renderParts();
    return st.lists[path];
  }

  async function loadUsage() {
    try {
      const r = await SFM.sharedUsage();
      if (r && r.ok !== false) { st.usage = r; renderUsage(); }
    } catch (e) {}
  }

  function enter() {
    if (!st.office || locked()) return;
    if (st.view === 'manage') { loadManage(); return; }
    if (!st.lists[''] || st.lists[''].stale) load('', true);
    ensurePath(st.cwd);
    if (st.view === 'trash') loadTrash();
    if (st.activityOpen) loadActivity();
    loadUsage();
  }

  // Load every ancestor of path (for the tree) and the path itself
  function ensurePath(path) {
    const parts = path ? path.split('/') : [];
    let acc = '';
    st.expanded.add('');
    parts.forEach(p => { acc = join(acc, p); st.expanded.add(parentOf(acc)); });
    const chain = [''];
    acc = '';
    parts.forEach(p => { acc = join(acc, p); chain.push(acc); });
    chain.forEach(p => { const l = st.lists[p]; if (!l || l.stale || (l.error && p === path)) load(p, true); });
  }

  function navigate(path, selectPath) {
    st.view = 'drive';
    st.cwd = path || '';
    st.selected = new Set(selectPath ? [selectPath] : []);
    st.anchor = selectPath || null;
    clearSearch(true);
    if (st.cwd) st.expanded.add(st.cwd);
    ensurePath(st.cwd);
    rebuildIfNeeded();
    renderParts();
    if (selectPath) setTimeout(() => {
      const row = document.querySelector(`#ofx-rows .ofx-row[data-path="${cssq(selectPath)}"]`);
      if (row) row.scrollIntoView({ block: 'nearest' });
    }, 350);
  }
  function cssq(s) { return String(s).replace(/["\\]/g, '\\$&'); }

  function cwdItems() { const l = st.lists[st.cwd]; return (l && l.items) || []; }
  function itemByPath(p) {
    const l = st.lists[parentOf(p)];
    return (l && l.items.find(i => i.path === p)) || null;
  }
  function pruneSelection() {
    const valid = new Set(cwdItems().map(i => i.path));
    [...st.selected].forEach(k => { if (!valid.has(k)) st.selected.delete(k); });
  }
  function selectedItems() { return cwdItems().filter(i => st.selected.has(i.path)); }

  // Mark folders touched by a change stale and reload the visible ones
  function invalidate(paths) {
    const dirs = new Set();
    (paths || []).forEach(p => {
      if (p == null) return;
      p = String(p).replace(/^\/+|\/+$/g, '');
      dirs.add(parentOf(p));
      if (st.lists[p]) dirs.add(p);
      let a = parentOf(p);   // child counts of ancestors change too
      while (a) { dirs.add(parentOf(a)); a = parentOf(a); }
    });
    dirs.forEach(d => {
      if (!st.lists[d]) return;
      if (d === st.cwd || st.expanded.has(d)) load(d, true);
      else st.lists[d].stale = true;
    });
  }

  // ── rendering: shell ─────────────────────────────────────────────────────
  function shellKey() {
    if (!st.loaded) return 'loading';
    if (!st.office) return st.error ? 'error' : 'onboard';
    const o = st.office;
    const base = [o.id, o.name, o.role, o.member_count].join('|');
    if (st.view === 'manage') return 'manage|' + base;
    if (locked()) return 'locked|' + base;
    return 'drive|' + base;
  }

  function render(body) {
    if (!body) return;
    const key = shellKey();
    let root = $('ofx-root');
    if (!root || !body.contains(root) || root.dataset.key !== key) {
      body.innerHTML = Account.areaSwitchHtml() + `<div id="ofx-root" class="ofx-root">${shellHtml(key)}</div>`;
      root = $('ofx-root');
      root.dataset.key = key;
      Account.wireAreaSwitch(body);
      wireShell(key);
    }
    renderParts();
  }
  function rebuildIfNeeded() {
    const root = $('ofx-root');
    if (root && root.dataset.key !== shellKey()) rerender();
  }

  function headHtml(subtitle, right = '') {
    const o = st.office;
    const rl = role();
    const pill = `<span class="ofx-role ofx-role-${esc(rl)}">${rl === 'owner' ? Icons.svg('crown', 12) : ''}${esc(rl[0].toUpperCase() + rl.slice(1))}</span>`;
    return `<div class="page-header ofx-head">
        <div class="page-header-icon">${Icons.svg('building', 20)}</div>
        <div class="flex-1 ofx-min0">
          <h2 class="page-title acct-h2"><span class="truncate">${esc(o.name)}</span>${pill}</h2>
          <p class="page-subtitle">${subtitle}</p>
        </div>${right}
      </div>`;
  }

  function shellHtml(key) {
    const kind = key.split('|')[0];
    if (kind === 'loading') return `<div class="cloud-empty"><span class="spinner"></span> Loading your office…</div>`;
    if (kind === 'error') return `<div class="callout error">${Icons.svg('alert-circle', 16)}<div class="callout-body"><div class="callout-title">Couldn't load the office drive</div>${esc(st.error)}</div>
        <button class="btn btn-sm" data-act="retry-state">Retry</button></div>`;
    if (kind === 'onboard') return onboardHtml();
    if (kind === 'manage') return manageShellHtml();
    if (kind === 'locked') return lockedHtml();
    const o = st.office;
    const n = Number(o.member_count) || 0;
    return headHtml(`Shared drive for ${n} ${n === 1 ? 'person' : 'people'} · Country › Program › Student`,
        `<div class="cloud-usage" id="ofx-usage"></div>`) + `
      <div class="cloud-toolbar ofx-toolbar">
        <div class="ofx-actions" id="ofx-actions"></div>
        <span class="flex-1"></span>
        <div class="input-group ofx-search">
          <span class="input-icon">${Icons.svg('search', 14)}</span>
          <input id="ofx-search" class="input-text" type="text" autocomplete="off" spellcheck="false"
                 placeholder="Search students, folders, files" value="${esc(st.query)}" aria-label="Search the office drive">
          <button class="icon-btn input-action ${st.query ? '' : 'hidden'}" id="ofx-search-clear" title="Clear search" aria-label="Clear search">${Icons.svg('x', 14)}</button>
        </div>
        <span class="toolbar-sep toolbar-sep-sm"></span>
        <button class="icon-btn" data-act="toggle-activity" id="ofx-btn-activity" title="Activity" aria-label="Activity">${Icons.svg('history', 16)}</button>
        <button class="icon-btn" data-act="toggle-trash" id="ofx-btn-trash" title="Office trash" aria-label="Office trash">${Icons.svg('trash', 16)}</button>
        <button class="icon-btn" data-act="manage" title="People &amp; settings" aria-label="People and settings">${Icons.svg('users', 16)}</button>
        <button class="icon-btn" data-act="refresh" title="Refresh (F5)" aria-label="Refresh">${Icons.svg('refresh', 16)}</button>
      </div>
      <div id="ofx-transfers" class="ofx-transfers"></div>
      <div class="ofx-layout" id="ofx-layout">
        <nav class="ofx-tree" id="ofx-tree" aria-label="Office folders"></nav>
        <section class="ofx-main" id="ofx-main"></section>
        <aside class="ofx-activity" id="ofx-activity" aria-label="Activity"></aside>
      </div>`;
  }

  function renderParts() {
    const root = $('ofx-root');
    if (!root) return;
    const kind = (root.dataset.key || '').split('|')[0];
    if (kind === 'onboard') { renderInvites(); return; }
    if (kind === 'manage') { renderManage(); return; }
    if (kind !== 'drive') return;
    renderUsage();
    renderActions();
    renderTree();
    renderMain();
    renderActivity();
    renderTransfers();
    $('ofx-layout').classList.toggle('with-activity', st.activityOpen);
    $('ofx-btn-activity').classList.toggle('active', st.activityOpen);
    $('ofx-btn-trash').classList.toggle('active', st.view === 'trash');
  }

  function renderUsage() {
    const el = $('ofx-usage');
    if (!el) return;
    const u = st.usage;
    const pct = u && u.quota ? Math.min(100, (u.used / u.quota) * 100) : 0;
    el.innerHTML = `
      <div class="cloud-usage-text"><strong>${u ? esc(fmtSize(u.used)) : '—'}</strong> <span>of ${u && u.quota ? esc(fmtSize(u.quota)) : '—'} used by the office</span></div>
      <div class="cloud-usage-bar"><div class="cloud-usage-fill ${pct > 90 ? 'full' : ''}" style="width:${pct.toFixed(1)}%"></div></div>`;
    el.title = 'Shared storage for everyone in the office. Files in the office trash do not count.';
  }

  function renderActions() {
    const el = $('ofx-actions');
    if (!el) return;
    const d = depth(st.cwd);
    const b = (act, icon, label, primary, title = '', disabled = false) =>
      `<button class="btn btn-sm ${primary ? 'btn-primary' : ''}" data-act="${act}" ${title ? `title="${esc(title)}"` : ''} ${disabled ? 'disabled' : ''}>${Icons.svg(icon, 14)}${label}</button>`;
    let h = '';
    if (st.view === 'trash') {
      h = b('back-drive', 'arrow-left', 'Back to drive', false);
    } else if (d >= 3) {
      h = b('upload', 'cloud-upload', 'Upload files…', true, `Upload into ${crumbText(st.cwd)}`)
        + b('upload-selected', 'upload', 'Upload selected', false, 'Upload the files selected in the Files workspace')
        + b('new-folder', 'folder-plus', 'New folder', false)
        + b('new-student', 'user-plus', 'New student', false);
    } else {
      h = b('new-student', 'user-plus', 'New student', true, 'Create a student folder with its standard subfolders');
      if (d === 0 && canStructure()) h += b('add-country', 'globe', 'Add country', false);
      if (d === 1 && canStructure()) h += b('add-program', 'graduation-cap', 'Add program', false);
    }
    el.innerHTML = h;
  }

  // ── tree ─────────────────────────────────────────────────────────────────
  function nodeIcon(level, size = 14) {
    if (level && LEVEL[level]) return `<span class="ofx-lvl-icon lvl-${level}">${Icons.svg(LEVEL[level].icon, size)}</span>`;
    return `<span class="ft ft-folder">${Icons.svg('folder', size)}</span>`;
  }
  function renderTree() {
    const el = $('ofx-tree');
    if (!el) return;
    const scroll = el.scrollTop;
    const activeDir = st.view === 'drive' && !st.query ? st.cwd : null;
    const node = (path, name, level, d, count) => {
      const l = st.lists[path];
      const open = st.expanded.has(path);
      const dirs = l && !l.stale ? l.items.filter(i => i.is_dir) : null;
      const hasKids = dirs ? dirs.length > 0 : (count == null || count > 0);
      const icon = path === '' ? `<span class="ofx-lvl-icon lvl-office">${Icons.svg('building', 14)}</span>` : nodeIcon(level);
      let h = `<div class="ofx-node ${activeDir === path ? 'active' : ''}" data-node="${esc(path)}" data-drop-dir="${esc(path)}" style="--d:${d}" title="${esc(crumbText(path))}">
          <button class="ofx-twisty ${open ? 'open' : ''} ${hasKids ? '' : 'leaf'}" data-twisty="${esc(path)}" tabindex="-1" aria-label="${open ? 'Collapse' : 'Expand'}">${Icons.svg('chevron-right', 12)}</button>
          ${icon}<span class="ofx-node-name">${esc(name)}</span>
          ${l && l.loading ? '<span class="spinner ofx-node-spin"></span>' : ''}
        </div>`;
      if (open && dirs) dirs.forEach(c => { h += node(c.path, c.name, levelOf(c), d + 1, c.child_count); });
      return h;
    };
    el.innerHTML = `<div class="ofx-tree-title section-title">Folders</div>` + node('', st.office.name, null, 0, null)
      + `<div class="ofx-tree-legend">
          <span>${nodeIcon('country', 12)}Country</span><span>${nodeIcon('program', 12)}Program</span><span>${nodeIcon('student', 12)}Student</span>
        </div>`;
    el.scrollTop = scroll;
  }

  // ── main area ────────────────────────────────────────────────────────────
  function levelPill(level) {
    return level && LEVEL[level] ? `<span class="ofx-pill lvl-${level}">${LEVEL[level].label}</span>` : '';
  }
  function itemIcon(it) {
    const lv = levelOf(it);
    if (lv) return nodeIcon(lv, 14);
    return Icons.file(it.is_dir ? { is_dir: true } : { name: it.name }, 16);
  }
  function crumbsHtml(path) {
    const l = st.lists[path];
    let parts;
    if (l && Array.isArray(l.breadcrumb) && l.breadcrumb.length) {
      parts = l.breadcrumb.filter(c => c && c.path).map(c => ({ name: c.name, path: c.path, level: c.level || DEPTH_LEVEL[depth(c.path)] }));
    } else {
      let acc = '';
      parts = (path ? path.split('/') : []).map(n => { acc = join(acc, n); return { name: n, path: acc, level: DEPTH_LEVEL[depth(acc)] }; });
    }
    const crumb = (p, name, label, icon) => `<button class="ofx-crumb" data-cd="${esc(p)}" data-drop-dir="${esc(p)}">
        <span class="ofx-crumb-level">${esc(label)}</span><span class="ofx-crumb-name">${icon}${esc(name)}</span></button>`;
    return [crumb('', st.office.name, 'Office', Icons.svg('building', 13))].concat(parts.map(c =>
      `<span class="ofx-crumb-sep">${Icons.svg('chevron-right', 12)}</span>` +
      crumb(c.path, c.name, c.level && LEVEL[c.level] ? LEVEL[c.level].label : 'Folder',
            c.level && LEVEL[c.level] ? Icons.svg(LEVEL[c.level].icon, 13) : Icons.svg('folder', 13)))).join('');
  }

  function renderMain() {
    const el = $('ofx-main');
    if (!el) return;
    if (st.query) { el.innerHTML = searchHtml(); return; }
    if (st.view === 'trash') { el.innerHTML = trashHtml(); return; }
    const rows = $('ofx-rows');
    const scroll = rows ? rows.scrollTop : 0;
    const l = st.lists[st.cwd];
    const items = cwdItems();
    const d = depth(st.cwd);
    const childLevel = DEPTH_LEVEL[d + 1];
    let body;
    if (!l || (l.loading && !items.length)) body = `<div class="cloud-empty"><span class="spinner"></span></div>`;
    else if (l.error && !items.length) body = `<div class="cloud-empty"><div class="callout error">${Icons.svg('alert-circle', 16)}<span>${esc(l.error)}</span></div>
        <button class="btn btn-sm" data-act="refresh">Try again</button></div>`;
    else if (!items.length) body = emptyHtml(d);
    else body = items.map(rowHtml).join('');

    const selbar = selbarHtml(items, childLevel);

    el.innerHTML = `
      <div class="ofx-main-head">
        <nav class="ofx-crumbs" aria-label="Location">${crumbsHtml(st.cwd)}</nav>
        <div class="ofx-selbar">${selbar}</div>
      </div>
      <div class="ofx-list" id="ofx-list" data-drop-dir="${esc(st.cwd)}" tabindex="0">
        <div class="ofx-row ofx-row-head">
          <span>Name</span><span>Type</span><span class="ofx-num">Items / size</span><span>Updated</span><span>Updated by</span>
        </div>
        <div class="ofx-rows" id="ofx-rows">${body}</div>
        <div class="ofx-drop-hint"><div>${Icons.svg('cloud-upload', 28)}<div id="ofx-drop-text">Drop to upload</div></div></div>
      </div>
      <div class="ofx-foot">${footHint(d)}</div>`;
    const r2 = $('ofx-rows');
    if (r2) r2.scrollTop = scroll;
    if (st.renaming) beginInlineRename(st.renaming, true);
  }

  function selbarHtml(items, childLevel) {
    const sel = selectedItems();
    const n = sel.length;
    let selbar;
    if (n) {
      const allMod = sel.every(i => canModify(i.path));
      const one = n === 1 ? sel[0] : null;
      const sb = (act, icon, label, cls = '', dis = false, title = '') =>
        `<button class="btn btn-sm btn-ghost ${cls}" data-act="${act}" ${dis ? 'disabled' : ''} ${title ? `title="${esc(title)}"` : ''}>${Icons.svg(icon, 14)}${label}</button>`;
      selbar = `<span class="ofx-selcount">${n} selected</span>`
        + (one ? sb('open-sel', one.is_dir ? 'folder-open' : 'external-link', 'Open') : '')
        + sb('download', 'download', 'Download')
        + sb('rename', 'pencil', 'Rename', '', !(one && canModify(one.path)), 'Rename (F2)')
        + sb('move', 'folder-input', 'Move…', '', !allMod)
        + sb('trash', 'trash', 'Trash', 'ofx-danger-ghost', !allMod, allMod ? 'Move to the office trash (Del)' : 'Only admins can remove countries and programs')
        + `<button class="icon-btn icon-btn-sm" data-act="clear-sel" title="Clear selection" aria-label="Clear selection">${Icons.svg('x', 14)}</button>`;
    } else {
      const dirs = items.filter(i => i.is_dir).length, files = items.length - dirs;
      const noun = childLevel && LEVEL[childLevel] ? (dirs === 1 ? LEVEL[childLevel].label.toLowerCase() : LEVEL[childLevel].plural) : (dirs === 1 ? 'folder' : 'folders');
      const bits = [];
      if (dirs) bits.push(`${dirs} ${noun}`);
      if (files) bits.push(`${files} file${files === 1 ? '' : 's'}`);
      selbar = `<span class="acct-hint">${bits.join(' · ')}</span>`;
    }

    return selbar;
  }
  // Selection changed: update rows and the selection bar in place, so a
  // double-click still lands on the same row element.
  function refreshSelection() {
    if (st.query || st.view !== 'drive') { renderMain(); return; }
    document.querySelectorAll('#ofx-rows .ofx-row[data-path]').forEach(r => r.classList.toggle('sel', st.selected.has(r.dataset.path)));
    const bar = document.querySelector('#ofx-main .ofx-selbar');
    if (bar) bar.innerHTML = selbarHtml(cwdItems(), DEPTH_LEVEL[depth(st.cwd) + 1]);
  }

  function footHint(d) {
    if (d >= 3) return canUploadInto(st.cwd)
      ? `${Icons.svg('info', 12)}Drag files or folders from Windows Explorer or the Files workspace onto this list or onto a folder to upload.`
      : '';
    if (!canStructure() && d < 2) return `${Icons.svg('lock', 12)}Only office admins can add or change ${d === 0 ? 'countries' : 'programs'}. Use “New student” to add a student.`;
    return `${Icons.svg('info', 12)}Files go inside a student folder. Use “New student” to create one with its standard subfolders.`;
  }

  function emptyHtml(d) {
    const box = (icon, title, text, btn = '') => `<div class="cloud-empty"><div class="empty-state-icon">${Icons.svg(icon, 24)}</div>
        <div class="empty-state-title">${title}</div><div class="empty-state-text">${text}</div>${btn}</div>`;
    const here = esc(baseName(st.cwd));
    if (d === 0) return box('globe', 'No countries yet', canStructure()
      ? 'Start by adding the countries your office works with, e.g. Bangladesh or Nepal.' : 'An office admin needs to add the countries first.',
      canStructure() ? `<button class="btn btn-primary btn-sm" data-act="add-country">${Icons.svg('globe', 14)}Add country</button>` : '');
    if (d === 1) return box('graduation-cap', `No programs in ${here} yet`, canStructure()
      ? 'Add the programs you offer here, e.g. MBBS or BBA.' : 'An office admin needs to add programs for this country.',
      canStructure() ? `<button class="btn btn-primary btn-sm" data-act="add-program">${Icons.svg('graduation-cap', 14)}Add program</button>` : '');
    if (d === 2) return box('user', `No students in ${here} yet`, 'Create a student folder — it gets the standard subfolders automatically.',
      `<button class="btn btn-primary btn-sm" data-act="new-student">${Icons.svg('user-plus', 14)}New student</button>`);
    return box('cloud-upload', 'This folder is empty', canUploadInto(st.cwd) ? 'Drop files here or use “Upload files…”.' : '');
  }

  function rowHtml(it) {
    const sel = st.selected.has(it.path);
    const lv = levelOf(it);
    const type = lv ? levelPill(lv) : it.is_dir ? '<span class="ofx-pill">Folder</span>'
      : `<span class="ofx-type">${esc(Icons.fileType((/\.[^.]+$/.exec(it.name) || [''])[0]).label)}</span>`;
    const cnt = it.child_count;
    const num = it.is_dir ? (cnt == null ? '' : `${cnt} item${cnt === 1 ? '' : 's'}`) : fmtSize(it.size);
    const by = it.updated_by || it.created_by || '';
    return `<div class="ofx-row ${sel ? 'sel' : ''} ${it.is_dir ? 'is-dir' : ''}" data-path="${esc(it.path)}" data-dir="${it.is_dir ? 1 : 0}"
          ${it.is_dir ? `data-drop-dir="${esc(it.path)}"` : ''} draggable="true">
        <span class="ofx-name">${itemIcon(it)}<span class="ofx-name-text" title="${esc(it.name)}">${esc(it.name)}</span></span>
        <span>${type}</span>
        <span class="ofx-num">${esc(num)}</span>
        <span class="ofx-date" title="${esc(fmtDateTime(it.updated_at))}">${esc(fmtRel(it.updated_at))}</span>
        <span class="ofx-by" title="${esc(by)}">${by ? `<span class="ofx-avatar">${esc(initialOf(by))}</span>${esc(isMe(by) ? 'You' : personName(by))}` : ''}</span>
      </div>`;
  }

  // ── search ───────────────────────────────────────────────────────────────
  let _searchTimer = null, _searchSeq = 0;
  function onSearchInput(v) {
    st.query = v;
    const clr = $('ofx-search-clear');
    if (clr) clr.classList.toggle('hidden', !v);
    clearTimeout(_searchTimer);
    if (!v.trim()) { st.results = null; st.searching = false; renderMain(); renderTree(); return; }
    st.searching = true;
    renderMain(); renderTree();
    _searchTimer = setTimeout(async () => {
      const seq = ++_searchSeq;
      let r;
      try { r = await SFM.sharedSearch(v.trim()); } catch (e) { r = { ok: false, error: String(e) }; }
      if (seq !== _searchSeq || st.query !== v) return;
      st.searching = false;
      st.results = r && r.ok !== false ? (r.items || []) : { error: err(r, 'Search failed') };
      renderMain();
    }, 250);
  }
  function clearSearch(silent) {
    clearTimeout(_searchTimer);
    st.query = ''; st.results = null; st.searching = false;
    const i = $('ofx-search');
    if (i) i.value = '';
    const c = $('ofx-search-clear');
    if (c) c.classList.add('hidden');
    if (!silent) renderParts();
  }
  function hl(name, q) {
    const i = name.toLowerCase().indexOf(q.toLowerCase());
    if (!q || i < 0) return esc(name);
    return esc(name.slice(0, i)) + `<mark class="hl">${esc(name.slice(i, i + q.length))}</mark>` + esc(name.slice(i + q.length));
  }
  function searchHtml() {
    const q = st.query.trim();
    const res = st.results;
    let body;
    if (st.searching && !Array.isArray(res)) body = `<div class="cloud-empty"><span class="spinner"></span> Searching…</div>`;
    else if (res && res.error) body = `<div class="cloud-empty"><div class="callout error">${Icons.svg('alert-circle', 16)}<span>${esc(res.error)}</span></div></div>`;
    else if (!res || !res.length) body = `<div class="cloud-empty"><div class="empty-state-icon">${Icons.svg('search', 24)}</div>
        <div class="empty-state-title">No matches for “${esc(q)}”</div><div class="empty-state-text">Try part of the student's name, a program or a file name.</div></div>`;
    else body = res.map((it, i) => {
      const lv = levelOf(it);
      const where = parentOf(it.path);
      return `<div class="ofx-result ${i === 0 ? 'first' : ''}" data-result="${esc(it.path)}" data-dir="${it.is_dir ? 1 : 0}" tabindex="-1">
          <span class="ofx-result-icon">${itemIcon(it)}</span>
          <div class="ofx-result-main">
            <div class="ofx-result-name"><span class="truncate">${hl(it.name, q)}</span>${lv ? levelPill(lv) : ''}</div>
            <div class="ofx-result-path">${Icons.svg('building', 11)}${esc(st.office.name)}${where ? ' › ' + esc(crumbText(where)) : ''}</div>
          </div>
          <span class="ofx-date">${esc(fmtRel(it.updated_at))}</span>
          <span class="ofx-result-go">${Icons.svg(it.is_dir ? 'chevron-right' : 'external-link', 14)}</span>
        </div>`;
    }).join('');
    const n = Array.isArray(res) ? res.length : 0;
    return `<div class="ofx-main-head">
        <div class="ofx-search-title">${Icons.svg('search', 14)}Results for “${esc(q)}”${n ? ` <span class="acct-hint">${n} found · Enter opens the first</span>` : ''}</div>
        <div class="ofx-selbar"><button class="btn btn-sm btn-ghost" data-act="clear-search">${Icons.svg('x', 14)}Clear search</button></div>
      </div>
      <div class="ofx-list ofx-results">${body}</div>`;
  }
  function openResult(path, isDir) {
    if (isDir) navigate(path);
    else navigate(parentOf(path), path);
  }

  // ── trash ────────────────────────────────────────────────────────────────
  async function loadTrash() {
    let r;
    try { r = await SFM.sharedTrashList(); } catch (e) { r = { ok: false, error: String(e) }; }
    if (r && r.ok !== false) { st.trash = r.items || []; st.trashError = null; }
    else st.trashError = err(r, 'Could not load the trash');
    const ids = new Set((st.trash || []).map(t => String(t.id)));
    [...st.trashSel].forEach(k => { if (!ids.has(k)) st.trashSel.delete(k); });
    renderParts();
  }
  function trashHtml() {
    const items = st.trash;
    let body;
    if (st.trashError) body = `<div class="cloud-empty"><div class="callout error">${Icons.svg('alert-circle', 16)}<span>${esc(st.trashError)}</span></div></div>`;
    else if (!items) body = `<div class="cloud-empty"><span class="spinner"></span></div>`;
    else if (!items.length) body = `<div class="cloud-empty"><div class="empty-state-icon">${Icons.svg('trash', 24)}</div>
        <div class="empty-state-title">The office trash is empty</div><div class="empty-state-text">Anything moved to the trash from the office drive can be restored here.</div></div>`;
    else body = items.map(t => {
      const sel = st.trashSel.has(String(t.id));
      const isDir = t.is_dir != null ? t.is_dir : !/\.[^./]+$/.test(t.name || '');
      return `<div class="ofx-row ofx-trash-row ${sel ? 'sel' : ''}" data-trash="${esc(t.id)}">
          <span class="ofx-name">${Icons.file(isDir ? { is_dir: true } : { name: t.name }, 16)}<span class="ofx-name-text">${esc(t.name)}</span></span>
          <span class="ofx-where" title="${esc(crumbText(parentOf(t.path)))}">${esc(crumbText(parentOf(t.path)))}</span>
          <span class="ofx-date" title="${esc(fmtDateTime(t.deleted_at))}">${esc(fmtRel(t.deleted_at))}</span>
          <span class="ofx-by" title="${esc(t.deleted_by)}">${t.deleted_by ? `<span class="ofx-avatar">${esc(initialOf(t.deleted_by))}</span>${esc(isMe(t.deleted_by) ? 'You' : personName(t.deleted_by))}` : ''}</span>
        </div>`;
    }).join('');
    const n = st.trashSel.size;
    return `<div class="ofx-main-head">
        <nav class="ofx-crumbs"><button class="ofx-crumb" data-cd=""><span class="ofx-crumb-level">Office</span><span class="ofx-crumb-name">${Icons.svg('building', 13)}${esc(st.office.name)}</span></button>
          <span class="ofx-crumb-sep">${Icons.svg('chevron-right', 12)}</span>
          <span class="ofx-crumb current"><span class="ofx-crumb-level">View</span><span class="ofx-crumb-name">${Icons.svg('trash', 13)}Trash</span></span></nav>
        <div class="ofx-selbar">${n ? `<span class="ofx-selcount">${n} selected</span>` : ''}
          <button class="btn btn-sm btn-primary" data-act="restore" ${n ? '' : 'disabled'}>${Icons.svg('restore', 14)}Restore${n ? ` (${n})` : ''}</button></div>
      </div>
      <div class="ofx-list">
        <div class="ofx-row ofx-trash-row ofx-row-head"><span>Name</span><span>Original location</span><span>Deleted</span><span>Deleted by</span></div>
        <div class="ofx-rows">${body}</div>
      </div>
      <div class="ofx-foot">${Icons.svg('info', 12)}Restored items go back to their original folder. Nothing is ever deleted from this computer.</div>`;
  }
  async function restoreSelected() {
    const ids = [...st.trashSel].map(k => { const t = (st.trash || []).find(x => String(x.id) === k); return t ? t.id : k; });
    if (!ids.length) return;
    const r = await SFM.sharedRestore(ids);
    if (!r || r.ok === false) { toast('Restore failed: ' + err(r), 'error', 5000); return; }
    toast(`Restored ${ids.length} item${ids.length === 1 ? '' : 's'}`, 'success');
    st.trashSel.clear();
    loadTrash();
    invalidate((st.trash || []).filter(t => ids.includes(t.id)).map(t => t.path));
    loadUsage();
  }

  // ── activity ─────────────────────────────────────────────────────────────
  async function loadActivity() {
    let r;
    try { r = await SFM.sharedActivity(60); } catch (e) { r = { ok: false, error: String(e) }; }
    if (r && r.ok !== false) { st.activity = r.events || []; st.activityError = null; }
    else st.activityError = err(r, 'Could not load activity');
    renderActivity();
  }
  let _actTimer = null;
  function scheduleActivity() { clearTimeout(_actTimer); _actTimer = setTimeout(() => { if (st.activityOpen) loadActivity(); }, 500); }

  const VERB = {
    upload: ['uploaded', 'to'], uploaded: ['uploaded', 'to'],
    mkdir: ['created folder', 'in'], create: ['created', 'in'], new_student: ['added student', 'in'], add_student: ['added student', 'in'],
    rename: ['renamed', 'in'], move: ['moved', 'to'], trash: ['moved to trash', 'from'], delete: ['moved to trash', 'from'],
    restore: ['restored', 'to'], download: ['downloaded', 'from'], open: ['opened', 'in'],
    create_folder: ['created folder', 'in'], replace: ['updated', 'in'], purge: ['permanently removed', 'from'],
  };
  // Office/membership events: whole sentences, no file path.
  const ROLE_WORD = { owner: 'owner', admin: 'admin', staff: 'staff member' };
  const OFFICE_EVENT = {
    office_created:        d => `created the office${d ? ` “${d}”` : ''}`,
    office_renamed:        d => `renamed the office${d ? ` to “${d}”` : ''}`,
    member_joined:         d => `joined the office${ROLE_WORD[d] ? ` as ${ROLE_WORD[d]}` : ''}`,
    member_invited:        d => `invited ${d || 'someone'}`,
    invite_revoked:        d => `cancelled the invitation${d ? ` for ${d}` : ''}`,
    member_left:           () => 'left the office',
    member_removed:        d => `removed ${d || 'a member'} from the office`,
    role_changed:          d => `changed a role${d ? ` (${d})` : ''}`,
    ownership_transferred: d => `transferred ownership${d ? ` to ${d}` : ''}`,
    settings_changed:      () => 'updated the folder settings',
  };
  // "8230 bytes" from the server → "8.0 KB"
  function prettyDetail(d) {
    const m = /^(\d+) bytes$/.exec(String(d || '').trim());
    if (!m) return d;
    const n = Number(m[1]);
    return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  }
  function eventHtml(e) {
    const who = isMe(e.user) ? 'You' : personName(e.user) || 'Someone';
    const path = String(e.path || '');
    const name = baseName(path);
    const where = parentOf(path);
    if (OFFICE_EVENT[e.action]) {
      return `<div class="ofx-event" title="${esc(fmtDateTime(e.ts))}">
          <span class="ofx-avatar ofx-avatar-md">${esc(initialOf(e.user))}</span>
          <div class="ofx-event-main"><div class="ofx-event-text"><strong>${esc(who)}</strong> ${esc(OFFICE_EVENT[e.action](e.detail))}</div><div class="acct-hint">${esc(fmtRel(e.ts))}</div></div>
        </div>`;
    }
    e = Object.assign({}, e, { detail: prettyDetail(e.detail) });
    const [verb, prep] = VERB[e.action] || [String(e.action || 'changed').replace(/_/g, ' '), 'in'];
    let text = `<strong>${esc(who)}</strong> ${esc(verb)}`;
    if (e.action === 'rename') text += ` <span class="ofx-ev-obj">${esc(name)}</span> to <span class="ofx-ev-obj">${esc(e.detail || '')}</span>`;
    else if (e.action === 'move' && e.detail) text += ` <span class="ofx-ev-obj">${esc(name)}</span> to <span class="ofx-ev-where">${esc(crumbText(e.detail))}</span>`;
    else if (name) {
      text += ` <span class="ofx-ev-obj">${esc(name)}</span>`;
      if (where) text += ` ${prep} <span class="ofx-ev-where">${esc(crumbText(where))}</span>`;
      if (e.detail && e.action !== 'move') text += ` <span class="acct-hint">· ${esc(e.detail)}</span>`;
    } else if (e.detail) text += ` ${esc(e.detail)}`;
    return `<div class="ofx-event" ${path ? `data-goto="${esc(path)}"` : ''} title="${esc(fmtDateTime(e.ts))}${path ? '\nClick to show it' : ''}">
        <span class="ofx-avatar ofx-avatar-md">${esc(initialOf(e.user))}</span>
        <div class="ofx-event-main"><div class="ofx-event-text">${text}</div><div class="acct-hint">${esc(fmtRel(e.ts))}</div></div>
      </div>`;
  }
  function renderActivity() {
    const el = $('ofx-activity');
    if (!el) return;
    if (!st.activityOpen) { el.innerHTML = ''; return; }
    let list;
    if (st.activityError) list = `<div class="acct-list-empty">${esc(st.activityError)}</div>`;
    else if (!st.activity) list = `<div class="acct-list-empty"><span class="spinner"></span> Loading…</div>`;
    else if (!st.activity.length) list = `<div class="acct-list-empty">No activity yet.</div>`;
    else list = st.activity.map(eventHtml).join('');
    el.innerHTML = `<div class="ofx-act-head"><span class="ofx-act-title">${Icons.svg('history', 14)}Activity</span>
        <span class="cloud-live-wrap" title="Updates live as staff work"><span class="cloud-live ${Account.state.live ? 'on' : ''}"></span>Live</span>
        <button class="icon-btn icon-btn-sm" data-act="toggle-activity" title="Close" aria-label="Close activity">${Icons.svg('x', 14)}</button></div>
      <div class="ofx-act-list">${list}</div>`;
  }

  // ── transfers ────────────────────────────────────────────────────────────
  function renderTransfers() {
    const el = $('ofx-transfers');
    if (!el) return;
    let h = '';
    st.jobs.forEach((j, id) => {
      const pct = j.total_bytes ? Math.min(100, (j.bytes / j.total_bytes) * 100) : (j.total ? (j.index / j.total) * 100 : 0);
      const up = j.kind === 'up';
      const what = j.file ? `${up ? 'Uploading' : 'Downloading'} ${Math.min(j.index + 1, j.total || 1)} of ${j.total || '…'}: ${esc(baseName(String(j.file).replace(/\\/g, '/')))}`
        : `${up ? 'Preparing upload to' : 'Preparing download from'} ${esc(up ? crumbText(j.dir) : j.label)}…`;
      h += `<div class="cloud-progress ofx-progress" data-job="${esc(id)}">
          <span class="cloud-progress-icon">${Icons.svg(up ? 'cloud-upload' : 'cloud-download', 18)}</span>
          <div class="cloud-progress-main">
            <div class="cloud-progress-text">${what}
              ${j.total_bytes ? `<span class="acct-hint">${esc(fmtSize(j.bytes))} / ${esc(fmtSize(j.total_bytes))}</span>` : ''}</div>
            <div class="cloud-usage-bar"><div class="cloud-usage-fill" style="width:${pct.toFixed(1)}%"></div></div>
          </div>
          <span class="acct-hint ofx-progress-pct">${Math.round(pct)}%</span></div>`;
    });
    el.innerHTML = h;
  }
  function newJobId(kind) { return `${kind}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`; }

  async function startUpload(localPaths, dir) {
    localPaths = (localPaths || []).filter(Boolean);
    if (!localPaths.length) return;
    if (!st.office || locked()) { toast('The office drive is not available', 'warning'); return; }
    if (!canUploadInto(dir)) {
      toast(depth(dir) < 3 ? 'Open a student folder first — files are uploaded inside a student folder' : 'You can\'t upload here', 'warning', 5000);
      return;
    }
    const job = newJobId('up');
    st.jobs.set(job, { kind: 'up', dir, total: localPaths.length, index: 0, file: '', bytes: 0, total_bytes: 0 });
    renderTransfers();
    let r;
    try { r = await SFM.sharedUpload(localPaths, dir, job); } catch (e) { r = { ok: false, error: String(e) }; }
    if (!r || r.ok === false) { st.jobs.delete(job); renderTransfers(); toast('Upload failed: ' + err(r), 'error', 6000); return; }
    if (r.job_id && r.job_id !== job) { st.jobs.set(r.job_id, st.jobs.get(job)); st.jobs.delete(job); }
    App.setStatus(`Uploading ${localPaths.length} item${localPaths.length > 1 ? 's' : ''} to ${crumbText(dir)}…`, true);
  }

  async function downloadPaths(paths) {
    if (!paths.length) return;
    const pick = await SFM.call('browse_for_folder');
    if (!pick || !pick.ok || !pick.path) return;
    const job = newJobId('dn');
    st.jobs.set(job, { kind: 'dn', label: paths.length === 1 ? baseName(paths[0]) : `${paths.length} items`, total: 0, index: 0, file: '', bytes: 0, total_bytes: 0, dest: pick.path });
    renderTransfers();
    let r;
    try { r = await SFM.sharedDownload(paths, pick.path, job); } catch (e) { r = { ok: false, error: String(e) }; }
    if (!r || r.ok === false) { st.jobs.delete(job); renderTransfers(); toast('Download failed: ' + err(r), 'error', 6000); return; }
    if (r.job_id && r.job_id !== job) { st.jobs.set(r.job_id, st.jobs.get(job)); st.jobs.delete(job); }
    App.setStatus(`Downloading to ${pick.path}…`, true);
  }

  async function openPath(it) {
    if (!it) return;
    if (it.is_dir) { navigate(it.path); return; }
    App.setStatus(`Opening ${it.name}…`, true);
    let r;
    try { r = await SFM.sharedOpen(it.path); } catch (e) { r = { ok: false, error: String(e) }; }
    App.setStatus('Ready');
    if (!r || r.ok === false) toast('Could not open the file: ' + err(r), 'error', 5000);
  }

  // ── create / rename / move / trash ───────────────────────────────────────
  function existsIn(dir, name) {
    const l = st.lists[dir];
    return !!(l && l.items.some(i => i.name.toLowerCase() === String(name).toLowerCase()));
  }
  async function mkdirFlow(kind) {
    const dir = st.cwd;
    const conf = {
      country: { title: 'Add country', label: 'Country name', placeholder: 'e.g. Bangladesh', icon: 'globe', ok: 'Add country' },
      program: { title: 'Add program', label: 'Program name', placeholder: 'e.g. MBBS', icon: 'graduation-cap', ok: 'Add program', subtitle: baseName(dir) },
      folder: { title: 'New folder', label: 'Folder name', placeholder: 'e.g. Offer letters', icon: 'folder-plus', ok: 'Create folder', subtitle: crumbText(dir) },
    }[kind];
    const name = await Dialogs.ask({ title: conf.title, label: conf.label, placeholder: conf.placeholder, icon: conf.icon,
      subtitle: conf.subtitle, okLabel: conf.ok, okIcon: 'plus',
      validate: v => validateName(v, kind === 'folder' ? 'a folder name' : `a ${kind} name`) || (existsIn(dir, v) ? `“${v}” already exists here.` : '') });
    if (!name) return;
    const r = await SFM.sharedMkdir(dir, name);
    if (!r || r.ok === false) { toast(`Could not create “${name}”: ${err(r)}`, 'error', 5000); return; }
    toast(`${kind === 'folder' ? 'Folder' : LEVEL[kind].label} “${name}” created`, 'success');
    await load(dir, true);
    if (r.path) { st.selected = new Set([r.path]); renderParts(); }
  }

  function beginInlineRename(path, restoring) {
    const row = document.querySelector(`#ofx-rows .ofx-row[data-path="${cssq(path)}"]`);
    if (!row) { if (!restoring) renameDialog(path); st.renaming = null; return; }
    st.renaming = path;
    const it = itemByPath(path);
    const cell = row.querySelector('.ofx-name-text');
    if (!cell || !it) return;
    row.draggable = false;
    cell.outerHTML = `<input class="input-text ofx-rename-input" value="${esc(it.name)}" spellcheck="false" aria-label="New name">
      <span class="ofx-rename-err hidden"></span>`;
    const input = row.querySelector('.ofx-rename-input');
    const errEl = row.querySelector('.ofx-rename-err');
    let done = false;
    const finish = async commit => {
      if (done) return;
      const v = input.value;
      if (commit && v !== it.name) {
        const e = validateName(v) || (existsIn(parentOf(path), v) && v.toLowerCase() !== it.name.toLowerCase() ? `“${v}” already exists here.` : '');
        if (e) { errEl.textContent = e; errEl.classList.remove('hidden'); input.classList.add('is-invalid'); input.focus(); return; }
      }
      done = true;
      st.renaming = null;
      if (!commit || v === it.name) { renderMain(); focusList(); return; }
      input.disabled = true;
      await doRename(path, v);
      focusList();
    };
    input.addEventListener('keydown', e => {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('input', () => { errEl.classList.add('hidden'); input.classList.remove('is-invalid'); });
    input.addEventListener('blur', () => setTimeout(() => { if (!done && document.activeElement !== input) finish(true); }, 120));
    ['click', 'dblclick', 'mousedown'].forEach(t => input.addEventListener(t, e => e.stopPropagation()));
    setTimeout(() => {
      input.focus();
      const dot = it.is_dir ? -1 : it.name.lastIndexOf('.');
      if (dot > 0) input.setSelectionRange(0, dot); else input.select();
    }, 20);
  }
  async function renameDialog(path) {
    const it = itemByPath(path) || { name: baseName(path), is_dir: true, path };
    const lv = levelOf(it);
    const name = await Dialogs.ask({ title: `Rename ${lv ? LEVEL[lv].label.toLowerCase() : it.is_dir ? 'folder' : 'file'}`, label: 'New name',
      value: it.name, selectStem: !it.is_dir, icon: 'pencil', subtitle: crumbText(parentOf(path)), okLabel: 'Rename',
      validate: v => validateName(v) || (v.toLowerCase() !== it.name.toLowerCase() && existsIn(parentOf(path), v) ? `“${v}” already exists here.` : '') });
    if (!name || name === it.name) return;
    await doRename(path, name);
  }
  async function doRename(path, name) {
    const r = await SFM.sharedRename(path, name);
    if (!r || r.ok === false) { toast('Rename failed: ' + err(r), 'error', 5000); renderMain(); return; }
    const np = r.path || join(parentOf(path), name);
    toast(`Renamed to “${name}”`, 'success');
    if (st.cwd === path || st.cwd.startsWith(path + '/')) st.cwd = np + st.cwd.slice(path.length);
    st.selected = new Set([np]);
    Object.keys(st.lists).forEach(k => { if (k === path || k.startsWith(path + '/')) delete st.lists[k]; });
    if (st.expanded.has(path)) { st.expanded.delete(path); st.expanded.add(np); }
    await load(parentOf(path), true);
    ensurePath(st.cwd);
  }

  async function trashPaths(paths) {
    if (!paths.length) return;
    const items = paths.map(p => itemByPath(p) || { path: p, name: baseName(p), is_dir: true });
    if (!items.every(i => canModify(i.path))) { toast('Only office admins can remove countries and programs', 'warning'); return; }
    const one = items.length === 1 ? items[0] : null;
    const lv = one && levelOf(one);
    const what = one ? `“${one.name}”` : `${items.length} items`;
    const dirs = items.filter(i => i.is_dir).length;
    const ok = await Dialogs.confirm({
      title: 'Move to office trash', icon: 'trash', tone: 'warning', okLabel: 'Move to trash', okIcon: 'trash',
      message: `Move ${what}${lv ? ` (${LEVEL[lv].label.toLowerCase()})` : ''} to the office trash?`,
      detail: (dirs ? 'Everything inside the selected folders goes with it. ' : '') + 'It disappears for everyone in the office until someone restores it from Trash.',
    });
    if (!ok) return;
    const r = await SFM.sharedTrash(paths);
    if (!r || r.ok === false) { toast('Could not move to trash: ' + err(r), 'error', 5000); return; }
    toast(`Moved ${what} to the office trash`, 'success');
    st.selected.clear();
    invalidate(paths);
    loadUsage();
    if (paths.some(p => st.cwd === p || st.cwd.startsWith(p + '/'))) navigate(parentOf(paths[0]));
  }

  // Move: a student folder goes into a program, country/program folders keep
  // their level, files and other folders go anywhere inside a student.
  function moveRule(it) {
    const d = depth(it.path);
    if (it.is_dir && d <= 3) return { dest: d - 1, text: d === 3 ? 'a program' : d === 2 ? 'a country' : 'the office root' };
    return { min: 3, text: 'a student folder' };
  }
  function moveAllowed(items, dest) {
    if (dest == null) return 'Choose a destination.';
    for (const it of items) {
      if (dest === it.path || dest.startsWith(it.path + '/')) return `Can't move “${it.name}” into itself.`;
      if (parentOf(it.path) === dest && items.length === 1) return 'It is already in this folder.';
      const rule = moveRule(it);
      const dd = depth(dest);
      if (rule.dest != null ? dd !== rule.dest : dd < rule.min) return `“${it.name}” can only go into ${rule.text}.`;
    }
    return '';
  }
  async function movePaths(paths, destDir) {
    const items = paths.map(p => itemByPath(p) || { path: p, name: baseName(p), is_dir: false });
    const why = moveAllowed(items, destDir);
    if (why) { toast(why, 'warning', 4500); return false; }
    const r = await SFM.sharedMove(paths, destDir);
    if (!r || r.ok === false) { toast('Move failed: ' + err(r), 'error', 5000); return false; }
    toast(`Moved ${items.length === 1 ? `“${items[0].name}”` : `${items.length} items`} to ${crumbText(destDir)}`, 'success');
    st.selected.clear();
    invalidate(paths.concat([join(destDir, '_')]));
    return true;
  }
  function openMoveDialog(paths) {
    const items = paths.map(p => itemByPath(p)).filter(Boolean);
    if (!items.length) return;
    const pick = { sel: null, expanded: new Set(['', ...[...st.expanded]]) };
    const body = `<div class="field"><div class="field-label">Destination</div>
        <div class="ofx-picker" id="ofx-move-tree"></div>
        <div class="field-hint" id="ofx-move-hint"></div></div>`;
    const ov = Dialogs.openModal('ofx-move', 'Move to…', body, [
      { label: 'Cancel', onClick: () => ov._close() },
      { label: 'Move here', primary: true, icon: 'folder-input', onClick: async () => {
        if (await movePaths(paths, pick.sel)) ov._close();
      } },
    ], { icon: 'folder-input', subtitle: items.length === 1 ? items[0].name : `${items.length} items` });
    const okBtn = ov.querySelector('.modal-footer .btn-primary');
    const draw = () => {
      const tree = ov.querySelector('#ofx-move-tree');
      if (!tree) return;
      const node = (path, name, d) => {
        const l = st.lists[path];
        const open = pick.expanded.has(path);
        const dirs = l ? l.items.filter(i => i.is_dir) : null;
        const lv = path === '' ? null : (DEPTH_LEVEL[d] || null);
        const why = moveAllowed(items, path);
        let h = `<div class="ofx-node ${pick.sel === path ? 'active' : ''} ${why ? 'is-dim' : ''}" data-pick="${esc(path)}" style="--d:${d}">
            <button class="ofx-twisty ${open ? 'open' : ''} ${dirs && !dirs.length ? 'leaf' : ''}" data-ptw="${esc(path)}" tabindex="-1">${Icons.svg('chevron-right', 12)}</button>
            ${path === '' ? `<span class="ofx-lvl-icon lvl-office">${Icons.svg('building', 14)}</span>` : nodeIcon(lv)}
            <span class="ofx-node-name">${esc(name)}</span>${l && l.loading ? '<span class="spinner ofx-node-spin"></span>' : ''}</div>`;
        if (open && dirs) dirs.forEach(c => { h += node(c.path, c.name, d + 1); });
        return h;
      };
      tree.innerHTML = node('', st.office.name, 0);
      const why = moveAllowed(items, pick.sel);
      ov.querySelector('#ofx-move-hint').textContent = pick.sel == null
        ? `Pick ${moveRule(items[0]).text}.` : (why || `Move to ${crumbText(pick.sel)}`);
      okBtn.disabled = !!why;
    };
    const ensure = async p => { if (!st.lists[p] || st.lists[p].stale) { await load(p, true); } draw(); };
    ov.querySelector('#ofx-move-tree').addEventListener('click', e => {
      const tw = e.target.closest('[data-ptw]');
      if (tw) {
        const p = tw.dataset.ptw;
        if (pick.expanded.has(p)) pick.expanded.delete(p); else { pick.expanded.add(p); ensure(p); }
        draw(); return;
      }
      const n = e.target.closest('[data-pick]');
      if (n) { pick.sel = n.dataset.pick; pick.expanded.add(pick.sel); ensure(pick.sel); }
    });
    [...pick.expanded].forEach(p => { if (!st.lists[p]) ensure(p); });
    draw();
  }

  // ── New student ──────────────────────────────────────────────────────────
  async function openNewStudent() {
    if (!st.lists[''] || st.lists[''].stale) await load('', true);
    const s = settings();
    const parts = st.cwd ? st.cwd.split('/') : [];
    const allowNew = canStructure();
    const countries = () => sortNames(uniq([...(s.countries || []), ...((st.lists[''] || {}).items || []).filter(i => i.is_dir).map(i => i.name)]));
    const programs = c => sortNames(uniq([...programsFor(c, s), ...(c && st.lists[c] ? st.lists[c].items.filter(i => i.is_dir).map(i => i.name) : [])]));
    const opt = (v, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(v)}</option>`;
    const subs = (s.student_subfolders && s.student_subfolders.length ? s.student_subfolders : []);
    const body = `
      <div class="field-grid">
        <div class="field">
          <label class="field-label" for="ns-country">${Icons.svg('globe', 12)} Country</label>
          <select id="ns-country" class="input-text"></select>
          <input id="ns-country-new" class="input-text hidden" placeholder="New country name" spellcheck="false">
        </div>
        <div class="field">
          <label class="field-label" for="ns-program">${Icons.svg('graduation-cap', 12)} Program</label>
          <select id="ns-program" class="input-text"></select>
          <input id="ns-program-new" class="input-text hidden" placeholder="New program name" spellcheck="false">
        </div>
      </div>
      <div class="field">
        <label class="field-label" for="ns-name">${Icons.svg('user', 12)} Student name</label>
        <input id="ns-name" class="input-text" placeholder="e.g. Rahim Uddin" autocomplete="off" spellcheck="false">
        <div class="field-error hidden" id="ns-err"></div>
        <div class="field-hint">Use the name as written on the passport. These characters are not allowed: \\ / : * ? " &lt; &gt; |</div>
      </div>
      <div class="ofx-ns-preview" id="ns-preview"></div>
      ${!allowNew ? `<div class="acct-hint">${Icons.svg('lock', 12)} Only office admins can add new countries or programs.</div>` : ''}`;
    const ov = Dialogs.openModal('ofx-new-student', 'New student', body, [
      { label: 'Cancel', onClick: () => ov._close() },
      { label: 'Create student', primary: true, icon: 'user-plus', onClick: () => submit() },
    ], { icon: 'user-plus', subtitle: 'Creates the student folder with its standard subfolders' });
    const q = id => ov.querySelector('#' + id);
    const cSel = q('ns-country'), pSel = q('ns-program'), cNew = q('ns-country-new'), pNew = q('ns-program-new'), nameIn = q('ns-name'), errEl = q('ns-err');
    const NEW = '__new__';
    const val = (sel, inp) => (sel.value === NEW ? inp.value.trim() : sel.value);
    const fillCountries = cur => {
      const list = countries();
      cSel.innerHTML = (list.length ? '' : '<option value="" disabled selected>No countries yet</option>') + list.map(c => opt(c, cur)).join('')
        + (allowNew ? `<option value="${NEW}">+ Add new country…</option>` : '');
      if (!list.length && allowNew) cSel.value = NEW;
      cNew.classList.toggle('hidden', cSel.value !== NEW);
    };
    const fillPrograms = cur => {
      const c = val(cSel, cNew);
      const list = cSel.value === NEW ? programsFor('', s).concat(Array.isArray(s.programs) ? [] : []) : programs(c);
      pSel.innerHTML = (list.length ? '' : '<option value="" disabled selected>No programs yet</option>') + list.map(p => opt(p, cur)).join('')
        + (allowNew ? `<option value="${NEW}">+ Add new program…</option>` : '');
      if (!list.length && allowNew) pSel.value = NEW;
      pNew.classList.toggle('hidden', pSel.value !== NEW);
    };
    const preview = () => {
      const c = val(cSel, cNew), p = val(pSel, pNew), n = nameIn.value.trim();
      const exists = c && p && n && existsIn(join(c, p), n);
      q('ns-preview').innerHTML = `
        <div class="ofx-ns-path">${nodeIcon('country', 12)}<span>${esc(c || 'Country')}</span>${Icons.svg('chevron-right', 12)}
          ${nodeIcon('program', 12)}<span>${esc(p || 'Program')}</span>${Icons.svg('chevron-right', 12)}
          ${nodeIcon('student', 12)}<strong>${esc(n || 'Student name')}</strong></div>
        ${exists ? `<div class="acct-hint ofx-warn">${Icons.svg('info', 12)} This student already exists — it will be opened instead.</div>`
          : subs.length ? `<div class="ofx-ns-subs"><span class="acct-hint">Subfolders:</span>${subs.map(x => `<span class="ofx-chip ofx-chip-static">${Icons.svg('folder', 12)}${esc(x)}</span>`).join('')}</div>` : ''}`;
    };
    const loadPrograms = async () => {
      const c = val(cSel, cNew);
      if (c && cSel.value !== NEW && (!st.lists[c] || st.lists[c].stale)) { await load(c, true); fillPrograms(pSel.value); preview(); }
    };
    const loadStudents = async () => {
      const c = val(cSel, cNew), p = val(pSel, pNew);
      if (c && p && cSel.value !== NEW && pSel.value !== NEW && !st.lists[join(c, p)]) { await load(join(c, p), true); preview(); }
    };
    fillCountries(parts[0] || '');
    fillPrograms(parts[1] || '');
    preview();
    loadPrograms().then(loadStudents);
    cSel.addEventListener('change', () => { cNew.classList.toggle('hidden', cSel.value !== NEW); if (cSel.value === NEW) cNew.focus(); fillPrograms(''); preview(); loadPrograms().then(loadStudents); });
    pSel.addEventListener('change', () => { pNew.classList.toggle('hidden', pSel.value !== NEW); if (pSel.value === NEW) pNew.focus(); preview(); loadStudents(); });
    [cNew, pNew, nameIn].forEach(i => i.addEventListener('input', () => { errEl.classList.add('hidden'); i.classList.remove('is-invalid'); preview(); }));
    [cNew, pNew, nameIn].forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }));
    setTimeout(() => (cSel.value === NEW ? cNew : nameIn).focus(), 40);

    async function submit() {
      const c = val(cSel, cNew), p = val(pSel, pNew), n = nameIn.value.trim();
      const fail = (input, msg) => { errEl.textContent = msg; errEl.classList.remove('hidden'); input.classList.add('is-invalid'); input.focus(); };
      let e;
      if ((e = validateName(c, 'a country'))) return fail(cSel.value === NEW ? cNew : cSel, 'Country: ' + e);
      if ((e = validateName(p, 'a program'))) return fail(pSel.value === NEW ? pNew : pSel, 'Program: ' + e);
      if ((e = validateName(n, 'the student\'s name'))) return fail(nameIn, e);
      const btn = ov.querySelector('.modal-footer .btn-primary');
      btn.classList.add('is-loading'); btn.disabled = true;
      let r;
      try { r = await SFM.sharedNewStudent(c, p, n); } catch (x) { r = { ok: false, error: String(x) }; }
      btn.classList.remove('is-loading'); btn.disabled = false;
      if (!r || r.ok === false) { fail(nameIn, err(r, 'Could not create the student')); return; }
      ov._close();
      const path = r.path || join(join(c, p), n);
      if (r.existed) toast(`${n} already exists in ${c} › ${p} — opened it`, 'info', 4500);
      else toast(`Created ${n} in ${c} › ${p}${(r.created || []).length > 1 ? ` with ${(r.created.length - 1)} subfolders` : ''}`, 'success');
      [c, join(c, p), '', path].forEach(k => { if (st.lists[k]) st.lists[k].stale = true; });
      st.expanded.add(c); st.expanded.add(join(c, p));
      navigate(path);
    }
  }

  // ── context menu ─────────────────────────────────────────────────────────
  function closeMenu() { const m = $('ofx-menu'); if (m) m.remove(); }
  function showMenu(x, y, entries) {
    closeMenu();
    const m = document.createElement('div');
    m.id = 'ofx-menu';
    m.className = 'context-menu';
    m.setAttribute('role', 'menu');
    m.innerHTML = entries.map((e, i) => e === '-' ? '<div class="ctx-sep"></div>'
      : `<div class="ctx-item ${e.danger ? 'danger' : ''} ${e.disabled ? 'disabled' : ''}" role="menuitem" data-i="${i}" ${e.title ? `title="${esc(e.title)}"` : ''}>
          <span class="ctx-icon">${Icons.svg(e.icon, 16)}</span><span class="ctx-label">${esc(e.label)}</span>${e.key ? `<span class="ctx-shortcut">${esc(e.key)}</span>` : ''}</div>`).join('');
    document.body.appendChild(m);
    const r = m.getBoundingClientRect();
    m.style.left = Math.min(x, window.innerWidth - r.width - 8) + 'px';
    m.style.top = Math.min(y, window.innerHeight - r.height - 8) + 'px';
    m.addEventListener('click', e => {
      const it = e.target.closest('[data-i]');
      if (!it) return;
      closeMenu();
      const en = entries[Number(it.dataset.i)];
      if (en && en.run) en.run();
    });
    setTimeout(() => {
      const away = e => { if (!m.contains(e.target)) { closeMenu(); document.removeEventListener('mousedown', away, true); } };
      document.addEventListener('mousedown', away, true);
    }, 0);
  }
  function rowMenu(x, y) {
    const sel = selectedItems();
    if (!sel.length) return bgMenu(x, y);
    const one = sel.length === 1 ? sel[0] : null;
    const paths = sel.map(i => i.path);
    const allMod = sel.every(i => canModify(i.path));
    const lv = one && levelOf(one);
    const e = [];
    if (one) e.push({ icon: one.is_dir ? 'folder-open' : 'external-link', label: one.is_dir ? 'Open' : 'Open with default app', key: 'Enter', run: () => openPath(one) });
    if (lv === 'program') e.push({ icon: 'user-plus', label: 'New student here…', run: () => { navigate(one.path); openNewStudent(); } });
    if (one && one.is_dir && canUploadInto(one.path)) e.push({ icon: 'cloud-upload', label: 'Upload files here…', run: () => pickAndUpload(one.path) });
    e.push({ icon: 'download', label: `Download${sel.length > 1 ? ` ${sel.length} items` : ''}…`, run: () => downloadPaths(paths) });
    e.push('-');
    e.push({ icon: 'pencil', label: 'Rename', key: 'F2', disabled: !(one && canModify(one.path)), run: () => beginInlineRename(one.path) });
    e.push({ icon: 'folder-input', label: 'Move to…', disabled: !allMod, run: () => openMoveDialog(paths) });
    e.push({ icon: 'copy', label: 'Copy location', run: () => { SFM.setClipboard(paths.map(p => `${st.office.name} › ${crumbText(p)}`).join('\n')); toast('Location copied', 'success', 2000); } });
    e.push('-');
    e.push({ icon: 'trash', label: 'Move to trash', key: 'Del', danger: true, disabled: !allMod,
      title: allMod ? '' : 'Only office admins can remove countries and programs', run: () => trashPaths(paths) });
    showMenu(x, y, e);
  }
  function bgMenu(x, y) {
    const d = depth(st.cwd);
    const e = [{ icon: 'user-plus', label: 'New student…', run: openNewStudent }];
    if (d === 0 && canStructure()) e.push({ icon: 'globe', label: 'Add country…', run: () => mkdirFlow('country') });
    if (d === 1 && canStructure()) e.push({ icon: 'graduation-cap', label: 'Add program…', run: () => mkdirFlow('program') });
    if (d >= 3) {
      e.push({ icon: 'cloud-upload', label: 'Upload files…', run: () => pickAndUpload(st.cwd) });
      e.push({ icon: 'folder-plus', label: 'New folder…', run: () => mkdirFlow('folder') });
    }
    e.push('-', { icon: 'refresh', label: 'Refresh', key: 'F5', run: refreshAll });
    showMenu(x, y, e);
  }

  async function pickAndUpload(dir) {
    if (!canUploadInto(dir)) { toast('Open a student folder first — files are uploaded inside a student folder', 'warning', 5000); return; }
    const r = await SFM.cloudPickFiles();
    if (r && r.ok && r.paths && r.paths.length) startUpload(r.paths, dir);
    else if (r && r.ok === false) toast(err(r, 'Could not open the file picker'), 'error');
  }
  function refreshAll() {
    Object.keys(st.lists).forEach(k => { st.lists[k].stale = true; });
    ensurePath(st.cwd);
    [...st.expanded].forEach(p => { if (st.lists[p] && st.lists[p].stale) load(p, true); });
    if (st.view === 'trash') loadTrash();
    if (st.activityOpen) loadActivity();
    loadUsage();
  }
  function focusList() { const l = $('ofx-list'); if (l) l.focus({ preventScroll: true }); }

  // ── shell wiring (delegated) ─────────────────────────────────────────────
  function wireShell(key) {
    const root = $('ofx-root');
    const kind = key.split('|')[0];
    root.addEventListener('click', e => {
      const a = e.target.closest('[data-act]');
      if (a && !a.disabled) { onAction(a.dataset.act, a); return; }
    });
    if (kind === 'onboard') { wireOnboard(); return; }
    if (kind === 'manage') { wireManage(); return; }
    if (kind === 'locked') return;
    if (kind !== 'drive') return;

    const search = $('ofx-search');
    search.addEventListener('input', () => onSearchInput(search.value));
    search.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); clearSearch(); search.blur(); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        const first = Array.isArray(st.results) && st.results[0];
        if (first) openResult(first.path, first.is_dir);
      }
    });
    $('ofx-search-clear').addEventListener('click', () => { clearSearch(); search.focus(); });

    const layout = $('ofx-layout');
    layout.addEventListener('click', e => {
      if (e.target.closest('[data-act]')) return;
      const tw = e.target.closest('[data-twisty]');
      if (tw) {
        const p = tw.dataset.twisty;
        if (st.expanded.has(p)) st.expanded.delete(p);
        else { st.expanded.add(p); if (!st.lists[p] || st.lists[p].stale) load(p, true); }
        renderTree(); return;
      }
      const node = e.target.closest('[data-node]');
      if (node) { navigate(node.dataset.node); return; }
      const cr = e.target.closest('[data-cd]');
      if (cr) { if (st.view === 'trash') st.view = 'drive'; navigate(cr.dataset.cd); return; }
      const res = e.target.closest('[data-result]');
      if (res) { openResult(res.dataset.result, res.dataset.dir === '1'); return; }
      const ev = e.target.closest('[data-goto]');
      if (ev) { gotoPath(ev.dataset.goto); return; }
      const tr = e.target.closest('[data-trash]');
      if (tr) {
        const k = tr.dataset.trash;
        if (e.ctrlKey || e.metaKey) { if (st.trashSel.has(k)) st.trashSel.delete(k); else st.trashSel.add(k); }
        else st.trashSel = new Set([k]);
        renderMain(); return;
      }
      const row = e.target.closest('.ofx-row[data-path]');
      if (row) { clickRow(row.dataset.path, e); return; }
      if (e.target.closest('#ofx-rows') && !e.ctrlKey && !e.shiftKey && st.selected.size) { st.selected.clear(); refreshSelection(); }
    });
    layout.addEventListener('dblclick', e => {
      const row = e.target.closest('.ofx-row[data-path]');
      if (row) { const it = itemByPath(row.dataset.path); if (it) openPath(it); }
    });
    layout.addEventListener('contextmenu', e => {
      if (st.view !== 'drive' || st.query) return;
      if (e.target.closest('input')) return;
      const row = e.target.closest('.ofx-row[data-path]');
      const node = e.target.closest('[data-node]');
      if (!row && !node && !e.target.closest('#ofx-list')) return;
      e.preventDefault();
      if (node) {
        const p = node.dataset.node;
        if (!p) { navigate(''); bgMenu(e.clientX, e.clientY); return; }
        if (parentOf(p) !== st.cwd) navigate(parentOf(p), p); else { st.selected = new Set([p]); renderMain(); }
        rowMenu(e.clientX, e.clientY);
        return;
      }
      if (row) {
        if (!st.selected.has(row.dataset.path)) { st.selected = new Set([row.dataset.path]); st.anchor = row.dataset.path; refreshSelection(); }
        rowMenu(e.clientX, e.clientY);
      } else bgMenu(e.clientX, e.clientY);
    });
    wireDrag(layout);
  }

  function gotoPath(path) {
    const it = itemByPath(path);
    if (it && it.is_dir) navigate(path);
    else navigate(parentOf(path), path);
  }

  function clickRow(path, e) {
    const items = cwdItems();
    if (e.shiftKey && st.anchor) {
      const a = items.findIndex(i => i.path === st.anchor), b = items.findIndex(i => i.path === path);
      if (a >= 0 && b >= 0) {
        const [s, t] = a < b ? [a, b] : [b, a];
        const range = items.slice(s, t + 1).map(i => i.path);
        st.selected = new Set(e.ctrlKey || e.metaKey ? [...st.selected, ...range] : range);
      }
    } else if (e.ctrlKey || e.metaKey) {
      if (st.selected.has(path)) st.selected.delete(path); else st.selected.add(path);
      st.anchor = path;
    } else { st.selected = new Set([path]); st.anchor = path; }
    refreshSelection();
    focusList();
  }

  function onAction(act, el) {
    switch (act) {
      case 'retry-state': loadState(); break;
      case 'refresh': refreshAll(); break;
      case 'new-student': openNewStudent(); break;
      case 'add-country': mkdirFlow('country'); break;
      case 'add-program': mkdirFlow('program'); break;
      case 'new-folder': mkdirFlow('folder'); break;
      case 'upload': pickAndUpload(st.cwd); break;
      case 'upload-selected': {
        const paths = (App.state.selectedPaths || []).slice();
        if (!paths.length && App.state.focusedPath) paths.push(App.state.focusedPath);
        if (!paths.length) { toast('Select files in the Files workspace first, or drag them here', 'warning', 4500); break; }
        startUpload(paths, st.cwd); break;
      }
      case 'toggle-activity':
        st.activityOpen = !st.activityOpen;
        if (st.activityOpen) loadActivity();
        renderParts(); break;
      case 'toggle-trash':
        st.view = st.view === 'trash' ? 'drive' : 'trash';
        clearSearch(true);
        if (st.view === 'trash') { st.trash = null; loadTrash(); }
        renderParts(); break;
      case 'back-drive': st.view = 'drive'; renderParts(); break;
      case 'restore': restoreSelected(); break;
      case 'manage': openManage(); break;
      case 'open-sel': { const s = selectedItems(); if (s.length === 1) openPath(s[0]); break; }
      case 'download': downloadPaths(selectedItems().map(i => i.path)); break;
      case 'rename': { const s = selectedItems(); if (s.length === 1) beginInlineRename(s[0].path); break; }
      case 'move': openMoveDialog(selectedItems().map(i => i.path)); break;
      case 'trash': trashPaths(selectedItems().map(i => i.path)); break;
      case 'clear-sel': st.selected.clear(); refreshSelection(); break;
      case 'clear-search': clearSearch(); break;
      case 'subscribe': subscribeOffice(); break;
      default: onManageAction(act, el);
    }
  }

  // ── drag & drop ──────────────────────────────────────────────────────────
  function wireDrag(layout) {
    let hot = null;
    const setHot = el => {
      if (hot === el) return;
      if (hot) hot.classList.remove('ofx-drop-target', 'ofx-drop-deny');
      hot = el;
    };
    const types = e => [...((e.dataTransfer && e.dataTransfer.types) || [])];
    const kindOf = e => { const t = types(e); return t.includes(SHARED_DRAG) ? 'shared' : t.includes(LOCAL_DRAG) ? 'local' : t.includes('Files') ? 'explorer' : null; };
    const targetOf = e => e.target.closest('[data-drop-dir]');
    layout.addEventListener('dragstart', e => {
      const row = e.target.closest('.ofx-row[data-path]');
      if (!row || st.renaming) return;
      const p = row.dataset.path;
      if (!st.selected.has(p)) { st.selected = new Set([p]); st.anchor = p; }
      const paths = [...st.selected];
      e.dataTransfer.setData(SHARED_DRAG, JSON.stringify(paths));
      e.dataTransfer.effectAllowed = 'move';
      setTimeout(() => document.querySelectorAll('#ofx-rows .ofx-row.sel').forEach(r => r.classList.add('is-dragging')), 0);
    });
    layout.addEventListener('dragend', () => {
      document.querySelectorAll('.ofx-row.is-dragging').forEach(r => r.classList.remove('is-dragging'));
      setHot(null); layout.classList.remove('ofx-dragging');
    });
    layout.addEventListener('dragover', e => {
      const k = kindOf(e);
      if (!k) return;
      const t = targetOf(e);
      e.preventDefault();
      e.stopPropagation();
      if (!t) { setHot(null); e.dataTransfer.dropEffect = 'none'; return; }
      const dir = t.dataset.dropDir;
      const ok = k === 'shared' ? true : canUploadInto(dir);
      setHot(t);
      t.classList.toggle('ofx-drop-target', ok);
      t.classList.toggle('ofx-drop-deny', !ok);
      layout.classList.toggle('ofx-dragging', k !== 'shared' && ok && t.id === 'ofx-list');
      const txt = $('ofx-drop-text');
      if (txt) txt.innerHTML = `Drop to upload to <strong>${esc(crumbText(dir))}</strong>`;
      e.dataTransfer.dropEffect = ok ? (k === 'shared' ? 'move' : 'copy') : 'none';
    });
    layout.addEventListener('dragleave', e => {
      if (!layout.contains(e.relatedTarget)) { setHot(null); layout.classList.remove('ofx-dragging'); }
    });
    layout.addEventListener('drop', e => {
      const k = kindOf(e);
      const t = targetOf(e);
      setHot(null); layout.classList.remove('ofx-dragging');
      if (!k) return;
      e.preventDefault();
      e.stopPropagation();
      if (!t) return;
      const dir = t.dataset.dropDir;
      if (k === 'shared') {
        let paths = [];
        try { paths = JSON.parse(e.dataTransfer.getData(SHARED_DRAG) || '[]'); } catch (_) {}
        paths = paths.filter(p => p !== dir);
        if (paths.length && !paths.every(p => parentOf(p) === dir)) movePaths(paths, dir);
        return;
      }
      if (!canUploadInto(dir)) {
        toast(depth(dir) < 3 ? 'Drop files onto a student folder — uploads go inside a student' : 'You can\'t upload here', 'warning', 4500);
        return;
      }
      if (k === 'local') {
        let paths = [];
        try { paths = JSON.parse(e.dataTransfer.getData(LOCAL_DRAG) || '[]'); } catch (_) {}
        startUpload(paths, dir);
      } else {
        // Explorer: Python delivers the full paths via cloud_files_dropped
        st.pendingDrop = { dir, t: Date.now() };
      }
    });
  }

  // Paths dropped from Windows Explorer (cloud_files_dropped, routed by account.js)
  function filesDropped(paths) {
    setTimeout(() => {
      const pd = st.pendingDrop && Date.now() - st.pendingDrop.t < 5000 ? st.pendingDrop.dir : st.cwd;
      st.pendingDrop = null;
      startUpload(paths, pd);
    }, 150);
  }
  function uploadHere(paths) { startUpload(paths, st.cwd); }

  // ── keyboard ─────────────────────────────────────────────────────────────
  function onKey(e) {
    if (!driveVisible() || !st.office || locked() || st.view === 'manage') return;
    if (document.querySelector('.modal-overlay') || $('acct-menu')) return;
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === 'f') { e.preventDefault(); e.stopPropagation(); const s = $('ofx-search'); if (s) { s.focus(); s.select(); } return; }
    if (typing) return;
    const swallow = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.key === 'Escape') { closeMenu(); if (st.selected.size) { st.selected.clear(); refreshSelection(); } return; }
    if (e.key === 'F5') { swallow(); refreshAll(); return; }
    if (st.view === 'trash') {
      if (['Delete', 'F2'].includes(e.key) || (ctrl && 'acxv'.includes(e.key.toLowerCase()))) swallow();
      return;
    }
    const sel = selectedItems();
    if (e.key === 'Delete') { swallow(); if (sel.length) trashPaths(sel.map(i => i.path)); return; }
    if (e.key === 'F2') { swallow(); if (sel.length === 1 && canModify(sel[0].path)) beginInlineRename(sel[0].path); return; }
    if (e.key === 'Enter') { swallow(); if (sel.length === 1) openPath(sel[0]); return; }
    if (e.key === 'Backspace' || (e.altKey && e.key === 'ArrowUp')) { swallow(); if (st.cwd) navigate(parentOf(st.cwd), st.cwd); return; }
    if (ctrl && e.key.toLowerCase() === 'a') { swallow(); st.selected = new Set(cwdItems().map(i => i.path)); refreshSelection(); return; }
    if (ctrl && 'cxvz'.includes(e.key.toLowerCase())) { swallow(); return; }   // never touch the local file list from here
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      swallow();
      const items = cwdItems();
      if (!items.length) return;
      const cur = items.findIndex(i => i.path === st.anchor);
      const next = Math.max(0, Math.min(items.length - 1, cur < 0 ? 0 : cur + (e.key === 'ArrowDown' ? 1 : -1)));
      const p = items[next].path;
      if (e.shiftKey && st.anchor) st.selected.add(p); else st.selected = new Set([p]);
      st.anchor = p;
      refreshSelection();
      const row = document.querySelector(`#ofx-rows .ofx-row[data-path="${cssq(p)}"]`);
      if (row) row.scrollIntoView({ block: 'nearest' });
    }
  }

  // ── onboarding (no office yet) ───────────────────────────────────────────
  function onboardHtml() {
    const step = (lv, title, ex) => `<div class="ofx-flow-step">${nodeIcon(lv, 16)}<div><div class="ofx-flow-title">${title}</div><div class="acct-hint">${ex}</div></div></div>`;
    return `<div class="page-header">
        <div class="page-header-icon">${Icons.svg('building', 20)}</div>
        <div class="flex-1"><h2 class="page-title">Office drive</h2>
          <p class="page-subtitle">One shared drive for everyone in your office, organised by country, program and student.</p></div>
      </div>
      <div id="ofx-invites"></div>
      <div class="ofx-flow">
        ${step('country', 'Country', 'Bangladesh, Nepal…')}<span class="ofx-flow-arrow">${Icons.svg('chevron-right', 16)}</span>
        ${step('program', 'Program', 'MBBS, BBA…')}<span class="ofx-flow-arrow">${Icons.svg('chevron-right', 16)}</span>
        ${step('student', 'Student', 'Rahim Uddin')}<span class="ofx-flow-arrow">${Icons.svg('chevron-right', 16)}</span>
        <div class="ofx-flow-step"><span class="ft ft-folder">${Icons.svg('files', 16)}</span><div><div class="ofx-flow-title">Documents</div><div class="acct-hint">Passport, Academic, Visa</div></div></div>
      </div>
      <div class="ofx-onboard-grid">
        <div class="card ofx-ob-card">
          <div class="acct-plan-icon">${Icons.svg('building', 18)}</div>
          <div class="card-title">Create an office</div>
          <p class="acct-muted">You become the owner. Set up countries and programs, then invite your staff.</p>
          <div class="field"><label class="field-label" for="ofx-create-name">Office name</label>
            <input id="ofx-create-name" class="input-text" placeholder="e.g. Shafiq Education Consultancy" autocomplete="off">
            <div class="field-error hidden" id="ofx-create-err"></div></div>
          <button class="btn btn-primary" id="ofx-create-btn">${Icons.svg('plus', 16)}Create office</button>
        </div>
        <div class="card ofx-ob-card">
          <div class="acct-plan-icon">${Icons.svg('ticket', 18)}</div>
          <div class="card-title">Join an office</div>
          <p class="acct-muted">Enter the invite code your office owner or admin sent you.</p>
          <div class="field"><label class="field-label" for="ofx-join-code">Invite code</label>
            <input id="ofx-join-code" class="input-text mono ofx-code-input" placeholder="OFX-XXXX-XXXX" maxlength="13" autocomplete="off" spellcheck="false">
            <div class="field-error hidden" id="ofx-join-err"></div></div>
          <button class="btn" id="ofx-join-btn">${Icons.svg('log-in', 16)}Join office</button>
        </div>
      </div>`;
  }
  function renderInvites() {
    const el = $('ofx-invites');
    if (!el) return;
    if (!st.invites.length) { el.innerHTML = ''; return; }
    el.innerHTML = `<div class="acct-section"><div class="section-title">Invitations for you</div>
      <div class="card card-flush">${st.invites.map(i => `
        <div class="acct-device ofx-invite-row">
          <span class="acct-plan-icon">${Icons.svg('mail', 18)}</span>
          <div class="acct-device-main">
            <div class="acct-device-name">${esc(i.office_name)} <span class="ofx-role ofx-role-${esc(i.role)}">${esc(i.role)}</span></div>
            <div class="acct-hint">Invited by ${esc(i.invited_by_email || 'the office')}</div>
          </div>
          <button class="btn btn-sm btn-ghost" data-act="decline-invite" data-id="${esc(i.id)}">Decline</button>
          <button class="btn btn-sm btn-primary" data-act="accept-invite" data-id="${esc(i.id)}">${Icons.svg('check', 14)}Accept</button>
        </div>`).join('')}</div></div>`;
  }
  function normCode(v) {
    const raw = String(v || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    const body = raw.startsWith('OFX') ? raw.slice(3) : raw;
    const p = ['OFX', body.slice(0, 4), body.slice(4, 8)].filter(Boolean);
    return body.length ? p.join('-') : (raw ? 'OFX' : '');
  }
  function wireOnboard() {
    const nameIn = $('ofx-create-name'), codeIn = $('ofx-join-code');
    const fe = (id, msg) => { const e = $(id); e.textContent = msg || ''; e.classList.toggle('hidden', !msg); };
    const create = async () => {
      const name = nameIn.value.trim();
      if (!name) { fe('ofx-create-err', 'Enter a name for your office.'); nameIn.focus(); return; }
      if (name.length > 80) { fe('ofx-create-err', 'Keep the name under 80 characters.'); return; }
      const b = $('ofx-create-btn'); b.classList.add('is-loading'); b.disabled = true;
      let r;
      try { r = await SFM.officeCreate(name); } catch (e) { r = { ok: false, error: String(e) }; }
      b.classList.remove('is-loading'); b.disabled = false;
      if (!r || r.ok === false) { fe('ofx-create-err', err(r, 'Could not create the office')); return; }
      st.office = r.office || st.office;
      toast(`Office “${name}” created`, 'success');
      await loadState();
      if (st.office) openSetupWizard();
    };
    const join = async () => {
      const code = normCode(codeIn.value);
      codeIn.value = code;
      if (!CODE_RE.test(code)) { fe('ofx-join-err', 'Codes look like OFX-AB12-CD34.'); codeIn.focus(); return; }
      const b = $('ofx-join-btn'); b.classList.add('is-loading'); b.disabled = true;
      let r;
      try { r = await SFM.officeJoin(code); } catch (e) { r = { ok: false, error: String(e) }; }
      b.classList.remove('is-loading'); b.disabled = false;
      if (!r || r.ok === false) { fe('ofx-join-err', err(r, 'That code didn\'t work')); return; }
      toast(`Joined ${(r.office && r.office.name) || 'the office'}`, 'success');
      Account.setCloudArea('office');
      loadState();
    };
    $('ofx-create-btn').addEventListener('click', create);
    $('ofx-join-btn').addEventListener('click', join);
    nameIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); create(); } });
    nameIn.addEventListener('input', () => fe('ofx-create-err', ''));
    codeIn.addEventListener('input', () => {
      fe('ofx-join-err', '');
      const atEnd = codeIn.selectionStart === codeIn.value.length;
      const v = normCode(codeIn.value);
      if (v !== codeIn.value && atEnd) codeIn.value = v;
    });
    codeIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); join(); } });
  }
  async function inviteAnswer(id, accept) {
    const r = accept ? await SFM.officeAcceptInvite(id) : await SFM.officeDeclineInvite(id);
    if (!r || r.ok === false) { toast(err(r, 'Could not answer the invitation'), 'error'); return; }
    toast(accept ? 'Welcome to the office' : 'Invitation declined', accept ? 'success' : 'info');
    if (accept) Account.setCloudArea('office');
    loadState();
  }

  // ── first-run setup wizard ───────────────────────────────────────────────
  function chipsHtml(name, list, editable, placeholder) {
    return `<div class="ofx-chips ${editable ? '' : 'is-readonly'}" data-chips="${esc(name)}">
        ${list.map((v, i) => `<span class="ofx-chip">${esc(v)}${editable ? `<button class="ofx-chip-x" data-chip-x="${i}" title="Remove ${esc(v)}" aria-label="Remove ${esc(v)}">${Icons.svg('x', 10)}</button>` : ''}</span>`).join('')}
        ${editable ? `<input class="ofx-chip-input" data-chip-in="${esc(name)}" placeholder="${esc(placeholder)}" spellcheck="false">` : (list.length ? '' : '<span class="acct-hint">None</span>')}
      </div>`;
  }
  // Wire chips inside root; get(name) → array, set(name, arr) re-renders
  function wireChips(root, get, set) {
    root.querySelectorAll('[data-chips]').forEach(box => {
      const name = box.dataset.chips;
      box.addEventListener('click', e => {
        const x = e.target.closest('[data-chip-x]');
        if (x) { const a = get(name).slice(); a.splice(Number(x.dataset.chipX), 1); set(name, a); return; }
        const inp = box.querySelector('.ofx-chip-input');
        if (inp && e.target === box) inp.focus();
      });
      const inp = box.querySelector('.ofx-chip-input');
      if (!inp) return;
      const add = () => {
        const vals = inp.value.split(',').map(v => v.trim()).filter(Boolean);
        if (!vals.length) return;
        const bad = vals.map(v => validateName(v)).find(Boolean);
        if (bad) { inp.classList.add('is-invalid'); inp.title = bad; toast(bad, 'warning', 3500); return; }
        set(name, uniq(get(name).concat(vals)), name);
      };
      inp.addEventListener('keydown', e => {
        e.stopPropagation();
        if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); }
        else if (e.key === 'Backspace' && !inp.value && get(name).length) { const a = get(name).slice(); a.pop(); set(name, a, name); }
      });
      inp.addEventListener('input', () => inp.classList.remove('is-invalid'));
      inp.addEventListener('blur', () => { if (inp.value.trim()) add(); });
    });
  }

  function openSetupWizard() {
    const s = settings();
    const d = {
      countries: (s.countries || []).slice(),
      programs: Array.isArray(s.programs) ? s.programs.slice() : [],
      student_subfolders: (s.student_subfolders && s.student_subfolders.length ? s.student_subfolders : DEFAULT_SUBFOLDERS).slice(),
      enforce_hierarchy: s.enforce_hierarchy !== false,
      makeFolders: true,
    };
    let step = 1;
    const invites = [{ email: '', role: 'staff' }];
    const sent = [];
    const ov = Dialogs.openModal('ofx-setup', 'Set up your office drive', '<div id="ofx-setup-body" class="ofx-setup"></div>', [
      { label: 'Skip for now', left: true, onClick: () => ov._close() },
      { label: 'Back', onClick: () => { if (step > 1) { step--; draw(); } } },
      { label: 'Next', primary: true, icon: 'arrow-right', onClick: () => next() },
    ], { icon: 'building', subtitle: st.office ? st.office.name : '' });
    const bodyEl = ov.querySelector('#ofx-setup-body');
    const btns = ov.querySelectorAll('.modal-footer .btn');
    const [skipBtn, backBtn, nextBtn] = btns;
    function steps() {
      return `<div class="ofx-steps">${['Folder structure', 'Invite staff'].map((t, i) =>
        `<span class="ofx-step ${step === i + 1 ? 'active' : ''} ${step > i + 1 ? 'done' : ''}"><span class="ofx-step-num">${step > i + 1 ? Icons.svg('check', 12) : i + 1}</span>${t}</span>`).join('<span class="ofx-step-line"></span>')}</div>`;
    }
    function draw() {
      backBtn.classList.toggle('hidden', step === 1);
      Dialogs.setBtn(nextBtn, step === 1 ? 'Next' : (sent.length ? 'Finish' : 'Send invites & finish'), step === 1 ? 'arrow-right' : 'check');
      if (step === 1) {
        bodyEl.innerHTML = steps() + `
          <p class="acct-muted">Everything in the office drive is organised as <strong>Country › Program › Student</strong>. You can change these later in People &amp; settings.</p>
          <div class="field"><div class="field-label">${Icons.svg('globe', 12)} Countries</div>${chipsHtml('countries', d.countries, true, 'Type a country, press Enter')}</div>
          <div class="field"><div class="field-label">${Icons.svg('graduation-cap', 12)} Programs</div>${chipsHtml('programs', d.programs, true, 'Type a program, press Enter')}
            <div class="field-hint">Offered in every country. You can set different programs per country later.</div></div>
          <div class="field"><div class="field-label">${Icons.svg('folder', 12)} Subfolders in every new student</div>${chipsHtml('student_subfolders', d.student_subfolders, true, 'Add a subfolder')}</div>
          <label class="switch"><input type="checkbox" id="ofx-w-enforce" ${d.enforce_hierarchy ? 'checked' : ''}><span class="switch-track"></span>Only admins can create countries and programs</label>
          <label class="check"><input type="checkbox" id="ofx-w-make" ${d.makeFolders ? 'checked' : ''}> Create the country and program folders now</label>`;
        wireChips(bodyEl, n => d[n], (n, a, refocus) => { d[n] = a; draw(); if (refocus) { const i = bodyEl.querySelector(`[data-chip-in="${refocus}"]`); if (i) i.focus(); } });
        bodyEl.querySelector('#ofx-w-enforce').addEventListener('change', e => { d.enforce_hierarchy = e.target.checked; });
        bodyEl.querySelector('#ofx-w-make').addEventListener('change', e => { d.makeFolders = e.target.checked; });
      } else {
        bodyEl.innerHTML = steps() + `
          <p class="acct-muted">Invite the people who work with student files. Each person gets a code to join; they also see the invitation when they sign in with that email.</p>
          <div class="ofx-invite-rows">${invites.map((v, i) => `
            <div class="acct-row">
              <input class="input-text" data-inv-email="${i}" type="email" placeholder="name@office.com" value="${esc(v.email)}">
              <select class="input-text ofx-role-select" data-inv-role="${i}"><option value="staff" ${v.role === 'staff' ? 'selected' : ''}>Staff</option><option value="admin" ${v.role === 'admin' ? 'selected' : ''}>Admin</option></select>
              <button class="icon-btn" data-inv-del="${i}" title="Remove" aria-label="Remove" ${invites.length === 1 ? 'disabled' : ''}>${Icons.svg('x', 14)}</button>
            </div>`).join('')}</div>
          <button class="btn btn-sm btn-ghost ofx-self-start" data-inv-add>${Icons.svg('plus', 14)}Add another</button>
          ${sent.length ? `<div class="result-list">${sent.map(x => `<div class="result-row ${x.ok ? 'ok' : 'err'}"><span class="result-icon">${Icons.svg(x.ok ? 'check-circle' : 'x-circle', 14)}</span>
              <span class="result-name">${esc(x.email)}</span><span class="result-detail mono">${esc(x.ok ? x.code : x.error)}</span></div>`).join('')}</div>` : ''}
          <div class="field-hint">Staff can add students and upload files. Admins can also manage countries, programs and people.</div>`;
        bodyEl.querySelectorAll('[data-inv-email]').forEach(i => i.addEventListener('input', () => { invites[Number(i.dataset.invEmail)].email = i.value; }));
        bodyEl.querySelectorAll('[data-inv-role]').forEach(i => i.addEventListener('change', () => { invites[Number(i.dataset.invRole)].role = i.value; }));
        bodyEl.querySelectorAll('[data-inv-del]').forEach(b => b.addEventListener('click', () => { invites.splice(Number(b.dataset.invDel), 1); draw(); }));
        bodyEl.querySelector('[data-inv-add]').addEventListener('click', () => { invites.push({ email: '', role: 'staff' }); draw(); bodyEl.querySelector(`[data-inv-email="${invites.length - 1}"]`).focus(); });
      }
    }
    async function next() {
      if (step === 1) {
        nextBtn.classList.add('is-loading'); nextBtn.disabled = true;
        const payload = { countries: d.countries, programs: d.programs, student_subfolders: d.student_subfolders, enforce_hierarchy: d.enforce_hierarchy };
        const r = await SFM.officeSettingsSet(payload).catch(e => ({ ok: false, error: String(e) }));
        if (r && r.ok !== false) {
          if (st.office) st.office.settings = Object.assign({}, st.office.settings, r.settings || payload);
          if (d.makeFolders) {
            for (const c of d.countries) {
              await SFM.sharedMkdir('', c).catch(() => {});
              for (const p of d.programs) await SFM.sharedMkdir(c, p).catch(() => {});
            }
            Object.keys(st.lists).forEach(k => { st.lists[k].stale = true; });
          }
        }
        nextBtn.classList.remove('is-loading'); nextBtn.disabled = false;
        if (!r || r.ok === false) { toast('Could not save the settings: ' + err(r), 'error', 5000); return; }
        step = 2; draw();
        return;
      }
      const todo = invites.filter(v => v.email.trim() && !sent.some(x => x.ok && x.email === v.email.trim()));
      if (!todo.length) { ov._close(); finishSetup(); return; }
      const bad = todo.find(v => !EMAIL_RE.test(v.email.trim()));
      if (bad) { toast(`“${bad.email}” doesn't look like an email address`, 'warning'); return; }
      nextBtn.classList.add('is-loading'); nextBtn.disabled = true;
      for (const v of todo) {
        const r = await SFM.officeInvite(v.email.trim(), v.role).catch(e => ({ ok: false, error: String(e) }));
        sent.push(r && r.ok !== false && r.invite ? { ok: true, email: v.email.trim(), code: r.invite.code } : { ok: false, email: v.email.trim(), error: err(r, 'failed') });
      }
      invites.splice(0, invites.length, { email: '', role: 'staff' });
      nextBtn.classList.remove('is-loading'); nextBtn.disabled = false;
      draw();
      if (sent.every(x => x.ok)) toast(`${sent.length} invitation${sent.length === 1 ? '' : 's'} created — share the codes with your staff`, 'success', 5000);
    }
    function finishSetup() { Account.setCloudArea('office'); st.view = 'drive'; navigate(''); rerender(); enter(); }
    skipBtn.addEventListener('click', () => finishSetup());
    draw();
  }

  // ── plan locked ──────────────────────────────────────────────────────────
  function lockedHtml() {
    const p = (st.office && st.office.plan) || {};
    const tip = p.billing_available === false ? 'Billing is not set up on this server — ask the administrator' : '';
    return headHtml('Shared drive · Country › Program › Student') + `
      <div class="cloud-cta">
        <div class="cloud-cta-icon">${Icons.svg('lock', 24)}</div>
        <h3>${p.status === 'past_due' ? 'The office plan is paused — payment overdue' : 'The shared drive is part of your office\'s monthly plan'}</h3>
        <p>Everyone in the office shares one drive, organised by country, program and student, with live updates and a restorable trash.</p>
        ${isOwner()
          ? `<button class="btn btn-primary" data-act="subscribe" ${tip ? `disabled title="${esc(tip)}"` : ''}>${Icons.svg('credit-card', 16)}Subscribe${p.plan_label ? ' — ' + esc(p.plan_label) : ''}</button>
             ${tip ? `<p class="acct-hint">${esc(tip)}</p>` : ''}`
          : `<p class="acct-hint">${Icons.svg('info', 12)} Ask your office owner to activate the plan. Your access starts as soon as it's active.</p>`}
        <button class="btn btn-ghost btn-sm" data-act="manage">${Icons.svg('users', 14)}People &amp; settings</button>
      </div>`;
  }
  async function subscribeOffice() {
    const r = await SFM.billingOpenCheckout();
    if (!r || r.ok === false) { toast('Could not open checkout: ' + err(r), 'error', 5000); return; }
    toast('Checkout opened in your browser — this page updates when payment completes', 'info', 5000);
  }

  // ── People & settings ────────────────────────────────────────────────────
  function openManage() {
    Account.setCloudArea('office');
    if (!panelVisible('cloud')) App.switchPanel('cloud');
    st.view = 'manage';
    rerender();
    loadManage();
  }
  function openDrive() {
    Account.setCloudArea('office');
    App.switchPanel('cloud');
    if (st.view === 'manage') st.view = 'drive';
    rerender();
    enter();
  }
  function cloneSettings(s) {
    s = s || {};
    return {
      countries: (s.countries || []).slice(),
      programs: Array.isArray(s.programs) ? s.programs.slice()
        : Object.fromEntries(Object.entries(s.programs || {}).map(([k, v]) => [k, (v || []).slice()])),
      student_subfolders: (s.student_subfolders || []).slice(),
      enforce_hierarchy: s.enforce_hierarchy !== false,
    };
  }
  async function loadManage() {
    if (!st.office) return;
    if (!st.draftDirty) st.draft = cloneSettings(settings());
    const jobs = [SFM.officeMembers().then(r => {
      if (r && r.ok !== false) { st.members = r.members || []; st.membersError = null; } else st.membersError = err(r, 'Could not load people');
    }).catch(e => { st.membersError = String(e); })];
    if (isAdmin()) jobs.push(SFM.officeInvites().then(r => { if (r && r.ok !== false) st.pending = r.invites || []; }).catch(() => {}));
    jobs.push(SFM.officeSettingsGet().then(r => {
      if (r && r.ok !== false && r.settings) { st.office.settings = r.settings; if (!st.draftDirty) st.draft = cloneSettings(r.settings); }
    }).catch(() => {}));
    renderManage();
    await Promise.all(jobs);
    renderManage();
  }

  function manageShellHtml() {
    const o = st.office;
    return `<div class="page-header ofx-head">
        <button class="icon-btn" data-act="back-from-manage" title="Back to the office drive" aria-label="Back to the office drive">${Icons.svg('arrow-left', 16)}</button>
        <div class="page-header-icon">${Icons.svg('users', 20)}</div>
        <div class="flex-1 ofx-min0"><h2 class="page-title">People &amp; settings</h2>
          <p class="page-subtitle">${esc(o.name)} · you are ${({ owner: 'the owner', admin: 'an admin' })[role()] || 'a staff member'}</p></div>
      </div>
      <div class="ofx-manage">
        <div class="card ofx-card" id="ofx-m-office"></div>
        <div class="acct-section" id="ofx-m-people"></div>
        <div class="card ofx-card" id="ofx-m-invite"></div>
        <div class="card ofx-card" id="ofx-m-structure"></div>
        <div class="card ofx-card ofx-danger-card" id="ofx-m-danger"></div>
      </div>`;
  }
  function renderManage() {
    if (!$('ofx-m-office')) return;
    renderMOffice(); renderMPeople(); renderMInvite(); renderMStructure(); renderMDanger();
  }
  function renderMOffice() {
    const o = st.office, p = o.plan || {};
    const planTxt = p.free_plan ? 'Included (free on this server)' : p.active
      ? (p.current_period_end ? `Active · renews ${fmtDate(p.current_period_end)}` : 'Active') : (p.status === 'past_due' ? 'Payment overdue' : 'Not active');
    $('ofx-m-office').innerHTML = `
      <div class="acct-plan-head"><div class="acct-plan-icon">${Icons.svg('building', 18)}</div>
        <div class="flex-1"><div class="section-title">Office</div><div class="acct-plan-name">${esc(o.name)}</div></div>
        <span class="acct-badge ${p.active || p.free_plan ? 'on' : 'off'}">${esc((p.plan_label ? p.plan_label + ' · ' : '') + planTxt)}</span></div>
      ${isAdmin() ? `<div class="field"><label class="field-label" for="ofx-m-name">Office name</label>
        <div class="acct-row"><input id="ofx-m-name" class="input-text" value="${esc(o.name)}" maxlength="80"><button class="btn" data-act="rename-office">Save</button></div></div>` : ''}
      <div class="acct-hint">${Number(o.member_count) || 0} member${Number(o.member_count) === 1 ? '' : 's'} · everyone shares the same drive and storage${!p.active && !p.free_plan && isOwner() ? ` · <button class="btn btn-link" data-act="subscribe">Subscribe</button>` : ''}</div>`;
  }
  function renderMPeople() {
    const el = $('ofx-m-people');
    let inner;
    if (st.membersError) inner = `<div class="acct-list-empty">${esc(st.membersError)}</div>`;
    else if (!st.members) inner = '<div class="acct-list-empty"><span class="spinner"></span> Loading…</div>';
    else inner = `<div class="ofx-mrow ofx-mrow-head"><span></span><span>Person</span><span>Role</span><span>Joined</span><span>Last active</span><span></span></div>` +
      st.members.map(m => {
        const canEdit = isAdmin() && !m.is_you && m.role !== 'owner' && !(role() === 'admin' && m.role === 'admin');
        const roleCell = canEdit
          ? `<select class="input-text ofx-role-select" data-role-for="${esc(m.user_id)}" aria-label="Role for ${esc(m.email)}">
              <option value="staff" ${m.role === 'staff' ? 'selected' : ''}>Staff</option><option value="admin" ${m.role === 'admin' ? 'selected' : ''}>Admin</option></select>`
          : `<span class="ofx-role ofx-role-${esc(m.role)}">${m.role === 'owner' ? Icons.svg('crown', 12) : ''}${esc(m.role[0].toUpperCase() + m.role.slice(1))}</span>`;
        return `<div class="ofx-mrow">
            <span class="ofx-avatar ofx-avatar-md">${esc(initialOf(m.email))}</span>
            <span class="ofx-mname"><span class="truncate">${esc(m.email)}</span>${m.is_you ? '<span class="pill pill-blue">You</span>' : ''}</span>
            <span>${roleCell}</span>
            <span class="ofx-date">${esc(fmtDate(m.joined_at))}</span>
            <span class="ofx-date" title="${esc(fmtDateTime(m.last_active))}">${esc(fmtRel(m.last_active) || '—')}</span>
            <span>${canEdit ? `<button class="icon-btn icon-btn-sm icon-btn-danger" data-act="remove-member" data-id="${esc(m.user_id)}" data-email="${esc(m.email)}" title="Remove from office" aria-label="Remove ${esc(m.email)}">${Icons.svg('x', 14)}</button>` : ''}</span>
          </div>`;
      }).join('');
    el.innerHTML = `<div class="section-head"><div class="section-title">People${st.members ? ` · ${st.members.length}` : ''}</div></div>
      <div class="card card-flush ofx-members">${inner}</div>`;
    el.querySelectorAll('[data-role-for]').forEach(sel => sel.addEventListener('change', async () => {
      const r = await SFM.officeSetRole(sel.dataset.roleFor, sel.value).catch(e => ({ ok: false, error: String(e) }));
      if (!r || r.ok === false) { toast('Could not change the role: ' + err(r), 'error', 5000); loadManage(); return; }
      toast('Role updated', 'success');
      loadManage();
    }));
  }
  function renderMInvite() {
    const el = $('ofx-m-invite');
    if (!isAdmin()) {
      el.innerHTML = `<div class="acct-plan-head"><div class="acct-plan-icon">${Icons.svg('user-plus', 18)}</div><div class="flex-1">
        <div class="card-title">Invite people</div><p class="acct-muted">Ask an office admin to invite new staff.</p></div></div>`;
      return;
    }
    const li = st.lastInvite;
    const pend = st.pending;
    el.innerHTML = `
      <div class="acct-plan-head"><div class="acct-plan-icon">${Icons.svg('user-plus', 18)}</div><div class="flex-1">
        <div class="card-title">Invite people</div><p class="acct-muted">They join with a code, or accept the invitation when they sign in with this email.</p></div></div>
      <div class="acct-row ofx-invite-form">
        <div class="input-group flex-1"><span class="input-icon">${Icons.svg('mail', 14)}</span>
          <input id="ofx-inv-email" class="input-text" type="email" placeholder="name@office.com" autocomplete="off"></div>
        <select id="ofx-inv-role" class="input-text ofx-role-select"><option value="staff">Staff</option><option value="admin">Admin</option></select>
        <button class="btn btn-primary" data-act="send-invite">${Icons.svg('user-plus', 16)}Invite</button>
      </div>
      ${li ? `<div class="callout success ofx-code-callout">${Icons.svg('check-circle', 16)}<div class="callout-body">
          <div class="callout-title">Invitation for ${esc(li.email)} (${esc(li.role)})</div>
          <div class="ofx-code-row"><span class="ofx-code mono selectable">${esc(li.code)}</span>
            <button class="btn btn-sm" data-act="copy-code" data-code="${esc(li.code)}">${Icons.svg('copy', 14)}Copy code</button></div>
          <span class="acct-hint">Share this code with them. It expires ${esc(fmtDateTime(li.expires_at) || 'soon')}.</span></div></div>` : ''}
      <div class="section-title">Pending invitations${pend && pend.length ? ` · ${pend.length}` : ''}</div>
      <div class="card card-flush ofx-pending">${!pend ? '<div class="acct-list-empty"><span class="spinner"></span> Loading…</div>'
        : !pend.length ? '<div class="acct-list-empty">No pending invitations.</div>'
        : pend.map(i => `<div class="acct-device">
            <span class="acct-device-icon">${Icons.svg('mail', 16)}</span>
            <div class="acct-device-main"><div class="acct-device-name">${esc(i.email)} <span class="ofx-role ofx-role-${esc(i.role)}">${esc(i.role)}</span></div>
              <div class="acct-hint"><span class="mono">${esc(i.code)}</span> · expires ${esc(fmtDate(i.expires_at))}</div></div>
            <button class="btn btn-sm btn-ghost" data-act="copy-code" data-code="${esc(i.code)}" title="Copy code">${Icons.svg('copy', 14)}</button>
            <button class="btn btn-sm btn-ghost" data-act="revoke-invite" data-id="${esc(i.id)}">Revoke</button></div>`).join('')}</div>`;
    const em = $('ofx-inv-email');
    em.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); onManageAction('send-invite'); } });
  }
  function renderMStructure() {
    const el = $('ofx-m-structure');
    const d = st.draft || cloneSettings(settings());
    st.draft = d;
    const ed = isAdmin();
    const per = !Array.isArray(d.programs);
    const countries = sortNames(uniq([...(d.countries || []), ...((st.lists[''] || {}).items || []).filter(i => i.is_dir).map(i => i.name)]));
    if (per && (!st.progCountry || !countries.includes(st.progCountry))) st.progCountry = countries[0] || '';
    const progList = per ? (d.programs[st.progCountry] || []) : d.programs;
    el.innerHTML = `
      <div class="acct-plan-head"><div class="acct-plan-icon">${Icons.svg('layers', 18)}</div><div class="flex-1">
        <div class="card-title">Folder structure</div>
        <p class="acct-muted">Country › Program › Student. These lists fill the “New student” menus; existing folders always appear too.</p></div>
        ${!ed ? '<span class="pill pill-neutral">View only</span>' : ''}</div>
      <div class="field"><div class="field-label">${Icons.svg('globe', 12)} Countries</div>${chipsHtml('countries', d.countries, ed, 'Add a country')}</div>
      <div class="field">
        <div class="section-head"><div class="field-label">${Icons.svg('graduation-cap', 12)} Programs</div>
          <label class="switch ofx-switch-sm"><input type="checkbox" id="ofx-per-country" ${per ? 'checked' : ''} ${ed ? '' : 'disabled'}><span class="switch-track"></span>Different for each country</label></div>
        ${per ? `<div class="acct-row ofx-per-row"><span class="acct-hint">Programs offered in</span>
            <select id="ofx-prog-country" class="input-text ofx-role-select">${countries.map(c => `<option ${c === st.progCountry ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>` : ''}
        ${per && !countries.length ? '<div class="acct-hint">Add a country first.</div>' : chipsHtml('programs', progList, ed, 'Add a program')}
      </div>
      <div class="field"><div class="field-label">${Icons.svg('folder', 12)} Subfolders created in every new student</div>${chipsHtml('student_subfolders', d.student_subfolders, ed, 'Add a subfolder, e.g. Visa')}</div>
      <label class="switch"><input type="checkbox" id="ofx-enforce" ${d.enforce_hierarchy ? 'checked' : ''} ${ed ? '' : 'disabled'}><span class="switch-track"></span>Only admins can create countries and programs</label>
      ${ed ? `<div class="acct-actions ofx-structure-actions">
          <span class="acct-hint flex-1">${st.draftDirty ? 'You have unsaved changes.' : 'Saved.'}</span>
          <button class="btn" data-act="discard-structure" ${st.draftDirty ? '' : 'disabled'}>Discard</button>
          <button class="btn btn-primary" data-act="save-structure" ${st.draftDirty ? '' : 'disabled'}>${Icons.svg('check', 16)}Save changes</button></div>` : ''}`;
    if (!ed) return;
    wireChips(el, n => (n === 'programs' ? progList : d[n]), (n, a, refocus) => {
      if (n === 'programs') { if (per) d.programs[st.progCountry] = a; else d.programs = a; } else d[n] = a;
      st.draftDirty = true;
      renderMStructure();
      if (refocus) { const i = el.querySelector(`[data-chip-in="${refocus}"]`); if (i) i.focus(); }
    });
    $('ofx-per-country').addEventListener('change', e => {
      if (e.target.checked) { const base = d.programs.slice(); d.programs = Object.fromEntries(countries.map(c => [c, base.slice()])); }
      else d.programs = uniq(Object.values(d.programs).flat());
      st.draftDirty = true; renderMStructure();
    });
    const pc = $('ofx-prog-country');
    if (pc) pc.addEventListener('change', () => { st.progCountry = pc.value; renderMStructure(); });
    $('ofx-enforce').addEventListener('change', e => { d.enforce_hierarchy = e.target.checked; st.draftDirty = true; renderMStructure(); });
  }
  function renderMDanger() {
    const el = $('ofx-m-danger');
    const owner = isOwner();
    el.innerHTML = `
      <div class="card-title">${owner ? 'Ownership' : 'Leave office'}</div>
      ${owner ? `<div class="card-row"><div class="flex-1"><div class="ofx-danger-title">Transfer ownership</div>
            <div class="acct-hint">Make another member the owner. You stay in the office as an admin.</div></div>
          <button class="btn" data-act="transfer">${Icons.svg('crown', 16)}Transfer…</button></div>
        <div class="card-row"><div class="flex-1"><div class="ofx-danger-title">Leave office</div>
            <div class="acct-hint">The owner can't leave — transfer ownership first.</div></div>
          <button class="btn btn-danger" disabled>${Icons.svg('log-out', 16)}Leave office</button></div>`
      : `<div class="card-row"><div class="flex-1"><div class="acct-hint">You lose access to the office drive. Files stay in the office for everyone else.</div></div>
          <button class="btn btn-danger" data-act="leave">${Icons.svg('log-out', 16)}Leave office</button></div>`}`;
  }

  async function onManageAction(act, el) {
    switch (act) {
      case 'accept-invite': return inviteAnswer(el.dataset.id, true);
      case 'decline-invite': return inviteAnswer(el.dataset.id, false);
      case 'back-from-manage': st.view = 'drive'; rerender(); enter(); return;
      case 'rename-office': {
        const v = $('ofx-m-name').value.trim();
        if (!v) { toast('Enter an office name', 'warning'); return; }
        if (v === st.office.name) return;
        const r = await SFM.officeRename(v).catch(e => ({ ok: false, error: String(e) }));
        if (!r || r.ok === false) { toast('Rename failed: ' + err(r), 'error'); return; }
        st.office.name = v; toast('Office renamed', 'success'); rerender(); return;
      }
      case 'send-invite': {
        const email = $('ofx-inv-email').value.trim(), rl = $('ofx-inv-role').value;
        if (!EMAIL_RE.test(email)) { toast('Enter a valid email address', 'warning'); $('ofx-inv-email').focus(); return; }
        const b = document.querySelector('[data-act="send-invite"]');
        if (b) { b.classList.add('is-loading'); b.disabled = true; }
        const r = await SFM.officeInvite(email, rl).catch(e => ({ ok: false, error: String(e) }));
        if (b) { b.classList.remove('is-loading'); b.disabled = false; }
        if (!r || r.ok === false || !r.invite) { toast('Could not invite: ' + err(r), 'error', 5000); return; }
        st.lastInvite = Object.assign({ email, role: rl }, r.invite);
        toast(`Invitation created for ${email}`, 'success');
        const inv = await SFM.officeInvites().catch(() => null);
        if (inv && inv.ok !== false) st.pending = inv.invites || [];
        renderMInvite(); return;
      }
      case 'copy-code': await SFM.setClipboard(el.dataset.code); toast('Invite code copied', 'success', 2000); return;
      case 'revoke-invite': {
        if (!(await Dialogs.confirm({ title: 'Revoke invitation', icon: 'x-circle', danger: true, okLabel: 'Revoke', message: 'Revoke this invitation? The code stops working.' }))) return;
        const r = await SFM.officeRevokeInvite(el.dataset.id).catch(e => ({ ok: false, error: String(e) }));
        if (!r || r.ok === false) { toast('Could not revoke: ' + err(r), 'error'); return; }
        toast('Invitation revoked', 'success');
        if (st.lastInvite && String(st.lastInvite.id) === String(el.dataset.id)) st.lastInvite = null;
        loadManage(); return;
      }
      case 'remove-member': {
        if (!(await Dialogs.confirm({ title: 'Remove from office', icon: 'user', danger: true, okLabel: 'Remove', okIcon: 'x',
          message: `Remove ${el.dataset.email} from the office?`, detail: 'They lose access to the office drive immediately. Files they uploaded stay in the office.' }))) return;
        const r = await SFM.officeRemoveMember(el.dataset.id).catch(e => ({ ok: false, error: String(e) }));
        if (!r || r.ok === false) { toast('Could not remove: ' + err(r), 'error'); return; }
        toast(`${el.dataset.email} removed`, 'success'); loadManage(); return;
      }
      case 'save-structure': {
        const r = await SFM.officeSettingsSet(st.draft).catch(e => ({ ok: false, error: String(e) }));
        if (!r || r.ok === false) { toast('Could not save: ' + err(r), 'error', 5000); return; }
        st.office.settings = r.settings || cloneSettings(st.draft);
        st.draftDirty = false;
        toast('Folder structure saved', 'success'); renderMStructure(); return;
      }
      case 'discard-structure': st.draftDirty = false; st.draft = cloneSettings(settings()); renderMStructure(); return;
      case 'transfer': return transferFlow();
      case 'leave': {
        if (!(await Dialogs.confirm({ title: 'Leave office', icon: 'log-out', danger: true, okLabel: 'Leave office', okIcon: 'log-out',
          message: `Leave ${st.office.name}?`, detail: 'You lose access to the shared drive. You need a new invitation to come back.' }))) return;
        const r = await SFM.officeLeave().catch(e => ({ ok: false, error: String(e) }));
        if (!r || r.ok === false) { toast('Could not leave: ' + err(r), 'error'); return; }
        toast('You left the office', 'info'); loadState(); return;
      }
    }
  }
  async function transferFlow() {
    if (!st.members) await loadManage();
    const others = (st.members || []).filter(m => !m.is_you);
    if (!others.length) { toast('Invite someone first — there is nobody to transfer to', 'warning', 4500); return; }
    const body = `<div class="field"><label class="field-label" for="ofx-tr-who">New owner</label>
        <select id="ofx-tr-who" class="input-text">${others.map(m => `<option value="${esc(m.user_id)}">${esc(m.email)} (${esc(m.role)})</option>`).join('')}</select>
        <div class="field-hint">The new owner controls billing and can remove anyone. You become an admin.</div></div>`;
    const ov = Dialogs.openModal('ofx-transfer', 'Transfer ownership', body, [
      { label: 'Cancel', onClick: () => ov._close() },
      { label: 'Continue', danger: true, icon: 'crown', onClick: async () => {
        const sel = ov.querySelector('#ofx-tr-who');
        const m = others.find(x => String(x.user_id) === sel.value);
        ov._close();
        if (!(await Dialogs.confirm({ title: 'Transfer ownership', danger: true, icon: 'crown', okLabel: 'Transfer ownership',
          message: `Make ${m.email} the owner of ${st.office.name}?`, detail: 'This can only be undone by the new owner.' }))) return;
        const r = await SFM.officeTransfer(m.user_id).catch(e => ({ ok: false, error: String(e) }));
        if (!r || r.ok === false) { toast('Transfer failed: ' + err(r), 'error', 5000); return; }
        toast(`${m.email} is now the owner`, 'success');
        await loadState(); loadManage();
      } },
    ], { icon: 'crown', tone: 'danger', size: 'sm' });
  }

  // ── Account panel card ───────────────────────────────────────────────────
  function accountCardHtml() {
    if (!st.loaded) return '';
    const o = st.office;
    if (!o) {
      const n = st.invites.length;
      return `<div class="card card-row ofx-acct-card">
          <div class="acct-plan-icon">${Icons.svg('building', 18)}</div>
          <div class="flex-1"><div class="section-title">Office</div>
            <div class="acct-plan-name">Share student files with your staff</div>
            <div class="acct-hint">${n ? `${n} invitation${n === 1 ? '' : 's'} waiting for you` : 'Create an office or join one with an invite code.'}</div></div>
          <button class="btn ${n ? 'btn-primary' : ''}" data-ofx-acct="drive">${Icons.svg(n ? 'mail' : 'building', 16)}${n ? 'View invitations' : 'Set up office…'}</button>
        </div>`;
    }
    const rl = o.role;
    return `<div class="card ofx-acct-card">
        <div class="acct-plan-head"><div class="acct-plan-icon">${Icons.svg('building', 18)}</div>
          <div class="flex-1 ofx-min0"><div class="section-title">Office</div>
            <div class="acct-plan-name truncate">${esc(o.name)}</div>
            <div class="acct-hint">${Number(o.member_count) || 0} member${Number(o.member_count) === 1 ? '' : 's'} · ${locked() ? 'plan not active' : 'shared drive active'}</div></div>
          <span class="ofx-role ofx-role-${esc(rl)}">${rl === 'owner' ? Icons.svg('crown', 12) : ''}${esc(rl[0].toUpperCase() + rl.slice(1))}</span></div>
        <div class="acct-actions">
          <button class="btn btn-primary" data-ofx-acct="drive">${Icons.svg('folder-open', 16)}Open office drive</button>
          <button class="btn" data-ofx-acct="manage">${Icons.svg('users', 16)}People &amp; settings</button>
        </div>
      </div>`;
  }
  function wireAccountCard(root) {
    root.querySelectorAll('[data-ofx-acct]').forEach(b => b.addEventListener('click', () => {
      if (b.dataset.ofxAcct === 'manage') openManage(); else openDrive();
    }));
  }

  // ── events ───────────────────────────────────────────────────────────────
  function initEvents() {
    SFM.on('office_changed', p => {
      loadState().then(() => { if (st.view === 'manage' && st.office) loadManage(); });
      if (p && p.reason === 'removed') toast('You were removed from the office', 'warning', 6000);
    });
    SFM.on('shared_changed', p => {
      if (!st.office) return;
      const paths = (p && p.paths) || [];
      invalidate(paths);
      if (st.view === 'trash') loadTrash();
      if (st.query) onSearchInput(st.query);
      scheduleActivity();
      if (p && p.actor && !isMe(p.actor) && driveVisible() && Date.now() - st.lastToast > 4000) {
        st.lastToast = Date.now();
        const where = paths[0] ? crumbText(parentOf(String(paths[0]))) : '';
        toast(`Updated by ${personName(p.actor)}${where ? ' · ' + where : ''}`, 'info', 2500);
      }
      clearTimeout(initEvents._u); initEvents._u = setTimeout(loadUsage, 1500);
    });
    SFM.on('shared_upload_progress', p => {
      if (!p) return;
      const j = st.jobs.get(p.job_id) || { kind: 'up', dir: st.cwd };
      st.jobs.set(p.job_id, Object.assign(j, { file: p.file, index: p.index || 0, total: p.total || j.total, bytes: p.bytes || 0, total_bytes: p.total_bytes || 0 }));
      renderTransfers();
    });
    SFM.on('shared_upload_done', r => {
      const j = st.jobs.get(r && r.job_id);
      st.jobs.delete(r && r.job_id);
      renderTransfers();
      App.setStatus('Ready');
      const n = (r && r.uploaded) || 0;
      const errs = (r && r.errors) || [];
      if (r && r.ok && !errs.length) toast(`Uploaded ${n} file${n === 1 ? '' : 's'}${j ? ' to ' + crumbText(j.dir) : ''}`, 'success');
      else {
        const first = errs[0] ? (errs[0].error || errs[0]) : (r && r.error) || 'unknown error';
        toast(`Uploaded ${n}, ${errs.length || 'some'} failed: ${first}`, 'error', 7000);
      }
      if (j) invalidate([join(j.dir, '_')]);
      loadUsage();
    });
    SFM.on('shared_download_progress', p => {
      if (!p) return;
      const j = st.jobs.get(p.job_id) || { kind: 'dn', label: '' };
      st.jobs.set(p.job_id, Object.assign(j, { file: p.file, index: p.index || 0, total: p.total || j.total, bytes: p.bytes || 0, total_bytes: p.total_bytes || 0 }));
      renderTransfers();
    });
    SFM.on('shared_download_done', r => {
      const j = st.jobs.get(r && r.job_id);
      st.jobs.delete(r && r.job_id);
      renderTransfers();
      App.setStatus('Ready');
      const n = Array.isArray(r && r.files) ? r.files.length : Number(r && r.files) || 0;
      const errs = (r && r.errors) || [];
      if (r && r.ok && !errs.length) toast(`Downloaded ${n} file${n === 1 ? '' : 's'}${j && j.dest ? ' to ' + j.dest : ''}`, 'success', 4500);
      else toast(`Downloaded ${n}, ${errs.length || 'some'} failed: ${errs[0] ? (errs[0].error || errs[0]) : (r && r.error) || 'unknown error'}`, 'error', 6000);
      if (j && j.dest && App.state.currentFolder === j.dest) { try { FileTree.refresh(); } catch (e) {} }
    });
  }

  function init() {
    initEvents();
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('blur', closeMenu);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return {
    state: st, render, loadState, reset, enter,
    hasOffice: () => !!st.office, hasInvites: () => st.invites.length > 0, loaded: () => st.loaded,
    filesDropped, uploadHere, openManage, openDrive,
    accountCardHtml, wireAccountCard,
  };
})();
