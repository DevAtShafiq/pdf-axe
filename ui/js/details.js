/**
 * details.js — Right-hand details pane
 *
 * Shows file metadata, quick actions, and the AI photo section.
 * (Rename is inline in the file list — see FileTree.startRename.)
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
    $('quick-actions').classList.remove('hidden');
    _showConvertAction(entry);
    _showAiSection(entry);
    // (const globals are not window properties — test with typeof)
    if (typeof PdfTools !== 'undefined') PdfTools.onDetails(entry);
    if (typeof Archive !== 'undefined') Archive.onDetails(entry);

    try {
      const info = await SFM.getFileInfo(entry.path);
      if (info.ok && _path === entry.path) _enrichDetails(info);
    } catch(e) {}
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

    $('quick-actions').classList.add('hidden');
    $('ai-photo-section').classList.add('hidden');
    if (typeof PdfTools !== 'undefined') PdfTools.onDetails(null, entries);
    if (typeof Archive !== 'undefined') Archive.onDetails(null, entries);
  }

  // ── Public: clear panel ───────────────────────────────────────────────────
  function clear() {
    _path = null; _entry = null;
    if (typeof Archive !== 'undefined') Archive.onDetails(null, []);
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
    if (info.slides)   _addRow('Slides', info.slides);
    if (info.words)    _addRow('Words', Number(info.words).toLocaleString());
    if (info.author)   _addRow('Author', info.author);
    if (info.created)  _addRow('Created', _fmtDate(info.created));
    if (info.title)    _addRow('Title', info.title);
    if (info.width && info.height) _addRow('Dimensions', `${info.width} × ${info.height}`);
    if (info.sheets?.length) _addRow('Sheets', info.sheets.join(', '));
  }

  // Renaming happens inline in the file list (FileTree.startRename: F2,
  // slow second click, context menu, "Rename (F2)" quick action).

  // ── Quick actions ─────────────────────────────────────────────────────────
  function _initQuickActions() {
    const wire = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };

    wire('qa-open',    () => { if (_path) SFM.openNative(_path); });
    wire('qa-opendir', () => { if (_path) SFM.openFolder(_path.replace(/[\\/][^\\/]+$/, '')); });
    wire('qa-rename',  () => { if (_path) FileTree.startRename(_path); });
    wire('qa-copy-path', () => {
      if (!_path) return;
      SFM.copyText(_path).then(ok => App.toast(ok ? 'Path copied' : 'Could not copy to the clipboard', ok ? 'success' : 'error'));
    });
    wire('qa-delete',  async () => {
      if (!_path) return;
      const n = _entry?.name || _path;
      if (!(await Dialogs.confirm({ title: 'Move to review', icon: 'archive', tone: 'warning', okLabel: 'Move to review', okIcon: 'archive', message: `Move “${n}” to the _to_review folder?`, detail: 'Nothing is deleted — it is moved into a _to_review folder next to it, where you can restore it.' }))) return;
      const r = await SFM.softDelete([_path]);
      if (r.ok) { App.toast('Moved to _to_review/', 'success'); FileTree.refresh(); clear(); }
      else       { App.toast('Failed: ' + r.error, 'error'); }
    });
    // Conversion / compression (convert-tools.js)
    wire('qa-convert-pdf', () => { if (_path) ConvertTools.quickImageToPdf(_path); });
    wire('qa-convert-img', () => { if (_path) ConvertTools.openConvertImage([_path]); });
    wire('qa-pdf-images',  () => { if (_path) ConvertTools.openPdfToImages(_path); });
    wire('qa-annotate',    () => { if (_path && window.Annotate) Annotate.open(_path); });
    wire('qa-edit-text',   () => { if (_path && window.PdfEdit) PdfEdit.open(_path); });
    wire('qa-compress',    () => {
      if (!_path) return;
      ConvertTools.openCompress([_path]);
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
    show('qa-annotate',    file && !!window.Annotate && Annotate.canAnnotate(entry.path));
    show('qa-edit-text',   isPdf && !!window.PdfEdit);
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
    _initQuickActions();
    _initAiPhoto();
    clear();
  }
  init();

  // ── Public: beginRename — compatibility shim (old details-pane rename box).
  // Starts the inline rename in the file list; `text` pre-fills the box.
  function beginRename(text) {
    return FileTree.startRename(_path || undefined, text);
  }

  return { showFile, showMultiple, clear, beginRename, runAiPhoto };
})();
