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

  const DEVICES = { review: 1, trial: 1, full: 5 };
  document.addEventListener('change', e => {
    if (!e.target.matches('select[data-devices]')) return;
    const n = e.target.form.querySelector('input[name="devices"]');
    if (n) n.value = DEVICES[e.target.value] || 1;
  });
})();
