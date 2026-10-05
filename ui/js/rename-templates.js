/**
 * rename-templates.js — document-name template suggestions + templates manager
 *
 *  RenameTemplates.attach(input, dropdown, opts)
 *      Turns a rename <input> into a template-suggesting combo box:
 *        - empty input / untouched name → browse list of all templates
 *        - typing filters by English, local name or "slugish" text
 *        - ↑/↓ highlight, Enter picks (a strong top match is pre-selected), Esc closes, mouse click picks
 *        - Ctrl+Enter renames to the typed name AND saves it as a template
 *        - language dropdown (International / Korean …) persisted to settings
 *      opts: { getEntry(): entry|null, rename({stem?, typed?, save?}) }
 *
 *  RenameTemplates.openManager()
 *      "Document name templates" manager (list, search, Add / Apply / Remove /
 *      Save to file / Close) — reachable from More (⋯) → Templates and from the
 *      suggestion dropdown.
 */

const RenameTemplates = (() => {

  let _lang      = '';           // current template language code
  let _languages = [];           // [{code,label,local_name,bilingual}]
  const _listeners = new Set();  // called when the language changes

  const _esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
                                   .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const _toast = (m, t = 'info') => { try { App.toast(m, t); } catch (_) {} };

  async function loadLang() {
    if (_lang && _languages.length) return _lang;
    try {
      const r = await SFM.getRenameLang();
      if (r.ok) { _lang = r.lang; _languages = r.languages || []; }
    } catch (_) {}
    if (!_languages.length) {
      _languages = [{ code: 'en', label: 'International (English)', bilingual: false },
                    { code: 'ko', label: 'Korean (KR-EN)', local_name: 'Korean', bilingual: true }];
    }
    return _lang || 'ko';
  }

  async function setLang(code) {
    const r = await SFM.setRenameLang(code);
    if (!r.ok) { _toast('Could not change template language: ' + r.error, 'error'); return false; }
    _lang = r.lang;
    _listeners.forEach(fn => { try { fn(_lang); } catch (_) {} });
    return true;
  }

  function _langInfo(code) { return _languages.find(l => l.code === (code || _lang)) || {}; }

  function _langSelectHtml(id) {
    return `<select id="${id}" class="rt-lang-select input-text" title="Template language">` +
      _languages.map(l => `<option value="${_esc(l.code)}" ${l.code === _lang ? 'selected' : ''}>${_esc(l.label)}</option>`).join('') +
      `</select>`;
  }

  // ── Suggestion combo box ────────────────────────────────────────────────
  function attach(input, dd, opts = {}) {
    if (!input || !dd) return;
    let items   = [];        // current suggestion objects
    let idx     = -1;        // highlighted row (items.length = "save" row)
    let seq     = 0;         // request sequence (drop stale responses)
    let typed   = false;     // user changed the text since showing the file
    let open    = false;

    dd.classList.add('rt-dropdown');

    const entry    = () => (opts.getEntry ? opts.getEntry() : null);
    const extOf    = () => { const e = entry(); return e && !e.is_dir ? (e.ext || '') : ''; };
    const query    = () => (typed ? input.value.trim() : '');
    const saveText = () => {
      let v = input.value.trim();
      const ext = extOf();
      if (ext) { const d = ext.startsWith('.') ? ext : '.' + ext; if (v.toLowerCase().endsWith(d.toLowerCase())) v = v.slice(0, -d.length).trim(); }
      return v;
    };

    async function refresh() {
      if (!entry()) { hide(); return; }
      await loadLang();
      const my = ++seq;
      let r;
      try { r = await SFM.filterSuggestions(query(), extOf(), _lang); } catch (_) { r = { ok: false }; }
      if (my !== seq || document.activeElement !== input) return;
      items = r.ok ? (r.suggestions || []) : [];
      // Like the old app: a strong match (exact, starts-with, or every typed
      // word starts a word of the name) is pre-selected, so plain Enter applies
      // it. Weaker matches (substring/typo) still need ↓ first, so a custom
      // name typed on purpose is not replaced.
      idx = (typed && items.length && items[0].score <= 2) ? 0 : -1;
      render();
      if (idx >= 0) highlight();
    }

    function render() {
      const L = _langInfo();
      const st = saveText();
      const canSave = typed && st && st !== (entry()?.name || '');
      let h = `<div class="rt-head">${_langSelectHtml('rt-lang-inline')}
                 <button type="button" class="rt-manage" title="Edit document name templates">Templates…</button></div>`;
      if (!items.length) {
        h += `<div class="rt-empty">No matching template${typed ? '' : 's'}</div>`;
      }
      items.forEach((s, i) => {
        h += `<div class="rt-item ac-item" data-i="${i}" title="${_esc(s.name)}">
                <div class="rt-label">${_esc(s.label || s.stem)}${s.builtin ? '' : ' <span class="rt-tag">custom</span>'}</div>
                <div class="rt-target">→ ${_esc(s.name)}</div>
              </div>`;
      });
      if (canSave) {
        h += `<div class="rt-item rt-save ac-item" data-i="${items.length}"
                   title="Rename and save as a ${_esc(L.label || '')} template (Ctrl+Enter)">
                <div class="rt-label">+ Save “${_esc(st)}” as template</div>
                <div class="rt-target">Ctrl+Enter</div>
              </div>`;
      }
      dd.innerHTML = h;
      dd.classList.remove('hidden');
      open = true;
      place();

      const sel = dd.querySelector('#rt-lang-inline');
      sel.addEventListener('mousedown', e => e.stopPropagation());
      sel.addEventListener('change', async () => {
        if (await setLang(sel.value)) { input.focus(); refresh(); }
      });
      dd.querySelector('.rt-manage').addEventListener('mousedown', e => {
        e.preventDefault(); hide(); openManager();
      });
      dd.querySelectorAll('.rt-item').forEach(el => {
        el.addEventListener('mousedown', e => { e.preventDefault(); pick(+el.dataset.i); });
      });
    }

    // The details pane is narrow and clips overflow, so float the list with
    // fixed positioning: right-aligned to the input, wider than it if needed.
    function place() {
      const r  = input.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      const w  = Math.min(Math.max(r.width, 300), vw - 16);
      const left = Math.max(8, Math.min(r.right - w, vw - w - 8));
      const below = vh - r.bottom - 10, above = r.top - 10;
      dd.style.position = 'fixed';
      dd.style.left  = left + 'px';
      dd.style.width = w + 'px';
      dd.style.right = 'auto';
      if (below >= 200 || below >= above) {
        dd.style.top = (r.bottom + 2) + 'px'; dd.style.bottom = 'auto';
        dd.style.maxHeight = Math.max(120, Math.min(340, below)) + 'px';
      } else {
        dd.style.top = 'auto'; dd.style.bottom = (vh - r.top + 2) + 'px';
        dd.style.maxHeight = Math.max(120, Math.min(340, above)) + 'px';
      }
    }
    window.addEventListener('resize', () => { if (open) place(); });
    document.addEventListener('scroll', () => { if (open) place(); }, true);

    function rows() { return dd.querySelectorAll('.rt-item'); }

    function highlight() {
      rows().forEach(el => el.classList.toggle('active', +el.dataset.i === idx));
      const a = dd.querySelector('.rt-item.active');
      if (a) a.scrollIntoView({ block: 'nearest' });
    }

    function hide() {
      dd.classList.add('hidden');
      open = false; idx = -1; seq++;
    }

    async function pick(i) {
      hide();
      if (i === items.length) { await saveTyped(); return; }
      const s = items[i];
      if (!s) return;
      await opts.rename({ stem: s.stem });
    }

    async function saveTyped() {
      const st = saveText();
      if (!st) { _toast('Type a name first', 'error'); return; }
      await opts.rename({ stem: st, save: true });
    }

    input.addEventListener('focus', () => { refresh(); });
    input.addEventListener('input', () => { typed = true; refresh(); });
    const _hideIfAway = () => setTimeout(() => {
      if (document.activeElement !== input && !dd.contains(document.activeElement)) hide();
    }, 150);
    input.addEventListener('blur', _hideIfAway);
    dd.addEventListener('focusout', _hideIfAway);

    input.addEventListener('keydown', async e => {
      const n = rows().length;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (!open) { refresh(); return; }
        if (n) { const cur = [...rows()].findIndex(el => +el.dataset.i === idx); idx = +rows()[Math.min(cur + 1, n - 1)].dataset.i; highlight(); }
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (open && n) {
          const cur = [...rows()].findIndex(el => +el.dataset.i === idx);
          idx = cur <= 0 ? -1 : +rows()[cur - 1].dataset.i; highlight();
        }
      } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault(); hide(); await saveTyped();
      } else if (e.key === 'Enter' && e.shiftKey) {
        // Shift+Enter: apply the highlighted / best template.
        e.preventDefault();
        if (open && idx >= 0) { pick(idx); return; }
        if (open && items.length) { pick(0); return; }
        let r; try { r = await SFM.filterSuggestions(query(), extOf(), _lang); } catch (_) { r = {}; }
        hide();
        const s = r.ok && r.suggestions && r.suggestions[0];
        if (s) await opts.rename({ stem: s.stem }); else await opts.rename({ typed: input.value.trim() });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (open && idx >= 0) { pick(idx); return; }
        hide();
        await opts.rename({ typed: input.value.trim() });
      } else if (e.key === 'Escape') {
        e.stopPropagation();
        if (open) { hide(); }
        else { input.value = entry()?.name || ''; typed = false; }
      }
    });

    _listeners.add(() => { if (open) refresh(); });

    return {
      reset() { typed = false; hide(); },
      refresh, hide,
    };
  }

  // ── Templates manager dialog ─────────────────────────────────────────────
  async function openManager() {
    await loadLang();
    let rows   = [];          // [{en, local, builtin, source}]
    let sel    = -1;
    let dirty  = false;
    let file   = '';

    const ov = document.createElement('div');
    ov.className = 'modal-overlay rt-overlay';
    ov.style.zIndex = 9400;
    ov.innerHTML = `
      <div class="modal rt-manager" role="dialog" aria-labelledby="rt-title">
        <div class="modal-header">
          <h2 class="modal-title" id="rt-title">Document name templates</h2>
          <button class="modal-close" aria-label="Close">&#x2715;</button>
        </div>
        <div class="modal-body">
          <div class="rt-row">
            <label class="detail-label" for="rt-lang">Language</label>
            ${_langSelectHtml('rt-lang')}
            <input id="rt-search" class="input-text" placeholder="Search templates…" autocomplete="off" spellcheck="false">
          </div>
          <div class="rt-hint text-muted" id="rt-hint"></div>
          <div id="rt-list" class="rt-list" tabindex="0"></div>
          <div class="rt-fields">
            <div class="rt-fields-title detail-label" id="rt-fields-title">New entry</div>
            <label class="detail-label" for="rt-en">English</label>
            <input id="rt-en" class="input-text" autocomplete="off" spellcheck="false" placeholder="e.g. Passport">
            <label class="detail-label rt-local" for="rt-local" id="rt-local-label">Korean</label>
            <input id="rt-local" class="input-text rt-local" autocomplete="off" spellcheck="false">
          </div>
          <div class="rt-actions">
            <button class="btn" id="rt-add">Add</button>
            <button class="btn" id="rt-apply" disabled>Apply to selected row</button>
            <button class="btn btn-danger" id="rt-remove" disabled>Remove</button>
          </div>
          <div class="rt-file text-muted" id="rt-file"></div>
        </div>
        <div class="modal-footer">
          <button class="btn" id="rt-close">Close</button>
          <button class="btn btn-primary" id="rt-save">Save to file</button>
        </div>
      </div>`;
    document.body.appendChild(ov);

    const $ = id => ov.querySelector('#' + id);
    const L = () => _langInfo();

    function close() {
      if (dirty && !confirm('Discard unsaved template changes?')) return;
      document.removeEventListener('keydown', onKey, true);
      ov.remove();
    }
    function onKey(e) {
      if (e.key === 'Escape' && document.body.lastElementChild === ov) { e.stopPropagation(); close(); }
    }
    document.addEventListener('keydown', onKey, true);
    ov.querySelector('.modal-close').addEventListener('click', close);
    $('rt-close').addEventListener('click', close);
    ov.addEventListener('mousedown', e => { if (e.target === ov) close(); });

    async function load() {
      const r = await SFM.getRenameTemplates(_lang);
      if (!r.ok) { _toast('Could not load templates: ' + r.error, 'error'); return; }
      rows = (r.rows || []).map(x => ({ en: x.en, local: x.local, builtin: !!x.builtin, source: x.source }));
      file = r.user_file || '';
      sel = -1; dirty = false;
      const bil = !!L().bilingual;
      ov.querySelectorAll('.rt-local').forEach(el => el.classList.toggle('hidden', !bil));
      $('rt-local-label').textContent = L().local_name || 'Local';
      $('rt-hint').textContent = bil
        ? `Files are named ${L().local_name}-English (e.g. 여권-passport.pdf). Built-in rows are kept; edits are saved to your file.`
        : 'International mode: files are named in English only (e.g. Passport.pdf). Names from the other language lists are included.';
      $('rt-file').textContent = file ? 'File: ' + file : '';
      fillFields(null);
      renderList();
    }

    function label(r) {
      if (!L().bilingual) return r.en || r.local;
      return r.local && r.en ? `${r.local}  ·  ${r.en}` : (r.local || r.en);
    }

    function renderList() {
      const q = $('rt-search').value.trim().toLowerCase();
      const qs = q.replace(/[^0-9a-zÀ-￿]/g, '');
      const html = rows.map((r, i) => {
        const hay = (r.en + ' ' + r.local).toLowerCase();
        if (q && !hay.includes(q) && !hay.replace(/[^0-9a-zÀ-￿]/g, '').includes(qs)) return '';
        const tag = r.source === 'custom' ? '' :
                    (r.source === 'derived' ? ' <span class="rt-tag">from other list</span>' : ' <span class="rt-tag">built-in</span>');
        return `<div class="rt-list-row ${i === sel ? 'active' : ''}" data-i="${i}">${_esc(label(r))}${tag}</div>`;
      }).join('');
      $('rt-list').innerHTML = html || '<div class="rt-empty">No templates</div>';
      $('rt-list').querySelectorAll('.rt-list-row').forEach(el => {
        el.addEventListener('click', () => select(+el.dataset.i));
        el.addEventListener('dblclick', () => { select(+el.dataset.i); $('rt-en').focus(); });
      });
      const custom = sel >= 0 && rows[sel] && !rows[sel].builtin;
      $('rt-apply').disabled  = sel < 0;
      $('rt-remove').disabled = !custom;
      $('rt-remove').title    = sel >= 0 && !custom ? 'Built-in templates cannot be removed' : '';
    }

    function fillFields(r) {
      $('rt-en').value    = r ? r.en : '';
      $('rt-local').value = r ? r.local : '';
      $('rt-fields-title').textContent = r ? (r.builtin ? 'Selected row (built-in — Apply saves an edited copy)' : 'Selected row (custom)') : 'New entry';
    }

    function select(i) {
      sel = i; fillFields(rows[i]); renderList();
    }

    function fields() {
      const en = $('rt-en').value.trim();
      const local = L().bilingual ? $('rt-local').value.trim() : '';
      if (!en && !local) { _toast(L().bilingual ? `Enter English, ${L().local_name}, or both` : 'Enter a name', 'error'); return null; }
      if (/[\\/:*?"<>|]/.test(en + local)) { _toast('Names cannot contain \\ / : * ? " < > |', 'error'); return null; }
      return { en, local };
    }

    function exists(f, skip = -1) {
      return rows.some((r, i) => i !== skip && r.en === f.en && (L().bilingual ? r.local === f.local : true));
    }

    $('rt-add').addEventListener('click', () => {
      const f = fields(); if (!f) return;
      if (exists(f)) { _toast('That template already exists', 'info'); return; }
      rows.unshift({ ...f, builtin: false, source: 'custom' });
      sel = 0; dirty = true;
      $('rt-search').value = '';
      fillFields(rows[0]); renderList();
      $('rt-list').scrollTop = 0;
    });

    $('rt-apply').addEventListener('click', () => {
      if (sel < 0) { _toast('Select a row first', 'info'); return; }
      const f = fields(); if (!f) return;
      const r = rows[sel];
      if (r.en === f.en && r.local === f.local) return;
      if (r.builtin) {
        // Built-ins stay; the edited copy becomes a custom row at the top.
        if (exists(f)) { _toast('That template already exists', 'info'); return; }
        rows.unshift({ ...f, builtin: false, source: 'custom' }); sel = 0;
      } else {
        rows[sel] = { ...f, builtin: false, source: 'custom' };
      }
      dirty = true; fillFields(rows[sel]); renderList();
    });

    $('rt-remove').addEventListener('click', () => {
      if (sel < 0 || rows[sel].builtin) return;
      rows.splice(sel, 1); sel = -1; dirty = true;
      fillFields(null); renderList();
    });

    $('rt-save').addEventListener('click', async () => {
      const custom = rows.filter(r => !r.builtin).map(r => ({ en: r.en, local: r.local }));
      const r = await SFM.saveRenameTemplates(custom, _lang);
      if (r.ok) { dirty = false; _toast(`Saved ${custom.length} custom template${custom.length === 1 ? '' : 's'}`, 'success'); await load(); }
      else _toast('Could not save: ' + r.error, 'error');
    });

    $('rt-search').addEventListener('input', renderList);
    $('rt-list').addEventListener('keydown', e => {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault();
      const vis = [...$('rt-list').querySelectorAll('.rt-list-row')].map(el => +el.dataset.i);
      if (!vis.length) return;
      const p = vis.indexOf(sel);
      const n = e.key === 'ArrowDown' ? vis[Math.min(p + 1, vis.length - 1)] : vis[Math.max(p - 1, 0)];
      select(n);
      $('rt-list').querySelector('.rt-list-row.active')?.scrollIntoView({ block: 'nearest' });
    });

    $('rt-lang').addEventListener('change', async e => {
      if (dirty && !confirm('Discard unsaved template changes?')) { e.target.value = _lang; return; }
      if (await setLang(e.target.value)) await load();
    });

    await load();
    $('rt-search').focus();
  }

  return { attach, openManager, loadLang, setLang, get lang() { return _lang; } };
})();
