// portal.js — copy buttons, confirm prompts and the device default on the
// new-key form. (Inline scripts are blocked by the page's CSP.)
(() => {
  function toast(msg) {
    let t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2000);
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove();
    }
    toast('Copied');
  }

  document.addEventListener('click', e => {
    const b = e.target.closest('[data-copy]');
    if (b) { e.preventDefault(); copy(b.dataset.copy); }
  });

  document.addEventListener('submit', e => {
    const msg = e.target.dataset.confirm;
    if (msg && !confirm(msg)) e.preventDefault();
  });

  const DEVICES = { review: 1, trial: 1, full: 2 };
  document.addEventListener('change', e => {
    if (!e.target.matches('select[data-devices]')) return;
    const n = e.target.form.querySelector('input[name="devices"]');
    if (n) n.value = DEVICES[e.target.value] || 1;
  });
})();

// Landing page: tour video chapters and the screenshot slider.
(() => {
  const video = document.getElementById('tour-video');
  if (video) {
    const btns = [...document.querySelectorAll('.chapters [data-seek]')];
    btns.forEach(b => b.addEventListener('click', () => {
      video.currentTime = parseFloat(b.dataset.seek) + 0.05;
      video.play().catch(() => {});
      video.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }));
    video.addEventListener('timeupdate', () => {
      let cur = -1;
      btns.forEach((b, i) => { if (video.currentTime >= parseFloat(b.dataset.seek)) cur = i; });
      btns.forEach((b, i) => b.classList.toggle('is-current', i === cur));
    });
  }

  const slider = document.getElementById('slider');
  if (!slider) return;
  const track = slider.querySelector('.slides');
  const slides = [...slider.querySelectorAll('.slide')];
  const dots = [...slider.querySelectorAll('.dot')];
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let index = 0, timer = null, paused = false;

  function go(i) {
    index = (i + slides.length) % slides.length;
    track.style.transform = `translateX(${-index * 100}%)`;
    slides.forEach((s, k) => s.setAttribute('aria-hidden', k === index ? 'false' : 'true'));
    dots.forEach((d, k) => d.setAttribute('aria-current', k === index ? 'true' : 'false'));
  }
  function schedule() {
    clearInterval(timer);
    if (!reduce && !paused) timer = setInterval(() => go(index + 1), 5000);
  }
  function pause(on) { paused = on; schedule(); }

  slider.querySelector('.prev').addEventListener('click', () => { go(index - 1); schedule(); });
  slider.querySelector('.next').addEventListener('click', () => { go(index + 1); schedule(); });
  dots.forEach(d => d.addEventListener('click', () => { go(+d.dataset.go); schedule(); }));
  slider.addEventListener('mouseenter', () => pause(true));
  slider.addEventListener('mouseleave', () => pause(false));
  slider.addEventListener('focusin', () => pause(true));
  slider.addEventListener('focusout', e => { if (!slider.contains(e.relatedTarget)) pause(false); });
  slider.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft') { go(index - 1); e.preventDefault(); }
    else if (e.key === 'ArrowRight') { go(index + 1); e.preventDefault(); }
  });
  // swipe on phones
  let x0 = null, y0 = null;
  slider.addEventListener('touchstart', e => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; pause(true); }, { passive: true });
  slider.addEventListener('touchend', e => {
    if (x0 !== null) {
      const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) go(index + (dx < 0 ? 1 : -1));
    }
    x0 = null;
    pause(false);
  });
  document.addEventListener('visibilitychange', () => pause(document.hidden));
  go(0);
  schedule();
})();

// Phone menu
(() => {
  const btn = document.querySelector('.nav-toggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const top = btn.closest('.top');
    const open = !top.classList.contains('nav-open');
    top.classList.toggle('nav-open', open);
    btn.setAttribute('aria-expanded', String(open));
  });
})();

// Pricing: Monthly / Yearly switch
document.querySelectorAll('.billing').forEach(box => {
  box.classList.add('js');
  box.querySelectorAll('.period-switch [data-period]').forEach(b => b.addEventListener('click', () => {
    box.dataset.period = b.dataset.period;
    box.querySelectorAll('.period-switch [data-period]').forEach(x => x.setAttribute('aria-checked', String(x === b)));
  }));
});

