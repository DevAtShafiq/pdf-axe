/**
 * photo-tools.js — Crop dialog (Cropper) and AI "Wear Suit & Tie" (AiPhoto).
 *
 *   Cropper.open(path)            crop box with handles, aspect presets, rotate,
 *                                 keyboard nudge; saves <name>_cropped.<ext>
 *   AiPhoto.run(path, action)     gated AI edit; result <name>_suit.<ext>
 *   AiPhoto.compare(src, out)     before / after slider
 *   PhotoTools.revealFile(path)   reload the folder and select a new file
 */

const PhotoTools = (() => {
  const norm = p => String(p || '').replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
  const dirOf = p => String(p || '').replace(/[\\/][^\\/]*$/, '');
  const base = p => String(p || '').split(/[\\/]/).pop();
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function inCurrentFolder(p) {
    const cur = App.state.currentFolder;
    return !!cur && norm(cur) === norm(dirOf(p));
  }
  // Reload the current folder (if *path* is in it) and select *path*.
  async function revealFile(path) {
    if (!inCurrentFolder(path)) { return false; }
    try { await FileTree.loadFolder(App.state.currentFolder); } catch (_) { return false; }
    const el = Array.from(document.querySelectorAll(
      '#filelist-list .file-item[data-path], #filelist-thumb .thumb-tile[data-path]'))
      .find(e => norm(e.dataset.path) === norm(path) && e.offsetParent !== null);
    if (el) { el.click(); el.scrollIntoView({ block: 'nearest' }); return true; }
    return false;
  }
  return { revealFile, inCurrentFolder, esc, base, norm };
})();


