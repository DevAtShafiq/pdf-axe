/**
 * photo-size.js — "Crop to photo size": passport / visa / ID photos at the
 * exact official size (Korea 35×45 mm, US 2×2 in, China 33×48 mm ...).
 *
 *   PhotoSize.open(path)   dialog: fixed-aspect crop box (drag to move, corner
 *                          handles keep the aspect, wheel = zoom), rotate 90°,
 *                          straighten ±15°, face guide (crown / eyes / chin),
 *                          max KB, 4×6 in print sheet. Saves a NEW
 *                          <name>_<preset>.jpg next to the photo (photo_size.py).
 *
 * Geometry (shared with photo_size.py): the photo is turned `rot` (0/90/180/270
 * clockwise) → W×H, then straightened `angle`° clockwise about its centre on a
 * frame Wf×Hf (white corners). The crop rect is in frame pixels (full size).
 * The preset list lives in photo_size.py + the office's photo_presets.json.
 */
const PhotoSize = (() => {
  const LS_KEY = 'sfm.photoSize.last';
  const MIN = 16;            // minimum crop side in full-resolution px
  const HANDLE = 10;         // handle hit radius (screen px)
  const MAX_ANGLE = 15;
  const esc = s => PhotoTools.esc(s);

  function loadPrefs() { try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}') || {}; } catch (_) { return {}; } }
  function savePrefs(o) { try { localStorage.setItem(LS_KEY, JSON.stringify(o)); } catch (_) {} }

  const fmtNum = v => (Math.round(v * 100) / 100).toString();
  function sizeLabel(p) {
    const u = p.unit === 'in' ? ' in' : p.unit === 'px' ? ' px' : ' mm';
    return `${fmtNum(p.w)} × ${fmtNum(p.h)}${u}`;
  }

  async function open(path) {
    App.setStatus('Loading image…', true);
    const [src, pr] = await Promise.all([
      SFM.getCropSource(path, 1600).catch(e => ({ ok: false, error: String(e) })),
      SFM.getPhotoPresets().catch(e => ({ ok: false, error: String(e) })),
    ]);
    App.setStatus('Ready');
    if (!src || !src.ok) { App.toast('Cannot load image: ' + esc((src && src.error) || 'unknown'), 'error'); return; }
    if (!pr || !pr.ok) { App.toast('Cannot load photo sizes: ' + esc((pr && pr.error) || 'unknown'), 'error'); return; }
    if (pr.warning) App.toast(esc(pr.warning), 'warning', 6000);

    const PRESETS = pr.presets || [];
    const prefs = loadPrefs();
    const groups = [];
    PRESETS.forEach(p => { if (!groups.includes(p.group)) groups.push(p.group); });
    const optHtml = groups.map(g => `<optgroup label="${esc(g)}">` +
        PRESETS.filter(p => p.group === g).map(p =>
          `<option value="${esc(p.key)}">${esc(p.name)} — ${esc(sizeLabel(p))}</option>`).join('') +
        '</optgroup>').join('') +
      `<optgroup label="Other"><option value="custom">Custom size…</option><option value="free">Free (any shape, no resize)</option></optgroup>`;

    const id = Dialogs.modal('photo-size', {
      title: 'Crop to photo size',
      icon: 'user',
      subtitle: PhotoTools.base(path) + ' · saved as a new JPEG',
      width: '1080px',
      cls: 'crop-modal ps-modal',
      extraStyle: 'max-height:94vh;height:94vh;display:flex;flex-direction:column;',
      body: '',
      footer: `<span class="crop-hint footer-left"><span class="kbd">Drag</span> move · <span class="kbd">Corners</span> resize ·
                 <span class="kbd">Wheel</span> zoom · <span class="kbd">Arrows</span> nudge · <span class="kbd">Enter</span> save</span>
               <button class="btn" id="ps-cancel-__ID__">Cancel</button>
               <button class="btn btn-primary" id="ps-save-__ID__">${Icons.svg('check', 16)}Save photo</button>`,
    });
    const root = document.getElementById('mo-' + id);
    root.querySelectorAll('[id*="__ID__"]').forEach(el => { el.id = el.id.replace('__ID__', id); });
    const body = root.querySelector('.modal-body');
    body.style.cssText = 'padding:0;display:flex;flex-direction:row;flex:1;min-height:0;gap:0;';
    body.innerHTML = `
      <div class="ps-main">
        <div class="crop-stage ps-stage" id="ps-stage-${id}"><canvas id="ps-canvas-${id}" tabindex="0" aria-label="Photo crop area"></canvas></div>
        <div class="crop-readout" id="ps-read-${id}"></div>
      </div>
      <aside class="ps-side">
        <div class="ps-sec">
          <label class="ps-lbl" for="ps-preset-${id}">Photo size</label>
          <select class="input-text" id="ps-preset-${id}">${optHtml}</select>
          <div class="ps-spec" id="ps-spec-${id}"></div>
          <div class="ps-custom hidden" id="ps-custom-${id}">
            <input class="input-text" id="ps-cw-${id}" type="number" min="0.1" step="any" aria-label="Custom width">
            <span>×</span>
            <input class="input-text" id="ps-ch-${id}" type="number" min="0.1" step="any" aria-label="Custom height">
            <select class="input-text" id="ps-cu-${id}" aria-label="Unit"><option value="mm">mm</option><option value="in">in</option><option value="px">px</option></select>
            <span class="ps-lbl">DPI</span>
            <input class="input-text" id="ps-cdpi-${id}" type="number" min="50" max="2400" value="300" aria-label="DPI">
          </div>
          <button class="btn-link ps-edit" id="ps-editlist-${id}" type="button">Edit size list…</button>
        </div>
        <div class="ps-sec">
          <span class="ps-lbl">Position</span>
          <div class="ps-row">
            <button class="icon-btn" id="ps-rl-${id}" data-tooltip="Rotate left 90°" aria-label="Rotate left 90°">${Icons.svg('rotate-ccw', 16)}</button>
            <button class="icon-btn" id="ps-rr-${id}" data-tooltip="Rotate right 90°" aria-label="Rotate right 90°">${Icons.svg('rotate-cw', 16)}</button>
            <span class="toolbar-sep toolbar-sep-sm"></span>
            <button class="icon-btn" id="ps-zo-${id}" data-tooltip="Zoom out (−)" aria-label="Zoom out">${Icons.svg('zoom-out', 16)}</button>
            <button class="icon-btn" id="ps-zi-${id}" data-tooltip="Zoom in (+)" aria-label="Zoom in">${Icons.svg('zoom-in', 16)}</button>
            <button class="icon-btn" id="ps-fit-${id}" data-tooltip="Fit box to photo" aria-label="Fit box to photo">${Icons.svg('maximize', 16)}</button>
            <button class="icon-btn hidden" id="ps-face-${id}" data-tooltip="Auto-centre on the face" aria-label="Auto-centre on the face">${Icons.svg('user', 16)}</button>
          </div>
          <label class="ps-lbl" for="ps-angle-${id}">Straighten <span id="ps-anglev-${id}" class="ps-val">0.0°</span></label>
          <div class="ps-row">
            <input type="range" id="ps-angle-${id}" min="-${MAX_ANGLE}" max="${MAX_ANGLE}" step="0.1" value="0" class="ps-range">
            <button class="icon-btn" id="ps-angle0-${id}" data-tooltip="Reset straighten" aria-label="Reset straighten">${Icons.svg('undo', 16)}</button>
          </div>
          <label class="ps-check"><input type="checkbox" id="ps-guide-${id}"> Face guide (crown · eyes · chin)</label>
        </div>
        <div class="ps-sec">
          <span class="ps-lbl">Output</span>
          <label class="ps-inline">Max file size
            <input class="input-text" id="ps-kb-${id}" type="number" min="1" step="1" placeholder="no limit" aria-label="Maximum file size in KB"> KB</label>
          <label class="ps-check"><input type="checkbox" id="ps-sheet-${id}"> Also make a 4×6 in print sheet</label>
          <label class="ps-inline ps-copies hidden" id="ps-copiesrow-${id}">Copies
            <input class="input-text" id="ps-copies-${id}" type="number" min="0" step="1" placeholder="fill" aria-label="Copies on the sheet">
            <span class="ps-muted" id="ps-fit-n-${id}"></span></label>
          <div class="ps-out" id="ps-out-${id}"></div>
        </div>
      </aside>`;
    const $ = s => document.getElementById(s + '-' + id);
    const canvas = $('ps-canvas'), ctx = canvas.getContext('2d'), stage = $('ps-stage');
    const readout = $('ps-read'), saveBtn = $('ps-save'), sel = $('ps-preset');

    // ── state (full-resolution px) ─────────────────────────────────────────
    const img = new Image();
    const fullW0 = src.width, fullH0 = src.height;
    let rot = 0, angle = 0;
    let W = fullW0, H = fullH0;              // after 90° turns
    let Wf = W, Hf = H;                      // straightened frame
    let rect = { x: 0, y: 0, w: W, h: H };
    let preset = null;                       // object from PRESETS (or null for custom/free)
    let key = 'free';
    let ratio = null;
    let view = { s: 1, ox: 0, oy: 0, dw: 0, dh: 0 };
    let drag = null, rotated = null;

    const rad = a => a * Math.PI / 180;
    function frameOf(w, h, a) {
      const c = Math.abs(Math.cos(rad(a))), s = Math.abs(Math.sin(rad(a)));
      return [w * c + h * s, w * s + h * c];
    }
    function buildRotated() {
      const pw = img.naturalWidth, ph = img.naturalHeight;
      const c = document.createElement('canvas');
      const swap = rot % 180 !== 0;
      c.width = swap ? ph : pw; c.height = swap ? pw : ph;
      const g = c.getContext('2d');
      g.translate(c.width / 2, c.height / 2); g.rotate(rad(rot));
      g.drawImage(img, -pw / 2, -ph / 2);
      rotated = c;
    }

    // Output size for the current choice (px) — null for Free.
    function customSpec() {
      const w = parseFloat($('ps-cw').value), h = parseFloat($('ps-ch').value);
      const unit = $('ps-cu').value, dpi = parseFloat($('ps-cdpi').value) || 300;
      if (!(w > 0 && h > 0)) return null;
      const px = v => unit === 'px' ? Math.round(v) : unit === 'in' ? Math.round(v * dpi) : Math.round(v / 25.4 * dpi);
      return { w, h, unit, dpi, px_w: Math.max(1, px(w)), px_h: Math.max(1, px(h)) };
    }
    function outSpec() {
      if (preset) return preset;
      if (key === 'custom') return customSpec();
      return null;
    }

    function layout() {
      const availW = Math.max(200, stage.clientWidth - 32);
      const availH = Math.max(200, stage.clientHeight - 32);
      const s = Math.min(availW / Wf, availH / Hf);
      const dpr = window.devicePixelRatio || 1;
      view = { s, dw: Math.round(Wf * s), dh: Math.round(Hf * s) };
      canvas.style.width = view.dw + 'px'; canvas.style.height = view.dh + 'px';
      canvas.width = Math.round(view.dw * dpr); canvas.height = Math.round(view.dh * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw();
    }

    function guideColor() { return '#22d3ee'; }
    function draw() {
      const { s, dw, dh } = view;
      ctx.save();
      ctx.clearRect(0, 0, dw, dh);
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, dw, dh);       // straighten fill is white
      if (rotated) {
        ctx.save();
        ctx.translate(Wf / 2 * s, Hf / 2 * s); ctx.rotate(rad(angle));
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(rotated, -W / 2 * s, -H / 2 * s, W * s, H * s);
        ctx.restore();
      }
      const x = rect.x * s, y = rect.y * s, w = rect.w * s, h = rect.h * s;
      ctx.fillStyle = 'rgba(0,0,0,.55)';
      ctx.fillRect(0, 0, dw, y); ctx.fillRect(0, y + h, dw, dh - y - h);
      ctx.fillRect(0, y, x, h); ctx.fillRect(x + w, y, dw - x - w, h);

      // face guide
      const g = preset && preset.guide;
      if (g && $('ps-guide').checked) {
        const gy = f => y + h * f;
        const cx = x + w / 2;
        ctx.save();
        ctx.strokeStyle = guideColor(); ctx.fillStyle = guideColor(); ctx.lineWidth = 1.5;
        ctx.font = '600 11px Segoe UI, sans-serif'; ctx.textBaseline = 'bottom';
        if (g.head) {   // allowed chin position band (head-height range from the crown)
          ctx.fillStyle = 'rgba(34,211,238,.14)';
          ctx.fillRect(x, gy(g.crown + g.head[0]), w, h * (g.head[1] - g.head[0]));
          ctx.fillStyle = guideColor();
        }
        const line = (f, label, dash) => {
          if (f == null) return;
          ctx.setLineDash(dash); ctx.beginPath(); ctx.moveTo(x, gy(f)); ctx.lineTo(x + w, gy(f)); ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillText(label, x + 4, gy(f) - 2);
        };
        line(g.crown, 'Top of head', [6, 4]);
        line(g.eye, 'Eyes', [2, 4]);
        line(g.chin, 'Chin', [6, 4]);
        // head oval + centre line
        const hh = h * (g.chin - g.crown), hw = hh * 0.74;
        ctx.globalAlpha = 0.9; ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.ellipse(cx, gy(g.crown) + hh / 2, hw / 2, hh / 2, 0, 0, Math.PI * 2); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(cx, y); ctx.lineTo(cx, y + h); ctx.stroke();
        ctx.restore();
      } else {
        ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 1;
        for (let i = 1; i < 3; i++) {
          ctx.beginPath(); ctx.moveTo(x + w * i / 3, y); ctx.lineTo(x + w * i / 3, y + h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(x, y + h * i / 3); ctx.lineTo(x + w, y + h * i / 3); ctx.stroke();
        }
      }
      const acc = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '').trim() || '#3b82f6';
      ctx.strokeStyle = acc; ctx.lineWidth = 2; ctx.strokeRect(x, y, w, h);
      handles().forEach(hd => {
        ctx.fillStyle = acc; ctx.fillRect(hd.x - 5, hd.y - 5, 10, 10);
        ctx.fillStyle = 'rgba(255,255,255,.95)'; ctx.fillRect(hd.x - 3, hd.y - 3, 6, 6);
      });
      ctx.restore();
      updateReadout();
    }

    function updateReadout() {
      const o = outSpec();
      const seg = (k, v, cls) => `<span class="crop-ro${cls ? ' ' + cls : ''}"><span class="crop-ro-k">${k}</span>${v}</span>`;
      const cw = Math.round(rect.w), ch = Math.round(rect.h);
      let html = seg('Crop', `${cw} × ${ch} px`);
      if (o) {
        html += seg('Output', `${o.px_w} × ${o.px_h} px · ${o.dpi || 300} DPI`);
        if (cw < o.px_w * 0.98) html += seg('', '⚠ Low resolution — the photo will be enlarged', 'ps-warn');
      } else {
        html += seg('Output', `${cw} × ${ch} px · 300 DPI`);
      }
      if (angle) html += seg('Straighten', angle.toFixed(1) + '°');
      if (rot) html += seg('Rotated', rot + '°');
      readout.innerHTML = html;
      saveBtn.disabled = rect.w < 1 || rect.h < 1 || (key === 'custom' && !customSpec());
    }

    function handles() {
      const { s } = view;
      const x = rect.x * s, y = rect.y * s, w = rect.w * s, h = rect.h * s;
      const c = [{ k: 'nw', x, y }, { k: 'ne', x: x + w, y }, { k: 'se', x: x + w, y: y + h }, { k: 'sw', x, y: y + h }];
      if (ratio) return c;
      return c.concat([{ k: 'n', x: x + w / 2, y }, { k: 'e', x: x + w, y: y + h / 2 },
                       { k: 's', x: x + w / 2, y: y + h }, { k: 'w', x, y: y + h / 2 }]);
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

    function clampMove(r) {
      r.x = Math.max(0, Math.min(Wf - r.w, r.x));
      r.y = Math.max(0, Math.min(Hf - r.h, r.y));
      return r;
    }
    // Largest box of `ratio` inside the frame, scaled by frac, centred on (cx, cy).
    function fitRect(frac = 0.9, cx = Wf / 2, cy = Hf / 2) {
      if (!ratio) return { x: 0, y: 0, w: Wf, h: Hf };
      let w = Wf * frac, h = w / ratio;
      if (h > Hf * frac) { h = Hf * frac; w = h * ratio; }
      return clampMove({ x: cx - w / 2, y: cy - h / 2, w, h });
    }
    // Scale the box about its centre (wheel / buttons): f < 1 zooms in.
    function zoomBox(f, ax, ay) {
      const cx = ax != null ? ax : rect.x + rect.w / 2, cy = ay != null ? ay : rect.y + rect.h / 2;
      let w = rect.w * f, h = rect.h * f;
      const maxF = Math.min(Wf / w, Hf / h, 1);
      w *= maxF; h *= maxF;
      if (w < MIN || h < MIN) return;
      const nx = cx - (cx - rect.x) * (w / rect.w), ny = cy - (cy - rect.y) * (h / rect.h);
      rect = clampMove({ x: nx, y: ny, w, h });
      draw();
    }

    function resize(k, fx, fy, st) {
      fx = Math.max(0, Math.min(Wf, fx)); fy = Math.max(0, Math.min(Hf, fy));
      if (ratio) {   // corners only, anchored at the opposite corner
        const ax = k.includes('w') ? st.x + st.w : st.x, ay = k.includes('n') ? st.y + st.h : st.y;
        let w = Math.max(Math.abs(fx - ax), Math.abs(fy - ay) * ratio, MIN);
        const maxW = Math.min(k.includes('w') ? ax : Wf - ax, (k.includes('n') ? ay : Hf - ay) * ratio);
        w = Math.min(w, maxW);
        const h = w / ratio;
        rect = { x: k.includes('w') ? ax - w : ax, y: k.includes('n') ? ay - h : ay, w, h };
        return;
      }
      let l = st.x, t = st.y, r = st.x + st.w, b = st.y + st.h;
      if (k.includes('w')) l = Math.min(fx, r - MIN);
      if (k.includes('e')) r = Math.max(fx, l + MIN);
      if (k.includes('n')) t = Math.min(fy, b - MIN);
      if (k.includes('s')) b = Math.max(fy, t + MIN);
      rect = { x: l, y: t, w: r - l, h: b - t };
    }

    function toFull(e) {
      const rc = canvas.getBoundingClientRect();
      const px = e.clientX - rc.left, py = e.clientY - rc.top;
      return { px, py, fx: px / view.s, fy: py / view.s };
    }
    canvas.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      canvas.focus();
      const p = toFull(e);
      const k = hit(p.px, p.py);
      if (!k) return;
      drag = { k, start: { ...rect }, ox: p.fx, oy: p.fy };
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', e => {
      const p = toFull(e);
      if (!drag) { canvas.style.cursor = CURSORS[hit(p.px, p.py)] || 'default'; return; }
      if (drag.k === 'move') rect = clampMove({ ...drag.start, x: drag.start.x + p.fx - drag.ox, y: drag.start.y + p.fy - drag.oy });
      else resize(drag.k, p.fx, p.fy, drag.start);
      draw();
    });
    const endDrag = () => { if (drag) { drag = null; draw(); } };
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    canvas.addEventListener('wheel', e => {
      e.preventDefault();
      zoomBox(e.deltaY > 0 ? 1.06 : 1 / 1.06);    // down = bigger box (zoom out)
    }, { passive: false });

    // ── presets ───────────────────────────────────────────────────────────
    function persist() {
      savePrefs({
        preset: key, guide: $('ps-guide').checked,
        maxKb: $('ps-kb').value, sheet: $('ps-sheet').checked, copies: $('ps-copies').value,
        custom: { w: $('ps-cw').value, h: $('ps-ch').value, unit: $('ps-cu').value, dpi: $('ps-cdpi').value },
      });
    }
    function updateSpec() {
      const o = outSpec();
      let html = '';
      if (preset) {
        html = `<b>${esc(sizeLabel(preset))}</b> · ${preset.px_w} × ${preset.px_h} px · ${preset.dpi} DPI` +
               (preset.max_kb ? ` · max ${preset.max_kb} KB` : '') +
               (preset.note ? `<div class="ps-note">${esc(preset.note)}</div>` : '');
      } else if (key === 'custom') {
        html = o ? `${o.px_w} × ${o.px_h} px · ${o.dpi} DPI` : 'Enter width and height';
      } else {
        html = 'Any shape. The crop is saved at its own pixel size (300 DPI).';
      }
      $('ps-spec').innerHTML = html;
      $('ps-guide').disabled = !(preset && preset.guide);
      // sheet copies that fit
      if (o) {
        const n = sheetFit(o.px_w, o.px_h, o.dpi || 300);
        $('ps-fit-n').textContent = n ? `(up to ${n})` : '(too big for 4×6)';
      } else $('ps-fit-n').textContent = '';
    }
    function sheetFit(pw, ph, dpi) {   // mirrors photo_size.sheet_layout
      let best = 0;
      for (const [m, gp] of [[Math.round(0.12 * dpi), Math.round(2 / 25.4 * dpi)], [0, 0]])
        for (const [sw, sh] of [[6, 4], [4, 6]]) {
          const SW = Math.round(sw * dpi), SH = Math.round(sh * dpi);
          const c = Math.max(0, Math.floor((SW - 2 * m + gp) / (pw + gp))), r = Math.max(0, Math.floor((SH - 2 * m + gp) / (ph + gp)));
          best = Math.max(best, c * r);
        }
      return best;
    }
    function setPreset(k, keepCentre) {
      key = k;
      preset = PRESETS.find(p => p.key === k) || null;
      if (!preset && k !== 'custom') key = 'free';
      sel.value = key;
      $('ps-custom').classList.toggle('hidden', key !== 'custom');
      const o = outSpec();
      ratio = o ? o.px_w / o.px_h : null;
      if (preset && preset.max_kb && !$('ps-kb').value) $('ps-kb').value = preset.max_kb;
      const cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
      if (ratio) {
        if (keepCentre) {
          let h = rect.h, w = h * ratio;
          if (w > Wf) { w = Wf; h = w / ratio; }
          if (h > Hf) { h = Hf; w = h * ratio; }
          rect = clampMove({ x: cx - w / 2, y: cy - h / 2, w, h });
        } else rect = fitRect(0.9);
      } else if (!keepCentre) rect = { x: 0, y: 0, w: Wf, h: Hf };
      updateSpec(); persist(); draw();
    }
    sel.addEventListener('change', () => setPreset(sel.value, true));
    ['ps-cw', 'ps-ch', 'ps-cu', 'ps-cdpi'].forEach(k => $(k).addEventListener('input', () => { if (key === 'custom') setPreset('custom', true); }));
    $('ps-guide').addEventListener('change', () => { persist(); draw(); });
    $('ps-kb').addEventListener('input', persist);
    $('ps-copies').addEventListener('input', persist);
    $('ps-sheet').addEventListener('change', () => { $('ps-copiesrow').classList.toggle('hidden', !$('ps-sheet').checked); persist(); });
    $('ps-editlist').addEventListener('click', async () => {
      const r = await SFM.openPhotoPresetsFile().catch(e => ({ ok: false, error: String(e) }));
      if (r && r.ok) App.toast('Opened ' + esc(r.path) + ' — reopen this dialog after saving it', 'info', 7000);
      else App.toast('Cannot open the size list: ' + esc((r && r.error) || ''), 'error');
    });

    // ── rotate / straighten / zoom ────────────────────────────────────────
    // Keep the box centre at the same point of the photo when the frame changes.
    function frameToImg(px, py) {   // frame → turned image (before straighten)
      const a = rad(angle), c = Math.cos(a), s = Math.sin(a), dx = px - Wf / 2, dy = py - Hf / 2;
      return [W / 2 + c * dx + s * dy, H / 2 - s * dx + c * dy];
    }
    function imgToFrame(ix, iy) {
      const a = rad(angle), c = Math.cos(a), s = Math.sin(a), dx = ix - W / 2, dy = iy - H / 2;
      return [Wf / 2 + c * dx - s * dy, Hf / 2 + s * dx + c * dy];
    }
    function setAngle(a) {
      a = Math.max(-MAX_ANGLE, Math.min(MAX_ANGLE, Math.round(a * 10) / 10));
      const [ix, iy] = frameToImg(rect.x + rect.w / 2, rect.y + rect.h / 2);
      angle = a;
      [Wf, Hf] = frameOf(W, H, angle);
      const [cx, cy] = imgToFrame(ix, iy);
      let w = Math.min(rect.w, Wf), h = ratio ? w / ratio : Math.min(rect.h, Hf);
      if (h > Hf) { h = Hf; w = ratio ? h * ratio : w; }
      rect = clampMove({ x: cx - w / 2, y: cy - h / 2, w, h });
      $('ps-angle').value = String(angle);
      $('ps-anglev').textContent = angle.toFixed(1) + '°';
      layout();
    }
    $('ps-angle').addEventListener('input', () => setAngle(parseFloat($('ps-angle').value) || 0));
    $('ps-angle0').addEventListener('click', () => setAngle(0));

    function rotate(dir) {
      const [ix, iy] = frameToImg(rect.x + rect.w / 2, rect.y + rect.h / 2);
      const oW = W, oH = H;
      rot = (rot + dir + 360) % 360;
      W = oH; H = oW;
      const [nx, ny] = dir > 0 ? [oH - iy, ix] : [iy, oW - ix];   // point after a 90° turn
      [Wf, Hf] = frameOf(W, H, angle);
      buildRotated();
      const [cx, cy] = imgToFrame(nx, ny);
      if (ratio) {
        let h = rect.h, w = h * ratio;
        const f = Math.min(1, Wf / w, Hf / h); w *= f; h *= f;
        rect = clampMove({ x: cx - w / 2, y: cy - h / 2, w, h });
      } else {
        let w = rect.h, h = rect.w;
        const f = Math.min(1, Wf / w, Hf / h); w *= f; h *= f;
        rect = clampMove({ x: cx - w / 2, y: cy - h / 2, w, h });
      }
      layout();
    }
    $('ps-rl').addEventListener('click', () => rotate(-90));
    $('ps-rr').addEventListener('click', () => rotate(90));
    $('ps-zi').addEventListener('click', () => zoomBox(1 / 1.1));
    $('ps-zo').addEventListener('click', () => zoomBox(1.1));
    $('ps-fit').addEventListener('click', () => { rect = ratio ? fitRect(1) : { x: 0, y: 0, w: Wf, h: Hf }; draw(); });

    // Auto-centre (only offered when photo_size reports an installed detector).
    if (pr.face) {
      $('ps-face').classList.remove('hidden');
      $('ps-face').addEventListener('click', async () => {
        const r = await SFM.photoFaceBox(path, rot).catch(e => ({ ok: false, error: String(e) }));
        if (!r || !r.ok) { App.toast(esc((r && r.error) || 'No face found'), 'warning'); return; }
        const b = r.box;
        const [fx, fy] = imgToFrame(b.x + b.w / 2, b.y + b.h / 2);
        const g = (preset && preset.guide) || { crown: 0.1, chin: 0.8 };
        const crown = fy - b.h * 0.85, chin = fy + b.h * 0.62;   // detector box ≈ brows..chin
        let h = (chin - crown) / (g.chin - g.crown), w = ratio ? h * ratio : h * 0.78;
        const f = Math.min(1, Wf / w, Hf / h); w *= f; h *= f;
        rect = clampMove({ x: fx - w / 2, y: crown - g.crown * h, w, h });
        draw();
      });
    }

    $('ps-cancel').addEventListener('click', () => Dialogs.closeModal(id));

    // ── keyboard ──────────────────────────────────────────────────────────
    const onKey = e => {
      if (!document.getElementById('mo-' + id)) return;
      const top = Array.from(document.querySelectorAll('.modal-overlay')).pop();
      if (top !== root) return;
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) && e.target.type !== 'range' && e.target.type !== 'checkbox') return;
      const dirs = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      if (dirs[e.key] && e.target === canvas) {
        e.preventDefault();
        const step = (e.shiftKey ? 10 : 1) / view.s;
        rect = clampMove({ ...rect, x: rect.x + dirs[e.key][0] * step, y: rect.y + dirs[e.key][1] * step });
        draw();
      } else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomBox(1 / 1.06); }
      else if (e.key === '-') { e.preventDefault(); zoomBox(1.06); }
      else if (e.key === 'Enter') { e.preventDefault(); save(); }
    };
    document.addEventListener('keydown', onKey);
    const ro = new ResizeObserver(() => layout());
    ro.observe(stage);
    const obs = new MutationObserver(() => {
      if (!document.getElementById('mo-' + id)) {
        document.removeEventListener('keydown', onKey);
        ro.disconnect(); obs.disconnect();
      }
    });
    obs.observe(document.body, { childList: true });

    // ── save ──────────────────────────────────────────────────────────────
    let saving = false;
    async function save() {
      if (saving || saveBtn.disabled) return;
      const params = {
        preset: key, rect: [rect.x, rect.y, rect.w, rect.h], rotate: rot, angle,
        max_kb: parseFloat($('ps-kb').value) || 0,
        print_sheet: $('ps-sheet').checked, copies: parseInt($('ps-copies').value, 10) || 0,
      };
      if (key === 'custom') Object.assign(params, { w: parseFloat($('ps-cw').value), h: parseFloat($('ps-ch').value),
                                                    unit: $('ps-cu').value, dpi: parseFloat($('ps-cdpi').value) || 300 });
      persist();
      saving = true; saveBtn.disabled = true; saveBtn.classList.add('is-loading');
      const r = await SFM.photoSizeSave(path, params).catch(e => ({ ok: false, error: String(e) }));
      saving = false; saveBtn.classList.remove('is-loading');
      if (!r || !r.ok) {
        App.toast('Photo not saved: ' + esc((r && r.error) || 'unknown error'), 'error', 6000);
        saveBtn.disabled = false; return;
      }
      const kb = (r.bytes / 1024).toFixed(0);
      let msg = `Saved ${esc(PhotoTools.base(r.out))} · ${r.width}×${r.height} px · ${kb} KB`;
      if (r.sheet) msg += ` + print sheet (${r.sheet_copies} copies)`;
      App.toast(msg, 'success', 5000);
      if (r.target_met === false) App.toast(`Could not get under ${params.max_kb} KB at this size — saved the smallest (${kb} KB)`, 'warning', 8000);
      $('ps-out').textContent = 'Last saved: ' + PhotoTools.base(r.out);
      root.dataset.lastOut = r.out;
      if (r.sheet) root.dataset.lastSheet = r.sheet;
      Dialogs.closeModal(id);
      if (!(await PhotoTools.revealFile(r.out))) { try { FileTree.refresh(); } catch (_) {} }
    }
    saveBtn.addEventListener('click', save);

    // ── init ──────────────────────────────────────────────────────────────
    const c = prefs.custom || {};
    $('ps-cw').value = c.w || '35'; $('ps-ch').value = c.h || '45';
    $('ps-cu').value = c.unit || 'mm'; $('ps-cdpi').value = c.dpi || '300';
    $('ps-guide').checked = prefs.guide !== false;
    $('ps-kb').value = prefs.maxKb || '';
    $('ps-sheet').checked = !!prefs.sheet;
    $('ps-copies').value = prefs.copies || '';
    $('ps-copiesrow').classList.toggle('hidden', !prefs.sheet);
    const first = prefs.preset && (prefs.preset === 'custom' || prefs.preset === 'free' || PRESETS.some(p => p.key === prefs.preset))
      ? prefs.preset : (PRESETS[0] ? PRESETS[0].key : 'free');

    // read-only state for the E2E test (qa/e2e/photo_crop_e2e.py)
    root._psState = () => ({ rect: { ...rect }, Wf, Hf, W, H, rot, angle, key, ratio, scale: view.s, ready: !!rotated });
    img.onload = () => { buildRotated(); setPreset(first, false); layout(); canvas.focus(); };
    img.onerror = () => App.toast('Could not display the image preview', 'error');
    img.src = src.data_url;
    return id;
  }

  return { open };
})();
