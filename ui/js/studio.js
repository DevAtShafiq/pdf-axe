/**
 * studio.js — PDF Studio: one window for every PDF tool.
 *
 *   Studio.open(path, {tool, page})   open a PDF (tool: 'read' | 'edit' | 'add-text'
 *                                     | any Annotate tool id | 'pages')
 *   Studio.isOpen()
 *
 * Left: the tool rail (Read & copy · Edit text · Comment · Fill & sign · Pages).
 * Middle: the active tool. The existing editors (annotate.js, pdf-edit.js and
 * the page arranger in pdf-tools.js) run "embedded" here without their own
 * title and save bars.
 *
 * Versions: the PDF is copied into a private working folder (pdf_studio.py).
 * Switching tool writes the current tool's changes into a NEW step file, so
 * Undo first undoes inside the tool and then steps back to the previous
 * version; nothing is ever overwritten. Save writes a new file (default) or
 * replaces the original after a backup in _to_review/, optionally compressed
 * to a size and protected with a password.
 */

var Studio = (() => {
  const isPdf = p => /\.pdf$/i.test(p || '');
  const baseOf = p => String(p || '').split(/[\\/]/).pop();
  const dirOf = p => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const stemOf = p => baseOf(p).replace(/\.[^.]+$/, '');
  const sepOf = d => (String(d).includes('/') && !String(d).includes('\\')) ? '/' : '\\';
  const joinP = (d, n) => String(d).replace(/[\\/]+$/, '') + sepOf(d) + n;
  const cleanName = n => String(n || '').trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim();
  const ic = (n, s = 16, c = '') => Icons.svg(n, s, c);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtSize = b => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';

  // Rail: [group, [{id, mode, tool, icon, label, hint}]]
  const RAIL = [
    ['View', [
      { id: 'read', mode: 'read', icon: 'text-select', label: 'Read & copy', hint: 'Select text and copy it · Ctrl+A selects a page' },
    ]],
    ['Edit', [
      { id: 'edit', mode: 'edit', tool: 'select', icon: 'file-pen', label: 'Edit text & objects', hint: 'Click text to change it, or a picture, shape or line to move, resize, recolour or delete it' },
      { id: 'add-text', mode: 'edit', tool: 'add', icon: 'type', label: 'Add text', hint: 'Click on the page to add a new line of text' },
      { id: 'add-image', mode: 'edit', tool: 'image', icon: 'image', label: 'Add image', hint: 'Place a picture on the page, then move or resize it' },
    ]],
    ['Comment', [
      { id: 'highlight', mode: 'annotate', tool: 'highlight', icon: 'highlighter', label: 'Highlight' },
      { id: 'underline', mode: 'annotate', tool: 'underline', icon: 'underline', label: 'Underline' },
      { id: 'strikeout', mode: 'annotate', tool: 'strikeout', icon: 'strikethrough', label: 'Strikeout' },
      { id: 'note', mode: 'annotate', tool: 'note', icon: 'message-square', label: 'Comment' },
      { id: 'text', mode: 'annotate', tool: 'text', icon: 'square', label: 'Text box' },
      { id: 'ink', mode: 'annotate', tool: 'ink', icon: 'pen', label: 'Draw' },
      { id: 'select', mode: 'annotate', tool: 'select', icon: 'mouse-pointer', label: 'More markup' },
    ]],
    ['Fill & sign', [
      { id: 'signature', mode: 'annotate', tool: 'signature', icon: 'signature', label: 'Signature' },
      { id: 'photo', mode: 'annotate', tool: 'photo', icon: 'image', label: 'Photo' },
      { id: 'stamp', mode: 'annotate', tool: 'stamp', icon: 'stamp', label: 'Stamp' },
      { id: 'cover', mode: 'annotate', tool: 'cover', icon: 'cover', label: 'Cover box' },
      { id: 'redact', mode: 'annotate', tool: 'redact', icon: 'redact', label: 'Redact', danger: true },
    ]],
    ['Pages', [
      { id: 'pages', mode: 'pages', icon: 'layers', label: 'Arrange pages', hint: 'Reorder, rotate, delete, duplicate or insert pages' },
    ]],
  ];
  const ITEMS = RAIL.flatMap(([, items]) => items);
  const ITEM = Object.fromEntries(ITEMS.map(i => [i.id, i]));
  const MODE_LABEL = { read: 'Read', edit: 'Text & object edits', annotate: 'Comments & signatures', pages: 'Page changes' };

  let _cur = null;

  async function open(path, opts = {}) {
    if (!isPdf(path)) { App.toast('Edit works with PDF files', 'info'); return; }
    if (_cur) { _cur.focus(); return; }
    if ((window.Annotate && Annotate.isOpen()) || (window.PdfEdit && PdfEdit.isOpen())) {
      App.toast('Close the open editor first', 'info'); return;
    }

    const ov = document.createElement('div');
    ov.className = 'modal-overlay st-overlay';
    ov.innerHTML = `
      <div class="st-window" role="dialog" aria-label="Edit PDF">
        <div class="st-head">
          <span class="modal-head-icon">${ic('file-pdf', 18)}</span>
          <div class="modal-heading">
            <h2 class="modal-title">Edit</h2>
            <div class="modal-subtitle" title="${esc(path)}">${esc(baseOf(path))}</div>
          </div>
          <span class="pill pill-neutral" id="st-ver">Original</span>
          <span class="st-spacer"></span>
          <div class="st-head-group">
            <button class="icon-btn" id="st-undo" data-tooltip="Undo · Ctrl+Z" aria-label="Undo">${ic('undo')}</button>
            <button class="icon-btn" id="st-redo" data-tooltip="Redo · Ctrl+Y" aria-label="Redo">${ic('redo')}</button>
          </div>
          <button class="btn btn-primary" id="st-save">${ic('save')}Save…</button>
          <button class="modal-close" id="st-x" aria-label="Close" title="Close (Esc)">${ic('x')}</button>
        </div>
        <div class="st-main">
          <nav class="st-rail" id="st-rail" aria-label="PDF tools">
            ${RAIL.map(([g, items]) => `
              <div class="st-group">
                <div class="st-group-label">${esc(g)}</div>
                ${items.map(i => `<button class="st-tool${i.danger ? ' st-tool-danger' : ''}" data-id="${i.id}" title="${esc(i.hint || i.label)}">${ic(i.icon, 16)}<span>${esc(i.label)}</span></button>`).join('')}
              </div>`).join('')}
          </nav>
          <div class="st-host" id="st-host">
            <div class="st-center"><span class="loading-spinner"></span><span>Opening…</span></div>
          </div>
          <aside class="st-sheet hidden" id="st-sheet" aria-label="Save"></aside>
        </div>
        <div class="st-busy hidden" id="st-busy"><span class="loading-spinner"></span><span id="st-busy-label">Working…</span></div>
      </div>`;
    document.body.appendChild(ov);
    const $ = s => ov.querySelector(s);
    const $$ = s => Array.from(ov.querySelectorAll(s));
    const host = $('#st-host');

    // ── state ──────────────────────────────────────────────────────────────
    let sess = null;                 // studio_begin result
    let password = '';               // what unlocked the original (kept for Save)
    let steps = [];                  // [{path, label}] — step 0 is the original
    let at = 0, savedAt = 0;         // current step, step last saved
    let mode = null, itemId = null, ctl = null, reader = null;
    let busy = false, closed = false, lastPage = 0;

    const cur = () => steps[at].path;
    const editorDirty = () => !!(ctl && ctl.dirty());
    const unsaved = () => at !== savedAt || editorDirty();

    function setBusy(on, label = 'Working…') {
      busy = on;
      $('#st-busy').classList.toggle('hidden', !on);
      $('#st-busy-label').textContent = label;
      sync();
    }
    function sync() {
      if (closed) return;
      const dirty = editorDirty();
      const pill = $('#st-ver');
      pill.textContent = (at === 0 ? 'Original' : `Version ${at}`) + (unsaved() ? ' · unsaved' : '');
      pill.title = steps.length ? steps.slice(1, at + 1).map((s, i) => `${i + 1}. ${s.label}`).join('\n') || 'No changes yet' : '';
      pill.className = 'pill ' + (unsaved() ? 'pill-yellow' : 'pill-neutral');
      $('#st-undo').disabled = busy || !((ctl && ctl.canUndo()) || at > 0);
      $('#st-redo').disabled = busy || !((ctl && ctl.canRedo()) || (at < steps.length - 1 && !dirty));
      $('#st-save').disabled = busy || !sess;
      $$('.st-tool').forEach(b => { b.classList.toggle('active', b.dataset.id === itemId); b.disabled = busy || !sess; });
    }

    // ── tool switching ─────────────────────────────────────────────────────
    // Write the active tool's changes into a new step. false = stay put.
    async function settle() {
      if (ctl && ctl.flush) ctl.flush();        // finish typing in an open text box
      if (!ctl || !ctl.dirty()) return true;
      const n = await SFM.studioNextStep(sess.session).catch(e => ({ ok: false, error: String(e) }));
      if (!n || !n.ok) { App.toast(esc((n && n.error) || 'Could not prepare the next version'), 'error', 7000); return false; }
      setBusy(true, 'Applying changes…');
      const r = await ctl.commit(n.path).catch(e => ({ ok: false, error: String(e) }));
      App.setStatus('Ready');
      setBusy(false);
      if (!r || !r.ok) {
        if (!(r && r.cancelled)) App.toast(esc((r && r.error) || 'Could not apply the changes'), 'error', 8000);
        return false;
      }
      if (r.unchanged) return true;
      steps = steps.slice(0, at + 1);
      steps.push({ path: r.out_path || n.path, label: MODE_LABEL[mode] || 'Changes' });
      at = steps.length - 1;
      const subs = (r.substituted || []).filter(x => String(x.wanted).toLowerCase() !== String(x.used).toLowerCase());
      if (subs.length) {
        const uniq = [...new Set(subs.map(s => `${s.wanted} → ${s.used}`))];
        App.toast('Some text uses a replacement font: ' + esc(uniq.join(', ')), 'warning', 8000);
      }
      (r.warnings || []).forEach(w => App.toast(esc(w), 'warning', 8000));
      return true;
    }

    function unmount() {
      if (ctl) { lastPage = ctl.page() || 0; ctl.close(); ctl = null; }
      if (reader) { lastPage = reader.page(); reader.close(); reader = null; }
      host.innerHTML = '';
    }

    // Show *id* (a rail item). keep=true: re-open the current tool on a new
    // step without committing (Undo/Redo across versions).
    async function show(id, { keep = false } = {}) {
      const it = ITEM[id] || ITEM.read;
      if (busy || closed) return;
      if (!keep && it.id === itemId && !(ctl && ctl.tool)) return;   // already showing it
      if (!keep && ctl && it.mode === mode && ctl.tool) {      // same editor: just switch its tool
        itemId = it.id; sync();
        if (it.tool) ctl.tool(it.tool);
        return;
      }
      if (!keep && !(await settle())) return;
      unmount();
      mode = it.mode; itemId = it.id;
      sync();
      const embed = { host, page: lastPage, onChange: sync, onKey: editorKey };
      setBusy(true, 'Opening…');
      try {
        if (mode === 'edit') ctl = await PdfEdit.open(cur(), { embed });
        else if (mode === 'annotate') ctl = await Annotate.open(cur(), { embed });
        else if (mode === 'pages') ctl = await PdfTools.openArrange([cur()], { embed, mode: 'arrange', name: stemOf(sess.name) + '_pages', folder: dirOf(sess.source), stem: stemOf(sess.name) });
        else reader = buildReader(host, cur(), lastPage);
      } finally {
        App.setStatus('Ready');
        setBusy(false);
      }
      if (closed) { if (ctl) ctl.close(); return; }
      if (mode !== 'read' && !ctl) {                            // the tool could not open
        mode = 'read'; itemId = 'read'; host.innerHTML = '';
        reader = buildReader(host, cur(), lastPage);
      }
      if (ctl && it.tool) ctl.tool(it.tool);
      sync();
      if (ctl) ctl.focus();
    }

    // Keys an embedded editor hands back to the Studio.
    function editorKey(action) {
      if (action === 'save') openSave();
      else if (action === 'close') tryClose();
      else if (action === 'undo') undo();
      else if (action === 'redo') redo();
    }

    async function undo() {
      if (busy) return;
      if (ctl && ctl.canUndo()) { ctl.undo(); sync(); return; }
      if (editorDirty()) return;
      if (at === 0) { App.toast('Nothing to undo', 'info', 1500); return; }
      at--;
      App.toast(`Back to ${at === 0 ? 'the original' : 'version ' + at}`, 'info', 1500);
      await show(itemId, { keep: true });
    }
    async function redo() {
      if (busy) return;
      if (ctl && ctl.canRedo()) { ctl.redo(); sync(); return; }
      if (editorDirty() || at >= steps.length - 1) return;
      at++;
      App.toast(`Forward to version ${at}`, 'info', 1500);
      await show(itemId, { keep: true });
    }

    // ── Save sheet ─────────────────────────────────────────────────────────
    function openSave() {
      if (busy || !sess) return;
      const sh = $('#st-sheet');
      if (!sh.classList.contains('hidden')) { const b = sh.querySelector('#st-go'); if (b) b.focus(); return; }
      const defName = stemOf(sess.source).replace(/_edited( \(\d+\))?$/, '') + '_edited';
      sh.innerHTML = `
        <div class="st-sheet-head">
          <span class="st-sheet-title">${ic('save', 16)}Save</span>
          <button class="icon-btn icon-btn-sm" id="st-sheet-x" aria-label="Close" title="Close">${ic('x', 14)}</button>
        </div>
        <div class="st-sheet-body">
          <div class="segmented segmented-block" role="radiogroup" aria-label="Save mode">
            <label class="seg-btn"><input type="radio" name="st-out" value="new" checked>${ic('file', 14)}New file</label>
            <label class="seg-btn" title="The current version is copied to _to_review/ first"><input type="radio" name="st-out" value="over">${ic('refresh', 14)}Replace original</label>
          </div>
          <div class="st-fields" id="st-fields">
            <label class="field-label" for="st-name">File name</label>
            <div class="field-row"><input class="input-text" id="st-name" value="${esc(defName)}"><span class="field-suffix">.pdf</span></div>
            <label class="field-label" for="st-dir">Folder</label>
            <div class="field-row"><input class="input-text" id="st-dir" value="${esc(dirOf(sess.source))}"><button class="btn btn-sm" id="st-browse">Browse…</button></div>
          </div>
          <div class="st-over-note hidden" id="st-over-note">${ic('info', 14)}<span>The current “${esc(sess.name)}” is copied to the <b>_to_review</b> folder first, so nothing is lost.</span></div>
          <div class="st-opt">
            <label class="switch"><input type="checkbox" id="st-kb-on"><span class="switch-track"></span>Make it smaller</label>
            <div class="field-row st-sub hidden" id="st-kb-row"><span class="field-label">At most</span><input class="input-text st-kb" id="st-kb" type="number" min="10" step="10" value="500"><span class="field-suffix">KB</span></div>
            <div class="field-hint st-sub hidden" id="st-kb-hint">Text stays selectable; only pictures are shrunk. If the size can't be reached, the smallest possible file is saved.</div>
          </div>
          <div class="st-opt">
            <label class="switch"><input type="checkbox" id="st-pw-on"${sess.protected ? ' checked' : ''}><span class="switch-track"></span>Protect with a password</label>
            <div class="st-sub${sess.protected ? '' : ' hidden'}" id="st-pw-rows">
              <input class="input-text" id="st-pw" type="password" autocomplete="new-password" placeholder="Password" value="${esc(sess.protected ? password : '')}">
              <input class="input-text" id="st-pw2" type="password" autocomplete="new-password" placeholder="Type it again" value="${esc(sess.protected ? password : '')}">
              ${sess.protected ? `<div class="field-hint">The original had a password; it is kept unless you turn this off.</div>` : ''}
            </div>
          </div>
          <div class="field-error hidden" id="st-err"></div>
        </div>
        <div class="st-sheet-foot">
          <button class="btn" id="st-cancel">Cancel</button>
          <button class="btn btn-primary" id="st-go">${ic('save')}Save</button>
        </div>`;
      sh.classList.remove('hidden');
      const q = s => sh.querySelector(s);
      const over = () => (sh.querySelector('input[name="st-out"]:checked') || {}).value === 'over';
      sh.querySelectorAll('input[name="st-out"]').forEach(rb => rb.addEventListener('change', () => {
        q('#st-fields').classList.toggle('is-dim', over());
        ['#st-name', '#st-dir', '#st-browse'].forEach(s => { q(s).disabled = over(); });
        q('#st-over-note').classList.toggle('hidden', !over());
        Dialogs.setBtn(q('#st-go'), over() ? 'Replace original' : 'Save', over() ? 'refresh' : 'save');
      }));
      q('#st-kb-on').addEventListener('change', () => ['#st-kb-row', '#st-kb-hint'].forEach(s => q(s).classList.toggle('hidden', !q('#st-kb-on').checked)));
      q('#st-pw-on').addEventListener('change', () => q('#st-pw-rows').classList.toggle('hidden', !q('#st-pw-on').checked));
      q('#st-browse').addEventListener('click', async () => {
        const r = await SFM.call('browse_for_folder').catch(() => null);
        if (r && r.ok && r.path) q('#st-dir').value = r.path;
      });
      q('#st-sheet-x').addEventListener('click', closeSheet);
      q('#st-cancel').addEventListener('click', closeSheet);
      const err = m => { q('#st-err').textContent = m; q('#st-err').classList.toggle('hidden', !m); };
      q('#st-go').addEventListener('click', async () => {
        err('');
        const opts = { mode: over() ? 'over' : 'new', out_path: '', target_kb: 0, password: '' };
        if (!over()) {
          const nm = cleanName(q('#st-name').value).replace(/\.pdf$/i, '');
          const dir = q('#st-dir').value.trim();
          if (!nm || !dir) { err('Enter a file name and folder'); return; }
          const de = await SFM.pathExists(dir).catch(() => null);
          if (!de || !de.ok || !de.is_dir) { err('Choose an existing folder'); return; }
          opts.out_path = joinP(dir, nm + '.pdf');
        }
        if (q('#st-kb-on').checked) {
          const kb = +q('#st-kb').value;
          if (!(kb >= 10)) { err('Enter a size of at least 10 KB'); return; }
          opts.target_kb = kb;
        }
        if (q('#st-pw-on').checked) {
          const p1 = q('#st-pw').value, p2 = q('#st-pw2').value;
          if (!p1) { err('Enter a password'); return; }
          if (p1 !== p2) { err('The two passwords are different'); return; }
          opts.password = p1;
        }
        if (over() && !(await Dialogs.confirm({ title: 'Replace original', icon: 'alert-triangle', tone: 'warning',
              okLabel: 'Replace original', okIcon: 'refresh', message: `Save your changes into “${sess.name}”?`,
              detail: 'The current version is first copied to the “_to_review” folder next to it, so nothing is lost.' }))) return;
        if (!(await settle())) return;
        setBusy(true, opts.target_kb ? 'Saving and compressing…' : 'Saving…');
        const r = await SFM.studioFinish(cur(), sess.source, opts).catch(e => ({ ok: false, error: String(e) }));
        setBusy(false);
        if (!r || !r.ok) { err((r && r.error) || 'Could not save'); return; }
        savedAt = at;
        closeSheet();
        sync();
        let msg = r.replaced ? `Saved <b>${esc(sess.name)}</b> · backup in _to_review/` : `Saved → <b>${esc(baseOf(r.out_path))}</b>`;
        msg += ` · ${fmtSize(r.size)}`;
        if (r.compress && r.compress.target_met === false) msg += ' · smallest possible (target not reached)';
        if (r.protected) msg += ' · password protected';
        App.toast(msg, 'success', 6000);
        try { if (FileTree.refreshAndSelect) FileTree.refreshAndSelect(r.out_path); else FileTree.refresh(); } catch (_) {}
        try {
          if (r.replaced && Preview.getCurrentPath && (Preview.getCurrentPath() || '').toLowerCase() === sess.source.toLowerCase()) Preview.previewFile(sess.source, '.pdf');
        } catch (_) {}
      });
      setTimeout(() => q('#st-go').focus(), 30);
    }

    function closeSheet() {
      const sh = $('#st-sheet');
      sh.classList.add('hidden'); sh.innerHTML = '';
      if (ctl) ctl.focus();
    }

    // ── close ──────────────────────────────────────────────────────────────
    function forceClose() {
      if (closed) return;
      closed = true;
      try { unmount(); } catch (_) {}
      document.removeEventListener('keydown', onKey, true);
      ov.remove();
      _cur = null;
    }
    async function tryClose() {
      if (busy) return;
      if (!$('#st-sheet').classList.contains('hidden')) { closeSheet(); return; }
      if (ctl && ctl.flush) ctl.flush();
      if (sess && unsaved() && !(await Dialogs.confirm({ title: 'Unsaved changes', icon: 'alert-triangle', tone: 'warning', okLabel: 'Close without saving',
            message: 'Close without saving?', detail: 'Your changes are not in any file yet. Your original PDF is unchanged.' }))) return;
      forceClose();
    }

    // ── keyboard (Read mode and the Studio itself; editors handle their own) ─
    function onKey(e) {
      if (closed || document.querySelector('.modal-overlay.dlg-top')) return;
      const t0 = e.target;
      if (t0 && t0.closest && t0.closest('.st-unlock')) return;   // the password box handles its keys
      if (t0 && t0.closest && t0.closest('#st-sheet')) {
        // Typing in the Save panel: the embedded editor must not see these keys.
        e.stopImmediatePropagation();
        if (e.key === 'Escape') { e.preventDefault(); closeSheet(); }
        else if (e.key === 'Enter' && t0.tagName === 'INPUT' && !/^(checkbox|radio)$/.test(t0.type)) { e.preventDefault(); $('#st-go').click(); }
        return;
      }
      if (ctl) return;                          // the embedded editor's own handler runs next
      const t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
      const ctrl = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
      e.stopPropagation();                      // the file list must not see our keys
      const handled = () => e.preventDefault();
      if (k === 'escape') { handled(); if (typing && !t.closest('#st-sheet')) { t.blur(); return; } tryClose(); return; }
      if (ctrl && k === 's') { handled(); openSave(); return; }
      if (typing) return;
      if (ctrl && k === 'z' && !e.shiftKey) { handled(); undo(); return; }
      if (ctrl && (k === 'y' || (k === 'z' && e.shiftKey))) { handled(); redo(); return; }
      if (reader && reader.key(e)) handled();
    }
    document.addEventListener('keydown', onKey, true);

    $('#st-rail').addEventListener('click', e => {
      const b = e.target.closest('.st-tool'); if (!b || b.disabled) return;
      show(b.dataset.id);
    });
    $('#st-undo').addEventListener('click', undo);
    $('#st-redo').addEventListener('click', redo);
    $('#st-save').addEventListener('click', openSave);
    $('#st-x').addEventListener('click', tryClose);

    _cur = { focus: () => { if (ctl) ctl.focus(); else host.focus(); }, path,
             state: () => ({ mode, itemId, at, steps: steps.map(s => s.path), savedAt, dirty: editorDirty(), session: sess && sess.session }) };

    // ── start: copy into the working folder (asking for a password if needed)
    let r = await SFM.studioBegin(path, '').catch(e => ({ ok: false, error: String(e) }));
    while (r && !r.ok && (r.code === 'need_password' || r.code === 'wrong_password')) {
      const pw = await askPassword(host, baseOf(path), r.code === 'wrong_password');
      if (closed) return;
      if (pw == null) { forceClose(); return; }
      password = pw;
      host.innerHTML = '<div class="st-center"><span class="loading-spinner"></span><span>Opening…</span></div>';
      r = await SFM.studioBegin(path, pw).catch(e => ({ ok: false, error: String(e) }));
    }
    if (closed) return;
    if (!r || !r.ok) { forceClose(); App.toast(esc((r && r.error) || 'Could not open the PDF'), 'error', 7000); return; }
    sess = r;
    steps = [{ path: r.work, label: 'Original' }];
    lastPage = Number.isInteger(opts.page) ? opts.page : 0;
    let start = opts.tool && ITEM[opts.tool] ? opts.tool : (r.has_text ? 'read' : 'select');
    if (opts.tool && !ITEM[opts.tool]) {                    // an Annotate tool id with no rail item
      const t = opts.tool;
      await show('select');
      if (ctl && ctl.tool) ctl.tool(t);
      return;
    }
    await show(start);
  }

  // ── password prompt inside the Studio window ──────────────────────────────
  function askPassword(host, name, wrong) {
    return new Promise(resolve => {
      host.innerHTML = `
        <form class="st-unlock card">
          <div class="st-unlock-icon">${ic('lock', 28)}</div>
          <div class="st-unlock-title">“${esc(name)}” is password-protected</div>
          <input class="input-text${wrong ? ' is-invalid' : ''}" type="password" id="st-unlock-pw" placeholder="Password" autocomplete="current-password">
          <div class="field-error${wrong ? '' : ' hidden'}">Wrong password. Try again.</div>
          <div class="st-unlock-btns">
            <button type="button" class="btn" id="st-unlock-cancel">Cancel</button>
            <button type="submit" class="btn btn-primary">${ic('lock', 14)}Unlock</button>
          </div>
        </form>`;
      const f = host.querySelector('form'), inp = host.querySelector('#st-unlock-pw');
      f.addEventListener('submit', e => { e.preventDefault(); if (inp.value) resolve(inp.value); });
      host.querySelector('#st-unlock-cancel').addEventListener('click', () => resolve(null));
      inp.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); resolve(null); } });
      setTimeout(() => inp.focus(), 30);
    });
  }

  // ── Read & copy: pages with a selectable text layer ───────────────────────
  function buildReader(host, file, startPage) {
    const el = document.createElement('div');
    el.className = 'st-reader';
    el.innerHTML = `
      <div class="an-bar st-read-bar" role="toolbar" aria-label="Read tools">
        <button class="btn btn-sm btn-ghost" data-a="copy-page" title="Copy the text of the current page">${ic('file-text', 14)}Copy page text</button>
        <button class="btn btn-sm btn-ghost" data-a="copy-all" title="Copy all the text in this PDF">${ic('files', 14)}Copy all text</button>
        <span class="an-spacer"></span>
        <span class="an-pageno"><span>Page</span><span id="st-rpg">1</span><span id="st-rpgn">/ 1</span></span>
        <span class="toolbar-sep"></span>
        <button class="icon-btn" data-a="zout" data-tooltip="Zoom out · Ctrl+−" aria-label="Zoom out">${ic('zoom-out')}</button>
        <button class="icon-btn" data-a="zin" data-tooltip="Zoom in · Ctrl+=" aria-label="Zoom in">${ic('zoom-in')}</button>
        <button class="icon-btn" data-a="fitw" data-tooltip="Fit width · Ctrl+0" aria-label="Fit width">${ic('fit-width')}</button>
      </div>
      <div class="st-read-view" tabindex="0"><div class="st-read-pages"></div></div>`;
    host.appendChild(el);
    const view = el.querySelector('.st-read-view'), pagesEl = el.querySelector('.st-read-pages');
    let count = 0, cur = 0, zoom = 1, obs = null, closed = false, gen = 0;

    const cssW = () => Math.max(280, Math.round((view.clientWidth - 48) * zoom));
    function build() {
      const g = ++gen;
      if (obs) obs.disconnect();
      pagesEl.innerHTML = '';
      const w = cssW();
      const dpi = Math.max(120, Math.min(300, Math.round((w * Math.min(window.devicePixelRatio || 1, 2.5)) / 8.5)));
      for (let i = 0; i < count; i++) {
        const slot = document.createElement('div');
        slot.className = 'st-read-slot';
        slot.style.cssText = `width:${w}px;height:${Math.round(w * 1.294)}px`;
        slot.dataset.pageSlot = i;
        pagesEl.appendChild(slot);
      }
      obs = new IntersectionObserver(async ents => {
        for (const en of ents) {
          if (!en.isIntersecting) continue;
          const slot = en.target, p = +slot.dataset.pageSlot;
          obs.unobserve(slot);
          const r = await SFM.getPdfPage(file, p, dpi).catch(() => null);
          if (closed || g !== gen || !slot.isConnected || !r || !r.ok) continue;
          const img = document.createElement('img');
          img.src = r.data_url;
          img.className = 'pdf-page-canvas';
          const box = PdfText.wrapPage(img, file, p, { width: w });
          box.classList.add('st-read-page');
          slot.replaceWith(box);
          PdfText.attach(box);
        }
      }, { root: view, rootMargin: '600px' });
      pagesEl.querySelectorAll('[data-page-slot]').forEach(s => obs.observe(s));
    }
    const pageEl = p => pagesEl.querySelector(`[data-page="${p}"]`) || pagesEl.querySelector(`[data-page-slot="${p}"]`);
    function goPage(p, smooth = false) {
      p = Math.max(0, Math.min(count - 1, p));
      const t = pageEl(p); if (t) view.scrollTop = t.offsetTop - 12;
      cur = p; el.querySelector('#st-rpg').textContent = p + 1;
    }
    view.addEventListener('scroll', () => {
      const top = view.scrollTop + 40;
      let p = 0;
      for (const n of pagesEl.children) { if (n.offsetTop <= top) p = +(n.dataset.page ?? n.dataset.pageSlot); else break; }
      cur = p; el.querySelector('#st-rpg').textContent = p + 1;
    });
    function setZoom(z) { const keep = cur; zoom = Math.max(0.4, Math.min(4, z)); build(); goPage(keep); }
    el.querySelector('.st-read-bar').addEventListener('click', e => {
      const b = e.target.closest('[data-a]'); if (!b) return;
      const a = b.dataset.a;
      if (a === 'copy-page') PdfText.copyPage(file, cur);
      else if (a === 'copy-all') PdfText.copyAll(file);
      else if (a === 'zin') setZoom(zoom * 1.2);
      else if (a === 'zout') setZoom(zoom / 1.2);
      else if (a === 'fitw') setZoom(1);
    });
    PdfText.enablePan(view);
    view.addEventListener('contextmenu', e => {
      const box = e.target.closest && e.target.closest('.pdf-page');
      if (!box) return;
      PdfText.menu(e, { path: file, page: +box.dataset.page, box });
    });

    (async () => {
      const r = await SFM.getPdfPageCount(file).catch(() => null);
      if (closed) return;
      count = (r && r.ok && (r.count || r.page_count)) || 0;
      el.querySelector('#st-rpgn').textContent = '/ ' + Math.max(1, count);
      build();
      if (startPage) requestAnimationFrame(() => goPage(startPage));
      view.focus({ preventScroll: true });
    })();

    return {
      page: () => cur,
      close() { closed = true; if (obs) obs.disconnect(); el.remove(); },
      key(e) {                       // → true when handled
        const ctrl = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
        if (ctrl && k === 'c') {
          const text = PdfText.selectedText();
          if (text.trim()) PdfText.copyText(text); else PdfText.copyPage(file, cur);
          return true;
        }
        if (ctrl && k === 'a') { const box = pagesEl.querySelector(`.pdf-page[data-page="${cur}"]`); if (box) PdfText.selectPage(box); return true; }
        if (ctrl && (k === '=' || k === '+')) { setZoom(zoom * 1.2); return true; }
        if (ctrl && k === '-') { setZoom(zoom / 1.2); return true; }
        if (ctrl && k === '0') { setZoom(1); return true; }
        if (k === 'pagedown') { goPage(cur + 1); return true; }
        if (k === 'pageup') { goPage(cur - 1); return true; }
        if (k === 'home' && !ctrl) { goPage(0); return true; }
        if (k === 'end' && !ctrl) { goPage(count - 1); return true; }
        return false;
      },
    };
  }

  // =========================================================================
  // ENTRY POINTS — "PDF Studio" preview button, Ctrl+Shift+P; Annotate and
  // Edit text open here for PDFs (annotate.js / pdf-edit.js / context menu).
  // =========================================================================
  function _candidate() {
    try {
      const sel = FileTree.getSelected ? FileTree.getSelected() : [];
      if (sel && sel.length === 1 && isPdf(sel[0])) return sel[0];
      if (sel && sel.length > 1) return null;
    } catch (_) {}
    try { const p = Preview.getCurrentPath && Preview.getCurrentPath(); if (p && isPdf(p)) return p; } catch (_) {}
    return null;
  }
  function _init() {
    const pb = document.getElementById('preview-studio');
    if (pb) {
      pb.addEventListener('click', () => { const p = Preview.getCurrentPath && Preview.getCurrentPath(); if (p) open(p); });
      const sync = () => {
        let p = null; try { p = Preview.getCurrentPath && Preview.getCurrentPath(); } catch (_) {}
        pb.classList.toggle('hidden', !(p && isPdf(p)));
      };
      const nameEl = document.getElementById('preview-file-name');
      if (nameEl && window.MutationObserver) new MutationObserver(() => setTimeout(sync, 0)).observe(nameEl, { childList: true, characterData: true, subtree: true });
      sync();
    }
    document.addEventListener('keydown', e => {
      if (!(e.ctrlKey || e.metaKey) || !e.shiftKey || e.altKey) return;
      if ((e.key || '').toLowerCase() !== 'p' && e.code !== 'KeyP') return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (_cur || document.querySelector('.modal-overlay')) return;
      e.preventDefault();
      const p = _candidate();
      if (p) open(p); else App.toast('Select one PDF to edit', 'info');
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();

  return { open, isOpen: () => !!_cur, _state: () => (_cur && _cur.state ? _cur.state() : null) };
})();
