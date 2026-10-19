/**
 * docx-edit.js — quick edits in a Word (.docx) file, in the app.
 *
 *   DocxEdit.open(path)   full-window editor; DocxEdit.isOpen()
 *
 * The document is shown like the Word preview (doc_preview.docx_to_html).
 * Click any paragraph, heading, list item or table cell and type. Save writes
 * "<name>_edited.docx" next to the original (never over it); only the changed
 * characters are rewritten (docx_edit.py), so fonts, tables, pictures and
 * layout stay as they were. Paragraphs with pictures or fields are read-only.
 */

var DocxEdit = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ic = (n, s = 16) => Icons.svg(n, s);
  const baseOf = p => String(p || '').split(/[\\/]/).pop();
  const dirOf = p => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const stemOf = p => baseOf(p).replace(/\.[^.]+$/, '');
  const sepOf = d => (String(d).includes('/') && !String(d).includes('\\')) ? '/' : '\\';
  const joinP = (d, n) => String(d).replace(/[\\/]+$/, '') + sepOf(d) + n;
  const cleanName = n => String(n || '').trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim();
  // what a paragraph reads now (tabs and line breaks as \t / \n, like docx_edit.para_text)
  const textOf = el => { const t = (el.innerText || '').replace(/\u200b/g, '').replace(/\n$/, ''); return t === '\u00a0' ? '' : t; };

  let _cur = null;

  async function open(path) {
    if (!/\.docx$/i.test(path || '')) { App.toast('Quick edit works with .docx Word files', 'info'); return; }
    if (_cur) { _cur.focus(); return; }

    const ov = document.createElement('div');
    ov.className = 'modal-overlay st-overlay dx-overlay';
    ov.innerHTML = `
      <div class="st-window" role="dialog" aria-label="Edit Word document">
        <div class="st-head">
          <span class="modal-head-icon">${ic('file-text', 18)}</span>
          <div class="modal-heading">
            <h2 class="modal-title">Edit Word</h2>
            <div class="modal-subtitle" title="${esc(path)}">${esc(baseOf(path))}</div>
          </div>
          <span class="pill pill-neutral" id="dx-state">No changes</span>
          <span class="st-spacer"></span>
          <div class="st-head-group">
            <button class="icon-btn" id="dx-undo" data-tooltip="Undo · Ctrl+Z" aria-label="Undo">${ic('undo')}</button>
            <button class="icon-btn" id="dx-redo" data-tooltip="Redo · Ctrl+Y" aria-label="Redo">${ic('redo')}</button>
          </div>
          <button class="btn btn-primary" id="dx-save" disabled>${ic('save')}Save…</button>
          <button class="modal-close" id="dx-x" aria-label="Close" title="Close (Esc)">${ic('x')}</button>
        </div>
        <div class="dx-hint">${ic('info', 14)}<span>Click any text and type. Fonts, tables, pictures and layout stay as they are. Parts with pictures or fields can't be changed here.</span></div>
        <div class="doc-page-wrap dx-wrap" style="--doc-zoom:1"><div class="doc-loading"><span class="spinner"></span><span>Reading document…</span></div></div>
      </div>`;
    document.body.appendChild(ov);
    const $ = s => ov.querySelector(s);
    const wrap = $('.dx-wrap');
    let closed = false, sig = '', busy = false;
    const orig = new Map();            // pi → text when opened

    function close() {
      closed = true;
      document.removeEventListener('keydown', onKey, true);
      ov.remove();
      _cur = null;
    }
    const changed = () => {
      const out = [];
      wrap.querySelectorAll('[data-pi][contenteditable]').forEach(el => {
        const pi = +el.dataset.pi, now = textOf(el);
        if (orig.has(pi) && now !== orig.get(pi)) out.push({ pi, old: orig.get(pi), new: now });
      });
      return out;
    };
    function sync() {
      const n = changed().length;
      $('#dx-state').textContent = n ? `${n} paragraph${n === 1 ? '' : 's'} changed` : 'No changes';
      $('#dx-state').className = 'pill ' + (n ? 'pill-yellow' : 'pill-neutral');
      $('#dx-save').disabled = busy || !n;
      wrap.querySelectorAll('[data-pi][contenteditable]').forEach(el => {
        el.classList.toggle('dx-changed', orig.has(+el.dataset.pi) && textOf(el) !== orig.get(+el.dataset.pi));
      });
    }

    const r = await SFM.docxPreviewHtml(path, true).catch(e => ({ ok: false, error: String(e) }));
    if (closed) return;
    if (!r || !r.ok) { close(); App.toast(esc((r && r.error) || 'Could not open the document'), 'error', 7000); return; }
    sig = r.sig || '';
    wrap.innerHTML = (r.truncated ? `<div class="doc-notice">${ic('info', 14)}<span class="doc-notice-text">This document is long: only the first part can be edited here.</span></div>` : '')
      + `<article class="doc-sheet dx-sheet">${r.html || '<p class="doc-empty-text">This document is empty.</p>'}</article>`;
    wrap.querySelectorAll('[data-pi]').forEach(el => {
      if (el.hasAttribute('data-ro')) { el.title = 'Has a picture or field: open in Word to change it'; return; }
      if (el.querySelector('[data-pi]')) return;
      el.setAttribute('contenteditable', 'plaintext-only');
      el.spellcheck = false;
      orig.set(+el.dataset.pi, textOf(el));
    });
    // links must not open while editing
    wrap.querySelectorAll('a').forEach(a => a.removeAttribute('href'));

    wrap.addEventListener('input', sync);
    // an empty paragraph shows a non-breaking space: drop it before typing
    wrap.addEventListener('focusin', e => {
      const el = e.target;
      if (el.isContentEditable && orig.get(+el.dataset.pi) === '' && el.textContent === '\u00a0') el.textContent = '';
    });
    wrap.addEventListener('paste', e => {
      if (!e.target.closest || !e.target.closest('[contenteditable]')) return;
      e.preventDefault();
      const t = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
      document.execCommand('insertText', false, t.replace(/\r?\n+/g, ' '));
    });

    async function save() {
      if (busy) return;
      const edits = changed();
      if (!edits.length) { App.toast('Nothing changed yet', 'info', 1500); return; }
      const dir = dirOf(path);
      let def = stemOf(path).replace(/_edited( \(\d+\))?$/i, '') + '_edited';
      for (let i = 2; i < 100; i++) {
        const x = await SFM.pathExists(joinP(dir, def + '.docx')).catch(() => null);
        if (!(x && x.ok && x.exists)) break;
        def = def.replace(/ \(\d+\)$/, '') + ` (${i})`;
      }
      const name = await Dialogs.ask({
        title: 'Save Word document', icon: 'save', label: 'File name', value: def, okLabel: 'Save', okIcon: 'save',
        hint: `Saved as a new .docx in ${dir}. The original is not changed.`,
        validate: v => cleanName(v).replace(/\.docx$/i, '') ? '' : 'Enter a file name',
      });
      if (!name || closed) return;
      const out = joinP(dir, cleanName(name).replace(/\.docx$/i, '') + '.docx');
      busy = true; sync();
      const res = await SFM.docxSaveEdits(path, edits, out, sig).catch(e => ({ ok: false, error: String(e) }));
      busy = false; sync();
      if (!res || !res.ok) { App.toast(esc((res && res.error) || 'Could not save'), 'error', 8000); return; }
      App.toast(`Saved <b>${esc(baseOf(res.out_path))}</b> (${res.changed} paragraph${res.changed === 1 ? '' : 's'} changed)`, 'success', 6000);
      close();
      try {
        if (window.FileTree && FileTree.refresh) await FileTree.refresh();
        if (window.FileTree && FileTree.selectPaths) FileTree.selectPaths([res.out_path]);
      } catch (_) {}
    }

    async function tryClose() {
      if (busy) return;
      if (changed().length && !(await Dialogs.confirm({ title: 'Unsaved changes', icon: 'alert-triangle', tone: 'warning',
            okLabel: 'Close without saving', message: 'Close without saving?', detail: 'Your original document is unchanged.' }))) return;
      close();
    }

    function onKey(e) {
      if (closed) return;
      const ctrl = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
      if (k === 'enter' && e.target.isContentEditable && ov.contains(e.target)) { e.preventDefault(); return; }   // one paragraph stays one paragraph
      if (document.querySelector('.modal-overlay.dlg-top')) return;
      e.stopPropagation();                      // the file list must not see our keys
      if (ctrl && k === 's') { e.preventDefault(); save(); return; }
      if (k === 'escape') {
        e.preventDefault();
        if (document.activeElement && document.activeElement.isContentEditable) { document.activeElement.blur(); return; }
        tryClose();
      }
    }
    document.addEventListener('keydown', onKey, true);
    $('#dx-save').addEventListener('click', save);
    $('#dx-x').addEventListener('click', tryClose);
    $('#dx-undo').addEventListener('click', () => { document.execCommand('undo'); sync(); });
    $('#dx-redo').addEventListener('click', () => { document.execCommand('redo'); sync(); });

    _cur = { focus: () => { const f = wrap.querySelector('[contenteditable]'); if (f) f.focus(); }, state: () => ({ changed: changed(), sig }) };
    sync();
  }

  return { open, isOpen: () => !!_cur, _state: () => _cur && _cur.state() };
})();
