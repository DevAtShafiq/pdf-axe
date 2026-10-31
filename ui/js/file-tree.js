/**
 * file-tree.js — File list (left panes) + thumbnail gallery
 *
 * Handles:
 *   - Folder listing (list view + thumbnail view)
 *   - Multi-select (click, shift-click, ctrl-click, keyboard)
 *   - Lazy PDF thumbnail loading
 *   - Inline rename (Explorer-style: F2 / slow second click / context menu,
 *     template suggestions under the name, Tab / Shift+Tab = next / previous)
 *   - Drag-and-drop
 *   - Breadcrumb
 *   - Search filtering
 *   - View mode switching (list ↔ thumb)
 *   - Thumbnail zoom
 *   - Context menu trigger
 */

const FileTree = (() => {

  // ── State ─────────────────────────────────────────────────────────────────
  let _entries      = [];    // raw entries from bridge
  let _filtered     = [];    // after search
  let _selected     = new Set();  // Set of paths
  let _focusIdx     = -1;   // index in _filtered
  let _shiftAnchor  = -1;
  let _viewMode     = 'list';
  let _thumbZoom    = 100;   // %
  let _thumbCache   = new Map();  // path → data-url
  let _thumbObserver = null;
  let _clipboard    = { mode: null, paths: [] }; // cut/copy
  let _edit         = null;  // active inline rename (see "Inline rename" below)
  let _searchQuery  = '';
  let _currentFolder = '';
  // Folders opened inline in the list view (chevron / → / *): their contents
  // show indented under them. Kept while you stay in this folder.
  let _expanded     = new Set();      // normalised folder paths
  let _childCache   = new Map();      // normalised folder path → its entries
  const EXPAND_ALL_DEPTH = 3, EXPAND_ALL_MAX = 2000;

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const listEl  = () => $('filelist-list');
  const thumbEl = () => $('filelist-thumb');
  const treeEl  = () => $('tree-body');

  // ── File-type icons ───────────────────────────────────────────────────────
  // Consistent line icons from icons.js, tinted per file type (.ft-* classes).
  function iconFor(entry, size = 16) {
    return Icons.file(entry, size);
  }
  const _folderIcon = (size = 16) => Icons.svg('folder', size);
  const _driveIcon  = (size = 16) => Icons.svg('hard-drive', size);
  const _chev = open => Icons.svg(open ? 'chevron-down' : 'chevron-right', 14);

  // Compact modified date for the list column
  function _fmtShortDate(ts) {
    if (!ts) return '';
    try {
      const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
      const now = new Date();
      if (d.toDateString() === now.toDateString())
        return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
      return d.toLocaleDateString(undefined, { year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric', month: 'short', day: 'numeric' });
    } catch (e) { return ''; }
  }

  // ── Load Folder ───────────────────────────────────────────────────────────
  let _loadSeq = 0;
  async function loadFolder(path) {
    if (App.normPath) path = App.normPath(path);
    const sameFolder = _currentFolder && _samePath(path, _currentFolder);
    if (!sameFolder) _expanded = new Set();
    _childCache = new Map();
    _sizeCache.clear();                    // F5 / after a file operation: measure again
    _fsJob = '';
    SFM.folderSizesCancel && SFM.folderSizesCancel().catch(() => {});
    _currentFolder = path;
    App.state.currentFolder = path;
    _selected.clear();
    _focusIdx = -1;
    _shiftAnchor = -1;   // a stale anchor from the last folder made Shift+click throw
    _entries  = [];
    _filtered = [];

    App.setStatus('Loading…', true);
    _renderBreadcrumb(path);

    const my = ++_loadSeq;
    try {
      const r = await SFM.listFolder(path);
      // Clicking folders quickly: an older, slower listing must not replace the newer one.
      if (my !== _loadSeq) return;
      if (!r.ok) { App.toast(r.error, 'error'); App.setStatus('Error'); return; }
      _entries  = _sortEntries(r.entries || []);
      _filtered = _entries;
      if (_expanded.size) await _reloadExpanded(my);
      if (my !== _loadSeq) return;
      _applySearchFilter(_searchQuery);
      _measureFolders();
      App.setStatus(`${_filtered.length} items`);
      App.setStatusFolder(path);
      _selectionChanged();
      _renderTreeRoot();
      _updateTreeHighlight(path);   // after the re-render, or the highlight is lost
    } catch(e) {
      if (my !== _loadSeq) return;
      App.toast('Failed to load folder: ' + e, 'error');
      App.setStatus('Error');
    }
  }

  function refresh() {
    return _currentFolder ? loadFolder(_currentFolder) : Promise.resolve();
  }

  // ── Sorting (click a column header; remembered) ───────────────────────────
  // Default: newest first, so freshly downloaded files are on top.
  const _SORT_STORE = 'oa.filelist.sort';
  let _sort = { key: 'mtime', dir: 'desc', foldersFirst: false };
  try { Object.assign(_sort, JSON.parse(localStorage.getItem(_SORT_STORE) || '{}')); } catch (_) {}
  const _byName = (a, b) => String(a.name).localeCompare(String(b.name), undefined, { numeric: true, sensitivity: 'base' });
  // folders count by their measured size once known (Size column), else first
  const _sizeKey = e => e.is_dir ? (e.size_str && e.size_str !== '…' ? e.size : -1) : (e.size || 0);
  function _sortEntries(list) {
    const k = _sort.key, m = _sort.dir === 'asc' ? 1 : -1;
    return list.slice().sort((a, b) => {
      if (_sort.foldersFirst && !!a.is_dir !== !!b.is_dir) return a.is_dir ? -1 : 1;
      let c = 0;
      if (k === 'name') c = _byName(a, b);
      else if (k === 'size') c = _sizeKey(a) - _sizeKey(b);
      else c = (a.mtime || 0) - (b.mtime || 0);
      return c ? c * m : _byName(a, b);
    });
  }
  function _renderSortHeader() {
    document.querySelectorAll('#fl-sort-bar .col-sort').forEach(h => {
      const on = h.dataset.sort === _sort.key;
      h.classList.toggle('active', on);
      h.setAttribute('aria-sort', on ? (_sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      const arrow = h.querySelector('.sort-arrow');
      if (arrow) arrow.textContent = on ? (_sort.dir === 'asc' ? ' ↑' : ' ↓') : '';
    });
    const ff = $('fl-folders-first');
    if (ff) { ff.classList.toggle('active', !!_sort.foldersFirst); ff.setAttribute('aria-pressed', String(!!_sort.foldersFirst)); }
  }
  function setSort(key, foldersFirst) {
    if (key) {
      if (_sort.key === key) _sort.dir = _sort.dir === 'asc' ? 'desc' : 'asc';
      else { _sort.key = key; _sort.dir = key === 'name' ? 'asc' : 'desc'; }
    }
    if (typeof foldersFirst === 'boolean') _sort.foldersFirst = foldersFirst;
    try { localStorage.setItem(_SORT_STORE, JSON.stringify(_sort)); } catch (_) {}
    _entries = _sortEntries(_entries);
    _applySearchFilter(_searchQuery);
    _renderSortHeader();
  }

  // After the visible list is rebuilt (sort / search), keep the focus ring and
  // Shift-anchor on the same FILES — not the same row numbers. Otherwise F2
  // renamed whichever file had moved into the old row. Selected items hidden
  // by the search are dropped from the selection (they can't be seen).
  function _remapAfterReorder(focusPath, anchorPath) {
    const at = p => p ? _filtered.findIndex(e => e.path === p) : -1;
    _focusIdx = at(focusPath);
    _shiftAnchor = at(anchorPath);
    const visible = new Set(_filtered.map(e => e.path));
    const before = _selected.size;
    _selected.forEach(p => { if (!visible.has(p)) _selected.delete(p); });
    if (_focusIdx < 0 && _selected.size) _focusIdx = _filtered.findIndex(e => _selected.has(e.path));
    return before !== _selected.size;
  }
  function _initSortHeader() {
    document.querySelectorAll('#fl-sort-bar .col-sort').forEach(h =>
      h.addEventListener('click', () => setSort(h.dataset.sort)));
    const ff = $('fl-folders-first');
    if (ff) ff.addEventListener('click', () => setSort(null, !_sort.foldersFirst));
    _renderSortHeader();
  }

  // Refresh, then select + reveal `path` when it is in the current folder
  // (used after an operation creates a new file, e.g. a merged PDF).
  async function refreshAndSelect(path) {
    if (!_currentFolder) return;
    const box = _viewMode === 'list' ? listEl() : thumbEl();
    const top = box ? box.scrollTop : 0;
    await loadFolder(_currentFolder);
    if (box) box.scrollTop = top;          // keep the scroll position
    const idx = _filtered.findIndex(e => _samePath(e.path, path));
    if (idx < 0) return;
    _selected.clear();
    _selected.add(_filtered[idx].path);
    _focusIdx = idx;
    _renderCurrent();
    _selectionChanged();
    _scrollFocusedIntoView();
  }
  const revealPath = refreshAndSelect;
  const _normPath = p => String(p || '').replace(/[\\/]+/g, '/').replace(/\/+$/, '').toLowerCase();
  const _samePath = (a, b) => _normPath(a) === _normPath(b);

  // ── Search ────────────────────────────────────────────────────────────────
  function applySearch(query) {
    _searchQuery = query.toLowerCase();
    _applySearchFilter(_searchQuery);
  }

  function _applySearchFilter(q) {
    const focusPath  = _filtered[_focusIdx] ? _filtered[_focusIdx].path : null;
    const anchorPath = _filtered[_shiftAnchor] ? _filtered[_shiftAnchor].path : null;
    _filtered = _flatten(q);
    const selChanged = _remapAfterReorder(focusPath, anchorPath);
    _renderCurrent();
    if (selChanged) _selectionChanged();
    else {
      App.state.focusedPath = _focusIdx >= 0 ? _filtered[_focusIdx].path : null;
    }
    App.setStatus(`${_filtered.length} items${q ? ' (filtered)' : ''}`);
  }


  // ── Folder sizes (Size column) ──────────────────────────────────────────
  // Measured in the background, visible folders first; cached per folder and
  // cleared on F5 / after file operations (any reload of the folder). Can be
  // switched off in Settings (slow network drives).
  const _FS_STORE = 'oa.filelist.folderSizes';
  const _sizeCache = new Map();          // normalised path → {bytes, size_str, files, folders, complete}
  let _fsJob = '', _fsWanted = new Set();
  const folderSizesOn = () => { try { return localStorage.getItem(_FS_STORE) !== '0'; } catch (_) { return true; } };
  function setFolderSizes(on) {
    try { localStorage.setItem(_FS_STORE, on ? '1' : '0'); } catch (_) {}
    if (!on) { SFM.folderSizesCancel().catch(() => {}); _fsJob = ''; }
    _applyKnownSizes();
    _renderCurrent();
    if (on) _measureFolders();
  }
  function _sizeTip(s) {
    const n = (k, w) => `${k.toLocaleString()} ${w}${k === 1 ? '' : 's'}`;
    return `${n(s.files, 'file')}, ${n(s.folders, 'folder')}${s.complete ? '' : ' (still more — stopped counting)'}`;
  }
  function _applyKnownSizes() {
    const on = folderSizesOn();
    const all = [..._entries];
    _childCache.forEach(list => all.push(...list));
    for (const e of all) {
      if (!e.is_dir) continue;
      const s = on ? _sizeCache.get(_normPath(e.path)) : null;
      e.size = s ? s.bytes : 0;
      e.size_str = s ? s.size_str : (on && _fsWanted.has(_normPath(e.path)) ? '…' : '');
      e.size_tip = s ? _sizeTip(s) : '';
    }
  }
  function _measureFolders() {
    if (!folderSizesOn() || _viewMode !== 'list') return;
    // visible rows first, then the rest of the list
    const box = listEl();
    const top = box ? box.scrollTop : 0, h = box ? box.clientHeight : 0, rowH = 30;
    const first = Math.max(0, Math.floor(top / rowH) - 5), last = Math.ceil((top + h) / rowH) + 5;
    const dirs = _filtered.map((e, i) => [e, i]).filter(([e]) => e.is_dir && !_sizeCache.has(_normPath(e.path)));
    if (!dirs.length) return;
    dirs.sort(([, a], [, b]) => ((a >= first && a <= last) ? 0 : 1) - ((b >= first && b <= last) ? 0 : 1) || a - b);
    const paths = dirs.map(([e]) => e.path);
    _fsWanted = new Set(paths.map(_normPath));
    _fsJob = 'fs' + Date.now();
    SFM.folderSizes(paths, _fsJob).catch(() => {});
    _applyKnownSizes();
    paths.forEach(p => _paintSize(p));
  }
  function _paintSize(path) {
    const row = listEl()?.querySelector(`.file-item[data-path="${CSS.escape(path)}"] .size`);
    const e = _filtered.find(x => x.path === path);
    if (row && e) { row.textContent = e.size_str || ''; row.title = e.size_tip || ''; row.classList.toggle('is-pending', e.size_str === '…'); }
  }
  let _fsResortTimer = 0;
  function _onFolderSize(d) {
    if (!d || d.job_id !== _fsJob) return;
    _sizeCache.set(_normPath(d.path), { bytes: d.bytes, size_str: d.size_str, files: d.files, folders: d.folders, complete: d.complete });
    _applyKnownSizes();
    _paintSize(d.path);
    if (_sort.key === 'size') {                 // folders join the size order as they arrive
      clearTimeout(_fsResortTimer);
      _fsResortTimer = setTimeout(() => { _entries = _sortEntries(_entries); _applySearchFilter(_searchQuery); }, 400);
    }
    if (_selected.size === 1 && _selected.has(d.path) && window.Details && Details.showFile)
      Details.showFile(_filtered.find(e => e.path === d.path) || null);
  }
  if (typeof SFM !== 'undefined' && SFM.on) SFM.on('folder_size', _onFolderSize);

  // ── Folders expanded inline (list view) ─────────────────────────────────
  // The visible list: top-level entries, each expanded folder followed by its
  // (sorted) contents one level deeper. With a search, matches are kept plus
  // the folders that lead to them.
  function _flatten(q) {
    const out = [];
    const tree = _viewMode === 'list';
    const walk = (list, depth, parent) => {
      let any = false;
      for (const e of list) {
        e.depth = depth;
        e.parentPath = parent;
        const k = _normPath(e.path);
        const open = tree && e.is_dir && _expanded.has(k) && _childCache.has(k);
        e.expanded = open;
        const at = out.length;
        out.push(e);
        let kidHit = false;
        if (open) kidHit = walk(_sortEntries(_childCache.get(k)), depth + 1, e.path);
        const hit = !q || e.name.toLowerCase().includes(q);
        if (!hit && !kidHit) out.splice(at);          // drop it (and nothing below it matched)
        else any = true;
      }
      return any;
    };
    walk(_entries, 0, '');
    return out;
  }

  async function _listChildren(path) {
    const k = _normPath(path);
    if (_childCache.has(k)) return _childCache.get(k);
    const r = await SFM.listFolder(path).catch(e => ({ ok: false, error: String(e) }));
    if (!r || !r.ok) throw new Error((r && r.error) || 'Could not open the folder');
    _childCache.set(k, r.entries || []);
    return _childCache.get(k);
  }

  // After a refresh: list the expanded folders again (drop the ones that vanished).
  async function _reloadExpanded(seq) {
    const want = [..._expanded];
    for (const k of want) {
      if (seq !== _loadSeq) return;
      try { await _listChildren(k); } catch (_) { _expanded.delete(k); }
    }
  }

  async function setExpanded(path, open) {
    const k = _normPath(path);
    if (open) {
      try { await _listChildren(path); }
      catch (e) { App.toast(_esc(e.message || String(e)), 'error'); return; }
      _expanded.add(k);
      setTimeout(_measureFolders, 0);
    } else {
      // collapsing hides everything inside; a selection in there moves to the folder
      for (const x of [..._expanded]) if (x === k || x.startsWith(k + '/')) _expanded.delete(x);
      const inside = [..._selected].filter(p => _normPath(p).startsWith(k + '/'));
      if (inside.length) {
        inside.forEach(p => _selected.delete(p));
        _selected.add(_filtered.find(e => _normPath(e.path) === k)?.path || path);
      }
    }
    const focus = _filtered[_focusIdx];
    const focusInside = focus && _normPath(focus.path).startsWith(k + '/');
    _applySearchFilter(_searchQuery);
    if (!open && focusInside) {
      _focusIdx = _filtered.findIndex(e => _normPath(e.path) === k);
      _shiftAnchor = _focusIdx;
      _renderCurrent();
      _selectionChanged();
    }
  }
  const toggleExpanded = entry => setExpanded(entry.path, !entry.expanded);

  /** Expand every folder under *root* (or the whole list), up to a limit. */
  async function listExpandAll(rootEntry) {
    if (_viewMode !== 'list') setViewMode('list');
    const queue = (rootEntry ? [rootEntry] : _entries.filter(e => e.is_dir)).map(e => ({ path: e.path, depth: 1 }));
    let count = _filtered.length, capped = false;
    App.setStatus('Expanding folders…', true);
    while (queue.length) {
      const { path, depth } = queue.shift();
      let kids;
      try { kids = await _listChildren(path); } catch (_) { continue; }
      if (count + kids.length > EXPAND_ALL_MAX) { capped = true; break; }
      _expanded.add(_normPath(path));
      count += kids.length;
      if (depth < EXPAND_ALL_DEPTH) kids.filter(k => k.is_dir).forEach(k => queue.push({ path: k.path, depth: depth + 1 }));
      else if (kids.some(k => k.is_dir)) capped = true;
    }
    _applySearchFilter(_searchQuery);
    _measureFolders();
    if (capped) App.toast(`Expanded ${EXPAND_ALL_DEPTH} levels / up to ${EXPAND_ALL_MAX.toLocaleString()} items — open deeper folders one by one`, 'info', 5000);
  }

  function listCollapseAll() {
    if (!_expanded.size) return;
    const focus = _filtered[_focusIdx];
    _expanded = new Set();
    const top = focus && focus.depth ? _topAncestor(focus) : null;
    _selected = new Set([..._selected].filter(p => _entries.some(e => e.path === p)));
    if (top) _selected.add(top);
    _applySearchFilter(_searchQuery);
    if (top) { _focusIdx = _filtered.findIndex(e => e.path === top); _shiftAnchor = _focusIdx; _renderCurrent(); }
    _selectionChanged();
  }
  function _topAncestor(entry) {
    let e = entry;
    while (e && e.parentPath) e = _filtered.find(x => x.path === e.parentPath);
    return e ? e.path : null;
  }

  // ── Rendering ─────────────────────────────────────────────────────────────
  function _renderCurrent() {
    if (_viewMode === 'list') _renderList();
    else                      _renderThumbs();
    if (_edit) _mountEditor();
  }

  function _renderList() {
    const el = listEl();
    el.innerHTML = '';
    if (!_filtered.length) {
      el.innerHTML = _emptyHtml();
      return;
    }
    _filtered.forEach((entry, idx) => {
      const row = document.createElement('div');
      row.className = 'file-item' + (entry.is_dir ? ' is-dir' : '')
                    + (_selected.has(entry.path) ? ' selected' : '')
                    + (idx === _focusIdx ? ' focused' : '');
      row.dataset.idx  = idx;
      row.dataset.path = entry.path;
      row.title = entry.name;
      const depth = entry.depth || 0;
      if (depth) row.style.paddingLeft = `calc(var(--space-2) + ${depth * 18}px)`;
      if (entry.expanded) row.classList.add('is-expanded');
      row.innerHTML = `
        ${entry.is_dir ? `<button type="button" class="twisty" tabindex="-1" aria-label="${entry.expanded ? 'Collapse' : 'Expand'} folder" aria-expanded="${!!entry.expanded}">${_chev(!!entry.expanded)}</button>`
                       : '<span class="twisty-space"></span>'}
        <span class="icon">${iconFor(entry)}</span>
        <span class="name">${_esc(entry.name)}</span>
        <span class="date">${_esc(_fmtShortDate(entry.mtime))}</span>
        <span class="size${entry.size_str === '…' ? ' is-pending' : ''}"${entry.size_tip ? ` title="${_esc(entry.size_tip)}"` : ''}>${entry.size_str || ''}</span>`;
      _bindRowEvents(row, idx, entry);
      el.appendChild(row);
    });
  }

  function _renderThumbs() {
    const el = thumbEl();
    el.innerHTML = '';
    const size = Math.round(80 * _thumbZoom / 100);

    if (!_filtered.length) {
      el.innerHTML = _emptyHtml();
      el.firstElementChild.style.gridColumn = '1 / -1';
      return;
    }

    _filtered.forEach((entry, idx) => {
      const tile = document.createElement('div');
      tile.className = 'thumb-tile' + (_selected.has(entry.path) ? ' selected' : '');
      tile.dataset.idx  = idx;
      tile.dataset.path = entry.path;

      const wrap = document.createElement('div');
      wrap.className = 'img-wrap';
      wrap.style.width = wrap.style.height = size + 'px';

      if (_thumbCache.has(entry.path)) {
        const img = document.createElement('img');
        img.src = _thumbCache.get(entry.path);
        wrap.appendChild(img);
      } else if (_isPreviewable(entry)) {
        // placeholder — lazy load
        const ic = document.createElement('span');
        ic.className = 'thumb-icon';
        ic.innerHTML = iconFor(entry, Math.round(size * 0.42));
        wrap.appendChild(ic);
        wrap.dataset.lazyPath = entry.path;
        wrap.dataset.lazyExt  = entry.ext;
      } else {
        const ic = document.createElement('span');
        ic.className = 'thumb-icon';
        ic.innerHTML = iconFor(entry, Math.round(size * 0.42));
        wrap.appendChild(ic);
      }

      const name = document.createElement('div');
      name.className = 'thumb-name';
      name.textContent = entry.name;
      name.title = entry.name;

      tile.appendChild(wrap);
      tile.appendChild(name);
      _bindTileEvents(tile, idx, entry);
      el.appendChild(tile);
    });

    _startLazyThumbLoad();
  }

  // Empty states: no folder yet / nothing matches the search / empty folder
  function _emptyHtml() {
    if (!_currentFolder) {
      return `<div class="empty-state">
        <div class="empty-state-icon">${Icons.svg('folder-open', 28)}</div>
        <p class="empty-state-title">Open a folder to get started</p>
        <p class="empty-state-text">Browse your PDFs and images, then merge, split, convert or compress them.</p>
        <button class="btn btn-primary" onclick="FileTree.browseFolder()">${Icons.svg('folder-open', 16)}Open folder</button>
      </div>`;
    }
    if (_searchQuery) {
      return `<div class="empty-state">
        <div class="empty-state-icon">${Icons.svg('search', 26)}</div>
        <p class="empty-state-title">No matches</p>
        <p class="empty-state-text">Nothing in this folder matches “${_esc(_searchQuery)}”.</p>
      </div>`;
    }
    return `<div class="empty-state">
      <div class="empty-state-icon">${Icons.svg('folder', 26)}</div>
      <p class="empty-state-title">This folder is empty</p>
    </div>`;
  }

  function _isPreviewable(entry) {
    return ['.pdf','.jpg','.jpeg','.png','.bmp','.webp','.gif'].includes(entry.ext);
  }

  // ── Lazy thumbnail loading (IntersectionObserver) ─────────────────────────
  function _startLazyThumbLoad() {
    if (_thumbObserver) _thumbObserver.disconnect();
    _thumbObserver = new IntersectionObserver(async entries => {
      for (const obs of entries) {
        if (!obs.isIntersecting) continue;
        const wrap = obs.target;
        const p    = wrap.dataset.lazyPath;
        const ext  = wrap.dataset.lazyExt;
        if (!p || _thumbCache.has(p)) continue;
        _thumbObserver.unobserve(wrap);
        try {
          let r;
          if (ext === '.pdf') r = await SFM.getPdfThumb(p, 0, 72);
          else                r = await SFM.getImagePreview(p, 200);
          if (r && r.ok && r.data_url) {
            _thumbCache.set(p, r.data_url);
            const img = document.createElement('img');
            img.src = r.data_url;
            wrap.innerHTML = '';
            wrap.appendChild(img);
          }
        } catch(e) {}
        delete wrap.dataset.lazyPath;
      }
    }, { rootMargin: '100px' });

    thumbEl().querySelectorAll('[data-lazy-path]').forEach(wrap => {
      _thumbObserver.observe(wrap);
    });
  }

  // ── Breadcrumb ────────────────────────────────────────────────────────────
  function _renderBreadcrumb(path) {
    const bc = $('breadcrumb');
    if (!bc._editBound) {
      bc._editBound = true;
      bc.addEventListener('click', e => { if (e.target === bc) editPath(); });
      bc.title = 'Click empty space to type or paste a path (Ctrl+L)';
    }
    bc.innerHTML = '';
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
    // Deep paths: show the drive, an ellipsis for the middle, and the last two
    // folders, instead of squeezing every segment down to a couple of letters.
    const collapse = parts.length > 4;
    const hiddenTo = parts.length - 2;   // parts[1 .. hiddenTo-1] are hidden
    // Drive letter gets special treatment
    // Network paths (\\server\share) keep their leading "//".
    let cumulative = /^[\\/]{2}/.test(path) ? '/' : '';
    parts.forEach((part, i) => {
      cumulative += (i === 0 ? '' : '/') + part;
      if (i === 0 && cumulative.startsWith('/')) cumulative = '/' + cumulative;
      if (collapse && i >= 1 && i < hiddenTo) {
        if (i === hiddenTo - 1) {
          const more = document.createElement('span');
          more.className = 'breadcrumb-seg breadcrumb-more';
          more.textContent = '…';
          more.title = cumulative.replace(/\//g, '\\');
          const capMore = cumulative;
          more.addEventListener('click', () => App.navigate(capMore + '/'));
          bc.appendChild(more);
          const arrow = document.createElement('span');
          arrow.className = 'breadcrumb-arrow';
          arrow.innerHTML = Icons.svg('chevron-right', 12);
          bc.appendChild(arrow);
        }
        return;
      }
      const seg = document.createElement('span');
      seg.className = 'breadcrumb-seg' + (i === parts.length - 1 ? ' last' : '');
      seg.title = cumulative.replace(/\//g, '\\');
      if (i === 0 && /^[A-Za-z]:$/.test(part)) {
        seg.innerHTML = Icons.svg('hard-drive', 14) + '<span>' + _esc(part) + '</span>';
      } else {
        seg.textContent = part;
      }
      const capPath = cumulative;
      seg.addEventListener('click', () => App.navigate(capPath + '/'));
      bc.appendChild(seg);
      if (i < parts.length - 1) {
        const arrow = document.createElement('span');
        arrow.className = 'breadcrumb-arrow';
        arrow.innerHTML = Icons.svg('chevron-right', 12);
        bc.appendChild(arrow);
      }
    });
  }

  // ── Type or paste a path (click empty space in the breadcrumb, Ctrl+L, Alt+D, F4)
  function editPath() {
    const bc = $('breadcrumb');
    if (!bc || bc.querySelector('.breadcrumb-input')) { bc && bc.querySelector('.breadcrumb-input')?.select(); return; }
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'breadcrumb-input';
    inp.spellcheck = false;
    inp.setAttribute('aria-label', 'Folder or file path');
    inp.placeholder = 'Type or paste a folder or file path, then press Enter';
    inp.value = _currentFolder || '';
    bc.innerHTML = '';
    bc.appendChild(inp);
    inp.focus();
    inp.select();
    let done = false;
    const restore = () => {
      if (done) return;
      done = true;
      _renderBreadcrumb(_currentFolder || '');
      if (!_currentFolder) bc.innerHTML = '<span class="breadcrumb-seg last">No folder open</span>';
    };
    inp.addEventListener('keydown', async e => {
      e.stopPropagation();
      if (e.key === 'Escape') { e.preventDefault(); restore(); (listEl() || document.body).focus(); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        const ok = await openPath(inp.value, { quietSame: true });
        if (ok) { restore(); (listEl() || document.body).focus(); } else inp.select();
      }
    });
    inp.addEventListener('blur', () => setTimeout(restore, 120));
  }

  /** Open a typed / pasted path: a folder opens; a file opens its folder,
   *  gets selected and previewed. Returns true when it went somewhere. */
  async function openPath(text, opts = {}) {
    const r = await SFM.resolveUserPath(String(text || '')).catch(e => ({ ok: false, error: String(e) }));
    if (!r || !r.ok) { if (!opts.silent) App.toast(_esc((r && r.error) || 'Couldn’t open that path'), 'warning', 5000); return false; }
    if (r.is_dir) {
      if (!(opts.quietSame && _samePath(r.path, _currentFolder))) App.navigate(r.path);
      return true;
    }
    if (!_samePath(r.folder, _currentFolder)) App.navigate(r.folder);
    await refreshAndSelect(r.path);
    const entry = _filtered.find(e => _samePath(e.path, r.path));
    if (entry && window.Preview && Preview.previewFile) Preview.previewFile(entry.path, entry.ext);
    return true;
  }

  // ── Selection ─────────────────────────────────────────────────────────────
  function _bindRowEvents(el, idx, entry) {
    const tw = el.querySelector('.twisty');
    if (tw) {
      tw.addEventListener('click', e => { e.stopPropagation(); toggleExpanded(entry); });
      tw.addEventListener('dblclick', e => e.stopPropagation());
      tw.addEventListener('mousedown', e => e.stopPropagation());
    }
    el.addEventListener('click', e => _handleRowClick(e, idx, entry));
    el.addEventListener('dblclick', e => _handleRowDblClick(e, entry));
    el.addEventListener('contextmenu', e => _handleRowCtx(e, entry));
    el.setAttribute('tabindex', '-1');
  }

  function _bindTileEvents(el, idx, entry) {
    el.addEventListener('click', e => _handleRowClick(e, idx, entry));
    el.addEventListener('dblclick', e => _handleRowDblClick(e, entry));
    el.addEventListener('contextmenu', e => _handleRowCtx(e, entry));
  }

  const SLOW_CLICK_RENAME = false;
  function _handleRowClick(e, idx, entry) {
    // Explorer "slow second click": a plain click on the name of the item that
    // was already the only selection (for > 500 ms) starts renaming it, unless
    // it turns into a double-click.
    clearTimeout(_slowTimer);
    const wasSole = _selected.size === 1 && _selected.has(entry.path) && _focusIdx === idx;
    // Off by default: it opened the rename box by surprise, and Ctrl+C then
    // copied the file NAME instead of the file. Rename with F2 / menu / tile.
    if (SLOW_CLICK_RENAME && wasSole && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0 && e.detail === 1
        && e.target && e.target.closest && e.target.closest('.name, .thumb-name')
        && Date.now() - _soleSince > 500 && !_edit) {
      _slowTimer = setTimeout(() => {
        if (!_edit && _selected.size === 1 && _selected.has(entry.path)) startRename(entry.path);
      }, 600);
      return;   // selection is unchanged - no re-render needed
    }
    if (e.ctrlKey || e.metaKey) {
      // toggle
      if (_selected.has(entry.path)) _selected.delete(entry.path);
      else                            _selected.add(entry.path);
      _shiftAnchor = idx;   // Explorer: a later Shift+click extends from here
    } else if (e.shiftKey && _shiftAnchor >= 0 && _shiftAnchor < _filtered.length) {
      // range select
      const lo = Math.min(_shiftAnchor, idx);
      const hi = Math.max(_shiftAnchor, idx);
      _selected.clear();
      for (let i = lo; i <= hi; i++) _selected.add(_filtered[i].path);
    } else {
      _selected.clear();
      _selected.add(entry.path);
      _shiftAnchor = idx;
    }
    _focusIdx = idx;
    _renderCurrent();
    _selectionChanged();
  }

  // Old-system behaviour: double-click a folder navigates into it;
  // double-click a PDF or image opens FULL VIEW; other files just preview.
  const _FULLVIEW_EXTS = new Set(['.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp', '.gif', '.jfif']);
  function _handleRowDblClick(e, entry) {
    clearTimeout(_slowTimer);
    if (entry.is_dir) {
      App.navigate(entry.path);
      return;
    }
    // ZIP → contents dialog; locked PDF → password first (archive.js)
    if (typeof Archive !== 'undefined' && Archive.onOpen(entry)) return;
    const ext = (entry.ext || '').toLowerCase();
    if (_FULLVIEW_EXTS.has(ext)) {
      Preview.previewFile(entry.path, entry.ext);   // keep side preview in sync
      Dialogs.openFullView(entry.path, ext);
      return;
    }
    Preview.previewFile(entry.path, entry.ext);
  }

  function _handleRowCtx(e, entry) {
    e.preventDefault();
    if (!_selected.has(entry.path)) {
      _selected.clear();
      _selected.add(entry.path);
      _focusIdx = _filtered.findIndex(f => f.path === entry.path);
      _renderCurrent();
      _selectionChanged();
    } else {
      // Keep the right-clicked item focused (context-menu Rename acts on it).
      _focusIdx = _filtered.findIndex(f => f.path === entry.path);
    }
    const selectedEntries = _filtered.filter(f => _selected.has(f.path));
    ContextMenu.show(e.clientX, e.clientY, selectedEntries);
  }

  function _selectionChanged() {
    const paths = Array.from(_selected);
    const soleKey = paths.length === 1 ? paths[0] : '';
    if (soleKey !== _soleKey) { _soleKey = soleKey; _soleSince = Date.now(); }
    App.state.selectedPaths = paths;
    App.state.focusedPath   = _focusIdx >= 0 ? (_filtered[_focusIdx]?.path || null) : null;
    if (App.syncFileTools) App.syncFileTools();
    App.setStatusSelection(paths.length, _filtered.length);

    // Update details pane
    if (paths.length === 1) {
      Details.showFile(_filtered.find(e => e.path === paths[0]) || null);
    } else if (paths.length > 1) {
      // Entry objects (not bare paths) so the details pane can total their sizes
      Details.showMultiple(_entries.filter(e => _selected.has(e.path)));
    } else {
      Details.clear();
    }

    // Update preview if single selection
    const focused = App.state.focusedPath;
    if (focused) {
      const entry = _filtered.find(e => e.path === focused);
      if (entry && !entry.is_dir) Preview.previewFile(focused, entry.ext);
    }
  }

  // ── Keyboard nav in file list ─────────────────────────────────────────────
  // ── Type to select (TypeSelect in type-select.js) ───────────────────────
  // Letters, digits and words pick a file: "15", "shila", "md shahed".
  const TYPE_PAUSE = 1200;               // ms between keys before a new search
  let _typeBuffer = '';
  let _typeTimer  = null;
  let _typeGuardUntil = 0;              // Backspace right after typing never leaves the folder

  function _typeHint(text, found) {
    let el = document.getElementById('type-select-hint');
    if (!text) { if (el) el.classList.remove('show'); return; }
    if (!el) {
      el = document.createElement('div');
      el.id = 'type-select-hint';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      (document.getElementById('pane-filelist') || document.body).appendChild(el);
    }
    el.innerHTML = `${Icons.svg('search', 13)}<span>${_esc(text)}</span>${found ? '' : '<em>no match</em>'}`;
    el.classList.toggle('no-match', !found);
    el.classList.add('show');
  }

  function _typeAheadJump(ch) {
    const fresh = !_typeBuffer;
    _typeBuffer += ch;
    clearTimeout(_typeTimer);
    _typeTimer = setTimeout(() => { _typeBuffer = ''; _typeHint(''); }, TYPE_PAUSE);
    _typeGuardUntil = Date.now() + TYPE_PAUSE + 300;
    const names = _filtered.map(e => e.name);
    let buf = _typeBuffer, again = fresh;
    // the same letter pressed again and again → next item with that letter
    if (TypeSelect.repeated(buf.toLowerCase())) {
      buf = buf[0]; again = true;
    }
    const idx = TypeSelect.find(names, buf, _focusIdx, again);
    _typeHint(_typeBuffer, idx >= 0);
    if (idx < 0) return;
    _focusIdx = idx;
    _selected.clear();
    _selected.add(_filtered[idx].path);
    _shiftAnchor = idx;
    _renderCurrent();
    _selectionChanged();
    _scrollFocusedIntoView();
  }

  // Backspace while typing: remove the last letter and search again. It never
  // leaves the folder; once nothing is left, the next Backspace goes up as usual
  // (after the pause).
  function _typeBackspace() {
    _typeBuffer = _typeBuffer.slice(0, -1);
    clearTimeout(_typeTimer);
    _typeTimer = setTimeout(() => { _typeBuffer = ''; _typeHint(''); }, TYPE_PAUSE);
    if (!_typeBuffer) { _typeHint(''); _typeGuardUntil = Date.now() + TYPE_PAUSE; return; }
    const idx = TypeSelect.find(_filtered.map(x => x.name), _typeBuffer, _focusIdx, false);
    _typeHint(_typeBuffer, idx >= 0);
    if (idx < 0 || idx === _focusIdx) return;
    _focusIdx = idx;
    _selected.clear();
    _selected.add(_filtered[idx].path);
    _shiftAnchor = idx;
    _renderCurrent();
    _selectionChanged();
    _scrollFocusedIntoView();
  }

  function _initKeyboardNav() {
    const handleKey = (e) => {
      clearTimeout(_slowTimer);
      // Only active when file list has focus (or body, no focused input)
      const active = document.activeElement;
      const onList = active && (active.id === 'filelist-list' || active.id === 'filelist-thumb');
      const onBody = !active || active === document.body || active.tagName === 'DIV';
      const typing = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
      // After clicking a toolbar button or the folder tree, letters still pick a
      // file (arrows / Enter / Space keep working the focused button).
      const onControl = active && /^(BUTTON|A|LI|SPAN|SVG)$/i.test(active.tagName) && !active.isContentEditable;
      if (!onList && !onBody && !(onControl && typing && (e.key !== ' ' || _typeBuffer))) return;
      // A dialog is open (its focused DIV counted as "body"): arrows, Space,
      // Ctrl+A and Backspace moved the selection behind it.
      if (App.blocked && App.blocked()) return;
      // The preview pane has focus (text selection, Ctrl+A on a page) — not ours.
      if (active && active.closest && active.closest('#preview-body')) return;
      // Don't hijack search box or rename inputs
      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' ||
                     active.tagName === 'SELECT' || active.isContentEditable)) return;
      if (e.isComposing) return;                     // IME (e.g. Bangla) still composing
      // While a word is being typed, Space is part of it ("md shahed").
      if (e.key === ' ' && _typeBuffer && !e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        _typeAheadJump(' ');
        return;
      }

      if (e.key === 'Backspace' && !e.ctrlKey && !e.altKey && (_typeBuffer || Date.now() < _typeGuardUntil)) {
        e.preventDefault();
        if (_typeBuffer) _typeBackspace(); else _typeGuardUntil = Date.now() + TYPE_PAUSE;
        return;
      }
      if (e.key.length > 1 && _typeBuffer) { _typeBuffer = ''; clearTimeout(_typeTimer); _typeHint(''); }

      // ── → / ← / * : open and close folders inline (list view) ─────────────
      if (_viewMode === 'list' && (onList || onBody) && !e.ctrlKey && !e.metaKey && !e.altKey &&
          (e.key === 'ArrowRight' || e.key === 'ArrowLeft' || (e.key === '*' && !_typeBuffer))) {
        const cur = _filtered[_focusIdx];
        if (!cur) return;
        e.preventDefault();
        if (e.key === '*') { if (cur.is_dir) listExpandAll(cur); return; }
        if (e.key === 'ArrowRight') {
          if (!cur.is_dir) return;
          if (!cur.expanded) { setExpanded(cur.path, true); return; }
          const next = _filtered[_focusIdx + 1];
          if (next && next.parentPath === cur.path) _focusIdx++; else return;
        } else {
          if (cur.is_dir && cur.expanded) { setExpanded(cur.path, false); return; }
          if (!cur.parentPath) return;
          const up = _filtered.findIndex(x => x.path === cur.parentPath);
          if (up < 0) return;
          _focusIdx = up;
        }
        const entry = _filtered[_focusIdx];
        _selected.clear(); _selected.add(entry.path); _shiftAnchor = _focusIdx;
        _renderCurrent(); _selectionChanged(); _scrollFocusedIntoView();
        return;
      }

      // ── Arrow navigation ──────────────────────────────────────────────────
      // ↓ on the last item goes to the first, ↑ on the first goes to the last
      // (Shift+arrow still stops at the ends so a range never wraps).
      const n = _filtered.length;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (!n) return;
        if (_focusIdx >= n - 1 && !e.shiftKey) _focusIdx = 0;
        else _focusIdx = Math.min(_focusIdx + 1, n - 1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (!n) return;
        if (_focusIdx <= 0 && !e.shiftKey) _focusIdx = _focusIdx === 0 ? n - 1 : 0;
        else _focusIdx = Math.max(_focusIdx - 1, 0);
      } else if (e.key === 'Home') {
        e.preventDefault();
        _focusIdx = 0;
      } else if (e.key === 'End') {
        e.preventDefault();
        _focusIdx = _filtered.length - 1;
      } else if (e.key === 'PageDown') {
        e.preventDefault();
        _focusIdx = Math.min(_focusIdx + 10, _filtered.length - 1);
      } else if (e.key === 'PageUp') {
        e.preventDefault();
        _focusIdx = Math.max(_focusIdx - 10, 0);

      // ── Enter: open / navigate ─────────────────────────────────────────
      } else if (e.key === 'Enter') {
        const entry = _filtered[_focusIdx];
        if (entry) _handleRowDblClick(e, entry);
        return;

      // ── Space: toggle selection without moving focus ───────────────────
      } else if (e.key === ' ') {
        e.preventDefault();
        const entry = _filtered[_focusIdx];
        if (!entry) return;
        if (_selected.has(entry.path)) _selected.delete(entry.path);
        else _selected.add(entry.path);
        _renderCurrent();
        _selectionChanged();
        return;

      // ── Ctrl+A: select all ─────────────────────────────────────────────
      // (not Ctrl+Shift+A — that opens Annotate for the selected file)
      } else if ((e.key === 'a' || e.key === 'A') && (e.ctrlKey || e.metaKey) && !e.shiftKey) {
        e.preventDefault();
        _filtered.forEach(en => _selected.add(en.path));
        _renderCurrent();
        _selectionChanged();
        return;

      // ── Backspace: go up one folder ────────────────────────────────────
      // (while a word is being typed it only removes the last letter)
      } else if (e.key === 'Backspace' && !e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        App.navUp();     // "C:\Users" → "C:\" (not "C:", which is drive C's cwd)
        return;

      // ── Printable character: type-ahead jump ───────────────────────────
      } else if (typing) {
        e.preventDefault();
        _typeAheadJump(e.key);
        return;

      } else { return; }

      // Common post-nav logic (Arrow / Home / End / Page)
      const entry = _filtered[_focusIdx];
      if (!entry) return;
      if (e.shiftKey && _shiftAnchor < 0) _shiftAnchor = _focusIdx;
      if (e.shiftKey && _shiftAnchor < _filtered.length) {
        // Shift+Arrow → extend selection range
        const lo = Math.min(_shiftAnchor, _focusIdx);
        const hi = Math.max(_shiftAnchor, _focusIdx);
        _selected.clear();
        for (let i = lo; i <= hi; i++) _selected.add(_filtered[i].path);
      } else if (!e.ctrlKey) {
        _selected.clear();
        _selected.add(entry.path);
        _shiftAnchor = _focusIdx;
      }
      _renderCurrent();
      _selectionChanged();
      _scrollFocusedIntoView();
    };

    document.addEventListener('keydown', handleKey);
  }

  function _scrollFocusedIntoView() {
    const container = _viewMode === 'list' ? listEl() : thumbEl();
    const el = container?.querySelector(`[data-idx="${_focusIdx}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  // ── View Mode ─────────────────────────────────────────────────────────────
  function setViewMode(mode) {
    _viewMode = mode;
    App.state.viewMode = mode;

    const listView  = $('filelist-list');
    const thumbView = $('filelist-thumb');
    const zoomBar   = $('thumb-zoom-bar');
    const listBtn   = $('fl-view-list');
    const thumbBtn  = $('fl-view-thumb');
    const tbListBtn = $('btn-view-list');
    const tbThumbBtn= $('btn-view-thumb');

    if (mode === 'list') {
      listView.classList.remove('hidden');
      thumbView.classList.add('hidden');
      if (zoomBar)    zoomBar.style.display    = 'none';
      if (listBtn)    listBtn.classList.add('active');
      if (thumbBtn)   thumbBtn.classList.remove('active');
      if (tbListBtn)  tbListBtn.classList.add('active');
      if (tbThumbBtn) tbThumbBtn.classList.remove('active');
    } else {
      listView.classList.add('hidden');
      thumbView.classList.remove('hidden');
      if (zoomBar)    zoomBar.style.display    = 'flex';
      if (listBtn)    listBtn.classList.remove('active');
      if (thumbBtn)   thumbBtn.classList.add('active');
      if (tbListBtn)  tbListBtn.classList.remove('active');
      if (tbThumbBtn) tbThumbBtn.classList.add('active');
    }
    _applySearchFilter(_searchQuery);
  }

  // ── Thumbnail Zoom ────────────────────────────────────────────────────────
  function _initThumbZoom() {
    const slider   = $('thumb-zoom-slider');
    const zoomIn   = $('thumb-zoom-in');
    const zoomOut  = $('thumb-zoom-out');
    if (slider) {
      slider.addEventListener('input', () => { _thumbZoom = +slider.value; _renderThumbs(); });
    }
    if (zoomIn)  zoomIn.addEventListener('click',  () => { _thumbZoom = Math.min(200, _thumbZoom + 20); if(slider) slider.value = _thumbZoom; _renderThumbs(); });
    if (zoomOut) zoomOut.addEventListener('click', () => { _thumbZoom = Math.max(50,  _thumbZoom - 20); if(slider) slider.value = _thumbZoom; if(slider) slider.value = _thumbZoom; _renderThumbs(); });

    // Ctrl+Scroll on thumbnail area
    const thumbArea = $('filelist-thumb');
    if (thumbArea) {
      thumbArea.addEventListener('wheel', e => {
        if (!e.ctrlKey) return;
        e.preventDefault();
        _thumbZoom = Math.max(50, Math.min(200, _thumbZoom + (e.deltaY < 0 ? 20 : -20)));
        if (slider) slider.value = _thumbZoom;
        _renderThumbs();
      }, { passive: false });
    }
  }

  // ── Clipboard (cut/copy/paste) ────────────────────────────────────────────
  // Files go on the real Windows clipboard, so they paste into Explorer,
  // WhatsApp, Outlook… and files copied in Explorer paste in here.
  async function _toWindowsClipboard(paths, cut) {
    let r;
    try { r = await SFM.setClipboardFiles(paths, cut); } catch (e) { r = { ok: false, error: String(e) }; }
    return r && r.ok ? '' : ((r && r.error) || 'clipboard unavailable');
  }
  async function copySelection() {
    const paths = Array.from(_selected);
    if (!paths.length) return;
    _clipboard = { mode: 'copy', paths };
    const err = await _toWindowsClipboard(paths, false);
    if (err) App.toast(`Copied inside Office Axe only (${err})`, 'warning', 3000);
    else App.toast(`${paths.length} item(s) copied — paste here or in Explorer, WhatsApp, email…`, 'success', 2200);
  }
  async function cutSelection() {
    const paths = Array.from(_selected);
    if (!paths.length) return;
    _clipboard = { mode: 'cut', paths };
    const err = await _toWindowsClipboard(paths, true);
    App.toast(`${paths.length} item(s) cut — paste to move them`, err ? 'warning' : 'info', 1800);
  }
  async function pasteSelection() {
    // Prefer whatever is on the Windows clipboard (e.g. copied in Explorer);
    // it also holds what was copied here, unless another app replaced it.
    let winFiles = false;
    try {
      const w = await SFM.getClipboardFiles();
      if (w && w.ok && w.paths && w.paths.length) {
        winFiles = true;
        const same = w.paths.length === _clipboard.paths.length && w.paths.every(p => _clipboard.paths.includes(p));
        if (!same) _clipboard = { mode: w.cut ? 'cut' : 'copy', paths: w.paths };
      }
    } catch (_) {}
    // No files on the clipboard but a path as text (copied from an email,
    // Explorer's address bar…) → go there instead.
    if (!winFiles) {
      const t = await SFM.getClipboardText().catch(() => null);
      const text = t && t.ok ? String(t.text || '').trim() : '';
      if (text && text.length < 1024 && /^["'“]?(?:[A-Za-z]:|\\\\|%|~|file:)/i.test(text)) {
        if (await openPath(text, { silent: false })) return;
        return;
      }
    }
    if (!_currentFolder) return;
    if (!_clipboard.paths.length) { App.toast('Nothing to paste — copy some files first', 'info', 2000); return; }
    const folder = _currentFolder;
    const cut = _clipboard.mode !== 'copy';
    const r = cut ? await SFM.moveFiles(_clipboard.paths, folder)
                  : await SFM.copyFiles(_clipboard.paths, folder);
    const done = (r && r.results || []).filter(x => x.ok && !x.unchanged);
    pushFileOpUndo(cut, done);
    if (cut && r && r.ok) _clipboard = { mode: null, paths: [] };
    if (r && r.ok) App.toast(cut ? `Moved ${done.length || r.results.length} item(s)` : `Pasted ${done.length} item(s)`, 'success');
    else App.toast(`${cut ? 'Move' : 'Paste'} failed${done.length ? ` for some items (${done.length} done)` : ''}: ${r ? r.error : 'unknown error'}`, 'error', 6000);
    if (!App.normPath || App.normPath(folder) !== App.normPath(_currentFolder)) return;
    // Show the pasted items selected, like Explorer.
    await refresh();
    const pasted = new Set(done.map(x => _normPath(x.dst)));
    if (pasted.size) {
      _selected = new Set(_filtered.filter(e => pasted.has(_normPath(e.path))).map(e => e.path));
      _focusIdx = _filtered.findIndex(e => _selected.has(e.path));
      _shiftAnchor = _focusIdx;
      _renderCurrent(); _selectionChanged(); _scrollFocusedIntoView();
    }
  }

  // ── New Folder ────────────────────────────────────────────────────────────
  // Explorer style: "New folder" (or "New folder (2)") is created right away,
  // selected, and its name opens for editing in place (Enter keeps the typed
  // name, Esc keeps "New folder").
  let _newFolderBusy = false;
  async function newFolder() {
    if (!_currentFolder) { App.toast('Open a folder first', 'warning'); return; }
    if (_newFolderBusy || (_edit && _edit.busy)) return;
    if (_edit) _closeEditor(false);
    _newFolderBusy = true;
    try {
      const listing = await SFM.listFolder(_currentFolder).catch(() => null);
      const names = (listing && listing.ok ? listing.entries : _entries).map(e => String(e.name).toLowerCase());
      const taken = new Set(names);
      let suggestion = 'New folder';
      for (let n = 2; taken.has(suggestion.toLowerCase()); n++) suggestion = `New folder (${n})`;
      const r = await SFM.createFolder(_currentFolder, suggestion);
      if (!r || !r.ok) { App.toast('Couldn’t create the folder: ' + ((r && r.error) || 'unknown error'), 'error'); return; }
      // Ctrl+Z removes it again (to the Recycle Bin). Renaming it right after
      // is its own undo step, undone first.
      _pushCreatedUndo(`New folder “${suggestion}”`, [r.path]);
      // A search that hides the new folder would leave nothing to rename.
      if (_searchQuery) {
        const si = $('search-input'); if (si) si.value = '';
        _searchQuery = ''; App.state.searchQuery = '';
      }
      await refreshAndSelect(r.path);
      if (!startRename(r.path)) App.toast(`Created folder “${suggestion}”`, 'success');
    } finally {
      _newFolderBusy = false;
    }
  }

  // Refresh and select several items (e.g. folders made from a list).
  async function selectPaths(paths) {
    await refresh();
    const want = new Set((paths || []).map(_normPath));
    _selected = new Set(_filtered.filter(e => want.has(_normPath(e.path))).map(e => e.path));
    _focusIdx = _filtered.findIndex(e => _selected.has(e.path));
    _shiftAnchor = _focusIdx;
    _renderCurrent(); _selectionChanged(); _scrollFocusedIntoView();
    return _selected.size;
  }

  // ── Delete ────────────────────────────────────────────────────────────────
  function deleteSelection() {
    return softDeletePaths(Array.from(_selected));
  }

  // Delete (Del key, context menu, details pane), like File Explorer: confirm,
  // move the items to the Recycle Bin, Ctrl+Z puts them back. On a drive with
  // no Recycle Bin (USB stick, network share) they go to _to_review instead.
  const _items = n => `${n} item${n === 1 ? '' : 's'}`;

  // Recycle *paths*; returns the undo records ({path, trashed_at} / {path, moved_to}).
  async function _recycle(paths) {
    const r = await SFM.recycleDelete(paths);
    const gone = ((r && r.results) || []).filter(x => x.ok)
      .map(x => x.moved_to ? { path: x.path, moved_to: x.moved_to } : { path: x.path, trashed_at: x.trashed_at });
    return { r, gone };
  }

  // Undo entry: Ctrl+Z restores what *gone* lists, Ctrl+Y deletes it again.
  function _pushRecycleUndo(label, gone, after = refresh) {
    App.pushUndo({
      label,
      undo: async () => {
        const u = await SFM.restoreRecycled(gone);
        await after();
        if (!u || !u.ok) throw new Error((u && u.error) || 'restore failed');
      },
      redo: async () => {
        const again = await _recycle(gone.map(g => g.path));
        gone = again.gone;
        await after();
        if (!again.r || !again.r.ok) throw new Error((again.r && again.r.error) || 'delete failed');
      },
    });
  }

  // Undo entry for a copy / move (paste, drag, "Move to…" dialog), like
  // Explorer: undoing a copy sends the copies to the Recycle Bin; undoing a
  // move puts the items back where they came from. *done* = [{src, dst}].
  function pushFileOpUndo(cut, done, after = refresh) {
    let items = (done || []).filter(x => x && x.dst && !x.unchanged).map(x => ({ src: x.src, dst: x.dst }));
    if (!items.length) return;
    const label = `${cut ? 'Move' : 'Copy'} ${_items(items.length)}`;
    if (!cut) { _pushCreatedUndo(label, items.map(x => x.dst), after); return; }
    // undo_moves answers one result per item, in order; each tells where the
    // item really landed ("name (2)" if the name had been taken meanwhile).
    App.pushUndo({
      label,
      undo: async () => {
        const u = await SFM.undoMoves(items);
        ((u && u.results) || []).forEach((x, k) => { if (x.ok && items[k]) items[k].src = x.dst; });
        await after();
        if (!u || !u.ok) throw new Error((u && u.error) || 'move back failed');
      },
      redo: async () => {
        const u = await SFM.undoMoves(items.map(i => ({ src: i.dst, dst: i.src })));
        ((u && u.results) || []).forEach((x, k) => { if (x.ok && items[k]) items[k].dst = x.dst; });
        await after();
        if (!u || !u.ok) throw new Error((u && u.error) || 'move failed');
      },
    });
  }

  // Undo entry for new items (copies, a new folder): Ctrl+Z sends them to the
  // Recycle Bin, Ctrl+Y brings them back.
  function _pushCreatedUndo(label, paths, after = refresh) {
    let gone = [];
    App.pushUndo({
      label,
      undo: async () => {
        const res = await _recycle(paths);
        gone = res.gone;
        await after();
        if (!res.r || !res.r.ok) throw new Error((res.r && res.r.error) || 'delete failed');
      },
      redo: async () => {
        const u = await SFM.restoreRecycled(gone);
        await after();
        if (!u || !u.ok) throw new Error((u && u.error) || 'restore failed');
      },
    });
  }

  async function softDeletePaths(paths) {
    paths = (paths || []).filter(Boolean);
    if (!paths.length) return false;
    const one = paths.length === 1 ? paths[0].split(/[\\/]/).pop() : '';
    const ok = await Dialogs.confirm({
      title: paths.length === 1 ? 'Delete' : `Delete ${_items(paths.length)}`,
      message: one ? `Move “${one}” to the Recycle Bin?` : `Move these ${_items(paths.length)} to the Recycle Bin?`,
      detail: `You can restore ${paths.length === 1 ? 'it' : 'them'} from the Recycle Bin, or press Ctrl+Z.`,
      okLabel: 'Delete', okIcon: 'trash', icon: 'trash', danger: true,
    });
    if (!ok) return false;
    const { r, gone } = await _recycle(paths);
    if (gone.length) _pushRecycleUndo(`Delete ${_items(gone.length)}`, gone);
    const review = gone.filter(g => g.moved_to).length;
    if (r && r.ok) {
      App.toast(review
        ? `${_items(gone.length)} removed — this drive has no Recycle Bin, so ${review === 1 ? 'it was' : 'they were'} moved to _to_review/. Ctrl+Z to undo`
        : `${_items(gone.length)} moved to the Recycle Bin — Ctrl+Z to undo`, 'success', review ? 6000 : 3500);
    } else {
      App.toast('Delete failed' + (gone.length ? ` for some items (${gone.length} deleted)` : '') + ': ' + _esc((r && r.error) || 'unknown error'), 'error', 7000);
    }
    await refresh();
    return !!(r && r.ok);
  }

  // ── Combine PDFs ──────────────────────────────────────────────────────────
  // Old system's Ctrl+Enter: combine multiple PDFs, or arrange a single PDF.
  function combineSelected() {
    const files = Array.from(_selected).filter(p => /\.(pdf|jpe?g|png|bmp|gif|tiff?|webp)$/i.test(p));
    if (!files.length) { App.toast('Select PDF or image files first (2+ to merge, 1 PDF to arrange its pages)', 'warning'); return; }
    if (files.length === 1 && /\.pdf$/i.test(files[0])) { Dialogs.openArrangePages(files[0]); return; }
    Dialogs.openCombinePdf(files, _currentFolder);
  }

  // ── Browse folder dialog ──────────────────────────────────────────────────
  async function browseFolder() {
    try {
      const r = await SFM.call('browse_for_folder');
      if (r && r.ok && r.path) App.navigate(r.path);
    } catch(e) {
      App.toast('Could not open folder dialog: ' + e, 'error');
    }
  }

  // ── Inline rename (Windows Explorer style) ───────────────────────────────
  //   F2 / context menu / slow second click on the selected name → the name
  //   cell becomes a text box (stem pre-selected, extension not; folders: all)
  //   with the template suggestions (RenameTemplates.attach) right under it.
  //   Enter      commit (the highlighted template, else the typed name)
  //   Tab        commit and rename the next item; Shift+Tab the previous one
  //              (at either end of the list it just commits)
  //   Esc        first closes the suggestion list, a second Esc cancels the
  //              edit (with the list already closed, one Esc cancels)
  //   Click elsewhere / focus leaves → commit the typed name (if changed)
  //   Typed names: invalid characters are blocked with a balloon tip; an
  //   extension change asks first; a name clash offers "x (2).pdf" (files) or
  //   keeps editing (folders). Template picks auto-suffix on the bridge.
  //   Every rename is undoable (Ctrl+Z). The list is not re-sorted until the
  //   next refresh (like Explorer), so Tab walks the order you see.
  const _BAD_CHARS   = /[\\/:*?"<>|]/;
  const _STRIP_CHARS = /[\\/:*?"<>|\r\n\t]/g;
  const _BAD_LIST    = '\\ / : * ? " < > |';
  const _RESERVED    = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;
  let _slowTimer  = null;
  let _soleKey    = '';
  let _soleSince  = 0;
  let _rtDd       = null;   // shared suggestion dropdown element
  let _tipEl      = null;   // balloon tip element
  let _tipTimer   = null;
  let _tipShownAt = 0;

  const _extOfName = n => { const m = /(\.[^.\s\\/]*)$/.exec(n || ''); return m && m.index > 0 ? m[1] : ''; };
  const _stemLen = (entry, text) => {
    if (entry.is_dir) return text.length;
    const dot = text.lastIndexOf('.');
    return dot > 0 ? dot : text.length;
  };

  function isRenaming() { return !!_edit; }

  // Public: start renaming `path` (default: the focused item). `initialText`
  // pre-fills the box (e.g. a decoded QR value) instead of the current name.
  function startRename(path, initialText) {
    if (_edit && _edit.busy) return false;
    if (_edit) _closeEditor(false);
    clearTimeout(_slowTimer);
    const idx = path ? _filtered.findIndex(e => _samePath(e.path, path)) : _focusIdx;
    const entry = _filtered[idx];
    if (!entry) { if (!path) App.toast('Select a file to rename', 'info', 1800); return false; }
    // Explorer renames the focused item only, even with several selected.
    const already = _selected.size === 1 && _selected.has(entry.path) && _focusIdx === idx;
    _selected.clear(); _selected.add(entry.path);
    _focusIdx = idx; _shiftAnchor = idx;
    _edit = { path: entry.path, entry, idx, input: null, rt: null, busy: false,
              initial: initialText != null ? String(initialText) : null };
    _buildEditor();
    _renderCurrent();                 // mounts the editor into the row
    if (!already) _selectionChanged();
    _scrollFocusedIntoView();
    return true;
  }

  function _ensureDropdown() {
    if (_rtDd && _rtDd.isConnected) return _rtDd;
    _rtDd = document.createElement('div');
    _rtDd.className = 'rename-suggestions inline-rename-suggestions hidden';
    document.body.appendChild(_rtDd);
    return _rtDd;
  }

  function _buildEditor() {
    const ed = _edit;
    const thumb = _viewMode !== 'list';
    const input = document.createElement(thumb ? 'textarea' : 'input');
    if (thumb) input.rows = 1; else input.type = 'text';
    input.className = 'inline-rename' + (thumb ? ' inline-rename-thumb' : '');
    input.spellcheck = false;
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('aria-label', 'New name');
    input.value = ed.initial != null ? ed.initial.replace(_STRIP_CHARS, ' ').trim() : ed.entry.name;
    ed.input = input;
    ed.mountedOnce = false;

    // Keep row/tile handlers (select, double-click open, context menu) away.
    ['mousedown', 'click', 'dblclick', 'contextmenu'].forEach(t =>
      input.addEventListener(t, ev => ev.stopPropagation()));

    // Registered before RenameTemplates, so the text is clean before it filters.
    input.addEventListener('input', () => {
      const v = input.value;
      if (/[\\/:*?"<>|\r\n\t]/.test(v)) {
        const pos = input.selectionStart;
        const before = v.slice(0, pos);
        const removed = (before.match(_STRIP_CHARS) || []).length;
        input.value = v.replace(_STRIP_CHARS, '');
        try { input.setSelectionRange(pos - removed, pos - removed); } catch (_) {}
        if (_BAD_CHARS.test(v)) _tip('A file name can’t contain any of the following characters:', _BAD_LIST);
      } else if (Date.now() - _tipShownAt > 1500) {
        _hideTip();
      }
      if (thumb) _autosize(input);
    });

    input.addEventListener('keydown', e => {
      // Keys typed in the box never reach the list / global shortcuts: a commit
      // can finish (and focus the list) before the event bubbles, and the
      // list's Enter would then open the file.
      e.stopPropagation();
      if (e.key === 'Tab') {
        e.preventDefault(); e.stopPropagation();
        _commit({ advance: e.shiftKey ? -1 : 1 });
      } else if (e.key === 'Enter' && thumb) {
        e.preventDefault();   // textarea: never insert a newline
      }
    });

    const onAway = () => setTimeout(() => {
      if (_edit !== ed || ed.busy || !input.isConnected) return;
      const a = document.activeElement;
      if (a === input || (_rtDd && _rtDd.contains(a))) return;
      if (!document.hasFocus()) return;          // switched windows: keep editing
      _commit({ typedOnly: true, blur: true });
    }, 0);
    input.addEventListener('blur', onAway);
    ed.onAway = onAway;
    _ensureDropdown().addEventListener('focusout', onAway);

    if (typeof RenameTemplates !== 'undefined') {
      ed.rt = RenameTemplates.attach(input, _ensureDropdown(), {
        place: 'below', inline: true,
        bounds: () => { const p = document.getElementById('pane-filelist'); return p ? p.getBoundingClientRect() : null; },
        getEntry: () => (_edit === ed ? ed.entry : null),
        rename: action => _commit({ action }),
        cancel: () => _cancelEdit(),
      });
      if (ed.initial != null) ed.rt.setTyped(true);
    } else {
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); _commit({}); }
        else if (e.key === 'Escape') { e.preventDefault(); _cancelEdit(); }
      });
    }
  }

  function _autosize(ta) {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight + 2, 96) + 'px';
  }

  // (Re)insert the editor into the row of the item being renamed — runs after
  // every render, so a list refresh does not drop an edit in progress.
  function _mountEditor() {
    const ed = _edit;
    const thumb = _viewMode !== 'list';
    const box = thumb ? thumbEl() : listEl();
    const idx = _filtered.findIndex(e => _samePath(e.path, ed.path));
    const row = idx >= 0 && box ? box.querySelector(`[data-idx="${idx}"]`) : null;
    const cell = row && row.querySelector(thumb ? '.thumb-name' : '.name');
    if (!cell || thumb !== ed.input.classList.contains('inline-rename-thumb')) {
      if (!ed.busy) _closeEditor(false);   // item gone or view switched
      return;
    }
    ed.idx = idx;
    const hadFocus = document.activeElement === ed.input;
    const s0 = ed.input.selectionStart, s1 = ed.input.selectionEnd;
    row.classList.add('renaming');
    row.removeAttribute('title');
    cell.textContent = '';
    cell.removeAttribute('title');
    cell.classList.add('is-editing');
    cell.appendChild(ed.input);
    if (thumb) _autosize(ed.input);
    if (ed.busy) return;
    if (!ed.mountedOnce) {
      ed.mountedOnce = true;
      ed.input.focus({ preventScroll: true });
      const v = ed.input.value;
      // Explorer: select the name without the extension (folders: everything);
      // a pre-filled value (e.g. QR text) is selected whole.
      ed.input.setSelectionRange(0, ed.initial != null ? v.length : _stemLen(ed.entry, v));
    } else if (hadFocus || document.activeElement === document.body) {
      ed.input.focus({ preventScroll: true });
      try { ed.input.setSelectionRange(s0, s1); } catch (_) {}
    }
  }

  // End the edit without renaming (Esc).
  function _cancelEdit() {
    if (!_edit || _edit.busy) return;
    const hadFocus = document.activeElement === _edit.input;
    _closeEditor(true);
    if (hadFocus) _focusList();
  }

  function _closeEditor(render) {
    const ed = _edit;
    if (!ed) return;
    _edit = null;
    if (ed.rt) ed.rt.detach();
    if (_rtDd) { _rtDd.classList.add('hidden'); _rtDd.removeEventListener('focusout', ed.onAway); }
    _hideTip();
    if (render) _renderCurrent();
  }

  function _focusList() {
    const box = _viewMode === 'list' ? listEl() : thumbEl();
    if (box) box.focus({ preventScroll: true });
  }

  // Commit the edit. action: {stem, save?} (template) | {typed} | undefined
  // (= highlighted template, else the typed text). advance: ±1 = Tab/Shift+Tab.
  async function _commit({ action, advance = 0, typedOnly = false, blur = false } = {}) {
    const ed = _edit;
    if (!ed || ed.busy) return;
    if (!action) {
      const s = !typedOnly && ed.rt ? ed.rt.selected() : null;
      action = s ? { stem: s.stem } : { typed: ed.input.value };
    }
    const next = advance ? _filtered[ed.idx + advance] : null;
    const nextPath = next ? next.path : null;
    ed.busy = true;
    if (ed.rt) ed.rt.hide();
    let res;
    try { res = await _applyRename(ed, action); }
    catch (e) { res = 'stay'; _tip('Could not rename', String((e && e.message) || e)); }
    finally { ed.busy = false; }

    if (res === 'stay') {
      if (_edit === ed && ed.input.isConnected) ed.input.focus({ preventScroll: true });
      return;
    }
    if (_edit === ed) _closeEditor(true);
    if (nextPath) startRename(nextPath);
    else if (!blur) _focusList();
  }

  // → 'done' (renamed, or nothing to do) | 'stay' (keep editing)
  async function _applyRename(ed, action) {
    const entry = ed.entry;
    const oldPath = entry.path, oldName = entry.name;
    let r, suffixed = false;

    if (action.stem != null) {
      const stem = String(action.stem).trim();
      if (!stem) return 'done';
      r = await SFM.renameWithTemplate(oldPath, stem, !!action.save);
      if (r && r.ok) {
        const want = stem + (entry.is_dir ? '' : (entry.ext || _extOfName(oldName)));
        const got = r.new_name || String(r.new_path || '').split(/[\\/]/).pop();
        suffixed = !!got && got.toLowerCase() !== want.toLowerCase() && got !== oldName;
      }
    } else {
      // Windows trims surrounding spaces and drops trailing dots; empty reverts.
      let name = String(action.typed == null ? '' : action.typed).replace(/[\r\n\t]/g, '').trim().replace(/[. ]+$/, '');
      if (!name || name === oldName) return 'done';
      if (_BAD_CHARS.test(name)) { _tip('A file name can’t contain any of the following characters:', _BAD_LIST); return 'stay'; }
      if (_RESERVED.test(name)) { _tip('The specified device name is invalid.', `“${name}” is reserved by Windows.`); return 'stay'; }
      if (!entry.is_dir) {
        const oe = _extOfName(oldName).toLowerCase(), ne = _extOfName(name).toLowerCase();
        if (oe !== ne) {
          const yes = await _ask({
            title: 'Rename', icon: 'alert-triangle', tone: 'warning',
            text: 'If you change a file name extension, the file might become unusable.',
            detail: 'Are you sure you want to change it?',
          });
          if (!yes) return 'stay';
        }
      }
      const clash = _entries.find(e => !_samePath(e.path, oldPath) && e.name.toLowerCase() === name.toLowerCase());
      if (clash) {
        if (entry.is_dir || clash.is_dir) {
          _tip(`There is already a ${clash.is_dir ? 'folder' : 'file'} with the same name in this location.`, 'Type a different name.');
          return 'stay';
        }
        const alt = _uniqueName(name, oldPath);
        const yes = await _ask({
          title: 'Rename file', icon: 'copy',
          text: 'There is already a file with the same name in this location.',
          detail: `Do you want to rename “${oldName}” to “${alt}”?`,
        });
        if (!yes) return 'stay';
        name = alt;
      }
      r = await SFM.renameFile(oldPath, name);
      if (r && r.ok) r.new_name = name;
    }

    if (!r || !r.ok) {
      _tip('Could not rename', (r && r.error) || 'Unknown error');
      return 'stay';
    }
    const newName = r.new_name || String(r.new_path || '').split(/[\\/]/).pop();
    const newPath = r.new_path || oldPath.replace(/[^\\/]+$/, newName);
    if (newName === oldName && _samePath(newPath, oldPath)) return 'done';

    if (_edit === ed) _closeEditor(false);
    _applyLocalRename(oldPath, newPath, newName);
    App.pushUndo({
      label: `Rename → ${newName}`,
      undo: async () => {
        const u = await SFM.renameFile(newPath, oldName);
        if (!u || !u.ok) throw new Error((u && u.error) || 'rename failed');
        await refreshAndSelect(u.new_path || oldPath);
      },
      redo: async () => {
        const u = await SFM.renameFile(oldPath, newName);
        if (!u || !u.ok) throw new Error((u && u.error) || 'rename failed');
        await refreshAndSelect(u.new_path || newPath);
      },
    });
    App.setStatus(`Renamed to ${newName}`);
    if (action.save) {
      App.toast(r.saved ? `Renamed to ${newName} · saved as template` : `Renamed to ${newName} (template not saved)`, r.saved ? 'success' : 'warning');
    } else if (suffixed) {
      App.toast(`Name was taken — renamed to ${newName}`, 'info', 2500);
    }
    return 'done';
  }

  // Update the renamed item in place (no re-sort until the next refresh) and
  // keep it selected / focused.
  function _applyLocalRename(oldPath, newPath, newName) {
    const fix = e => {
      if (!_samePath(e.path, oldPath)) return e;
      const n = { ...e, name: newName, path: newPath };
      if (!e.is_dir) n.ext = _extOfName(newName).toLowerCase();
      return n;
    };
    const wasSame = _filtered === _entries;
    _entries = _entries.map(fix);
    _filtered = wasSame ? _entries : _filtered.map(fix);
    if (_thumbCache.has(oldPath)) { _thumbCache.set(newPath, _thumbCache.get(oldPath)); _thumbCache.delete(oldPath); }
    const wasSel = [..._selected].some(p => _samePath(p, oldPath));
    _selected = new Set([..._selected].filter(p => !_samePath(p, oldPath)));
    if (wasSel) _selected.add(newPath);
    const i = _filtered.findIndex(e => _samePath(e.path, newPath));
    if (i >= 0) _focusIdx = i;
    _renderCurrent();
    _selectionChanged();
  }

  // "name (2).ext", "name (3).ext" … not used by another item in this folder.
  function _uniqueName(name, selfPath) {
    const ext = _extOfName(name);
    const stem = (ext ? name.slice(0, -ext.length) : name).replace(/ \(\d+\)$/, '');
    const taken = new Set(_entries.filter(e => !_samePath(e.path, selfPath)).map(e => e.name.toLowerCase()));
    for (let n = 2; n < 10000; n++) {
      const cand = `${stem} (${n})${ext}`;
      if (!taken.has(cand.toLowerCase())) return cand;
    }
    return name;
  }

  // Yes / No question in a design-system modal → Promise<boolean>.
  // Enter = Yes (focused), Esc / × / backdrop / No = false.
  function _ask({ title, text, detail, yes = 'Yes', no = 'No', icon = 'info', tone }) {
    return new Promise(resolve => {
      let done = false, mo = null, ov = null;
      const finish = v => {
        if (done) return;
        done = true;
        if (mo) mo.disconnect();
        try { if (ov && ov.isConnected) ov._close(); } catch (_) {}
        resolve(v);
      };
      const body = `<div class="ir-ask"><p class="ir-ask-text">${_esc(text)}</p>${detail ? `<p class="ir-ask-detail">${_esc(detail)}</p>` : ''}</div>`;
      ov = Dialogs.openModal('inline-rename-ask', title, body, [
        { label: no,  onClick: () => finish(false) },
        { label: yes, primary: true, onClick: () => finish(true) },
      ], { icon, tone, size: 'sm' });
      mo = new MutationObserver(() => { if (!ov.isConnected) finish(false); });
      mo.observe(document.body, { childList: true });
      const y = ov.querySelector(`[data-modal-btn="${yes}"]`);
      if (y) setTimeout(() => y.focus(), 0);
    });
  }

  // Windows-style balloon tip under the rename box (above it while the
  // suggestion list is open below).
  function _tip(title, text) {
    const ed = _edit;
    if (!ed || !ed.input || !ed.input.isConnected) { App.toast(title + (text ? ' ' + text : ''), 'error', 4000); return; }
    if (!_tipEl) {
      _tipEl = document.createElement('div');
      _tipEl.className = 'inline-rename-tip';
      _tipEl.setAttribute('role', 'alert');
      document.body.appendChild(_tipEl);
    }
    _tipEl.innerHTML = `<span class="inline-rename-tip-icon">${Icons.svg('alert-circle', 16)}</span>
      <div class="inline-rename-tip-body"><div class="inline-rename-tip-title">${_esc(title)}</div>${text ? `<div class="inline-rename-tip-text">${_esc(text)}</div>` : ''}</div>`;
    const r = ed.input.getBoundingClientRect();
    const tw = Math.min(320, window.innerWidth - 16);
    _tipEl.style.width = tw + 'px';
    _tipEl.style.left = Math.max(8, Math.min(r.left, window.innerWidth - tw - 8)) + 'px';
    _tipEl.classList.add('show');
    const th = _tipEl.offsetHeight;
    const ddOpen = _rtDd && !_rtDd.classList.contains('hidden');
    const above = ddOpen ? (r.top - th - 8 >= 4) : (window.innerHeight - r.bottom < th + 12);
    _tipEl.classList.toggle('above', above);
    _tipEl.style.top = (above ? r.top - th - 8 : r.bottom + 8) + 'px';
    clearTimeout(_tipTimer);
    _tipTimer = setTimeout(_hideTip, 5000);
    _tipShownAt = Date.now();
  }
  function _hideTip() { clearTimeout(_tipTimer); if (_tipEl) _tipEl.classList.remove('show'); }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function getSelected()     { return Array.from(_selected); }
  function getFocusedEntry() { return _filtered[_focusIdx] || null; }
  function getEntry(path)    { return _entries.find(e => _samePath(e.path, path)) || null; }
  function getCurrentFolder(){ return _currentFolder; }

  // ── Expand / Collapse All ────────────────────────────────────────────────
  async function expandAll() {
    const rows = treeEl().querySelectorAll('.tree-row[data-path]');
    for (const row of rows) {
      const path = row.dataset.path;
      if (!_treeState.expanded.has(path)) {
        await _toggleTreeNode(row, path, 1);
      }
    }
  }

  function collapseAll() {
    _treeState.expanded.clear();
    _renderTreeRoot();
  }

  // ── Left Tree Navigator ───────────────────────────────────────────────────
  // Shows drives + expandable folder tree in #tree-body
  const _treeState = {
    expanded: new Set(),   // expanded folder paths
    drives: [],
  };

  async function _initTree() {
    const el = treeEl();
    if (!el) return;
    el.innerHTML = '<div class="tree-loading" style="padding:12px 8px">Loading drives…</div>';
    try {
      const r = await SFM.getDrives();
      _treeState.drives = (r && r.ok && r.drives && r.drives.length) ? r.drives : [];
      _renderTreeRoot();
    } catch(e) {
      _treeState.drives = [];
      _renderTreeRoot();   // will show Browse button as fallback
    }
  }

  function _renderTreeRoot() {
    const el = treeEl();
    if (!el) return;
    el.innerHTML = '';

    // Always show a Browse button at the top
    const browseBtn = document.createElement('button');
    browseBtn.className = 'btn btn-sm tree-browse-btn';
    browseBtn.innerHTML = Icons.svg('folder-open', 14) + 'Open folder…';
    browseBtn.addEventListener('click', () => browseFolder());
    el.appendChild(browseBtn);

    if (_treeState.drives.length) {
      // Section header: This PC
      const hdr = document.createElement('div');
      hdr.className = 'tree-section-hdr';
      hdr.textContent = 'This PC';
      el.appendChild(hdr);

      _treeState.drives.forEach(d => {
        el.appendChild(_makeDriveNode(d));
      });
    }

    // Section: Current Folder (quick tree)
    if (_currentFolder) {
      const hdr2 = document.createElement('div');
      hdr2.className = 'tree-section-hdr';
      hdr2.textContent = 'Current folder';
      el.appendChild(hdr2);
      const folderName = _currentFolder.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || _currentFolder;
      const node = _makeTreeNode({ path: _currentFolder, name: folderName, is_dir: true }, 0, false);
      el.appendChild(node);
    }
  }

  function _makeDriveNode(drive) {
    const path = drive.path || drive;
    const label = drive.name || drive.label || path;
    const row = document.createElement('div');
    row.className = 'tree-node tree-drive';
    row.dataset.path = path;
    const isExp = _treeState.expanded.has(path);
    row.innerHTML = `<div class="tree-line" style="padding-left:4px">
      <span class="tree-arrow">${_chev(isExp)}</span>
      <span class="tree-icon">${_driveIcon(16)}</span>
      <span class="tree-label" title="${_esc(path)}">${_esc(label)}</span></div>`;
    row.querySelector('.tree-arrow').addEventListener('click', async (e) => {
      e.stopPropagation();
      await _toggleTreeNode(row, path, 1);
    });
    row.querySelector('.tree-label').addEventListener('click', async (e) => {
      e.stopPropagation();
      App.navigate(path);
      if (!_treeState.expanded.has(path)) await _toggleTreeNode(row, path, 1);
    });
    row.addEventListener('dblclick', e => { e.stopPropagation(); App.navigate(path); });

    const children = document.createElement('div');
    children.className = 'tree-children';
    children.dataset.parentPath = path;
    if (isExp) _loadTreeChildren(children, path, 1);
    row.appendChild(children);
    return row;
  }

  function _makeTreeNode(entry, depth, autoExpand) {
    const row = document.createElement('div');
    row.className = 'tree-node';
    row.dataset.path = entry.path;
    const isExp = _treeState.expanded.has(entry.path);
    row.innerHTML = `<div class="tree-line" style="padding-left:${depth * 14 + 4}px">
      <span class="tree-arrow">${_chev(isExp)}</span>
      <span class="tree-icon">${_folderIcon(16)}</span>
      <span class="tree-label" title="${_esc(entry.path)}">${_esc(entry.name)}</span></div>`;

    row.querySelector('.tree-arrow').addEventListener('click', async (e) => {
      e.stopPropagation();
      await _toggleTreeNode(row, entry.path, depth + 1);
    });
    row.querySelector('.tree-label').addEventListener('click', async (e) => {
      e.stopPropagation();
      App.navigate(entry.path);
      if (!_treeState.expanded.has(entry.path)) await _toggleTreeNode(row, entry.path, depth + 1);
    });
    row.addEventListener('dblclick', e => { e.stopPropagation(); App.navigate(entry.path); });

    const children = document.createElement('div');
    children.className = 'tree-children';
    children.dataset.parentPath = entry.path;
    row.appendChild(children);

    // Re-rendering the tree (every folder change) must keep nested folders
    // open: they showed an open arrow with no children before.
    if (autoExpand || isExp) _loadTreeChildren(children, entry.path, depth + 1);
    if (_samePath(entry.path, _currentFolder)) row.classList.add('active');
    return row;
  }

  async function _toggleTreeNode(row, path, childDepth) {
    const children = row.querySelector('.tree-children');
    const arrow = row.querySelector('.tree-arrow');
    if (!children) return;
    if (_treeState.expanded.has(path)) {
      _treeState.expanded.delete(path);
      children.innerHTML = '';
      if (arrow) arrow.innerHTML = _chev(false);
    } else {
      _treeState.expanded.add(path);
      if (arrow) arrow.innerHTML = _chev(true);
      await _loadTreeChildren(children, path, childDepth);
    }
  }

  async function _loadTreeChildren(container, parentPath, depth) {
    container.innerHTML = '<div class="tree-loading">Loading…</div>';
    try {
      const r = await SFM.listFolder(parentPath);
      container.innerHTML = '';
      if (!r || !r.ok) return;
      const dirs = r.entries.filter(e => e.is_dir);
      if (!dirs.length) {
        // No subfolders — leaf node, just leave container empty (folder may still have files)
        return;
      }
      dirs.forEach(e => container.appendChild(_makeTreeNode(e, depth, false)));
    } catch(er) {
      container.innerHTML = '';
    }
  }

  // Update tree highlight when folder changes
  function _updateTreeHighlight(path) {
    const el = treeEl();
    if (!el) return;
    el.querySelectorAll('.tree-node.active').forEach(n => n.classList.remove('active'));
    el.querySelectorAll('.tree-node[data-path]').forEach(n => {
      if (_samePath(n.dataset.path, path)) n.classList.add('active');
    });
  }

  // ── Init ────────────────────────────────────────────────────────────────────────────
  function init() {
    _initKeyboardNav();
    _initThumbZoom();
    _initSortHeader();

    // Make file list areas keyboard-focusable for arrow nav
    ['filelist-list','filelist-thumb'].forEach(id => {
      const el = $(id);
      if (el) el.setAttribute('tabindex', '0');
    });

    // Boot the left tree (async, non-blocking)
    _initTree();
  }

  init();

  return {
    loadFolder, refresh, refreshAndSelect, revealPath, applySearch, setViewMode, editPath, openPath,
    setExpanded, listExpandAll, listCollapseAll, setFolderSizes, folderSizesOn,
    copySelection, cutSelection, pasteSelection,
    newFolder, selectPaths, deleteSelection, softDeletePaths, pushFileOpUndo, pushCreatedUndo: _pushCreatedUndo,
    combineSelected, browseFolder,
    getSelected, getFocusedEntry, getCurrentFolder, getEntry,
    expandAll, collapseAll,
    startRename, isRenaming, setSort,
  };
})();