// ════════════════════════════════════════════════════════════════════════════
// Cropper
// ════════════════════════════════════════════════════════════════════════════
const Cropper = (() => {
  const PRESETS = [
    { key: 'free',  label: 'Free',     r: null },
    { key: 'orig',  label: 'Original', r: 'orig' },
    { key: '1:1',   label: '1:1',      r: 1 },
    { key: '3:4',   label: '3:4 passport', r: 3 / 4 },
    { key: '35x45', label: '35×45 mm', r: 35 / 45 },
    { key: '4:6',   label: '4:6',      r: 4 / 6 },
    { key: '16:9',  label: '16:9',     r: 16 / 9 },
    { key: 'custom', label: 'Custom',  r: 'custom' },
  ];
  const MIN = 8;          // minimum crop size in full-resolution px
  const HANDLE = 9;       // handle hit radius in screen px

  async function open(path) {
    App.setStatus('Loading image…', true);
    const src = await SFM.getCropSource(path, 1600).catch(e => ({ ok: false, error: String(e) }));
    App.setStatus('Ready');
    if (!src || !src.ok) { App.toast('Cannot load image: ' + ((src && src.error) || 'unknown'), 'error'); return; }

    const id = Dialogs.modal('crop-image', {
      title: 'Crop image',
      icon: 'crop',
      subtitle: PhotoTools.base(path) + ' · saved as a new file',
      width: '900px',
      cls: 'crop-modal',
      extraStyle: 'max-height:94vh;display:flex;flex-direction:column;',
      body: '',
      footer: `<span class="crop-hint footer-left"><span class="kbd">Drag</span> draw · <span class="kbd">Arrows</span> nudge
                 (<span class="kbd">Shift</span> ×10, <span class="kbd">Alt</span> resize) · <span class="kbd">Enter</span> save</span>
               <button class="btn" id="cr-cancel-__ID__">Cancel</button>
               <button class="btn btn-primary" id="cr-save-__ID__">${Icons.svg('check', 16)}Save as new file</button>`,
    });
    const root = document.getElementById('mo-' + id);
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
    const body = root.querySelector('.modal-body');
    body.style.cssText = 'padding:0;display:flex;flex-direction:column;flex:1;min-height:0;gap:0;';
    body.innerHTML = `
      <div class="crop-bar">
        <span class="crop-lbl">Aspect</span>
        <div class="segmented crop-presets" role="radiogroup" aria-label="Aspect ratio">
          ${PRESETS.map(p => `<button type="button" class="seg-btn" data-preset="${p.key}">${p.label}</button>`).join('')}
        </div>
        <span id="cr-custom-${id}" class="crop-custom" style="display:none">
          <input class="input-text" id="cr-cw-${id}" type="number" min="1" value="2" title="Width ratio" aria-label="Width ratio">
          <span class="crop-lbl">:</span>
          <input class="input-text" id="cr-ch-${id}" type="number" min="1" value="3" title="Height ratio" aria-label="Height ratio">
        </span>
        <button class="icon-btn" id="cr-flip-${id}" data-tooltip="Swap portrait / landscape" aria-label="Swap portrait / landscape">${Icons.svg('arrow-left-right', 16)}</button>
        <span class="toolbar-sep toolbar-sep-sm"></span>
        <button class="icon-btn" id="cr-rl-${id}" data-tooltip="Rotate left 90°" aria-label="Rotate left">${Icons.svg('rotate-ccw', 16)}</button>
        <button class="icon-btn" id="cr-rr-${id}" data-tooltip="Rotate right 90°" aria-label="Rotate right">${Icons.svg('rotate-cw', 16)}</button>
        <button class="icon-btn" id="cr-reset-${id}" data-tooltip="Reset" aria-label="Reset">${Icons.svg('undo', 16)}</button>
      </div>
      <div class="crop-stage" id="cr-stage-${id}">
        <canvas id="cr-canvas-${id}" tabindex="0"></canvas>
      </div>
      <div class="crop-readout" id="cr-read-${id}"></div>`;
    const $ = s => document.getElementById(s + '-' + id);
    const canvas = $('cr-canvas'), ctx = canvas.getContext('2d'), stage = $('cr-stage');
    const readout = $('cr-read'), saveBtn = $('cr-save');

    // ── state (all in FULL-resolution px of the rotated image) ─────────────
    const img = new Image();
    const fullW0 = src.width, fullH0 = src.height;
    let rot = 0;                         // 0/90/180/270 clockwise
    let W = fullW0, H = fullH0;
    let rect = { x: 0, y: 0, w: W, h: H };
    let ratio = null;                    // w/h or null
    let presetKey = 'free';
    let view = { s: 1, dw: 0, dh: 0 };   // display scale (screen px per full px)
    let drag = null;
    let rotated = null;                  // preview canvas rotated by `rot`

    function buildRotated() {
      const pw = img.naturalWidth, ph = img.naturalHeight;
      const c = document.createElement('canvas');
      const swap = rot % 180 !== 0;
      c.width = swap ? ph : pw; c.height = swap ? pw : ph;
      const g = c.getContext('2d');
      g.translate(c.width / 2, c.height / 2);
      g.rotate(rot * Math.PI / 180);
      g.drawImage(img, -pw / 2, -ph / 2);
      rotated = c;
    }

    function layout() {
      const availW = Math.max(200, stage.clientWidth - 24);
      const availH = Math.max(220, window.innerHeight * 0.94 - 280);   // minus header, bars, readout, footer
      const s = Math.min(availW / W, availH / H);
      const dpr = window.devicePixelRatio || 1;
      view = { s, dw: Math.round(W * s), dh: Math.round(H * s) };
      canvas.style.width = view.dw + 'px'; canvas.style.height = view.dh + 'px';
      canvas.width = Math.round(view.dw * dpr); canvas.height = Math.round(view.dh * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw();
    }

    function draw() {
      const { s, dw, dh } = view;
      ctx.clearRect(0, 0, dw, dh);
      if (rotated) { ctx.imageSmoothingQuality = 'high'; ctx.drawImage(rotated, 0, 0, dw, dh); }
      const x = rect.x * s, y = rect.y * s, w = rect.w * s, h = rect.h * s;
      ctx.fillStyle = 'rgba(0,0,0,.55)';
      ctx.fillRect(0, 0, dw, y); ctx.fillRect(0, y + h, dw, dh - y - h);
      ctx.fillRect(0, y, x, h); ctx.fillRect(x + w, y, dw - x - w, h);
      ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 1;
      for (let i = 1; i < 3; i++) {   // rule of thirds
        ctx.beginPath(); ctx.moveTo(x + w * i / 3, y); ctx.lineTo(x + w * i / 3, y + h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x, y + h * i / 3); ctx.lineTo(x + w, y + h * i / 3); ctx.stroke();
      }
      const acc = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '').trim() || 'blue';
      ctx.strokeStyle = acc; ctx.lineWidth = 2;
      ctx.strokeRect(x, y, w, h);
      handles().forEach(hd => {
        ctx.fillStyle = acc; ctx.fillRect(hd.x - 5, hd.y - 5, 10, 10);
        ctx.fillStyle = 'rgba(255,255,255,.95)'; ctx.fillRect(hd.x - 3, hd.y - 3, 6, 6);
      });
      const seg = (k, v) => `<span class="crop-ro"><span class="crop-ro-k">${k}</span>${v}</span>`;
      readout.innerHTML = seg('Selection', `${Math.round(rect.w)} × ${Math.round(rect.h)} px`) +
        seg('Position', `${Math.round(rect.x)}, ${Math.round(rect.y)}`) +
        seg('Image', `${W} × ${H}${rot ? ` · rotated ${rot}°` : ''}`) +
        (ratio ? seg('Ratio', (rect.w / rect.h).toFixed(3)) : '');
      saveBtn.disabled = rect.w < 1 || rect.h < 1;
    }

    function handles() {
      const { s } = view;
      const x = rect.x * s, y = rect.y * s, w = rect.w * s, h = rect.h * s;
      return [
        { k: 'nw', x, y }, { k: 'n', x: x + w / 2, y }, { k: 'ne', x: x + w, y },
        { k: 'e', x: x + w, y: y + h / 2 }, { k: 'se', x: x + w, y: y + h },
        { k: 's', x: x + w / 2, y: y + h }, { k: 'sw', x, y: y + h }, { k: 'w', x, y: y + h / 2 },
      ];
    }
    const CURSORS = { nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize',
                      n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize', move: 'move' };

    function hit(px, py) {
      const hd = handles().find(h => Math.abs(h.x - px) <= HANDLE && Math.abs(h.y - py) <= HANDLE);
      if (hd) return hd.k;
      const { s } = view;
      if (px >= rect.x * s && px <= (rect.x + rect.w) * s && py >= rect.y * s && py <= (rect.y + rect.h) * s) return 'move';
      return null;
    }

    // Largest rect of `ratio` that fits W×H, centred (or the whole image when free).
    function fitRect(r, frac = 1) {
      if (!r) return { x: 0, y: 0, w: W, h: H };
      let w = W * frac, h = w / r;
      if (h > H * frac) { h = H * frac; w = h * r; }
      return { x: (W - w) / 2, y: (H - h) / 2, w, h };
    }

    function clampMove(r) {
      r.x = Math.max(0, Math.min(W - r.w, r.x));
      r.y = Math.max(0, Math.min(H - r.h, r.y));
      return r;
    }

    // Resize from handle k with the pointer at full-res (fx, fy), honouring `ratio`.
    function resize(k, fx, fy, start) {
      let { x, y, w, h } = start;
      let left = x, top = y, right = x + w, bottom = y + h;
      fx = Math.max(0, Math.min(W, fx)); fy = Math.max(0, Math.min(H, fy));
      if (k.includes('w')) left = Math.min(fx, right - MIN);
      if (k.includes('e')) right = Math.max(fx, left + MIN);
      if (k.includes('n')) top = Math.min(fy, bottom - MIN);
      if (k.includes('s')) bottom = Math.max(fy, top + MIN);
      let nw = right - left, nh = bottom - top;
      if (ratio) {
        const corner = k.length === 2;
        if (corner) {
          // follow the larger movement, then derive the other side
          if (nw / nh > ratio) nh = nw / ratio; else nw = nh * ratio;
          // limit to the image from the anchored corner
          const maxW = k.includes('w') ? right : W - left;
          const maxH = k.includes('n') ? bottom : H - top;
          if (nw > maxW) { nw = maxW; nh = nw / ratio; }
          if (nh > maxH) { nh = maxH; nw = nh * ratio; }
          if (k.includes('w')) left = right - nw; else right = left + nw;
          if (k.includes('n')) top = bottom - nh; else bottom = top + nh;
        } else if (k === 'e' || k === 'w') {
          nh = nw / ratio;
          const cy = (start.y + start.h / 2);
          const maxH = 2 * Math.min(cy, H - cy);
          if (nh > maxH) { nh = maxH; nw = nh * ratio; if (k === 'w') left = right - nw; else right = left + nw; }
          top = cy - nh / 2; bottom = top + nh;
        } else {
          nw = nh * ratio;
          const cx = (start.x + start.w / 2);
          const maxW = 2 * Math.min(cx, W - cx);
          if (nw > maxW) { nw = maxW; nh = nw / ratio; if (k === 'n') top = bottom - nh; else bottom = top + nh; }
          left = cx - nw / 2; right = left + nw;
        }
      }
      rect = { x: left, y: top, w: right - left, h: bottom - top };
    }

    // New rect dragged from (ax, ay) to (fx, fy).
    function drawNew(ax, ay, fx, fy) {
      fx = Math.max(0, Math.min(W, fx)); fy = Math.max(0, Math.min(H, fy));
      let w = Math.abs(fx - ax), h = Math.abs(fy - ay);
      if (ratio) {
        if (w / Math.max(h, 1e-6) > ratio) h = w / ratio; else w = h * ratio;
        const maxW = fx >= ax ? W - ax : ax, maxH = fy >= ay ? H - ay : ay;
        if (w > maxW) { w = maxW; h = w / ratio; }
        if (h > maxH) { h = maxH; w = h * ratio; }
      }
      rect = { x: fx >= ax ? ax : ax - w, y: fy >= ay ? ay : ay - h, w, h };
    }

    function toFull(e) {
      const rc = canvas.getBoundingClientRect();
      return { px: e.clientX - rc.left, py: e.clientY - rc.top,
               fx: (e.clientX - rc.left) / view.s, fy: (e.clientY - rc.top) / view.s };
    }

    canvas.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      canvas.focus();
      const p = toFull(e);
      const k = hit(p.px, p.py);
      drag = { k: k || 'new', start: { ...rect }, ax: Math.max(0, Math.min(W, p.fx)),
               ay: Math.max(0, Math.min(H, p.fy)), ox: p.fx, oy: p.fy, moved: false };
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', e => {
      const p = toFull(e);
      if (!drag) { canvas.style.cursor = CURSORS[hit(p.px, p.py)] || 'crosshair'; return; }
      if (!drag.moved && Math.abs(p.fx - drag.ox) * view.s < 3 && Math.abs(p.fy - drag.oy) * view.s < 3) return;
      drag.moved = true;
      if (drag.k === 'move') {
        rect = clampMove({ ...drag.start, x: drag.start.x + p.fx - drag.ox, y: drag.start.y + p.fy - drag.oy });
      } else if (drag.k === 'new') {
        drawNew(drag.ax, drag.ay, p.fx, p.fy);
      } else {
        resize(drag.k, p.fx, p.fy, drag.start);
      }
      draw();
    });
    const endDrag = () => {
      if (!drag) return;
      if (drag.k === 'new' && (!drag.moved || rect.w < MIN || rect.h < MIN)) rect = drag.start;
      drag = null; draw();
    };
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);

    // ── presets / rotation ────────────────────────────────────────────────
    function customRatio() {
      const a = parseFloat($('cr-cw').value), b = parseFloat($('cr-ch').value);
      return (a > 0 && b > 0) ? a / b : null;
    }
    function setPreset(key, keepRect) {
      presetKey = key;
      const p = PRESETS.find(x => x.key === key);
      ratio = p.r === 'orig' ? W / H : p.r === 'custom' ? customRatio() : p.r;
      root.querySelectorAll('[data-preset]').forEach(b => b.classList.toggle('active', b.dataset.preset === key));
      $('cr-flip').disabled = !ratio;
      $('cr-custom').style.display = key === 'custom' ? 'inline-flex' : 'none';
      if (!keepRect) rect = fitRect(ratio, ratio ? 0.9 : 1);
      draw();
    }
    root.querySelectorAll('[data-preset]').forEach(b => b.addEventListener('click', () => setPreset(b.dataset.preset)));
    ['cr-cw', 'cr-ch'].forEach(k => $(k).addEventListener('input', () => { if (presetKey === 'custom') setPreset('custom'); }));
    $('cr-flip').addEventListener('click', () => {
      if (!ratio) return;
      if (presetKey === 'custom') { const a = $('cr-cw').value; $('cr-cw').value = $('cr-ch').value; $('cr-ch').value = a; }
      ratio = 1 / ratio;
      const cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
      let w = rect.h, h = rect.w;
      const f = Math.min(1, W / w, H / h); w *= f; h *= f;
      rect = clampMove({ x: cx - w / 2, y: cy - h / 2, w, h });
      draw();
    });

    function rotate(dir) {   // dir: +90 clockwise, -90 counter-clockwise
      const r = rect, oW = W, oH = H;
      rot = (rot + dir + 360) % 360;
      W = oH; H = oW;
      rect = dir > 0 ? { x: oH - (r.y + r.h), y: r.x, w: r.h, h: r.w }
                     : { x: r.y, y: oW - (r.x + r.w), w: r.h, h: r.w };
      if (ratio) ratio = (presetKey === 'orig') ? W / H : 1 / ratio;
      buildRotated(); layout();
    }
    $('cr-rl').addEventListener('click', () => rotate(-90));
    $('cr-rr').addEventListener('click', () => rotate(90));

    $('cr-reset').addEventListener('click', () => {
      const needRebuild = rot !== 0;
      rot = 0; W = fullW0; H = fullH0;
      if (needRebuild) buildRotated();
      setPreset('free'); layout();
    });
    $('cr-cancel').addEventListener('click', () => Dialogs.closeModal(id));

    // ── keyboard ──────────────────────────────────────────────────────────
    const onKey = e => {
      if (!document.getElementById('mo-' + id)) return;
      const top = Array.from(document.querySelectorAll('.modal-overlay')).pop();
      if (top !== root) return;
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
      const dirs = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      if (dirs[e.key]) {
        e.preventDefault();
        const step = e.shiftKey ? Math.max(10, 10 / view.s) : Math.max(1, 1 / view.s);
        const [dx, dy] = dirs[e.key];
        if (e.altKey || e.ctrlKey) {
          const start = { ...rect };     // Alt/Ctrl+arrows move the right / bottom edge
          if (dx) resize('e', rect.x + rect.w + dx * step, 0, start);
          else resize('s', 0, rect.y + rect.h + dy * step, start);
        } else {
          rect = clampMove({ ...rect, x: rect.x + dx * step, y: rect.y + dy * step });
        }
        draw();
      } else if (e.key === 'Enter') {
        e.preventDefault(); save();
      }
    };
    document.addEventListener('keydown', onKey);
    const onResize = () => layout();
    window.addEventListener('resize', onResize);
    const obs = new MutationObserver(() => {
      if (!document.getElementById('mo-' + id)) {
        document.removeEventListener('keydown', onKey);
        window.removeEventListener('resize', onResize);
        obs.disconnect();
      }
    });
    obs.observe(document.body, { childList: true });

    // ── save ──────────────────────────────────────────────────────────────
    let saving = false;
    async function save() {
      if (saving || rect.w < 1 || rect.h < 1) return;
      saving = true;
      saveBtn.disabled = true; saveBtn.classList.add('is-loading');
      const x = Math.round(rect.x), y = Math.round(rect.y);
      const w = Math.round(rect.x + rect.w) - x, h = Math.round(rect.y + rect.h) - y;
      const r = await SFM.cropImage(path, x, y, w, h, '', rot).catch(e => ({ ok: false, error: String(e) }));
      saving = false;
      if (r && r.ok) {
        App.toast('Cropped → ' + PhotoTools.esc(PhotoTools.base(r.out)), 'success', 4000);
        Dialogs.closeModal(id);
        if (!(await PhotoTools.revealFile(r.out))) { try { FileTree.refresh(); } catch (_) {} }
      } else {
        App.toast('Crop failed: ' + PhotoTools.esc((r && r.error) || 'unknown error'), 'error', 6000);
        saveBtn.disabled = false; saveBtn.classList.remove('is-loading');
      }
    }
    saveBtn.addEventListener('click', save);

    img.onload = () => { buildRotated(); setPreset('free'); layout(); canvas.focus(); };
    img.onerror = () => App.toast('Could not display the image preview', 'error');
    img.src = src.data_url;
  }

  return { open };
})();


