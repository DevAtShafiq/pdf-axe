/**
 * folder-tools.js — folder creation + the Copy / Move folder picker
 *
 *   FolderTools.openPicker('copy' | 'move', paths)
 *       Explorer-style folder tree of the drive (or share) the items are on:
 *       lazy-loaded children, the current folder opened and highlighted,
 *       recent destinations on top, a search box (searches the whole drive)
 *       and "New folder" inside the picker. Copy / Move use the bridge's
 *       copy_files / move_files, which never replace anything: a name that
 *       is taken gets " (2)".
 *   FolderTools.openFromList(parent?, text?)
 *       "New folders from a list": paste names (an Excel column, tab-separated
 *       cells, one per line), live preview with invalid / duplicate / existing
 *       names flagged, optional prefix, suffix and numbering, then every valid
 *       folder is created at once (Python: plan/create_folders_from_list).
 *   Also wires the file list's empty-space menu and the toolbar's New folder
 *   drop-down.
 */
const FolderTools = (() => {

  const RECENT_KEY = 'oa.folderPicker.recent';
  const _esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const _norm = p => (App.normPath ? App.normPath(p) : String(p || ''));
  const _key = p => _norm(p).toLowerCase();
  const _base = p => { const s = _norm(p); return s.replace(/\\+$/, '').split('\\').pop() || s; };
  const _parent = p => (App.parentPath ? App.parentPath(p) : '');
  const _isRoot = p => /^[A-Za-z]:\\$/.test(_norm(p)) || /^\\\\[^\\]+\\[^\\]+$/.test(_norm(p));

  // Drive ("E:\") or share ("\\server\share") a path is on.
  function _rootOf(p) {
    const s = _norm(p);
    const m = /^([A-Za-z]:)/.exec(s);
    if (m) return m[1].toUpperCase() + '\\';
    const u = /^(\\\\[^\\]+\\[^\\]+)/.exec(s);
    return u ? u[1] : s;
  }
  function _countLabel(paths) {
    const n = (paths || []).length;
    return n === 1 ? _base(paths[0]) : `${n} items`;
  }
  function _recentLoad() {
    try { const a = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); return Array.isArray(a) ? a : []; }
    catch (_) { return []; }
  }
  function _recentAdd(p) {
    const list = [_norm(p), ..._recentLoad().filter(x => _key(x) !== _key(p))].slice(0, 8);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (_) {}
  }

  // ═════════════════════════════════════════════════════════════════════════
  //  Copy to… / Move to…  — folder tree picker
  // ═════════════════════════════════════════════════════════════════════════
  async function openPicker(mode, paths) {
    paths = (paths || []).filter(Boolean).map(_norm);
    if (!paths.length) { App.toast('Select files or folders first', 'warning'); return; }
    const isMove = mode === 'move';
    const verb = isMove ? 'Move' : 'Copy';
    const srcDir = _parent(paths[0]);
    const cur = _norm(FileTree.getCurrentFolder() || srcDir);
    const root = _rootOf(srcDir || cur);

    const body = `
      <div class="fp-top">
        <div class="input-group fp-search">
          <span class="input-icon">${Icons.svg('search', 14)}</span>
          <input id="fp-filter" class="input-text" type="search" autocomplete="off" spellcheck="false"
                 placeholder="Search folders on ${_esc(root)}" aria-label="Search folders">
        </div>
        <button type="button" class="btn btn-sm" id="fp-newfolder" title="Make a new folder inside the selected folder">${Icons.svg('folder-plus', 14)}New folder</button>
      </div>
      <div class="fp-tree" id="fp-tree" tabindex="0" role="tree" aria-label="Folders">
        <div class="fp-section" id="fp-recent-sec" hidden>
          <div class="fp-section-hdr">${Icons.svg('history', 12)}Recent</div>
          <div id="fp-recent"></div>
        </div>
        <div class="fp-section" id="fp-tree-sec">
          <div class="fp-section-hdr">${Icons.svg('hard-drive', 12)}This PC</div>
          <div id="fp-drives" role="group"><div class="fp-msg"><span class="spinner"></span>Loading folders…</div></div>
        </div>
        <div class="fp-results" id="fp-results" hidden></div>
      </div>
      <div class="fp-dest" id="fp-dest" aria-live="polite"></div>`;

    const overlay = Dialogs.openModal('folderpick', isMove ? 'Move to folder' : 'Copy to folder', body, [
      { label: 'Cancel', onClick: () => overlay._close() },
      { label: verb, primary: true, icon: isMove ? 'folder-input' : 'copy', onClick: () => go() },
    ], { icon: isMove ? 'folder-input' : 'copy', subtitle: _countLabel(paths), size: 'lg', cls: 'fp-modal' });

    const $ = sel => overlay.querySelector(sel);
    const treeEl = $('#fp-tree'), drivesEl = $('#fp-drives'), resultsEl = $('#fp-results');
    const filterEl = $('#fp-filter'), destEl = $('#fp-dest');
    const okBtn = overlay.querySelector(`[data-modal-btn="${verb}"]`);
    const st = { sel: '', nodes: new Map(), seq: 0, timer: 0, busy: false };
    overlay.dataset.mode = mode;

    // ── nodes ──────────────────────────────────────────────────────────────
    function makeNode(path, name, depth, hasChildren, kind) {
      path = _norm(path);
      const wrap = document.createElement('div');
      wrap.className = 'fp-node';
      const row = document.createElement('div');
      row.className = 'fp-row' + (kind === 'drive' ? ' is-drive' : '') + (_key(path) === _key(cur) ? ' is-current' : '');
      row.setAttribute('role', 'treeitem');
      row.dataset.path = path;
      row.style.paddingLeft = (6 + depth * 18) + 'px';
      row.title = path;
      row.innerHTML = `<span class="fp-twist${hasChildren ? '' : ' leaf'}">${Icons.svg('chevron-right', 14)}</span>
        <span class="fp-icon">${Icons.svg(kind === 'drive' ? 'hard-drive' : 'folder', 16)}</span>
        <span class="fp-label">${_esc(name)}</span>
        ${_key(path) === _key(cur) ? '<span class="badge badge-blue fp-cur-badge">Current</span>' : ''}`;
      const kids = document.createElement('div');
      kids.className = 'fp-children';
      kids.setAttribute('role', 'group');
      wrap.append(row, kids);
      const node = { path, name, depth, hasChildren, expanded: false, loaded: false, loading: null, row, kids, wrap };
      st.nodes.set(_key(path), node);
      row.querySelector('.fp-twist').addEventListener('click', e => { e.stopPropagation(); toggle(node); });
      row.addEventListener('click', () => select(path));
      row.addEventListener('dblclick', e => { if (!e.target.closest('.fp-twist')) toggle(node); });
      if (st.sel && _key(st.sel) === _key(path)) row.classList.add('selected');
      return node;
    }

    function setLeaf(node, leaf) {
      node.hasChildren = !leaf;
      node.row.querySelector('.fp-twist').classList.toggle('leaf', leaf);
    }

    function load(node, force) {
      if (node.loaded && !force) return Promise.resolve();
      if (node.loading && !force) return node.loading;
      node.kids.innerHTML = `<div class="fp-msg" style="padding-left:${30 + node.depth * 18}px"><span class="spinner"></span>Loading…</div>`;
      node.loading = (async () => {
        let r = null;
        try { r = await SFM.call('list_subfolders', node.path); } catch (e) { r = { ok: false, error: String(e) }; }
        node.kids.innerHTML = '';
        // forget the old nodes below this one (a reload replaces them)
        const nk = _key(node.path), pre = nk.endsWith('\\') ? nk : nk + '\\';
        for (const k of Array.from(st.nodes.keys())) if (k !== nk && k.startsWith(pre)) st.nodes.delete(k);
        if (!r || !r.ok) {
          node.kids.innerHTML = `<div class="fp-msg fp-msg-err" style="padding-left:${30 + node.depth * 18}px">${Icons.svg('alert-circle', 13)}Can’t open this folder</div>`;
        } else {
          (r.folders || []).forEach(f => node.kids.appendChild(makeNode(f.path, f.name, node.depth + 1, f.has_children).wrap));
          setLeaf(node, !(r.folders || []).length);
        }
        node.loaded = true;
        node.loading = null;
      })();
      return node.loading;
    }

    async function expand(node) {
      node.expanded = true;
      node.wrap.classList.add('expanded');
      node.row.setAttribute('aria-expanded', 'true');
      await load(node);
    }
    function collapse(node) {
      node.expanded = false;
      node.wrap.classList.remove('expanded');
      node.row.setAttribute('aria-expanded', 'false');
    }
    function toggle(node) { return node.expanded ? collapse(node) : expand(node); }

    // Open every folder from the drive down to `target` and select it.
    async function reveal(target, opts) {
      target = _norm(target);
      const chain = [];
      for (let p = target; p; p = _parent(p)) { chain.unshift(p); if (_isRoot(p)) break; }
      let node = st.nodes.get(_key(chain[0]));
      if (!node) {   // share or a drive that was not listed
        node = makeNode(chain[0], chain[0], 0, true, 'drive');
        drivesEl.appendChild(node.wrap);
      }
      for (let i = 1; i < chain.length; i++) {
        await expand(node);
        let next = st.nodes.get(_key(chain[i]));
        if (!next) {   // hidden / system folder that the listing leaves out
          next = makeNode(chain[i], _base(chain[i]), node.depth + 1, true);
          node.kids.appendChild(next.wrap);
          setLeaf(node, false);
        }
        node = next;
      }
      if (opts && opts.expandLast) await expand(node);
      select(target);
      requestAnimationFrame(() => node.row.scrollIntoView({ block: 'center' }));
      return node;
    }

    // ── selection + destination checks ─────────────────────────────────────
    function problem(dest) {
      if (!dest) return 'Choose a destination folder.';
      const dk = _key(dest);
      for (const p of paths) {
        const pk = _key(p);
        if (dk === pk || dk.startsWith(pk + '\\')) return `A folder can’t be ${isMove ? 'moved' : 'copied'} into itself.`;
      }
      if (isMove && paths.every(p => _key(_parent(p)) === dk)) return 'The items are already in this folder.';
      return '';
    }
    function select(path) {
      st.sel = path ? _norm(path) : '';
      const k = _key(st.sel);
      overlay.querySelectorAll('.fp-row[data-path]').forEach(r => {
        const on = !!st.sel && _key(r.dataset.path) === k;
        r.classList.toggle('selected', on);
        r.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      const why = problem(st.sel);
      destEl.className = 'fp-dest' + (why && st.sel ? ' has-problem' : '');
      destEl.innerHTML = st.sel
        ? `<span class="fp-dest-label">${isMove ? 'Move to' : 'Copy to'}</span>
           <span class="fp-dest-path" title="${_esc(st.sel)}">${Icons.svg('folder', 14)}<span class="truncate">${_esc(st.sel)}</span></span>
           ${why ? `<span class="fp-dest-why">${Icons.svg('alert-triangle', 13)}${_esc(why)}</span>` : ''}`
        : `<span class="fp-dest-label">Choose a destination folder</span>`;
      if (okBtn) {
        okBtn.disabled = !!why;
        Dialogs.setBtn(okBtn, st.sel && !why ? `${verb} here` : verb, isMove ? 'folder-input' : 'copy');
      }
    }

    // ── recent destinations ────────────────────────────────────────────────
    async function renderRecent() {
      const seen = new Set(), list = [];
      [..._recentLoad(), ...((App.state && App.state.recentFolders) || [])].forEach(p => {
        const k = _key(p);
        if (p && !seen.has(k)) { seen.add(k); list.push(_norm(p)); }
      });
      const checks = await Promise.all(list.slice(0, 10).map(p =>
        SFM.pathExists(p).then(r => (r && r.is_dir) ? p : null).catch(() => null)));
      const ok = checks.filter(Boolean).slice(0, 6);
      const box = $('#fp-recent');
      if (!box) return;
      box.innerHTML = '';
      ok.forEach(p => {
        const row = document.createElement('div');
        row.className = 'fp-row fp-recent-row' + (_key(p) === _key(cur) ? ' is-current' : '');
        row.dataset.path = p;
        row.title = p;
        row.setAttribute('role', 'treeitem');
        row.innerHTML = `<span class="fp-icon">${Icons.svg('folder', 16)}</span>
          <span class="fp-label">${_esc(_base(p))}</span><span class="fp-sub">${_esc(_parent(p) || p)}</span>`;
        row.addEventListener('click', () => select(p));
        row.addEventListener('dblclick', () => reveal(p));
        box.appendChild(row);
      });
      $('#fp-recent-sec').hidden = !ok.length;
      if (st.sel) select(st.sel);
    }

    // ── search ─────────────────────────────────────────────────────────────
    function showResults(on) {
      resultsEl.hidden = !on;
      $('#fp-tree-sec').hidden = on;
      $('#fp-recent-sec').hidden = on || !$('#fp-recent').children.length;
    }
    function _mark(name, words) {
      let html = _esc(name);
      words.forEach(w => {
        const re = new RegExp('(' + _esc(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
        html = html.replace(re, '<mark>$1</mark>');
      });
      return html;
    }
    async function runSearch(q) {
      const my = ++st.seq;
      const words = q.split(/\s+/).filter(Boolean);
      resultsEl.innerHTML = `<div class="fp-msg"><span class="spinner"></span>Searching ${_esc(root)}…</div>`;
      let r = null;
      try { r = await SFM.call('find_folders', root, q, 200, cur); } catch (e) { r = { ok: false, error: String(e) }; }
      if (my !== st.seq) return;
      const res = (r && r.results) || [];
      if (!res.length) {
        resultsEl.innerHTML = `<div class="empty-state empty-state-sm"><div class="empty-state-icon">${Icons.svg('search', 22)}</div>
          <div class="empty-state-text">No folders on ${_esc(root)} match “${_esc(q)}”.</div></div>`;
        return;
      }
      resultsEl.innerHTML = '';
      res.forEach(f => {
        const row = document.createElement('div');
        row.className = 'fp-row fp-result-row' + (_key(f.path) === _key(cur) ? ' is-current' : '');
        row.dataset.path = _norm(f.path);
        row.title = f.path;
        row.setAttribute('role', 'treeitem');
        row.innerHTML = `<span class="fp-icon">${Icons.svg('folder', 16)}</span>
          <span class="fp-label">${_mark(f.name, words)}</span><span class="fp-sub">${_esc(_norm(f.parent))}</span>`;
        row.addEventListener('click', () => select(f.path));
        row.addEventListener('dblclick', () => { filterEl.value = ''; showResults(false); reveal(f.path); });
        resultsEl.appendChild(row);
      });
      if (r.truncated) {
        const more = document.createElement('div');
        more.className = 'fp-msg';
        more.textContent = 'Showing the first matches — type more of the name to narrow the search.';
        resultsEl.appendChild(more);
      }
      if (st.sel) select(st.sel);
    }
    filterEl.addEventListener('input', () => {
      clearTimeout(st.timer);
      const q = filterEl.value.trim();
      if (!q) { st.seq++; showResults(false); return; }
      showResults(true);
      st.timer = setTimeout(() => runSearch(q), 250);
    });
    filterEl.addEventListener('keydown', e => {
      if (e.key === 'Escape' && filterEl.value) { e.stopPropagation(); filterEl.value = ''; st.seq++; showResults(false); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); treeEl.focus(); moveSel(1); }
      else if (e.key === 'Enter') { e.preventDefault(); if (st.sel && !problem(st.sel)) go(); }
    });

    // ── keyboard in the tree ───────────────────────────────────────────────
    function visibleRows() {
      return Array.from(treeEl.querySelectorAll('.fp-row[data-path]')).filter(r => r.offsetParent !== null);
    }
    function moveSel(d) {
      const rows = visibleRows();
      if (!rows.length) return;
      let i = rows.findIndex(r => r.classList.contains('selected'));
      i = i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + d));
      select(rows[i].dataset.path);
      rows[i].scrollIntoView({ block: 'nearest' });
    }
    treeEl.addEventListener('keydown', e => {
      if (e.target !== treeEl) return;
      const node = st.sel ? st.nodes.get(_key(st.sel)) : null;
      const inTree = node && node.row.offsetParent !== null && !node.row.closest('.fp-results, #fp-recent');
      if (e.key === 'ArrowDown') { e.preventDefault(); moveSel(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); moveSel(-1); }
      else if (e.key === 'ArrowRight' && inTree) {
        e.preventDefault();
        if (!node.expanded && node.hasChildren) expand(node); else moveSel(1);
      } else if (e.key === 'ArrowLeft' && inTree) {
        e.preventDefault();
        if (node.expanded) collapse(node);
        else { const par = st.nodes.get(_key(_parent(node.path))); if (par) { select(par.path); par.row.scrollIntoView({ block: 'nearest' }); } }
      } else if (e.key === 'Enter') { e.preventDefault(); if (st.sel && !problem(st.sel)) go(); }
      else if (e.key === 'F2' || (e.ctrlKey && e.shiftKey && (e.key === 'N' || e.key === 'n'))) { e.preventDefault(); newFolderInPicker(); }
    });

    // ── New folder inside the picker (inline name, Explorer style) ─────────
    async function newFolderInPicker() {
      if (st.busy) return;
      if (filterEl.value) { filterEl.value = ''; st.seq++; showResults(false); }
      const parentPath = st.sel || cur;
      const parent = await reveal(parentPath, { expandLast: true });
      overlay.querySelector('.fp-new')?.remove();
      const taken = new Set();
      for (const n of st.nodes.values()) if (_key(_parent(n.path)) === _key(parent.path)) taken.add(n.name.toLowerCase());
      let name = 'New folder';
      for (let i = 2; taken.has(name.toLowerCase()); i++) name = `New folder (${i})`;
      const ed = document.createElement('div');
      ed.className = 'fp-new';
      ed.innerHTML = `<div class="fp-row fp-new-row" style="padding-left:${6 + (parent.depth + 1) * 18}px">
          <span class="fp-twist leaf">${Icons.svg('chevron-right', 14)}</span>
          <span class="fp-icon">${Icons.svg('folder-plus', 16)}</span>
          <input class="fp-new-input" type="text" spellcheck="false" autocomplete="off" aria-label="New folder name" value="${_esc(name)}">
        </div>
        <div class="fp-new-err" style="padding-left:${46 + (parent.depth + 1) * 18}px" hidden></div>`;
      parent.kids.prepend(ed);
      const input = ed.querySelector('input'), err = ed.querySelector('.fp-new-err');
      let done = false;
      const cancel = () => { if (done) return; done = true; ed.remove(); treeEl.focus(); };
      const commit = async () => {
        if (done || st.busy) return;
        const v = input.value.trim();
        if (!v) { cancel(); return; }
        st.busy = true;
        const r = await SFM.createFolder(parent.path, v).catch(e => ({ ok: false, error: String(e) }));
        st.busy = false;
        if (!r || !r.ok) {
          err.hidden = false;
          err.textContent = (r && r.error) || 'Couldn’t create the folder';
          input.classList.add('is-invalid');
          input.focus();
          return;
        }
        done = true;
        ed.remove();
        await load(parent, true);
        setLeaf(parent, false);
        const made = st.nodes.get(_key(r.path));
        select(r.path);
        if (made) made.row.scrollIntoView({ block: 'nearest' });
        treeEl.focus();
        if (_key(parent.path) === _key(FileTree.getCurrentFolder())) FileTree.refresh();
      };
      input.addEventListener('keydown', e => {
        e.stopPropagation();   // Esc must not close the dialog, Enter must not copy
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
      });
      input.addEventListener('input', () => {
        const bad = /[\\/:*?"<>|]/.test(input.value);
        err.hidden = !bad;
        err.textContent = bad ? 'A folder name can’t contain \\ / : * ? " < > |' : '';
        input.classList.toggle('is-invalid', bad);
      });
      input.addEventListener('blur', () => setTimeout(() => { if (!done && document.activeElement !== input) commit(); }, 0));
      input.focus();
      input.select();
      ed.scrollIntoView({ block: 'nearest' });
    }
    $('#fp-newfolder').addEventListener('click', newFolderInPicker);

    // ── do it ──────────────────────────────────────────────────────────────
    async function go() {
      const dest = st.sel;
      const why = problem(dest);
      if (why) { App.toast(why, 'warning'); return; }
      overlay._close();
      App.setStatus(isMove ? 'Moving…' : 'Copying…', true);
      let r;
      try { r = await (isMove ? SFM.moveFiles(paths, dest) : SFM.copyFiles(paths, dest)); }
      catch (e) { r = { ok: false, error: String(e), results: [] }; }
      App.setStatus('Ready');
      const done = ((r && r.results) || []).filter(x => x.ok && !x.unchanged);
      const renamed = done.filter(x => _base(x.dst).toLowerCase() !== _base(x.src).toLowerCase());
      const where = _base(dest);
      if (done.length) {
        _recentAdd(dest);
        let msg = `${isMove ? 'Moved' : 'Copied'} ${done.length} item${done.length === 1 ? '' : 's'} to “${_esc(where)}”`;
        if (renamed.length) {
          msg += ` — ${renamed.length} renamed so nothing was replaced: ${renamed.slice(0, 3).map(x => '“' + _esc(_base(x.dst)) + '”').join(', ')}${renamed.length > 3 ? '…' : ''}`;
        }
        App.toast(msg, r.ok ? 'success' : 'warning', renamed.length ? 6500 : 3500);
      }
      if (!r || !r.ok) App.toast(`${verb} failed${done.length ? ' for some items' : ''}: ${_esc((r && r.error) || 'unknown error')}`, 'error', 7000);
      if (_key(dest) === _key(FileTree.getCurrentFolder())) await FileTree.selectPaths(done.map(x => x.dst));
      else await FileTree.refresh();
    }

    // ── build: drives, then open the current folder ────────────────────────
    select(cur);
    renderRecent();
    let drives = [];
    try { const r = await SFM.getDrives(); drives = (r && r.ok && r.drives) || []; } catch (_) {}
    drivesEl.innerHTML = '';
    drives.forEach(d => drivesEl.appendChild(makeNode(d.path, d.name || d.path, 0, true, 'drive').wrap));
    try {
      await reveal(cur || root);
      const curNode = st.nodes.get(_key(cur));
      if (curNode && curNode.hasChildren) await expand(curNode);
    } catch (e) {
      console.error('[FolderTools] reveal', e);
    }
    treeEl.focus();
    overlay._reveal = reveal;          // used by the e2e tests
    overlay.dataset.ready = '1';
    return overlay;
  }

  // ═════════════════════════════════════════════════════════════════════════
  //  New folders from a list
  // ═════════════════════════════════════════════════════════════════════════
  const STATUS = {
    ok:        { icon: 'check-circle', label: n => 'to create' },
    exists:    { icon: 'folder', label: n => n === 1 ? 'already exists' : 'already exist' },
    duplicate: { icon: 'copy', label: n => n === 1 ? 'duplicate' : 'duplicates' },
    invalid:   { icon: 'alert-circle', label: n => 'invalid' },
  };
  const MAX_ROWS = 1000;

  function openFromList(parent, text) {
    parent = _norm(parent || FileTree.getCurrentFolder());
    if (!parent) { App.toast('Open a folder first', 'warning'); return null; }
    const body = `
      <div class="nfl-grid">
        <div class="nfl-left">
          <div class="field nfl-text-field">
            <label class="field-label" for="nfl-text">Folder names</label>
            <textarea id="nfl-text" class="input-text nfl-text" spellcheck="false" wrap="off"
              placeholder="Paste names here — for example a column copied from Excel.&#10;One name per line; cells separated by tabs each become a folder."></textarea>
            <div class="field-hint">Blank lines are skipped and spaces around each name are removed.</div>
          </div>
          <div class="field-grid">
            <div class="field">
              <label class="field-label" for="nfl-prefix">Prefix</label>
              <input id="nfl-prefix" class="input-text" type="text" autocomplete="off" spellcheck="false" placeholder="e.g. 2025 - ">
            </div>
            <div class="field">
              <label class="field-label" for="nfl-suffix">Suffix</label>
              <input id="nfl-suffix" class="input-text" type="text" autocomplete="off" spellcheck="false" placeholder="e.g.  (scans)">
            </div>
          </div>
          <div class="field">
            <span class="field-label">Numbering</span>
            <div class="segmented segmented-block" id="nfl-num" role="radiogroup" aria-label="Numbering">
              <button type="button" class="seg-btn active" data-num="none">None</button>
              <button type="button" class="seg-btn" data-num="before">01 Name</button>
              <button type="button" class="seg-btn" data-num="after">Name 01</button>
            </div>
          </div>
          <div class="field-grid nfl-numopts" id="nfl-numopts" hidden>
            <div class="field">
              <label class="field-label" for="nfl-start">Start at</label>
              <input id="nfl-start" class="input-text" type="number" min="0" step="1" value="1">
            </div>
            <div class="field">
              <label class="field-label" for="nfl-sep">Separator</label>
              <select id="nfl-sep" class="input-text">
                <option value=" ">Space</option>
                <option value=" - ">Dash ( - )</option>
                <option value="_">Underscore ( _ )</option>
                <option value=". ">Dot ( . )</option>
              </select>
            </div>
          </div>
        </div>
        <div class="nfl-right">
          <div class="nfl-prev-head">
            <span class="field-label">Preview</span>
            <span class="nfl-total" id="nfl-total"></span>
          </div>
          <div class="nfl-summary" id="nfl-summary"></div>
          <div class="nfl-list" id="nfl-list" role="list"></div>
        </div>
      </div>`;

    const overlay = Dialogs.openModal('newfolders', 'New folders from a list', body, [
      { label: 'Cancel', onClick: () => overlay._close() },
      { label: 'Create folders', primary: true, icon: 'folder-plus', onClick: () => create() },
    ], { icon: 'folder-plus', subtitle: parent, size: 'xl', cls: 'nfl-modal' });

    const $ = sel => overlay.querySelector(sel);
    const ta = $('#nfl-text'), listEl = $('#nfl-list'), sumEl = $('#nfl-summary'), totalEl = $('#nfl-total');
    const okBtn = overlay.querySelector('[data-modal-btn="Create folders"]');
    let numbering = 'none', seq = 0, timer = 0, plan = null, busy = false;

    const opts = () => ({
      prefix: $('#nfl-prefix').value,
      suffix: $('#nfl-suffix').value,
      numbering,
      start: parseInt($('#nfl-start').value, 10) || 0,
      sep: $('#nfl-sep').value,
    });

    function render() {
      const rows = (plan && plan.rows) || [];
      const c = (plan && plan.counts) || { total: 0, ok: 0, exists: 0, duplicate: 0, invalid: 0 };
      totalEl.textContent = c.total ? `${c.total} name${c.total === 1 ? '' : 's'}` : '';
      sumEl.innerHTML = c.total ? Object.keys(STATUS).filter(k => c[k]).map(k =>
        `<span class="badge nfl-badge is-${k}">${Icons.svg(STATUS[k].icon, 12)}${c[k]} ${STATUS[k].label(c[k])}</span>`).join('') : '';
      if (!rows.length) {
        listEl.innerHTML = `<div class="empty-state empty-state-sm nfl-empty">
          <div class="empty-state-icon">${Icons.svg('folder-plus', 22)}</div>
          <div class="empty-state-title">Nothing to preview yet</div>
          <div class="empty-state-text">Paste names on the left. Each one becomes a folder in “${_esc(_base(parent))}”.</div></div>`;
      } else {
        const shown = rows.slice(0, MAX_ROWS);
        listEl.innerHTML = shown.map(r => `
          <div class="nfl-row is-${r.status}" role="listitem" title="${_esc(r.reason || r.name)}">
            <span class="nfl-idx">${r.index + 1}</span>
            <span class="nfl-st">${Icons.svg(STATUS[r.status].icon, 14)}</span>
            <span class="nfl-name">${_esc(r.name)}</span>
            ${r.reason ? `<span class="nfl-reason">${_esc(r.reason)}</span>` : ''}
          </div>`).join('')
          + (rows.length > MAX_ROWS ? `<div class="fp-msg">…and ${rows.length - MAX_ROWS} more</div>` : '');
      }
      if (okBtn) {
        okBtn.disabled = !c.ok || busy;
        Dialogs.setBtn(okBtn, c.ok ? `Create ${c.ok} folder${c.ok === 1 ? '' : 's'}` : 'Create folders', 'folder-plus');
        okBtn.title = c.total > c.ok ? `${c.total - c.ok} name${c.total - c.ok === 1 ? '' : 's'} with a problem will be skipped` : '';
      }
    }

    async function refresh() {
      const my = ++seq;
      let r = null;
      try { r = await SFM.call('plan_folders_from_list', parent, ta.value, opts()); } catch (e) { r = { ok: false, error: String(e) }; }
      if (my !== seq) return;
      if (!r || !r.ok) { plan = null; render(); App.toast('Preview failed: ' + _esc((r && r.error) || ''), 'error'); return; }
      plan = r;
      render();
    }
    const schedule = () => { clearTimeout(timer); timer = setTimeout(refresh, 120); };

    async function create() {
      if (busy) return;
      clearTimeout(timer);
      await refresh();                       // the folder may have changed meanwhile
      if (!plan || !plan.counts.ok) return;
      busy = true; render();
      let r = null;
      try { r = await SFM.call('create_folders_from_list', parent, ta.value, opts()); } catch (e) { r = { ok: false, error: String(e) }; }
      busy = false;
      if (!r || !r.ok) { render(); App.toast('Couldn’t create the folders: ' + _esc((r && r.error) || ''), 'error', 6000); return; }
      overlay._close();
      const made = r.created || [], skipped = r.skipped || [];
      if (made.length) {
        App.toast(`Created ${made.length} folder${made.length === 1 ? '' : 's'}${skipped.length ? ` — ${skipped.length} skipped` : ''}`, skipped.length ? 'warning' : 'success', 5000);
        // Ctrl+Z moves the new (empty) folders to _to_review — nothing is deleted.
        let moved = [];
        App.pushUndo({
          label: `Create ${made.length} folder${made.length === 1 ? '' : 's'}`,
          undo: async () => {
            const u = await SFM.softDelete(made);
            moved = ((u && u.results) || []).filter(x => x.ok && x.moved_to).map(x => ({ path: x.path, moved_to: x.moved_to }));
            await FileTree.refresh();
            if (!u || !u.ok) throw new Error((u && u.error) || 'move failed');
          },
          redo: async () => {
            const u = await SFM.restoreSoftDeleted(moved);
            await FileTree.refresh();
            if (!u || !u.ok) throw new Error((u && u.error) || 'restore failed');
          },
        });
      } else {
        App.toast('No folders were created — every name had a problem', 'warning', 5000);
      }
      if (_key(parent) === _key(FileTree.getCurrentFolder())) await FileTree.selectPaths(made);
    }

    ta.addEventListener('input', schedule);
    ['#nfl-prefix', '#nfl-suffix', '#nfl-start'].forEach(s => $(s).addEventListener('input', schedule));
    $('#nfl-sep').addEventListener('change', schedule);
    overlay.querySelectorAll('#nfl-num [data-num]').forEach(b => b.addEventListener('click', () => {
      numbering = b.dataset.num;
      overlay.querySelectorAll('#nfl-num .seg-btn').forEach(x => x.classList.toggle('active', x === b));
      $('#nfl-numopts').hidden = numbering === 'none';
      schedule();
    }));
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); create(); }
    });
    if (text) ta.value = text;
    render();
    if (text) refresh();
    setTimeout(() => ta.focus(), 30);
    overlay._refresh = refresh;   // used by the e2e tests
    return overlay;
  }

  // ═════════════════════════════════════════════════════════════════════════
  //  Entry points: empty-space menu, toolbar drop-down, shortcut
  // ═════════════════════════════════════════════════════════════════════════
  function folderMenuItems() {
    const folder = FileTree.getCurrentFolder();
    return [
      { icon: 'folder-plus', label: 'New folder', shortcut: 'Ctrl+Shift+N', run: () => FileTree.newFolder(), disabled: !folder },
      { icon: 'list-checks', label: 'New folders from a list\u2026', shortcut: 'Ctrl+Shift+L', run: () => openFromList(), disabled: !folder },
    ];
  }

  function showBackgroundMenu(x, y) {
    const folder = FileTree.getCurrentFolder();
    ContextMenu.showItems(x, y, [
      ...folderMenuItems(),
      'sep',
      { icon: 'paste', label: 'Paste', shortcut: 'Ctrl+V', run: () => FileTree.pasteSelection(), disabled: !folder },
      'sep',
      { icon: 'refresh', label: 'Refresh', shortcut: 'F5', run: () => FileTree.refresh(), disabled: !folder },
      { icon: 'folder-open', label: 'Open in Explorer', run: () => SFM.openFolder(folder, false), disabled: !folder },
      { icon: 'link', label: 'Copy Folder Path', disabled: !folder, run: () =>
          SFM.copyText(folder).then(ok => App.toast(ok ? 'Path copied' : 'Could not copy to the clipboard', ok ? 'success' : 'error')) },
    ], { kind: 'background' });
  }

  function _init() {
    ['filelist-list', 'filelist-thumb'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('contextmenu', e => {
        if (e.target.closest('.file-item, .thumb-tile, input, textarea, button')) return;
        e.preventDefault();
        showBackgroundMenu(e.clientX, e.clientY);
      });
    });
    const caret = document.getElementById('btn-new-folder-menu');
    if (caret) caret.addEventListener('click', () => {
      const r = (document.getElementById('btn-new-folder') || caret).getBoundingClientRect();
      ContextMenu.showItems(r.left, r.bottom + 4, folderMenuItems(), { kind: 'new-folder' });
    });
    const fl = document.getElementById('fl-new-folder');
    if (fl) fl.addEventListener('click', () => FileTree.newFolder());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();

  return {
    openPicker,
    openCopyTo: paths => openPicker('copy', paths),
    openMoveTo: paths => openPicker('move', paths),
    openFromList,
    showBackgroundMenu,
  };
})();
