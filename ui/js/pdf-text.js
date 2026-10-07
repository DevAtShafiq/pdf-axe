/**
 * pdf-text.js — selectable / copyable text over rendered PDF page images
 *
 * Every PDF page in the preview (and full view) is a PNG. PdfText puts a
 * transparent text layer on top (like pdf.js): one absolutely positioned span
 * per word, sized from the PDF's own coordinates (bridge `pdf_text_layer`),
 * so drag-selection highlights the real words and Ctrl+C / "Copy" give proper
 * text with line breaks.
 *
 *   PdfText.wrapPage(img, path, page, {width, height})  → .pdf-page container
 *   PdfText.attach(container)       → load + build the layer (lazy; idempotent)
 *   PdfText.copyPage(path, page)    → copy one page's text (toast)
 *   PdfText.copyAll(path)           → copy the whole document's text (toast)
 *   PdfText.selectPage(container)   → select that page's text (Ctrl+A)
 *   PdfText.copyText(text)          → clipboard helper (toast "Copied N characters")
 *   PdfText.menu(e, {path, page, container}) → right-click menu on a page
 *   PdfText.selectionIn(el)         → selected text inside el ('' if none)
 *
 * Layout: the layer's word boxes are in % of the page, font sizes are
 * calc(var(--s) * h px) where --s = displayed width / page width (points),
 * kept current by a ResizeObserver — so zoom / fit-width never desync it.
 */

