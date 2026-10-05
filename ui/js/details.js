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

    if (_rt) _rt.reset();
  }

  // ── Public: multi-select summary ──────────────────────────────────────────
  function showMultiple(entries) {
    _path  = null;
    _entry = null;
    _showPanel();
    $('file-type-badge').innerHTML = Icons.svg('files', 14) + '<span>' + entries.length + ' ITEMS</span>';
    $('file-type-badge').className = 'type-badge type-file';

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
      badge.innerHTML = Icons.svg('folder', 14) + '<span>FOLDER</span>';
      badge.className = 'type-badge type-folder';
    } else {
      const [label, cls] = iconMap[ext] || [ext.replace('.','').toUpperCase() || 'FILE', 'type-file'];
      badge.innerHTML = Icons.svg(Icons.fileType(ext).icon, 14) + '<span>' + _esc(label) + '</span>';
      badge.className = `type-badge ${cls}`;
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
    _addRow('Modified', _fmtDate(entry.mtime || entry.modified));
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

  // Template suggestions dropdown (rename-templates.js) — attached in init().
  let _rt = null;

  function _initRenameBar() {
    const input = $('rename-input');
    const ac    = $('rename-autocomplete');
    if (!input || !ac) return;
    if (typeof RenameTemplates !== 'undefined') {
      _rt = RenameTemplates.attach(input, ac, {
        getEntry: () => _entry,
        rename:   opts => _commitRename(opts),
      });
    } else {
      input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); _commitRename(); } });
    }
    const btn = $('rename-btn');
    if (btn) btn.addEventListener('click', () => _commitRename());
  }

  // opts: {}                → rename to the typed name as-is
  //       { stem }          → rename to <stem><original ext>, auto " (2)" on clash
  //       { stem, save }    → same, and save the name as a custom template
  async function _commitRename(opts = {}) {
    const input = $('rename-input');
    if (!input || !_path) return;
    const oldPath = _path, oldName = _entry?.name || '';
    let r;
    if (opts.stem != null) {
      if (!String(opts.stem).trim()) return;
      r = await SFM.renameWithTemplate(oldPath, opts.stem, !!opts.save);
    } else {
      const newName = (opts.typed ?? input.value).trim();
      if (!newName || newName === oldName) return;
      r = await SFM.renameFile(oldPath, newName);
      if (r.ok) r.new_name = newName;
    }
    if (r.ok) {
      const newName = r.new_name || (r.new_path || '').split(/[\\/]/).pop();
      const newPath = r.new_path;
      App.pushUndo({
        label: `Rename → ${newName}`,
        undo: async () => { await SFM.renameFile(newPath, oldName); FileTree.refresh(); },
        redo: async () => { await SFM.renameFile(oldPath, newName); FileTree.refresh(); }
      });
      _path = newPath;
      if (_entry) _entry = { ..._entry, name: newName, path: newPath };
      input.value = newName;
      if (_rt) _rt.reset();
      App.toast(opts.save
        ? (r.saved ? `Renamed to ${newName} · saved as template` : `Renamed to ${newName} (template not saved)`)
        : `Renamed to ${newName}`, 'success');
      FileTree.refresh();
    } else {
      App.toast('Rename failed: ' + r.error, 'error');
      input.value = oldName;
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
    // Conversion / compression (convert-tools.js)
    wire('qa-convert-pdf', () => { if (_path) ConvertTools.quickImageToPdf(_path); });
    wire('qa-convert-img', () => { if (_path) ConvertTools.openConvertImage([_path]); });
    wire('qa-pdf-images',  () => { if (_path) ConvertTools.openPdfToImages(_path); });
    wire('qa-compress',    () => {
      if (!_path) return;
      if (ConvertTools.isPdf(_path)) ConvertTools.openCompressPdf([_path]);
      else ConvertTools.openCompressImages([_path]);
    });
  }

  // Image → PDF / format / compress for images; → images / compress for PDFs.
  function _showConvertAction(entry) {
    const file  = entry && !entry.is_dir;
    const isImg = file && ConvertTools.isImage(entry.path);
    const isPdf = file && ConvertTools.isPdf(entry.path);
    const show = (id, on) => { const b = $(id); if (b) b.classList.toggle('hidden', !on); };
    show('qa-convert-pdf', isImg);
    show('qa-convert-img', isImg);
    show('qa-pdf-images',  isPdf);
    show('qa-compress',    isImg || isPdf);
  }

  // ── AI Photo section ──────────────────────────────────────────────────────
  function _showAiSection(entry) {
    const imgExts = ['.jpg','.jpeg','.png','.bmp','.webp'];
    const isImg   = imgExts.includes((entry.ext||'').toLowerCase());
    $('ai-photo-section').classList.toggle('hidden', !isImg);
    if (isImg && typeof AiPhoto !== 'undefined') AiPhoto.onShowFile(entry);
  }

  // Shared AI photo runner (also used by the context menu). The flow — plan
  // gate, API-key check, options, busy state, result reveal and before/after —
  // lives in photo-tools.js (AiPhoto). Resolves with the ai_photo_result payload.
  function runAiPhoto(path, action) {
    return AiPhoto.run(path, action || 'wear_suit');
  }

  function _initAiPhoto() {
    document.querySelectorAll('#ai-photo-section [data-ai-action]').forEach(btn => {
      btn.addEventListener('click', () => { if (_path) runAiPhoto(_path, btn.dataset.aiAction); });
    });
    const crop = $('ai-crop');
    if (crop) crop.addEventListener('click', () => { if (_path) Cropper.open(_path); });
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