// ════════════════════════════════════════════════════════════════════════════
// AiPhoto — "Wear Suit & Tie"
// ════════════════════════════════════════════════════════════════════════════
const AiPhoto = (() => {
  const LABELS = { wear_suit: 'Wear Suit & Tie' };
  const PREF_KEY = 'sfm.aiPhoto.opts';
  const jobs = new Map();       // job_id → {path, action, started, resolve}
  const pairs = new Map();      // norm(result) → source path (for Compare)
  const early = new Map();      // results that arrived before run() registered the job
  let currentPath = null;
  let ticker = null;

  const $ = id => document.getElementById(id);
  const norm = PhotoTools.norm;

  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}') || {}; } catch (_) { return {}; }
  }
  function savePrefs(o) { try { localStorage.setItem(PREF_KEY, JSON.stringify(o)); } catch (_) {} }

  function getOpts() {
    const p = loadPrefs();
    const o = { suit_color: p.suit_color || 'black', tie: p.tie !== false, quality: p.quality || 'high' };
    if ($('ai-suit-color')) o.suit_color = $('ai-suit-color').value;
    if ($('ai-tie')) o.tie = !!$('ai-tie').checked;
    if ($('ai-quality')) o.quality = $('ai-quality').value;
    return o;
  }

  function jobFor(path) {
    for (const [jid, j] of jobs) if (norm(j.path) === norm(path)) return [jid, j];
    return null;
  }

  // ── details-panel button state ───────────────────────────────────────────
  function refreshButtons() {
    const btn = $('ai-wear-suit');
    const st = $('ai-photo-status');
    const running = currentPath ? jobFor(currentPath) : null;
    if (btn) {
      if (running || (btn.dataset.checking === '1')) {
        btn.disabled = true; btn.classList.add('is-busy');
        const secs = running ? Math.round((Date.now() - running[1].started) / 1000) : 0;
        btn.innerHTML = `<span class="spinner spinner-on-accent"></span>${running ? `Working… ${secs}s` : 'Checking…'}`;
      } else {
        btn.disabled = false; btn.classList.remove('is-busy');
        btn.innerHTML = Icons.svg('shirt', 16) + 'Wear suit &amp; tie';
      }
    }
    if (st) {
      const others = jobs.size - (running ? 1 : 0);
      st.textContent = running ? 'Usually 20–60 s. You can keep working — the result is saved as a new file.'
                     : others > 0 ? `${others} AI edit${others > 1 ? 's' : ''} running in the background…` : '';
    }
    const cmp = $('ai-compare');
    if (cmp) cmp.classList.toggle('hidden', !(currentPath && pairs.has(norm(currentPath))));
    // Plan hint: shown when an account server is configured but the plan is not active.
    const note = $('ai-plan-note');
    if (note) {
      let locked = false;
      try { locked = !!(typeof Account !== 'undefined' && Account.state && Account.state.configured && !Account.isActive()); } catch (_) {}
      note.classList.toggle('hidden', !locked);
    }
    if (jobs.size && !ticker) ticker = setInterval(refreshButtons, 1000);
    if (!jobs.size && ticker) { clearInterval(ticker); ticker = null; }
  }

  // Shows the output file name ("Saves as photo_suit.jpg"). No cost figures.
  async function refreshEstimate() {
    const el = $('ai-out-name');
    if (!el || !currentPath) return;
    const path = currentPath;
    const r = await SFM.aiPhotoEstimate(path, getOpts()).catch(() => null);
    if (path !== currentPath || !r || !r.ok || !r.out_name) { if (path === currentPath) el.innerHTML = ''; return; }
    el.innerHTML = `${Icons.svg('file-image', 12)}<span>Saves as <strong>${PhotoTools.esc(r.out_name)}</strong></span>`;
    el.title = 'The original photo is not changed';
  }

  // Swatches / segmented control mirror the hidden #ai-suit-color / #ai-quality selects.
  function syncControls() {
    const suit = $('ai-suit-color'), q = $('ai-quality');
    document.querySelectorAll('#ai-suit-swatches .swatch').forEach(b => {
      const on = suit && b.dataset.v === suit.value;
      b.classList.toggle('active', on); b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    const nm = $('ai-suit-name');
    if (nm && suit) nm.textContent = suit.options[suit.selectedIndex]?.text || '';
    document.querySelectorAll('#ai-quality-seg .seg-btn').forEach(b => b.classList.toggle('active', !!q && b.dataset.v === q.value));
  }
  function wireMirror(groupId, selectId) {
    const g = $(groupId), sel = $(selectId);
    if (!g || !sel) return;
    g.addEventListener('click', e => {
      const b = e.target.closest('[data-v]'); if (!b) return;
      sel.value = b.dataset.v;
      sel.dispatchEvent(new Event('change'));
      syncControls();
    });
  }

  // Called by Details when an image is shown in the details pane.
  function onShowFile(entry) {
    currentPath = entry && !entry.is_dir ? entry.path : null;
    refreshButtons();
    refreshEstimate();
  }

  // ── gate / key messages ──────────────────────────────────────────────────
  function showNeedPlan(r) {
    const login = !!(r && r.need_login);
    const id = Dialogs.modal('ai-need-plan', {
      title: login ? 'Sign in to use AI photo edits' : 'Subscription required',
      icon: 'lock',
      subtitle: 'Wear suit & tie',
      width: '460px',
      body: `<p class="ai-dlg-text">AI photo editing is part of the PDF Axe monthly plan.
               ${login ? 'Sign in to your account, then subscribe from the Account page.'
                       : 'Your account does not have an active subscription.'}</p>
             <ul class="ai-dlg-list">
               <li>${Icons.svg('check', 14)}AI photo edits such as Wear suit &amp; tie</li>
               <li>${Icons.svg('check', 14)}Cloud storage and automatic sync</li>
               <li>${Icons.svg('check', 14)}Cancel any time</li>
             </ul>`,
      footer: `<button class="btn" id="anp-cancel-__ID__">Not now</button>
               <button class="btn btn-primary" id="anp-go-__ID__">${Icons.svg(login ? 'log-in' : 'credit-card', 16)}${login ? 'Sign in' : 'Subscribe'}</button>`,
    });
    const root = document.getElementById('mo-' + id);
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
    $('anp-cancel-' + id).addEventListener('click', () => Dialogs.closeModal(id));
    $('anp-go-' + id).addEventListener('click', () => {
      Dialogs.closeModal(id);
      App.switchPanel('account');
      try { Account.refresh(); } catch (_) {}
    });
  }

  function showNeedKey(msg) {
    const id = Dialogs.modal('ai-need-key', {
      title: 'OpenAI API key needed',
      icon: 'key',
      subtitle: 'AI photo edits',
      width: '460px',
      body: `<p class="ai-dlg-text">${PhotoTools.esc(msg || 'Add your OpenAI API key in Settings to use AI photo editing.')}</p>
             <div class="field-hint">The key is stored on this computer only.</div>`,
      footer: `<button class="btn" id="ank-cancel-__ID__">Cancel</button>
               <button class="btn btn-primary" id="ank-go-__ID__">${Icons.svg('settings', 16)}Open Settings</button>`,
    });
    const root = document.getElementById('mo-' + id);
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
    $('ank-cancel-' + id).addEventListener('click', () => Dialogs.closeModal(id));
    $('ank-go-' + id).addEventListener('click', () => { Dialogs.closeModal(id); Dialogs.openSettings(); });
  }

  // ── run ──────────────────────────────────────────────────────────────────
  async function run(path, action = 'wear_suit', opts) {
    if (!path) return { ok: false, error: 'No image selected' };
    if (jobFor(path)) { App.toast('An AI edit is already running for this photo', 'warning'); return { ok: false, error: 'busy' }; }
    const label = LABELS[action] || action;
    const o = Object.assign(getOpts(), opts || {});
    const jobId = 'ai' + Date.now() + Math.floor(Math.random() * 1000);
    const btn = $('ai-wear-suit');
    const isCurrent = currentPath && norm(currentPath) === norm(path);
    if (btn && isCurrent) { btn.dataset.checking = '1'; refreshButtons(); }

    let r;
    try { r = await SFM.runAiPhoto(path, action, Object.assign({}, o, { job_id: jobId })); }
    catch (e) { r = { ok: false, error: String(e) }; }
    if (btn) { delete btn.dataset.checking; }

    if (!r || !r.ok) {
      refreshButtons();
      if (r && (r.need_subscription || r.need_login)) showNeedPlan(r);
      else if (r && r.need_api_key) showNeedKey(r.error);
      else App.toast(`${label}: ${PhotoTools.esc((r && r.error) || 'could not start')}`, 'error', 6000);
      return r || { ok: false };
    }

    const jid = r.job_id || jobId;
    const done = new Promise(resolve => {
      jobs.set(jid, { path, action, started: Date.now(), resolve, label });
    });
    if (early.has(jid)) { const ev = early.get(jid); early.delete(jid); onResult(ev); return done; }
    refreshButtons();
    App.toast(`${label} started — you can keep working`, 'info', 4000);
    return done;
  }

  async function onResult(r) {
    if (!r) return;
    const job = jobs.get(r.job_id);
    if (!job) {   // run() has not registered it yet (very fast failure) — keep it
      if (r.job_id) { early.set(r.job_id, r); setTimeout(() => early.delete(r.job_id), 60000); }
      return;
    }
    jobs.delete(r.job_id);
    refreshButtons();
    if (r.ok) {
      const src = r.src || job.path;
      pairs.set(norm(r.out), src);
      const name = PhotoTools.base(r.out);
      const key = 'aic' + Date.now();
      App.toast(`${job.label} saved → ${PhotoTools.esc(name)} &nbsp;<button class="btn btn-sm" id="${key}">Compare</button>`, 'success', 9000);
      setTimeout(() => {
        const b = document.getElementById(key);
        if (b) b.addEventListener('click', () => compare(src, r.out));
      }, 0);
      // Select the new file only if the user is still on the source (or nothing),
      // so a running job never yanks the selection away from other work.
      const sel = App.state.selectedPaths || [];
      const onSource = !sel.length || (sel.length === 1 && norm(sel[0]) === norm(src));
      if (PhotoTools.inCurrentFolder(r.out) && onSource) await PhotoTools.revealFile(r.out);
    } else {
      App.toast(`${job.label} failed: ${PhotoTools.esc(r.error || 'unknown error')}`, 'error', 8000);
    }
    try { job.resolve(r); } catch (_) {}
  }

  // ── before / after ───────────────────────────────────────────────────────
  async function compare(src, out) {
    if (!src || !out) {
      out = currentPath; src = out ? pairs.get(norm(out)) : null;
      if (!src) { App.toast('No original known for this photo', 'warning'); return; }
    }
    const [a, b] = await Promise.all([SFM.getImagePreview(src, 1400), SFM.getImagePreview(out, 1400)])
      .catch(() => [null, null]);
    if (!a || !a.ok || !b || !b.ok) { App.toast('Could not load images to compare', 'error'); return; }
    const id = Dialogs.modal('ai-compare', {
      title: 'Before and after',
      icon: 'compare',
      subtitle: PhotoTools.base(out),
      width: '780px',
      body: `<div class="cmp-stage">
               <div class="cmp-wrap" id="cmp-wrap-__ID__">
                 <img src="${b.data_url}" alt="After" id="cmp-after-__ID__">
                 <div class="cmp-before" id="cmp-before-__ID__"><img src="${a.data_url}" alt="Before" id="cmp-bimg-__ID__"></div>
                 <div class="cmp-line" id="cmp-line-__ID__"><span class="cmp-knob">${Icons.svg('arrow-left-right', 14)}</span></div>
                 <span class="cmp-tag cmp-tag-l">Before</span>
                 <span class="cmp-tag cmp-tag-r">After</span>
               </div>
             </div>
             <div class="cmp-controls">
               <span class="text-xs text-muted">Before</span>
               <input type="range" class="cmp-range" id="cmp-range-__ID__" min="0" max="100" value="50" aria-label="Before / after position">
               <span class="text-xs text-muted">After</span>
             </div>`,
      footer: `<button class="btn btn-ghost footer-left" id="cmp-open-__ID__">${Icons.svg('external-link', 16)}Open result</button>
               <button class="btn btn-primary" id="cmp-close-__ID__">Close</button>`,
    });
    const root = document.getElementById('mo-' + id);
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
    const g = k => document.getElementById(k + '-' + id);
    const after = g('cmp-after'), before = g('cmp-before'), bimg = g('cmp-bimg'), line = g('cmp-line'), range = g('cmp-range');
    function apply() {
      const w = after.clientWidth, h = after.clientHeight, pct = range.value / 100;
      bimg.style.width = w + 'px'; bimg.style.height = h + 'px';
      before.style.width = (w * pct) + 'px';
      line.style.left = (w * pct - 1) + 'px';
    }
    range.addEventListener('input', apply);
    g('cmp-wrap').addEventListener('pointermove', e => {
      if (!(e.buttons & 1)) return;
      const rc = after.getBoundingClientRect();
      range.value = Math.max(0, Math.min(100, (e.clientX - rc.left) * 100 / rc.width)); apply();
    });
    after.onload = apply;
    if (after.complete) apply();
    g('cmp-close').addEventListener('click', () => Dialogs.closeModal(id));
    g('cmp-open').addEventListener('click', () => SFM.openNative(out));
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  function init() {
    SFM.on('ai_photo_result', onResult);
    const p = loadPrefs();
    if ($('ai-suit-color') && p.suit_color) $('ai-suit-color').value = p.suit_color;
    if ($('ai-tie') && p.tie === false) $('ai-tie').checked = false;
    if ($('ai-quality') && p.quality) $('ai-quality').value = p.quality;
    ['ai-suit-color', 'ai-tie', 'ai-quality'].forEach(k => {
      const el = $(k);
      if (el) el.addEventListener('change', () => { savePrefs(getOpts()); refreshEstimate(); });
    });
    wireMirror('ai-suit-swatches', 'ai-suit-color');
    wireMirror('ai-quality-seg', 'ai-quality');
    syncControls();
    $('ai-compare')?.addEventListener('click', () => compare());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { run, compare, onShowFile, getOpts };
})();
