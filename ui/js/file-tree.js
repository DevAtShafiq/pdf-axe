/**
 * file-tree.js — File list (left panes) + thumbnail gallery
 *
 * Handles:
 *   - Folder listing (list view + thumbnail view)
 *   - Multi-select (click, shift-click, ctrl-click, keyboard)
 *   - Lazy PDF thumbnail loading
 *   - Inline rename
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
  let _renameIdx    = -1;
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
      _entries  = r.entries;
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

  // Refresh, then select + reveal `path` when it is in the current folder
  // (used after an operation creates a new file, e.g. a merged PDF).
  async function refreshAndSelect(path) {
    if (!_currentFolder) return;
    await loadFolder(_currentFolder);
    const norm = p => String(p || '').replace(/[\\/]+/g, '/').replace(/\/+$/, '').toLowerCase();
    const idx = _filtered.findIndex(e => norm(e.path) === norm(path));
    if (idx < 0) return;
    _selected.clear();
    _selected.add(_filtered[idx].path);
    _focusIdx = idx;
    _renderCurrent();
    _selectionChanged();
    _scrollFocusedIntoView();
  }
  const revealPath = refreshAndSelect;

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
    // Drive letter gets special treatment
    let cumulative = '';
    parts.forEach((part, i) => {
      cumulative += (i === 0 ? '' : '/') + part;
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

  function _handleRowClick(e, idx, entry) {
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
    if (entry.is_dir) {
      App.navigate(entry.path);
      return;
    }
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
    }
    const selectedEntries = _filtered.filter(f => _selected.has(f.path));
    ContextMenu.show(e.clientX, e.clientY, selectedEntries);
  }

  function _selectionChanged() {
    const paths = Array.from(_selected);
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
      // Only active when file list has focus (or body, no focused input)
      const active = document.activeElement;
      const onList = active && (active.id === 'filelist-list' || active.id === 'filelist-thumb');
      const onBody = !active || active === document.body || active.tagName === 'DIV';
      if (!onList && !onBody) return;
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
      } else if (e.key === 'a' && (e.ctrlKey || e.metaKey)) {
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
  function copySelection() {
    _clipboard = { mode: 'copy', paths: Array.from(_selected) };
    App.toast(`${_clipboard.paths.length} item(s) copied`, 'info', 1500);
  }
  function cutSelection() {
    _clipboard = { mode: 'cut', paths: Array.from(_selected) };
    App.toast(`${_clipboard.paths.length} item(s) cut`, 'info', 1500);
  }
  async function pasteSelection() {
    if (!_clipboard.paths.length || !_currentFolder) return;
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
    const name = prompt('Folder name:');
    if (!name) return;
    const r = await SFM.createFolder(_currentFolder, name);
    if (r.ok) { App.toast(`Created: ${name}`, 'success'); refresh(); }
    else       { App.toast('Failed: ' + r.error, 'error'); }
  }

  // ── Delete ────────────────────────────────────────────────────────────────
  async function deleteSelection() {
    const paths = Array.from(_selected);
    if (!paths.length) return;
    const confirm = window.confirm
      ? window.confirm(`Move ${paths.length} item(s) to _to_review/?`)
      : true;
    if (!confirm) return;
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

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _esc(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
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
  };
})();
