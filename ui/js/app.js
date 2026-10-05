/**
 * app.js — Core application: state, routing, toasts, keyboard shortcuts,
 *           undo/redo registry, pane resizing, theme toggle.
 */

// ── Global App State ─────────────────────────────────────────────────────────
const App = (() => {

  const state = {
    currentFolder:   '',
    selectedPaths:   [],
    focusedPath:     null,
    viewMode:        'list',   // 'list' | 'thumb'
    historyBack:     [],
    historyFwd:      [],
    recentFolders:   [],   // last 10 visited
    undoStack:       [],
    redoStack:       [],
    previewPath:     null,
    pdfPage:         0,
    pdfPageCount:    0,
    thumbZoom:       100,      // %
    searchQuery:     '',
    currentPanel:    'workspace',
  };

  // ── Panel Switching ───────────────────────────────────────────────────────
  function switchPanel(name) {
    state.currentPanel = name;
    document.querySelectorAll('.panel-view').forEach(el => {
      el.style.display = 'none';
      el.classList.add('hidden');
    });
    const target = document.getElementById(`panel-${name}`);
    // .hidden is display:none !important, so it must come off as well
    if (target) { target.classList.remove('hidden'); target.style.display = 'flex'; }

    document.querySelectorAll('.sidebar-btn[data-panel]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.panel === name);
    });
  }

  // ── Toast Notifications ───────────────────────────────────────────────────
  function toast(message, type = 'info', duration = 3500) {
    const icons = { info: 'info', success: 'check-circle', error: 'x-circle', warning: 'alert-triangle' };
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.innerHTML = `<span class="icon">${Icons.svg(icons[type] || 'info', 16)}</span><span>${message}</span>`;
    container.appendChild(el);
    setTimeout(() => {
      el.classList.add('fade-out');
      setTimeout(() => el.remove(), 300);
    }, duration);
  }

  // ── Status Bar ────────────────────────────────────────────────────────────
  function setStatus(text, busy = false) {
    document.getElementById('status-text').textContent = text;
    const dot = document.getElementById('status-dot');
    dot.className = 'dot' + (busy ? ' busy' : '');
  }

  function setStatusFolder(folder) {
    const el = document.getElementById('status-folder');
    const nameEl = document.getElementById('status-folder-name');
    if (folder) {
      nameEl.textContent = folder.split(/[\\/]/).pop() || folder;
      nameEl.title = folder;
      el.style.display = 'flex';
      document.getElementById('status-sep-sel').style.display = '';
    } else {
      el.style.display = 'none';
    }
  }

  function setStatusSelection(count, totalFiles) {
    const el = document.getElementById('status-sel');
    const textEl = document.getElementById('status-sel-text');
    if (count > 0) {
      textEl.textContent = `${count} selected`;
      el.style.display = 'flex';
    } else {
      el.style.display = 'none';
    }
  }

  // ── Undo / Redo ───────────────────────────────────────────────────────────
  function pushUndo(action) {
    // action = { type, label, undo: async fn, redo: async fn }
    state.undoStack.push(action);
    state.redoStack = [];
    _refreshUndoButtons();
  }

  async function undo() {
    const action = state.undoStack.pop();
    if (!action) return;
    try {
      await action.undo();
      state.redoStack.push(action);
      toast(`Undone: ${action.label}`, 'info', 2000);
    } catch(e) {
      toast(`Undo failed: ${e.message}`, 'error');
      state.undoStack.push(action); // restore
    }
    _refreshUndoButtons();
  }

  async function redo() {
    const action = state.redoStack.pop();
    if (!action) return;
    try {
      await action.redo();
      state.undoStack.push(action);
      toast(`Redone: ${action.label}`, 'info', 2000);
    } catch(e) {
      toast(`Redo failed: ${e.message}`, 'error');
      state.redoStack.push(action);
    }
    _refreshUndoButtons();
  }

  function _refreshUndoButtons() {
    const canUndo = state.undoStack.length > 0;
    const canRedo = state.redoStack.length > 0;
    ['btn-undo', 'btn-undo-rename'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.disabled = !canUndo;
    });
    ['btn-redo', 'btn-redo-rename'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.disabled = !canRedo;
    });
  }

  // ── Navigation History ────────────────────────────────────────────────────
  function navigate(folder, pushHistory = true) {
    if (pushHistory && state.currentFolder) {
      state.historyBack.push(state.currentFolder);
      state.historyFwd = [];
      _addRecent(folder);
    }
    state.currentFolder = folder;
    _updateNavButtons();
    setStatusFolder(folder);
    FileTree.loadFolder(folder);
  }

  function _addRecent(folder) {
    if (!folder) return;
    state.recentFolders = [folder, ...state.recentFolders.filter(f => f !== folder)].slice(0, 10);
  }

  function _showRecentDropdown(anchorEl) {
    document.getElementById('recent-dropdown')?.remove();
    if (!state.recentFolders.length) { toast('No recent folders yet', 'info'); return; }
    const dd = document.createElement('div');
    dd.id = 'recent-dropdown';
    dd.style.cssText = 'position:fixed;background:var(--bg-surface);border:1px solid var(--border);border-radius:8px;box-shadow:0 4px 20px #0006;z-index:9999;min-width:300px;max-width:500px;padding:4px 0;max-height:320px;overflow-y:auto;font-size:13px;';
    const rect = anchorEl.getBoundingClientRect();
    dd.style.left = rect.left + 'px';
    dd.style.top  = (rect.bottom + 4) + 'px';
    state.recentFolders.forEach(f => {
      const row = document.createElement('div');
      row.style.cssText = 'padding:6px 14px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
      row.title = f;
      row.innerHTML = Icons.svg('folder', 14, 'text-muted') + '<span class="truncate">'
        + _escHtml(f.split(/[\\/]/).slice(-2).join('/')) + '</span>';
      row.addEventListener('mouseenter', () => row.style.background = 'var(--bg-hover)');
      row.addEventListener('mouseleave', () => row.style.background = '');
      row.addEventListener('click', () => { dd.remove(); navigate(f); });
      dd.appendChild(row);
    });
    document.body.appendChild(dd);
    const close = e => { if (!dd.contains(e.target) && e.target !== anchorEl) { dd.remove(); document.removeEventListener('mousedown', close); } };
    setTimeout(() => document.addEventListener('mousedown', close), 50);
  }

  function navBack() {
    const prev = state.historyBack.pop();
    if (!prev) return;
    state.historyFwd.push(state.currentFolder);
    navigate(prev, false);
  }

  function navForward() {
    const next = state.historyFwd.pop();
    if (!next) return;
    state.historyBack.push(state.currentFolder);
    navigate(next, false);
  }

  function navUp() {
    if (!state.currentFolder) return;
    const parent = state.currentFolder.replace(/[\\/][^\\/]+$/, '') || state.currentFolder;
    if (parent !== state.currentFolder) navigate(parent);
  }

  function _updateNavButtons() {
    document.getElementById('btn-nav-back').disabled    = state.historyBack.length === 0;
    document.getElementById('btn-nav-forward').disabled = state.historyFwd.length === 0;
  }

  function _escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Theme ─────────────────────────────────────────────────────────────────
  // Dark is the default (:root); light sets data-theme="light" on <html>.
  function applyTheme(theme) {
    const light = theme === 'light';
    const html = document.documentElement;
    if (light) html.setAttribute('data-theme', 'light'); else html.removeAttribute('data-theme');
    window._sfmTheme = light ? 'light' : 'dark';
    const tb = document.getElementById('btn-theme');
    if (tb) {
      tb.innerHTML = Icons.svg(light ? 'sun' : 'moon', 16);
      tb.title = light ? 'Switch to dark theme' : 'Switch to light theme';
    }
  }

  // ── Pane Resizing ─────────────────────────────────────────────────────────
  function _initPaneResizers() {
    const resizers = [
      { id: 'resizer-tree-filelist',    leftId: 'pane-tree',     rightId: 'pane-filelist' },
      { id: 'resizer-filelist-preview', leftId: 'pane-filelist', rightId: 'pane-preview' },
      { id: 'resizer-preview-details',  leftId: 'pane-preview',  rightId: 'pane-details' },
    ];

    resizers.forEach(({ id, leftId, rightId }) => {
      const resizer = document.getElementById(id);
      if (!resizer) return;
      let startX, startLeftW, startRightW;

      resizer.addEventListener('mousedown', e => {
        e.preventDefault();
        startX      = e.clientX;
        startLeftW  = document.getElementById(leftId).offsetWidth;
        startRightW = document.getElementById(rightId).offsetWidth;
        resizer.classList.add('dragging');
        document.body.classList.add('resizing-h');

        function onMove(e) {
          const dx = e.clientX - startX;
          const leftEl  = document.getElementById(leftId);
          const rightEl = document.getElementById(rightId);
          const newLeft  = Math.max(120, startLeftW  + dx);
          const newRight = Math.max(120, startRightW - dx);
          leftEl.style.width  = newLeft  + 'px';
          rightEl.style.width = newRight + 'px';
          leftEl.style.flex   = 'none';
          rightEl.style.flex  = 'none';
          // preview pane keeps flex:1 unless explicitly resized
        }
        function onUp() {
          resizer.classList.remove('dragging');
          document.body.classList.remove('resizing-h');
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup',   onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup',   onUp);
      });
    });
  }

  // ── Global Keyboard Shortcuts ─────────────────────────────────────────────
  function _initKeyboard() {
    document.addEventListener('keydown', e => {
      // Don't hijack keys while the user is typing in a text field —
      // otherwise Ctrl+V pastes files instead of text, Delete soft-deletes
      // the selected file mid-rename, Ctrl+Z runs file-op undo, etc.
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;

      const ctrl  = e.ctrlKey || e.metaKey;
      const shift = e.shiftKey;
      const alt   = e.altKey;
      const key   = e.key;

      // Prevent default for our shortcuts
      if (ctrl && key === 'z' && !shift) { e.preventDefault(); undo(); return; }
      if (ctrl && (key === 'y' || (key === 'z' && shift))) { e.preventDefault(); redo(); return; }
      if (ctrl && key === 'c' && !shift) { FileTree.copySelection(); return; }
      if (ctrl && key === 'x')           { FileTree.cutSelection();  return; }
      if (ctrl && key === 'v')           { FileTree.pasteSelection(); return; }
      if (ctrl && shift && key === 'N')  { e.preventDefault(); FileTree.newFolder(); return; }
      if (ctrl && key === 'Enter')       { e.preventDefault(); FileTree.combineSelected(); return; }
      if (key === 'F5')                  { e.preventDefault(); FileTree.refresh(); return; }
      if (key === 'F2')                  { e.preventDefault(); Details.beginRename(); return; }
      if (key === 'Delete')              { FileTree.deleteSelection(); return; }
      if (alt && key === 'ArrowLeft')    { e.preventDefault(); navBack(); return; }
      if (alt && key === 'ArrowRight')   { e.preventDefault(); navForward(); return; }
      if (alt && key === 'ArrowUp')      { e.preventDefault(); navUp(); return; }
      if (ctrl && key === '+' || ctrl && key === '=') { Preview.zoomIn();    return; }
      if (ctrl && key === '-')                         { Preview.zoomOut();   return; }
      if (ctrl && key === '0')           { e.preventDefault(); Preview.zoomReset(); return; }
      if (key === 'Escape')              { ContextMenu.hide(); return; }
    });
  }

  // ── SFM Events from Python ────────────────────────────────────────────────
  function _initEventHandlers() {
    // Long-op log lines
    // Non-smart-split log events → console only (dialogs handle their own)
    ['ocr_rename_log','split_ocr_log'].forEach(ev => {
      SFM.on(ev, ({ log }) => console.log(`[${ev}]`, log));
    });
    // smart_rename_log is handled inside Dialogs.openSmartSplitProgress — no global handler needed

    // Long-op completions (smart_rename_done handled inside the progress dialog)
    SFM.on('ocr_rename_done', r => {
      setStatus('Ready');
      if (r.ok) { toast('OCR rename complete', 'success'); FileTree.refresh(); }
      else       { toast('OCR rename failed: ' + r.error, 'error', 5000); }
    });
    SFM.on('compress_done', r => {
      setStatus('Ready');
      if (r.ok) toast(`Compressed — saved ${r.saved_str}`, 'success');
      else      toast('Compress failed: ' + r.error, 'error');
      FileTree.refresh();
    });
    SFM.on('convert_pdf_done', r => {
      setStatus('Ready');
      if (r.ok) { toast('Conversion complete', 'success'); FileTree.refresh(); }
      else       { toast('Conversion failed', 'error'); }
    });
    SFM.on('qr_result',  r => {
      setStatus('Ready');
      if (r.ok) {
        const decoded = r.text || r.url || '';
        const pageInfo = r.page ? ` (page ${r.page})` : '';
        // Put decoded text into rename bar so user can use it immediately
        const ri = document.getElementById('rename-input');
        if (ri && decoded) ri.value = decoded;
        // If it looks like a URL, offer to open it
        if (decoded.match(/^https?:\/\//i)) {
          toast(`QR decoded${pageInfo}: ${decoded.slice(0, 80)}`, 'success', 8000);
        } else {
          toast(`QR decoded${pageInfo}: ${decoded.slice(0, 80)}`, 'success', 6000);
        }
      } else {
        toast('QR: ' + (r.error || 'No QR code found'), 'warning', 4000);
      }
    });
    SFM.on('ai_photo_done', r => {
      setStatus('Ready');
      if (r.ok) { toast('AI edit complete', 'success'); FileTree.refresh(); }
      else      { toast('AI edit failed: ' + r.error, 'error', 5000); }
    });
  }

  // ── Toolbar Wiring ────────────────────────────────────────────────────────
  function _initToolbar() {
    const wire = (id, fn) => { const el = document.getElementById(id); if(el) el.addEventListener('click', fn); };

    wire('btn-nav-back',    navBack);
    wire('btn-nav-forward', navForward);
    wire('btn-nav-up',      navUp);
    const _openFolderBtn = document.getElementById('btn-open-folder');
    if (_openFolderBtn) {
      _openFolderBtn.addEventListener('click', () => FileTree.browseFolder());
      _openFolderBtn.addEventListener('contextmenu', e => { e.preventDefault(); _showRecentDropdown(_openFolderBtn); });
      _openFolderBtn.title = 'Open folder (right-click for recent)';
    }
    wire('btn-refresh',     () => FileTree.refresh());
    wire('btn-new-folder',  () => FileTree.newFolder());
    wire('btn-undo',        undo);
    wire('btn-redo',        redo);
    wire('btn-view-list',   () => FileTree.setViewMode('list'));
    wire('btn-view-thumb',  () => FileTree.setViewMode('thumb'));
    wire('btn-combine-pdf', () => FileTree.combineSelected());
    wire('btn-smart-split', () => {
      const p = state.focusedPath || state.selectedPaths[0];
      if (!p) { toast('Select a PDF first', 'warning'); return; }
      Dialogs.openSmartSplitProgress(p);
    });
    wire('btn-compress-pdf', () => {
      const p = state.focusedPath || state.selectedPaths[0];
      if (!p) { toast('Select a PDF or image first', 'warning'); return; }
      // Images selected → image compression; PDFs → PDF compression (batch).
      const all  = (state.selectedPaths || []).length ? state.selectedPaths : [p];
      const imgs = all.filter(ConvertTools.isImage);
      const pdfs = all.filter(ConvertTools.isPdf);
      if (ConvertTools.isImage(p) || (!pdfs.length && imgs.length)) { Dialogs.openCompressImages(imgs.length ? imgs : [p]); return; }
      if (pdfs.length) { Dialogs.openCompressPdf(pdfs); return; }
      toast('Compress works on PDFs and images', 'warning');
    });
    wire('btn-convert', () => {
      const p = state.focusedPath || state.selectedPaths[0];
      const all  = (state.selectedPaths || []).length ? state.selectedPaths : (p ? [p] : []);
      const imgs = all.filter(ConvertTools.isImage);
      const pdfs = all.filter(ConvertTools.isPdf);
      if (imgs.length && (ConvertTools.isImage(p) || !pdfs.length)) { ConvertTools.openImagesToPdf(imgs); return; }
      if (pdfs.length) { ConvertTools.openPdfToImages(ConvertTools.isPdf(p) ? p : pdfs[0]); return; }
      toast('Select image(s) to make a PDF, or a PDF to save its pages as images', 'warning', 4500);
    });
    // QR: native pick overlay — click a code anywhere on screen (qr.js / qr_pick.py)
    wire('btn-qr',    () => QrScan.pick());
    wire('btn-more',  () => Dialogs.openMoreMenu());
    wire('btn-theme', () => {
      const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      applyTheme(next);
      SFM.saveSettings({ theme: next }).catch(() => {});
    });
    // Restore saved theme from settings
    SFM.getSettings().then(r => {
      if (r.ok && r.settings && r.settings.theme === 'light') applyTheme('light');
    }).catch(() => {});

    // Brand mark in the sidebar
    const logo = document.getElementById('sidebar-logo');
    if (logo && !logo.firstChild) logo.innerHTML = Icons.logo(32);

    // Search
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
      let searchTimer;
      searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
          state.searchQuery = searchInput.value.trim();
          FileTree.applySearch(state.searchQuery);
        }, 250);
      });
      searchInput.addEventListener('keydown', e => {
        if (e.key === 'Escape') { searchInput.value = ''; state.searchQuery = ''; FileTree.applySearch(''); }
      });
    }

    // View mode buttons
    ['list','thumb'].forEach(mode => {
      const btn = document.getElementById(`btn-view-${mode}`);
      const flBtn = document.getElementById(`fl-view-${mode}`);
      if (btn)   btn.addEventListener('click', () => FileTree.setViewMode(mode));
      if (flBtn) flBtn.addEventListener('click', () => FileTree.setViewMode(mode));
    });

    // Sidebar
    document.querySelectorAll('.sidebar-btn[data-panel]').forEach(btn => {
      btn.addEventListener('click', () => switchPanel(btn.dataset.panel));
    });

    // Alert strip
    const dismiss = document.getElementById('alert-dismiss');
    if (dismiss) dismiss.addEventListener('click', () => {
      document.getElementById('alert-strip').style.display = 'none';
    });
  }

  // ── Boot ──────────────────────────────────────────────────────────────────
  // These initialisers were previously defined but never invoked, which left
  // keyboard shortcuts, toolbar buttons, Python→JS event handlers,
  // pane resizers and status pollers all dead.
  function _boot() {
    const safe = (fn, name) => { try { fn(); } catch (e) { console.error('[App boot]', name, e); } };
    safe(_initToolbar,       '_initToolbar');
    safe(_initKeyboard,      '_initKeyboard');
    safe(_initEventHandlers, '_initEventHandlers');
    safe(_initPaneResizers,  '_initPaneResizers');
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
  } else {
    _boot();
  }

  // ── Exports ───────────────────────────────────────────────────────────────
  return {
    state,
    navigate, toast, setStatus, setStatusFolder,
    setStatusSelection, pushUndo, undo, redo,
    switchPanel, navBack, navForward, navUp, applyTheme,
  };
})();
