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
  async function loadFolder(path) {
    _currentFolder = path;
    App.state.currentFolder = path;
    _selected.clear();
    _focusIdx = -1;
    _entries  = [];
    _filtered = [];

    App.setStatus('Loading…', true);
    _renderBreadcrumb(path);

    try {
      const r = await SFM.listFolder(path);
      if (!r.ok) { App.toast(r.error, 'error'); App.setStatus('Error'); return; }
      _entries  = _sortEntries(r.entries || []);
      _filtered = _entries;
      _applySearchFilter(_searchQuery);
      App.setStatus(`${_filtered.length} items`);
      App.setStatusFolder(path);
      _selectionChanged();
      _updateTreeHighlight(path);
      _renderTreeRoot();
    } catch(e) {
      App.toast('Failed to load folder: ' + e, 'error');
      App.setStatus('Error');
    }
  }

  function refresh() {
    if (_currentFolder) loadFolder(_currentFolder);
  }

  // ── Sorting (click a column header; remembered) ───────────────────────────
  // Default: newest first, so freshly downloaded files are on top.
  const _SORT_STORE = 'oa.filelist.sort';
  let _sort = { key: 'mtime', dir: 'desc', foldersFirst: false };
  try { Object.assign(_sort, JSON.parse(localStorage.getItem(_SORT_STORE) || '{}')); } catch (_) {}
  const _byName = (a, b) => String(a.name).localeCompare(String(b.name), undefined, { numeric: true, sensitivity: 'base' });
  function _sortEntries(list) {
    const k = _sort.key, m = _sort.dir === 'asc' ? 1 : -1;
    return list.slice().sort((a, b) => {
      if (_sort.foldersFirst && !!a.is_dir !== !!b.is_dir) return a.is_dir ? -1 : 1;
      let c = 0;
      if (k === 'name') c = _byName(a, b);
      else if (k === 'size') c = (a.is_dir ? -1 : (a.size || 0)) - (b.is_dir ? -1 : (b.size || 0));
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
    const keep = Array.from(_selected);
    _entries = _sortEntries(_entries);
    _applySearchFilter(_searchQuery);
    keep.forEach(p => _selected.add(p));
    _renderSortHeader();
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
    if (!q) {
      _filtered = _entries;
    } else {
      _filtered = _entries.filter(e =>
        e.name.toLowerCase().includes(q)
      );
    }
    _renderCurrent();
    App.setStatus(`${_filtered.length} items${q ? ' (filtered)' : ''}`);
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
      row.innerHTML = `
        <span class="icon">${iconFor(entry)}</span>
        <span class="name">${_esc(entry.name)}</span>
        <span class="date">${_esc(_fmtShortDate(entry.mtime))}</span>
        <span class="size">${entry.size_str || ''}</span>`;
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
    bc.innerHTML = '';
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
    // Deep paths: show the drive, an ellipsis for the middle, and the last two
    // folders, instead of squeezing every segment down to a couple of letters.
    const collapse = parts.length > 4;
    const hiddenTo = parts.length - 2;   // parts[1 .. hiddenTo-1] are hidden
    // Drive letter gets special treatment
    let cumulative = '';
    parts.forEach((part, i) => {
      cumulative += (i === 0 ? '' : '/') + part;
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

  // ── Selection ─────────────────────────────────────────────────────────────
  function _bindRowEvents(el, idx, entry) {
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
    } else if (e.shiftKey && _shiftAnchor >= 0) {
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
  // ── Type-ahead state ──────────────────────────────────────────────────────
  let _typeBuffer = '';
  let _typeTimer  = null;

  function _typeAheadJump(ch) {
    // Append char and reset 600ms clear timer
    _typeBuffer += ch.toLowerCase();
    clearTimeout(_typeTimer);
    _typeTimer = setTimeout(() => { _typeBuffer = ''; }, 600);

    const buf = _typeBuffer;
    const n   = _filtered.length;
    if (!n) return;

    // Search from (focusIdx + 1) wrapping around, so same letter cycles
    const start = (_focusIdx + 1) % n;
    for (let i = 0; i < n; i++) {
      const idx  = (start + i) % n;
      const name = _filtered[idx].name.toLowerCase();
      if (name.startsWith(buf)) {
        _focusIdx = idx;
        _selected.clear();
        _selected.add(_filtered[idx].path);
        _shiftAnchor = idx;
        _renderCurrent();
        _selectionChanged();
        _scrollFocusedIntoView();
        return;
      }
    }
    // No match — clear buffer and try single char from current position
    if (buf.length > 1) {
      _typeBuffer = ch.toLowerCase();
      _typeAheadJump('');   // recurse with fresh single-char buffer (harmless if buf='')
    }
  }

  function _initKeyboardNav() {
    const handleKey = (e) => {
      clearTimeout(_slowTimer);
      // Only active when file list has focus (or body, no focused input)
      const active = document.activeElement;
      const onList = active && (active.id === 'filelist-list' || active.id === 'filelist-thumb');
      const onBody = !active || active === document.body || active.tagName === 'DIV';
      if (!onList && !onBody) return;
      // The preview pane has focus (text selection, Ctrl+A on a page) — not ours.
      if (active && active.closest && active.closest('#preview-body')) return;
      // Don't hijack search box or rename inputs
      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) return;

      // ── Arrow navigation ──────────────────────────────────────────────────
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        _focusIdx = Math.min(_focusIdx + 1, _filtered.length - 1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        _focusIdx = Math.max(_focusIdx - 1, 0);
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
      } else if ((e.key === 'a' || e.key === 'A') && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        _filtered.forEach(en => _selected.add(en.path));
        _renderCurrent();
        _selectionChanged();
        return;

      // ── Backspace: go up one folder ────────────────────────────────────
      } else if (e.key === 'Backspace' && !e.ctrlKey && !e.shiftKey) {
        if (!_currentFolder) return;
        const parent = _currentFolder.replace(/[\\/]+$/, '').replace(/[\\/][^\\/]*$/, '') || _currentFolder;
        if (parent !== _currentFolder) App.navigate(parent);
        return;

      // ── Printable character: type-ahead jump ───────────────────────────
      } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        _typeAheadJump(e.key);
        return;

      } else { return; }

      // Common post-nav logic (Arrow / Home / End / Page)
      const entry = _filtered[_focusIdx];
      if (!entry) return;
      if (e.shiftKey && _shiftAnchor >= 0) {
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
    _renderCurrent();
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
    if (!_currentFolder) return;
    // Prefer whatever is on the Windows clipboard (e.g. copied in Explorer);
    // it also holds what was copied here, unless another app replaced it.
    try {
      const w = await SFM.getClipboardFiles();
      if (w && w.ok && w.paths && w.paths.length) {
        const same = w.paths.length === _clipboard.paths.length && w.paths.every(p => _clipboard.paths.includes(p));
        if (!same) _clipboard = { mode: w.cut ? 'cut' : 'copy', paths: w.paths };
      }
    } catch (_) {}
    if (!_clipboard.paths.length) { App.toast('Nothing to paste — copy some files first', 'info', 2000); return; }
    if (_clipboard.mode === 'copy') {
      const r = await SFM.copyFiles(_clipboard.paths, _currentFolder);
      if (r.ok) { App.toast('Pasted', 'success'); refresh(); }
      else       { App.toast('Paste failed: ' + r.error, 'error'); }
    } else {
      const r = await SFM.moveFiles(_clipboard.paths, _currentFolder);
      if (r.ok) { App.toast('Moved', 'success'); _clipboard = {mode:null,paths:[]}; refresh(); }
      else       { App.toast('Move failed: ' + r.error, 'error'); }
    }
  }

  // ── New Folder ────────────────────────────────────────────────────────────
  async function newFolder() {
    if (!_currentFolder) { App.toast('Open a folder first', 'warning'); return; }
    const taken = new Set(_entries.map(e => String(e.name).toLowerCase()));
    let suggestion = 'New folder';
    for (let n = 2; taken.has(suggestion.toLowerCase()); n++) suggestion = `New folder (${n})`;
    const name = await Dialogs.ask({
      title: 'New folder',
      subtitle: _currentFolder,
      icon: 'folder-plus',
      label: 'Folder name',
      value: suggestion,
      okLabel: 'Create folder',
      okIcon: 'folder-plus',
      validate: v => {
        if (!v) return 'Enter a folder name.';
        if (/[\\/:*?"<>|]/.test(v)) return 'A folder name can’t contain any of these characters: \\ / : * ? " < > |';
        if (/[. ]$/.test(v)) return 'A folder name can’t end with a dot or a space.';
        if (taken.has(v.toLowerCase())) return 'A file or folder with this name already exists here.';
        return '';
      },
    });
    if (!name) return;
    const r = await SFM.createFolder(_currentFolder, name);
    if (r.ok) {
      App.toast(`Created folder “${name}”`, 'success');
      await refreshAndSelect(r.path || (_currentFolder.replace(/[\\/]+$/, '') + '\\' + name));
    } else { App.toast('Couldn’t create the folder: ' + r.error, 'error'); }
  }

  // ── Delete ────────────────────────────────────────────────────────────────
  async function deleteSelection() {
    const paths = Array.from(_selected);
    if (!paths.length) return;
    const one = paths.length === 1 ? paths[0].split(/[\\/]/).pop() : '';
    const ok = await Dialogs.confirm({
      title: 'Move to review',
      message: one ? `Move “${one}” to the _to_review folder?` : `Move ${paths.length} items to the _to_review folder?`,
      detail: 'Nothing is deleted — the items are moved into a _to_review folder next to them, where you can restore them.',
      okLabel: 'Move to review', okIcon: 'archive', icon: 'archive', tone: 'warning',
    });
    if (!ok) return;
    const r = await SFM.softDelete(paths);
    if (r.ok) {
      const saved = paths.map(p => ({ path: p, moved: r.results?.find(x=>x.path===p)?.moved_to }));
      App.pushUndo({
        label: `Deleted ${paths.length} item(s)`,
        undo: async () => { /* move back not implemented yet */ },
        redo: async () => SFM.softDelete(paths),
      });
      App.toast(`${paths.length} item(s) moved to _to_review/`, 'success');
      refresh();
    } else {
      App.toast('Delete failed: ' + r.error, 'error');
    }
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

    if (autoExpand) _loadTreeChildren(children, entry.path, depth + 1);
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
    const found = el.querySelector(`.tree-node[data-path="${CSS.escape(path)}"]`);
    if (found) found.classList.add('active');
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
    loadFolder, refresh, refreshAndSelect, revealPath, applySearch, setViewMode,
    copySelection, cutSelection, pasteSelection,
    newFolder, deleteSelection,
    combineSelected, browseFolder,
    getSelected, getFocusedEntry, getCurrentFolder,
    expandAll, collapseAll,
    startRename, isRenaming, setSort,
  };
})();