// Feature cards: the real app screen for each feature.
// Desktop: hover shows a large preview; click (or Enter) opens the full-size viewer.
// Phones: tap opens the full-size viewer (pinch to zoom, swipe for next / previous).
(() => {
  const cards = [...document.querySelectorAll('.feat[data-shot]')];
  if (!cards.length) return;
  const canHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  const big = c => c.dataset.shot, small = c => c.dataset.shot.replace('.webp', '-900.webp');
  const title = c => c.querySelector('h3').textContent, text = c => c.querySelector('p').textContent;

  // ── large hover preview ────────────────────────────────────────────────
  const pop = document.createElement('div');
  pop.id = 'feat-pop';
  pop.setAttribute('aria-hidden', 'true');
  pop.innerHTML = '<img alt="" decoding="async"><div class="cap"><b></b><span>Click to open full size</span></div>';
  document.body.appendChild(pop);
  const pimg = pop.querySelector('img');
  let hoverCard = null, hoverTimer = 0;
  function showPop(card) {
    hoverCard = card;
    pimg.srcset = small(card) + ' 900w, ' + big(card) + ' 1800w';
    pimg.sizes = '70vw';
    pimg.src = big(card);
    pop.querySelector('b').textContent = title(card);
    const r = card.getBoundingClientRect();
    pop.classList.toggle('at-top', r.top + r.height / 2 > window.innerHeight / 2);   // keep clear of the card
    pop.classList.add('show');
  }
  function hidePop() { clearTimeout(hoverTimer); pop.classList.remove('show'); hoverCard = null; }

  // ── full-size viewer ──────────────────────────────────────────────────
  const box = document.createElement('div');
  box.id = 'lightbox';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-label', 'Feature screenshot');
  box.hidden = true;
  box.innerHTML =
    '<div class="lb-top"><div class="lb-title"><b></b><span></span></div><span class="lb-count"></span>' +
    '<button type="button" class="lb-close" aria-label="Close (Esc)">×</button></div>' +
    '<div class="lb-stage"><img alt="" decoding="async"></div>' +
    '<button type="button" class="lb-nav lb-prev" aria-label="Previous feature">‹</button>' +
    '<button type="button" class="lb-nav lb-next" aria-label="Next feature">›</button>';
  document.body.appendChild(box);
  const limg = box.querySelector('img');
  let idx = 0, lastFocus = null;
  function render() {
    const c = cards[idx];
    limg.srcset = small(c) + ' 900w, ' + big(c) + ' 1800w';
    limg.sizes = '96vw';
    limg.src = big(c);
    limg.alt = 'Office Axe — ' + title(c);
    box.querySelector('.lb-title b').textContent = title(c);
    box.querySelector('.lb-title span').textContent = text(c);
    box.querySelector('.lb-count').textContent = (idx + 1) + ' / ' + cards.length;
    // load the neighbours early so next / previous is instant
    [idx + 1, idx - 1].forEach(k => {
      const n = cards[(k + cards.length) % cards.length];
      const im = new Image(); im.src = canHover ? big(n) : small(n);
    });
  }
  function open(i) {
    hidePop();
    idx = i; render();
    lastFocus = document.activeElement;
    box.hidden = false;
    document.documentElement.classList.add('lb-open');
    requestAnimationFrame(() => box.classList.add('show'));
    box.querySelector('.lb-close').focus();
  }
  function close() {
    box.classList.remove('show');
    box.hidden = true;
    document.documentElement.classList.remove('lb-open');
    if (lastFocus) lastFocus.focus();
  }
  const go = d => { idx = (idx + d + cards.length) % cards.length; render(); };
  box.querySelector('.lb-close').addEventListener('click', close);
  box.querySelector('.lb-prev').addEventListener('click', () => go(-1));
  box.querySelector('.lb-next').addEventListener('click', () => go(1));
  box.addEventListener('click', e => { if (e.target === box || e.target.classList.contains('lb-stage')) close(); });
  document.addEventListener('keydown', e => {
    if (box.hidden) { if (e.key === 'Escape') hidePop(); return; }
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowRight') go(1);
    else if (e.key === 'ArrowLeft') go(-1);
    else if (e.key === 'Tab') {                      // keep focus inside the viewer
      const f = [...box.querySelectorAll('button')];
      const i = f.indexOf(document.activeElement);
      e.preventDefault();
      f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
    }
  });
  let x0 = null;
  box.addEventListener('touchstart', e => { x0 = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
  box.addEventListener('touchend', e => {
    if (x0 === null || (window.visualViewport && window.visualViewport.scale > 1.05)) return;   // zoomed in: let them pan
    const dx = e.changedTouches[0].clientX - x0;
    if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
    x0 = null;
  });

  cards.forEach((card, i) => {
    card.setAttribute('aria-haspopup', 'dialog');
    if (canHover) {
      card.addEventListener('mouseenter', () => { clearTimeout(hoverTimer); hoverTimer = setTimeout(() => showPop(card), 120); });
      card.addEventListener('mouseleave', hidePop);
    }
    card.addEventListener('click', () => open(i));
    card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(i); } });
  });
  window.addEventListener('scroll', () => { if (hoverCard) hidePop(); }, { passive: true });
})();
