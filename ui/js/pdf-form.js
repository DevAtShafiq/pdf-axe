/**
 * pdf-form.js — Fill form: type into a PDF's own fields, like Acrobat.
 *
 *   PdfForm.open(path, {embed}) → controller (PDF Studio only)
 *
 * Every fillable field (text, check box, radio button, drop-down / list,
 * date) gets a live input laid over the page in the PDF's own tab order, so
 * Tab / Shift+Tab walk the form. Required fields are marked red. The values
 * are written by pdf_form.fill when the Studio commits the step: the result
 * stays a fillable form unless "Lock fields" is on (then the values become
 * part of the page). Adobe-only (dynamic XFA) forms get a warning instead.
 *
 * Controller: kind 'form', dirty, flush, commit(outPath), canUndo/canRedo,
 * undo/redo, page, goPage, focus, busy, close.
 */

var PdfForm = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ic = (n, s = 16) => Icons.svg(n, s);
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

  // Acrobat date format (AFDate_FormatEx) for a JS Date: d dd m mm mmm mmmm yy yyyy
  function fmtDate(d, fmt) {
    const map = {
      yyyy: String(d.getFullYear()), yy: String(d.getFullYear()).slice(-2),
      mmmm: MONTHS[d.getMonth()], mmm: MONTHS[d.getMonth()].slice(0, 3),
      mm: String(d.getMonth() + 1).padStart(2, '0'), m: String(d.getMonth() + 1),
      dd: String(d.getDate()).padStart(2, '0'), d: String(d.getDate()),
    };
    return String(fmt || 'dd/mm/yyyy').replace(/yyyy|yy|mmmm|mmm|mm|m|dd|d/g, t => map[t]);
  }

  async function open(file, { embed } = {}) {
    if (!embed) return null;
    const r = await SFM.formFields(file).catch(e => ({ ok: false, error: String(e) }));
    if (!r || !r.ok) { App.toast(esc((r && r.error) || 'Could not read the form'), 'error', 7000); return null; }

    const fields = r.fields.filter(f => f.type !== 'signature' && f.type !== 'button');
    const xfa = r.xfa;                       // none | hybrid | dynamic
    const byId = Object.fromEntries(fields.map(f => [f.id, f]));
    const orig = Object.fromEntries(fields.map(f => [f.id, f.value]));
    const vals = { ...orig };
    let undoStack = [], redoStack = [], closed = false, lock = false, zoom = 1, curPage = embed.page || 0, obs = null, gen = 0;

    const el = document.createElement('div');
    el.className = 'pf-root pf-hl';
    const fillable = fields.some(f => !f.readonly) && xfa !== 'dynamic';
    el.innerHTML = `
      <div class="an-bar pf-bar" role="toolbar" aria-label="Form tools">
        <span class="pf-count">${ic('list-checks', 14)}<span id="pf-count"></span></span>
        <label class="switch pf-sw" title="Show where the fields are"><input type="checkbox" id="pf-hl" checked><span class="switch-track"></span>Highlight fields</label>
        <label class="switch pf-sw" title="Turns the typed values into part of the page; nobody can change them afterwards"><input type="checkbox" id="pf-lock"><span class="switch-track"></span>Lock fields</label>
        <button class="btn btn-sm btn-ghost" id="pf-clear" title="Empty every field">${ic('eraser', 14)}Clear form</button>
        <span class="an-spacer"></span>
        <span class="an-pageno"><span>Page</span><span id="pf-pg">1</span><span>/ ${r.pages.length}</span></span>
        <span class="toolbar-sep"></span>
        <button class="icon-btn" data-z="-1" data-tooltip="Zoom out · Ctrl+−" aria-label="Zoom out">${ic('zoom-out')}</button>
        <button class="icon-btn" data-z="1" data-tooltip="Zoom in · Ctrl+=" aria-label="Zoom in">${ic('zoom-in')}</button>
        <button class="icon-btn" data-z="0" data-tooltip="Fit width · Ctrl+0" aria-label="Fit width">${ic('fit-width')}</button>
      </div>
      ${xfa === 'dynamic' ? `<div class="pf-banner pf-warn">${ic('alert-triangle', 16)}<span>This is an Adobe XFA form. It can only be filled in Adobe Acrobat or Adobe Reader.</span></div>` : ''}
      ${xfa === 'hybrid' ? `<div class="pf-banner">${ic('info', 16)}<span>This form also has an Adobe XFA copy. It is removed when you fill it, so every PDF viewer shows your values.</span></div>` : ''}
      ${!fields.length && xfa !== 'dynamic' ? `<div class="pf-banner">${ic('info', 16)}<span>This PDF has no fillable fields. Use <b>Text box</b> or <b>Add text</b> to type on it.</span></div>` : ''}
      <div class="st-read-view pf-view" tabindex="-1"><div class="st-read-pages pf-pages"></div></div>`;
    embed.host.appendChild(el);
    const $ = s => el.querySelector(s);
    const view = $('.pf-view'), pagesEl = $('.pf-pages');
    if (!fillable) ['#pf-lock', '#pf-clear'].forEach(s => { $(s).disabled = true; });

    const changed = () => Object.keys(vals).filter(id => vals[id] !== orig[id]);
    const isEmpty = f => f.type === 'checkbox' ? !vals[f.id] : f.type === 'radio'
      ? !fields.some(g => g.type === 'radio' && g.name === f.name && vals[g.id]) : !String(vals[f.id] ?? '').trim();
    function updCount() {
      const req = fields.filter(f => f.required && !f.readonly);
      const left = req.filter(isEmpty).length;
      $('#pf-count').textContent = `${fields.length} field${fields.length === 1 ? '' : 's'}` +
        (req.length ? ` · ${left ? left + ' required still empty' : 'all required filled'}` : '');
      el.querySelectorAll('.pf-f.pf-req').forEach(b => b.classList.toggle('pf-empty', isEmpty(byId[b.dataset.id])));
    }

    // ── field inputs ───────────────────────────────────────────────────────
    function makeField(f, pg) {
      const box = document.createElement('div');
      box.className = `pf-f pf-${f.type}` + (f.required ? ' pf-req' : '') + (f.readonly ? ' pf-ro' : '');
      box.dataset.id = f.id;
      const [x0, y0, x1, y1] = f.rect;
      box.style.cssText = `left:${x0 / pg.w * 100}%;top:${y0 / pg.h * 100}%;width:${(x1 - x0) / pg.w * 100}%;height:${(y1 - y0) / pg.h * 100}%;` +
        `--pt:${f.font_size || (f.multiline ? 10 : Math.max(6, Math.min(12, (y1 - y0) * 0.68)))}`;
      const tip = [f.label || f.name, f.required ? 'Required' : '', (f.format || '').startsWith('date:') ? 'Date: ' + f.format.slice(5) : ''].filter(Boolean).join(' · ');
      box.title = tip;
      const dis = f.readonly || xfa === 'dynamic' ? ' disabled' : '';
      const aria = `aria-label="${esc(f.label || f.name)}"${f.required ? ' aria-required="true"' : ''}`;
      if (f.type === 'checkbox') box.innerHTML = `<input type="checkbox" ${aria}${dis}>`;
      else if (f.type === 'radio') box.innerHTML = `<input type="radio" name="pf-${esc(f.name)}" ${aria}${dis}>`;
      else if (f.type === 'combo' || f.type === 'list') {
        const opts = f.options.slice();
        if (vals[f.id] && !opts.includes(vals[f.id])) opts.unshift(vals[f.id]);
        box.innerHTML = `<select ${aria}${dis}${f.type === 'list' ? ' size="' + Math.max(2, Math.min(opts.length, 6)) + '"' : ''}>` +
          (f.type === 'combo' ? '<option value=""></option>' : '') + opts.map(o => `<option>${esc(o)}</option>`).join('') + '</select>';
      } else {
        const date = (f.format || '').startsWith('date:');
        const ph = date ? f.format.slice(5) : '';
        const attrs = `${aria}${dis}${f.maxlen ? ` maxlength="${f.maxlen}"` : ''}${ph ? ` placeholder="${esc(ph)}"` : ''}` +
          `${f.format === 'number' ? ' inputmode="decimal"' : ''} spellcheck="false"`;
        box.innerHTML = f.multiline ? `<textarea ${attrs}></textarea>` : `<input type="${f.password ? 'password' : 'text'}" ${attrs}>`;
        if (f.comb && f.maxlen) box.querySelector('input').style.letterSpacing = `calc(${(x1 - x0) / f.maxlen}px * var(--s) - 0.6em)`;
        if (date && !dis) {
          box.insertAdjacentHTML('beforeend', `<button class="pf-cal" tabindex="-1" title="Pick a date" aria-label="Pick a date">${ic('calendar', 12)}</button><input type="date" class="pf-date" tabindex="-1" aria-hidden="true">`);
          const dp = box.querySelector('.pf-date');
          box.querySelector('.pf-cal').addEventListener('click', e => { e.preventDefault(); try { dp.showPicker(); } catch { dp.focus(); dp.click(); } });
          dp.addEventListener('change', () => {
            if (!dp.value) return;
            const [y, m, d] = dp.value.split('-').map(Number);
            setVal(f.id, fmtDate(new Date(y, m - 1, d), f.format.slice(5)), true);
          });
        }
      }
      return box;
    }
    const inputOf = id => el.querySelector(`.pf-f[data-id="${id}"] input:not(.pf-date), .pf-f[data-id="${id}"] textarea, .pf-f[data-id="${id}"] select`);
    function show(id) {
      const f = byId[id], inp = inputOf(id);
      if (!inp) return;
      if (f.type === 'checkbox' || f.type === 'radio') inp.checked = !!vals[id];
      else inp.value = vals[id] ?? '';
    }
    // Set a value (record=true pushes one undo entry; radio switches its group)
    function setVal(id, v, record = true) {
      endTyping();
      const f = byId[id];
      const ch = [];
      if (f.type === 'radio' && v) fields.filter(g => g.type === 'radio' && g.name === f.name && g.id !== id && vals[g.id])
        .forEach(g => ch.push([g.id, vals[g.id], false]));
      if (vals[id] !== v) ch.push([id, vals[id], v]);
      if (!ch.length) { show(id); return; }
      ch.forEach(([i, , nv]) => { vals[i] = nv; show(i); });
      if (record) { undoStack.push(ch); redoStack = []; }
      updCount(); embed.onChange();
    }
    function applyEntry(ch, back) {
      ch.forEach(([i, ov, nv]) => { vals[i] = back ? ov : nv; show(i); });
      const first = inputOf(ch[ch.length - 1][0]);
      if (first) first.scrollIntoView({ block: 'nearest' });
      updCount(); embed.onChange();
    }

    // Typing updates the value live; one undo step per visit to a field.
    let typed = null;                       // {id, before}
    const isTextIn = t => (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') && !/^(checkbox|radio|date)$/.test(t.type);
    function endTyping() {
      if (!typed) return;
      const { id, before } = typed;
      typed = null;
      if (vals[id] !== before) { undoStack.push([[id, before, vals[id]]]); redoStack = []; embed.onChange(); }
    }
    pagesEl.addEventListener('input', e => {
      const t = e.target, box = t.closest('.pf-f'); if (!box || !isTextIn(t)) return;
      if (!typed || typed.id !== box.dataset.id) { endTyping(); typed = { id: box.dataset.id, before: vals[box.dataset.id] }; }
      vals[box.dataset.id] = t.value;
      updCount(); embed.onChange();
    });
    pagesEl.addEventListener('change', e => {
      const box = e.target.closest('.pf-f'); if (!box || e.target.classList.contains('pf-date')) return;
      if (isTextIn(e.target)) { endTyping(); return; }
      const f = byId[box.dataset.id];
      setVal(f.id, f.type === 'checkbox' || f.type === 'radio' ? e.target.checked : e.target.value);
    });
    // a checked radio can be clicked off again, like Acrobat
    pagesEl.addEventListener('pointerdown', e => {
      const t = e.target;
      if (t.type === 'radio' && t.checked) { t.dataset.was = '1'; }
    });
    pagesEl.addEventListener('click', e => {
      const t = e.target;
      if (t.type === 'radio' && t.dataset.was) { delete t.dataset.was; t.checked = false; setVal(t.closest('.pf-f').dataset.id, false); }
    });
    pagesEl.addEventListener('focusin', e => {
      const box = e.target.closest('.pf-f'); if (!box) return;
      const p = +box.closest('.pf-page').dataset.page;
      if (p !== curPage) { curPage = p; $('#pf-pg').textContent = p + 1; }
    });

    // ── pages ──────────────────────────────────────────────────────────────
    const fitW = () => Math.max(280, Math.round(view.clientWidth - 48));
    function layout() {
      const w = fitW() * zoom;
      el.querySelectorAll('.pf-page').forEach(pe => {
        const pg = r.pages[+pe.dataset.page];
        pe.style.width = w + 'px'; pe.style.height = Math.round(w * pg.h / pg.w) + 'px';
        pe.style.setProperty('--s', (w / pg.w).toFixed(4));
      });
    }
    function build() {
      const g = ++gen;
      if (obs) obs.disconnect();
      pagesEl.innerHTML = '';
      const dpi = Math.max(110, Math.min(220, Math.round(fitW() * Math.min(window.devicePixelRatio || 1, 2) / 8.5)));
      r.pages.forEach((pg, i) => {
        const pe = document.createElement('div');
        pe.className = 'pf-page st-read-page';
        pe.dataset.page = i;
        pe.innerHTML = '<img class="pf-img" alt="" draggable="false">';
        fields.filter(f => f.page === i).forEach(f => pe.appendChild(makeField(f, pg)));
        pagesEl.appendChild(pe);
      });
      fields.forEach(f => show(f.id));
      layout();
      obs = new IntersectionObserver(async ents => {
        for (const en of ents) {
          if (!en.isIntersecting) continue;
          const pe = en.target, p = +pe.dataset.page;
          obs.unobserve(pe);
          const res = await SFM.formPagePng(file, p, dpi).catch(() => null);
          if (closed || g !== gen || !res || !res.ok) continue;
          pe.querySelector('.pf-img').src = res.data_url;
        }
      }, { root: view, rootMargin: '600px' });
      pagesEl.querySelectorAll('.pf-page').forEach(pe => obs.observe(pe));
      updCount();
    }
    function goPage(p) {
      p = Math.max(0, Math.min(r.pages.length - 1, p | 0));
      const pe = pagesEl.children[p]; if (pe) view.scrollTop = pe.offsetTop - 12;
      curPage = p; $('#pf-pg').textContent = p + 1;
    }
    view.addEventListener('scroll', () => {
      const top = view.scrollTop + 40;
      let p = 0;
      for (const n of pagesEl.children) { if (n.offsetTop <= top) p = +n.dataset.page; else break; }
      if (p !== curPage) { curPage = p; $('#pf-pg').textContent = p + 1; }
    });
    function setZoom(z) { const p = curPage; zoom = z ? Math.max(0.5, Math.min(3, zoom * (z > 0 ? 1.2 : 1 / 1.2))) : 1; layout(); goPage(p); }
    el.querySelectorAll('[data-z]').forEach(b => b.addEventListener('click', () => setZoom(+b.dataset.z)));
    let rz = null;
    const ro = new ResizeObserver(() => { clearTimeout(rz); rz = setTimeout(() => { if (!closed) layout(); }, 80); });
    ro.observe(view);

    // ── toolbar ────────────────────────────────────────────────────────────
    $('#pf-hl').addEventListener('change', e => el.classList.toggle('pf-hl', e.target.checked));
    $('#pf-lock').addEventListener('change', e => { lock = e.target.checked; embed.onChange(); });
    $('#pf-clear').addEventListener('click', () => {
      endTyping();
      const ch = fields.filter(f => !f.readonly && !isEmptyVal(vals[f.id])).map(f => [f.id, vals[f.id], typeof vals[f.id] === 'boolean' ? false : '']);
      if (!ch.length) { App.toast('The form is already empty', 'info', 1500); return; }
      ch.forEach(([i, , nv]) => { vals[i] = nv; show(i); });
      undoStack.push(ch); redoStack = [];
      updCount(); embed.onChange();
    });
    const isEmptyVal = v => v === false || v === '' || v == null;

    // ── keyboard ───────────────────────────────────────────────────────────
    function onKey(e) {
      if (closed || !document.body.contains(el)) return;
      if (document.querySelector('.modal-overlay.dlg-top')) return;
      const t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
      const ctrl = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase();
      if (k === 'tab') {                          // Tab walks the fields (the page's own order)
        e.stopPropagation();
        if (!typing || !el.contains(t)) {
          const first = fields.find(f => !f.readonly);
          if (first && xfa !== 'dynamic') { e.preventDefault(); inputOf(first.id).focus(); }
        }
        return;
      }
      e.stopPropagation();
      const handled = () => e.preventDefault();
      if (ctrl && k === 's') { handled(); flush(); embed.onKey('save'); return; }
      if (k === 'escape') { handled(); if (typing) { t.blur(); view.focus(); } else embed.onKey('close'); return; }
      if (ctrl && (k === '=' || k === '+')) { handled(); setZoom(1); return; }
      if (ctrl && k === '-') { handled(); setZoom(-1); return; }
      if (ctrl && k === '0') { handled(); setZoom(0); return; }
      if (typing && (t.tagName !== 'INPUT' || !/^(checkbox|radio)$/.test(t.type))) return;
      if (ctrl && k === 'z' && !e.shiftKey) { handled(); if (undoStack.length) ctl.undo(); else embed.onKey('undo'); return; }
      if (ctrl && (k === 'y' || (k === 'z' && e.shiftKey))) { handled(); if (redoStack.length) ctl.redo(); else embed.onKey('redo'); return; }
      if (!typing && k === 'pagedown') { handled(); goPage(curPage + 1); return; }
      if (!typing && k === 'pageup') { handled(); goPage(curPage - 1); return; }
    }
    document.addEventListener('keydown', onKey, true);

    // finish the field being typed in (one undo step)
    function flush() { endTyping(); }

    build();
    goPage(curPage);

    const ctl = {
      kind: 'form',
      dirty: () => changed().length > 0 || lock,
      flush,
      busy: () => false,
      canUndo: () => undoStack.length > 0 || !!(typed && vals[typed.id] !== typed.before),
      canRedo: () => redoStack.length > 0,
      undo() { endTyping(); const ch = undoStack.pop(); if (ch) { redoStack.push(ch); applyEntry(ch, true); } },
      redo() { const ch = redoStack.pop(); if (ch) { undoStack.push(ch); applyEntry(ch, false); } },
      async commit(outPath) {
        flush();
        const values = Object.fromEntries(changed().map(id => [id, vals[id]]));
        const res = await SFM.formFill(file, values, outPath, lock);
        if (!res || !res.ok) return res || { ok: false, error: 'Could not fill the form' };
        const left = fields.filter(f => f.required && !f.readonly && isEmpty(f)).length;
        const warnings = left ? [`${left} required field${left === 1 ? ' is' : 's are'} still empty`] : [];
        return { ok: true, out_path: res.out_path, warnings };
      },
      page: () => curPage,
      goPage,
      focus() { const a = document.activeElement; if (!(a && el.contains(a))) view.focus({ preventScroll: true }); },
      close() { closed = true; if (obs) obs.disconnect(); ro.disconnect(); document.removeEventListener('keydown', onKey, true); el.remove(); },
    };
    return ctl;
  }

  return { open, fmtDate };
})();
