/**
 * details.js — Right-hand details pane
 *
 * Shows file metadata, rename bar with autocomplete,
 * quick actions, and AI photo section.
 */

const Details = (() => {

  // ── State ─────────────────────────────────────────────────────────────────
  let _path         = null;
  let _entry        = null;
  let _suggestions  = [];
  let _acIdx        = -1;

  // ── DOM helpers ───────────────────────────────────────────────────────────
  const $  = id  => document.getElementById(id);

  // ── Public: show one file ─────────────────────────────────────────────────
  async function showFile(entry) {
    _entry = entry;
    _path  = entry.path;

    _showPanel();
    _renderBadge(entry);
    _renderRows(entry);
    _setRenameValue(entry.name);
    _showConvertAction(entry);
    _showAiSection(entry);
    if (window.PdfTools) PdfTools.onDetails(entry);

    try {
      const info = await SFM.getFileInfo(entry.path);
      if (info.ok && _path === entry.path) _enrichDetails(info);
    } catch(e) {}

    _loadSuggestions(entry);
  }

  // ── Public: multi-select summary ──────────────────────────────────────────
  function showMultiple(entries) {
    _path  = null;
    _entry = null;
    _showPanel();
    $('file-type-badge').textContent = '📁';
    $('file-type-badge').className   = 'type-badge type-folder';

    const totalSize = entries.reduce((a, e) => a + (e.size || 0), 0);
    _clearRows();
    _addRow('Selected', entries.length + ' items');
    _addRow('Total size', _fmtSize(totalSize));

    $('rename-wrap').classList.add('hidden');
    $('quick-actions').classList.add('hidden');
    $('ai-photo-section').classList.add('hidden');
    if (window.PdfTools) PdfTools.onDetails(null, entries);
  }

  // ── Public: clear panel ───────────────────────────────────────────────────
  function clear() {
    _path = null; _entry = null;
    $('details-empty').classList.remove('hidden');
    $('details-content').classList.add('hidden');
  }

  // ── Panel show/hide ───────────────────────────────────────────────────────
  function _showPanel() {
    $('details-empty').classList.add('hidden');
    $('details-content').classList.remove('hidden');
  }

  // ── Type badge ────────────────────────────────────────────────────────────
  function _renderBadge(entry) {
    const badge = $('file-type-badge');
    const ext   = (entry.ext || '').toLowerCase();
    const iconMap = {
      '.pdf': ['PDF','type-pdf'], '.docx':['DOC','type-doc'], '.doc':['DOC','type-doc'],
      '.xlsx':['XLS','type-xls'], '.xls':['XLS','type-xls'], '.csv':['CSV','type-xls'],
      '.pptx':['PPT','type-ppt'], '.ppt':['PPT','type-ppt'],
      '.jpg': ['IMG','type-img'], '.jpeg':['IMG','type-img'], '.png':['IMG','type-img'],
      '.gif': ['GIF','type-img'], '.bmp': ['IMG','type-img'], '.webp':['IMG','type-img'],
      '.zip': ['ZIP','type-zip'], '.7z':['7Z','type-zip'], '.rar':['RAR','type-zip'],
      '.txt': ['TXT','type-txt'], '.md':  ['MD','type-txt'],
    };
    if (entry.is_dir) {
      badge.textContent = '📁'; badge.className = 'type-badge type-folder';
    } else {
      const [label, cls] = iconMap[ext] || [ext.replace('.','').toUpperCase() || 'FILE', 'type-file'];
      badge.textContent = label; badge.className = `type-badge ${cls}`;
    }
  }

  // ── Detail rows ───────────────────────────────────────────────────────────
  function _clearRows() {
    const c = $('detail-rows');
    if (c) c.innerHTML = '';
  }

  function _addRow(label, value) {
    const c = $('detail-rows');
    if (!c) return;
    const row = document.createElement('div');
    row.className = 'detail-row';
    row.innerHTML = `<span class="detail-label">${_esc(label)}</span><span class="detail-value">${_esc(String(value ?? '—'))}</span>`;
    c.appendChild(row);
  }

  function _renderRows(entry) {
    _clearRows();
    _addRow('Name', entry.name);
    _addRow('Type', entry.is_dir ? 'Folder' : (entry.ext || 'File').replace('.','').toUpperCase());
    if (!entry.is_dir) _addRow('Size', _fmtSize(entry.size));
    _addRow('Modified', _fmtDate(entry.modified));
    _addRow('Location', _shortPath(entry.path));
  }

  function _enrichDetails(info) {
    if (info.pages)    _addRow('Pages', info.pages);
    if (info.author)   _addRow('Author', info.author);
    if (info.created)  _addRow('Created', _fmtDate(info.created));
    if (info.title)    _addRow('Title', info.title);
    if (info.width && info.height) _addRow('Dimensions', `${info.width} × ${info.height}`);
    if (info.sheets?.length) _addRow('Sheets', info.sheets.join(', '));
  }

  // ── Rename bar ────────────────────────────────────────────────────────────
  function _setRenameValue(name) {
    const input = $('rename-input');
    if (!input) return;
    input.value = name;
    $('rename-wrap').classList.remove('hidden');
    $('quick-actions').classList.remove('hidden');
  }

  async function _loadSuggestions(entry) {
    try {
      const r = await SFM.filterSuggestions('', entry.ext || '');
      _suggestions = r.ok ? (r.suggestions || []) : [];
    } catch(e) { _suggestions = []; }
  }

  function _initRenameBar() {
    const input = $('rename-input');
    const ac    = $('rename-autocomplete');
    if (!input || !ac) return;

    input.addEventListener('input', async () => {
      const q = input.value.trim();
      _acIdx  = -1;
      if (!q) { _hideAc(); return; }
      try {
        const ext = _entry?.ext || '';
        const r   = await SFM.filterSuggestions(q, ext);
        _showAc(r.ok ? (r.suggestions || []) : [], input);
      } catch(e) { _hideAc(); }
    });

    input.addEventListener('keydown', async e => {
      const items = ac.querySelectorAll('.ac-item');
      if (e.key === 'ArrowDown') { e.preventDefault(); _acIdx = Math.min(_acIdx + 1, items.length - 1); _acHighlight(items); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); _acIdx = Math.max(_acIdx - 1, -1); _acHighlight(items); }
      else if (e.key === 'Enter' && e.shiftKey) {
        // Shift+Enter: apply the best template suggestion for this file, then rename.
        e.preventDefault();
        let chosen = null;
        if (_acIdx >= 0 && items[_acIdx]) chosen = items[_acIdx].dataset.val;   // highlighted suggestion
        else if (items.length)            chosen = items[0].dataset.val;        // top listed suggestion
        else {
          // No dropdown open — fetch the best matching template for the current name/ext.
          try {
            const r = await SFM.filterSuggestions(input.value.trim(), _entry?.ext || '');
            const sugg = (r.ok && r.suggestions) ? r.suggestions : [];
            if (sugg.length) chosen = sugg[0];
          } catch (_) {}
        }
        if (chosen) input.value = chosen;      // fall back to the typed value when nothing matched
        _hideAc();
        _commitRename();
      }
      else if (e.key === 'Enter') {
        // Plain Enter only accepts a highlighted autocomplete suggestion.
        if (_acIdx >= 0 && items[_acIdx]) { e.preventDefault(); input.value = items[_acIdx].dataset.val; _hideAc(); }
      }
      else if (e.key === 'Escape') { _hideAc(); input.value = _entry?.name || ''; }
    });

    input.addEventListener('blur', () => { setTimeout(_hideAc, 120); });

    const btn = $('rename-btn');
    if (btn) btn.addEventListener('click', _commitRename);
  }

  function _showAc(items, input) {
    const ac = $('rename-autocomplete');
    if (!ac) return;
    ac.innerHTML = '';
    items.slice(0, 12).forEach(val => {
      const d = document.createElement('div');
      d.className = 'ac-item'; d.dataset.val = val;
      d.textContent = val;
      d.addEventListener('mousedown', e => { e.preventDefault(); input.value = val; _hideAc(); });
      ac.appendChild(d);
    });
    ac.classList.toggle('hidden', items.length === 0);
  }

  function _hideAc() {
    const ac = $('rename-autocomplete');
    if (ac) ac.classList.add('hidden');
    _acIdx = -1;
  }

  function _acHighlight(items) {
    items.forEach((el, i) => el.classList.toggle('active', i === _acIdx));
    if (_acIdx >= 0 && items[_acIdx]) $('rename-input').value = items[_acIdx].dataset.val;
  }

  async function _commitRename() {
    const input = $('rename-input');
    if (!input || !_path) return;
    const newName = input.value.trim();
    if (!newName || newName === _entry?.name) return;

    const r = await SFM.renameFile(_path, newName);
    if (r.ok) {
      App.pushUndo({
        label: `Rename → ${newName}`,
        undo: async () => { await SFM.renameFile(r.new_path, _entry.name); FileTree.refresh(); },
        redo: async () => { await SFM.renameFile(_path, newName); FileTree.refresh(); }
      });
      _path = r.new_path;
      if (_entry) _entry = { ..._entry, name: newName, path: r.new_path };
      App.toast(`Renamed to ${newName}`, 'success');
      FileTree.refresh();
    } else {
      App.toast('Rename failed: ' + r.error, 'error');
      input.value = _entry?.name || '';
    }
  }

  // ── Quick actions ─────────────────────────────────────────────────────────
  function _initQuickActions() {
    const wire = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };

    wire('qa-open',    () => { if (_path) SFM.openNative(_path); });
    wire('qa-opendir', () => { if (_path) SFM.openFolder(_path.replace(/[\\/][^\\/]+$/, '')); });
    wire('qa-copy-path', () => {
      if (!_path) return;
      navigator.clipboard.writeText(_path).catch(() => SFM.setClipboard(_path));
      App.toast('Path copied', 'success');
    });
    wire('qa-delete',  async () => {
      if (!_path) return;
      const n = _entry?.name || _path;
      if (!confirm(`Move "${n}" to _to_review/?`)) return;
      const r = await SFM.softDelete([_path]);
      if (r.ok) { App.toast('Moved to _to_review/', 'success'); FileTree.refresh(); clear(); }
      else       { App.toast('Failed: ' + r.error, 'error'); }
    });
    wire('qa-convert-pdf', async () => {
      if (!_path) return;
      App.setStatus('Converting to PDF…', true);
      const r = await SFM.convertToPdf(_path);
      App.setStatus('Ready');
      if (r.ok) { App.toast('Converted: ' + String(r.out_path || '').split(/[\\/]/).pop(), 'success'); FileTree.refresh(); }
      else       { App.toast('Failed: ' + r.error, 'error'); }
    });
  }

  // "Convert to PDF" is image → PDF only.
  const _PDF_CONVERTIBLE_EXTS = ['.jpg','.jpeg','.png','.bmp','.webp','.gif','.tif','.tiff'];
  function _showConvertAction(entry) {
    const btn = $('qa-convert-pdf');
    if (!btn) return;
    const isImg = !entry.is_dir && _PDF_CONVERTIBLE_EXTS.includes((entry.ext||'').toLowerCase());
    btn.classList.toggle('hidden', !isImg);
  }

  // ── AI Photo section ──────────────────────────────────────────────────────
  function _showAiSection(entry) {
    const imgExts = ['.jpg','.jpeg','.png','.bmp','.webp'];
    const isImg   = imgExts.includes((entry.ext||'').toLowerCase());
    $('ai-photo-section').classList.toggle('hidden', !isImg);
  }

  // Shared AI photo runner (also used by the context menu).
  // Resolves with the ai_photo_result payload: {ok, out, action} | {ok:false, error}.
  let _aiBusy = false;
  const _AI_LABELS = {
    wear_suit: 'Wear Suit & Tie',
  };

  function _setAiBusy(on, msg) {
    _aiBusy = on;
    document.querySelectorAll('#ai-photo-section [data-ai-action]').forEach(b => { b.disabled = on; });
    const st = $('ai-photo-status');
    if (st) st.textContent = msg || '';
  }

  async function runAiPhoto(path, action) {
    if (!path) return { ok: false, error: 'No image selected' };
    if (_aiBusy) { App.toast('An AI photo edit is already running', 'warning'); return { ok: false, error: 'busy' }; }
    const label = _AI_LABELS[action] || action;
    try {
      const k = await SFM.getApiKey();
      if (!k || !k.has_key) {
        App.toast('Set your OpenAI API key in Settings first', 'error', 5000);
        return { ok: false, error: 'No API key' };
      }
    } catch (e) { /* let the backend report key problems */ }

    _setAiBusy(true, `Running ${label}… (may take up to a minute)`);
    App.setStatus(`AI: ${label}…`, true);
    App.toast(`AI ${label} started…`, 'info');

    let unsub = null;
    const result = await new Promise(async resolve => {
      unsub = SFM.on('ai_photo_result', r => resolve(r || { ok: false, error: 'No result' }));
      try {
        const r = await SFM.runAiPhoto(path, action, {});
        if (!r || !r.ok) resolve({ ok: false, error: (r && r.error) || 'Could not start AI edit' });
      } catch (e) { resolve({ ok: false, error: String(e) }); }
    });
    if (unsub) unsub();

    _setAiBusy(false);
    App.setStatus('Ready');
    if (result.ok) {
      const name = String(result.out || '').split(/[\\/]/).pop();
      App.toast(`${label} saved → ${name}`, 'success', 5000);
      try { FileTree.refresh(); } catch (_) {}
      try {
        const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '';
        Preview.previewFile(result.out, ext);
      } catch (_) {}
    } else {
      App.toast(`AI ${label} failed: ${result.error}`, 'error', 6000);
    }
    return result;
  }

  function _initAiPhoto() {
    document.querySelectorAll('#ai-photo-section [data-ai-action]').forEach(btn => {
      btn.addEventListener('click', () => { if (_path) runAiPhoto(_path, btn.dataset.aiAction); });
    });
    const crop = $('ai-crop');
    if (crop) crop.addEventListener('click', () => { if (_path) Dialogs.openCropImage(_path); });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _fmtSize(bytes) {
    if (!bytes) return '—';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(2) + ' GB';
  }

  function _fmtDate(ts) {
    if (!ts) return '—';
    try {
      const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
      return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    } catch(e) { return String(ts); }
  }

  function _shortPath(p) {
    if (!p) return '—';
    const parts = p.replace(/\\/g,'/').split('/');
    if (parts.length <= 3) return p;
    return '…/' + parts.slice(-3, -1).join('/');
  }

  function _esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    _initRenameBar();
    _initQuickActions();
    _initAiPhoto();
    clear();
  }
  init();

  // ── Public: beginRename — F2 shortcut ────────────────────────────────────
  function beginRename() {
    const input = $('rename-input');
    if (!input || !_path) return;
    input.focus();
    const name = input.value;
    const dot  = name.lastIndexOf('.');
    input.setSelectionRange(0, dot > 0 ? dot : name.length);
  }

  return { showFile, showMultiple, clear, beginRename, runAiPhoto };
})();