const PdfText = (() => {
  const FONT = 'sans-serif';
  const _ctx = document.createElement('canvas').getContext('2d');
  const _ro = typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(entries => entries.forEach(en => _syncScale(en.target)))
    : null;

  function _measure(text, size) {
    _ctx.font = `${size}px ${FONT}`;
    return _ctx.measureText(text).width;
  }

  function _syncScale(box) {
    const layer = box.querySelector(':scope > .pdf-text-layer');
    const W = +box.dataset.ptWidth || 0;
    if (!layer || !W) return;
    const w = box.clientWidth;
    if (w > 0) layer.style.setProperty('--s', (w / W).toFixed(5));
  }

  // ── Page container ────────────────────────────────────────────────────────
  // Wraps the page <img> in a positioned .pdf-page box. size = displayed px.
  function wrapPage(img, path, page, size = {}) {
    const box = document.createElement('div');
    box.className = 'pdf-page';
    box.dataset.page = page;
    box.dataset.path = path;
    if (size.width)  box.style.width  = size.width + 'px';
    if (size.height) box.style.height = size.height + 'px';
    img.removeAttribute('data-page');
    img.draggable = false;
    img.alt = `Page ${+page + 1}`;
    box.appendChild(img);
    return box;
  }

  // ── Text layer ────────────────────────────────────────────────────────────
  async function attach(box) {
    if (!box || box.dataset.textLayer) return;
    box.dataset.textLayer = 'loading';
    const path = box.dataset.path, page = +box.dataset.page;
    let r;
    try { r = await SFM.call('pdf_text_layer', path, page); } catch (e) { r = null; }
    if (!box.isConnected) return;
    if (!r || !r.ok) { box.dataset.textLayer = 'error'; return; }
    box.dataset.ptWidth = r.width;
    box.dataset.ptHeight = r.height;
    box.dataset.textLayer = r.has_text ? 'ready' : 'empty';
    const layer = document.createElement('div');
    layer.className = 'pdf-text-layer';
    layer.setAttribute('aria-label', `Text of page ${page + 1}`);
    _build(layer, r);
    box.appendChild(layer);
    if (!r.has_text && r.scanned) {
      const hint = document.createElement('div');
      hint.className = 'pdf-scan-hint';
      hint.innerHTML = `${Icons.svg('image', 14)}<span>This page is a scanned image — no selectable text</span>`;
      box.appendChild(hint);
    }
    _syncScale(box);
    if (_ro) _ro.observe(box);
  }

  function _build(layer, data) {
    const W = data.width || 1, H = data.height || 1;
    const frag = document.createDocumentFragment();
    for (const line of data.lines || []) {
      const rot = Math.abs(line.a) > 0.05 ? `rotate(${line.a}deg) ` : '';
      line.w.forEach((w, i) => {
        const [t, x, y, len, h, sp] = w;
        if (i > 0 && sp) frag.appendChild(document.createTextNode(' '));
        const s = document.createElement('span');
        s.textContent = t;
        s.style.left = (x / W * 100).toFixed(4) + '%';
        s.style.top  = (y / H * 100).toFixed(4) + '%';
        s.style.fontSize = `calc(var(--s, 1) * ${h}px)`;
        const m = _measure(t, h);
        const sx = m > 0 ? len / m : 1;
        s.style.transform = rot + `scaleX(${sx.toFixed(4)})`;
        frag.appendChild(s);
      });
      frag.appendChild(document.createElement('br'));
    }
    layer.appendChild(frag);
  }

  // ── Selection helpers ─────────────────────────────────────────────────────
  function selectionIn(el) {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return '';
    const range = sel.getRangeAt(0);
    if (el && !el.contains(range.commonAncestorContainer)) return '';
    return sel.toString();
  }

  // Text of a range over PDF text layers: words as laid out, <br> → newline,
  // a blank line between pages. Deterministic, unlike the browser's own
  // serialisation of absolutely positioned spans.
  function _rangeText(range) {
    let root = range.commonAncestorContainer;
    if (root.nodeType !== 1) root = root.parentNode;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let out = '';
    const visit = node => {
      if (!range.intersectsNode(node)) return;
      if (node.nodeType === 3) {
        if (!node.parentElement || !node.parentElement.closest('.pdf-text-layer')) return;
        let t = node.data;
        const s = node === range.startContainer ? range.startOffset : 0;
        const e = node === range.endContainer ? range.endOffset : t.length;
        out += t.slice(s, e);
      } else if (node.tagName === 'BR' && node.parentElement && node.parentElement.classList.contains('pdf-text-layer')) {
        out += '\n';
      } else if (node.classList && node.classList.contains('pdf-text-layer') && out.trim()) {
        out = out.replace(/\n*$/, '\n\n');
      }
    };
    visit(root);
    let n;
    while ((n = walker.nextNode())) visit(n);
    return out.replace(/[ \t]+\n/g, '\n').replace(/\n+$/, '').replace(/^\n+/, '');
  }

  function _selInLayers(sel) {
    if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
    const r = sel.getRangeAt(0);
    let n = r.commonAncestorContainer;
    if (n.nodeType !== 1) n = n.parentNode;
    if (n.closest && n.closest('.pdf-text-layer')) return true;
    return !!(n.querySelector && Array.from(n.querySelectorAll('.pdf-text-layer')).some(l => r.intersectsNode(l)));
  }

  // The selected text as it will be copied ('' when nothing is selected).
  function selectedText() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return '';
    return _selInLayers(sel) ? _rangeText(sel.getRangeAt(0)) : sel.toString();
  }

  // Ctrl+C / Copy on a PDF text selection → clean text; any preview copy → toast.
  document.addEventListener('copy', e => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    const anchor = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    const inPreview = anchor && anchor.closest && anchor.closest('#preview-body, #fullview-wrap');
    if (_selInLayers(sel)) {
      const text = _rangeText(sel.getRangeAt(0));
      e.clipboardData.setData('text/plain', text);
      e.preventDefault();
      if (text) App.toast(`Copied ${_plural(text.length, 'character')}`, 'success', 1800);
    } else if (inPreview) {
      const n = sel.toString().length;
      if (n) App.toast(`Copied ${_plural(n, 'character')}`, 'success', 1800);
    }
  });

  function selectPage(box) {
    const layer = box && box.querySelector('.pdf-text-layer');
    const sel = window.getSelection();
    if (!layer || !sel) return false;
    const range = document.createRange();
    range.selectNodeContents(layer);
    sel.removeAllRanges();
    sel.addRange(range);
    return true;
  }

  // ── Clipboard ─────────────────────────────────────────────────────────────
  async function _clip(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0';
      document.body.appendChild(ta); ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      if (ok) return true;
    } catch (_) {}
    try { const r = await SFM.setClipboard(text); return !!(r && r.ok); } catch (_) { return false; }
  }

  function _plural(n, w) { return `${n.toLocaleString()} ${w}${n === 1 ? '' : 's'}`; }

  async function copyText(text, emptyMsg = 'No text to copy') {
    text = String(text || '').replace(/ /g, ' ');
    if (!text.trim()) { App.toast(emptyMsg, 'info'); return false; }
    const ok = await _clip(text);
    if (ok) App.toast(`Copied ${_plural(text.length, 'character')}`, 'success', 1800);
    else App.toast('Could not copy to the clipboard', 'error');
    return ok;
  }

  async function copyPage(path, page) {
    if (!path) return;
    const r = await SFM.call('pdf_page_text', path, page);
    if (!r || !r.ok) { App.toast('Could not read the page text: ' + ((r && r.error) || ''), 'error'); return; }
    return copyText(r.text, 'This page has no selectable text (it may be a scanned image)');
  }

  async function copyAll(path) {
    if (!path) return;
    App.setStatus && App.setStatus('Reading text…', true);
    let r;
    try { r = await SFM.call('pdf_all_text', path); }
    finally { App.setStatus && App.setStatus('Ready'); }
    if (!r || !r.ok) { App.toast('Could not read the text: ' + ((r && r.error) || ''), 'error'); return; }
    return copyText(r.text, 'This PDF has no selectable text (it may be scanned)');
  }

  // ── Right-click menu ──────────────────────────────────────────────────────
  let _menuEl = null;
  function _hideMenu() { if (_menuEl) { _menuEl.remove(); _menuEl = null; } }
  document.addEventListener('mousedown', e => { if (_menuEl && !_menuEl.contains(e.target)) _hideMenu(); }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') _hideMenu(); });
  window.addEventListener('blur', _hideMenu);

  // items (optional): [{icon, label, shortcut, fn, disabled} | {sep:true}] replaces the PDF items.
  function menu(e, { path, page, box, items } = {}) {
    e.preventDefault();
    _hideMenu();
    if (typeof ContextMenu !== 'undefined') ContextMenu.hide();
    const m = document.createElement('div');
    m.className = 'context-menu pdf-text-menu';
    m.setAttribute('role', 'menu');
    const item = (icon, label, shortcut, fn, disabled) => {
      const d = document.createElement('div');
      d.className = 'ctx-item' + (disabled ? ' disabled' : '');
      d.setAttribute('role', 'menuitem');
      d.innerHTML = `<span class="ctx-icon">${Icons.svg(icon, 16)}</span><span class="ctx-label">${label}</span>`
        + (shortcut ? `<span class="ctx-shortcut">${shortcut}</span>` : '');
      if (!disabled) d.addEventListener('click', () => { _hideMenu(); fn(); });
      m.appendChild(d);
    };
    const sep = () => { const d = document.createElement('div'); d.className = 'ctx-sep'; m.appendChild(d); };
    if (items) {
      items.forEach(x => x.sep ? sep() : item(x.icon, x.label, x.shortcut || '', x.fn, x.disabled));
    } else {
      const selected = selectedText();
      item('copy', 'Copy', 'Ctrl+C', () => copyText(selected), !selected);
      if (box) item('text-cursor', 'Select all text on this page', 'Ctrl+A', () => selectPage(box),
        box.dataset.textLayer !== 'ready');
      sep();
      if (page != null) item('file-text', `Copy text of page ${page + 1}`, '', () => copyPage(path, page));
      item('files', 'Copy all text', '', () => copyAll(path));
    }
    document.body.appendChild(m);
    const vw = window.innerWidth, vh = window.innerHeight;
    const mw = m.offsetWidth || 240, mh = m.offsetHeight || 160;
    m.style.left = Math.max(4, Math.min(e.clientX, vw - mw - 4)) + 'px';
    m.style.top  = Math.max(4, Math.min(e.clientY, vh - mh - 4)) + 'px';
    _menuEl = m;
  }

  // ── Drag-to-pan where there is no text (keeps text selection on words) ──
  // scroller: the scrolling element; pages inside it.
  function enablePan(scroller) {
    if (!scroller || scroller._pdfPan) return;
    scroller._pdfPan = true;
    let drag = null;
    scroller.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      const t = e.target;
      if (!(t instanceof Element)) return;
      if (t.closest('.pdf-text-layer span')) return;           // starting a text selection
      if (!t.closest('.pdf-page')) return;
      const canPan = scroller.scrollHeight > scroller.clientHeight + 1 || scroller.scrollWidth > scroller.clientWidth + 1;
      if (!canPan) return;
      e.preventDefault();                                       // no selection / image drag
      window.getSelection()?.removeAllRanges();
      drag = { x: e.clientX, y: e.clientY, l: scroller.scrollLeft, t: scroller.scrollTop };
      scroller.classList.add('is-panning');
    });
    window.addEventListener('mousemove', e => {
      if (!drag) return;
      scroller.scrollLeft = drag.l - (e.clientX - drag.x);
      scroller.scrollTop  = drag.t - (e.clientY - drag.y);
    });
    window.addEventListener('mouseup', () => {
      if (!drag) return;
      drag = null;
      scroller.classList.remove('is-panning');
    });
  }

  return { wrapPage, attach, selectPage, selectionIn, selectedText, copyText, copyPage, copyAll, menu, enablePan };
})();
