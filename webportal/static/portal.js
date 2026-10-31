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
