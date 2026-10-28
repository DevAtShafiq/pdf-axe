/**
 * subscription.js — plans, checkout, the lock screen and the Subscription page.
 *
 *   Subscription.refresh()      reload the status (shows the lock screen when
 *                               there is no active subscription on this PC)
 *   Subscription.openPlans()    choose / renew a plan (closable when active)
 *   Subscription.state()        last status from the bridge
 *
 * Office Axe can't be used without an active subscription: the lock screen
 * covers the whole window until one is bought (or a device slot is freed).
 * Payments go through subscription.py's gateway; until a real one is plugged
 * in it is a placeholder that takes test payments without charging.
 */

var Subscription = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ic = (n, s = 16) => Icons.svg(n, s);
  const tk = n => '৳' + Number(n || 0).toLocaleString('en-US');
  const date = ts => ts ? new Date(ts * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
  const PERIOD = { month: 'month', year: 'year' };
  const METHOD_ICON = { card: 'credit-card', bkash: 'key', nagad: 'key', rocket: 'key' };
  const FEATURES = [
    'Every PDF, Word and photo tool',
    'Use on up to 5 devices',
    'All updates while subscribed',
    'Renew or switch plans any time',
  ];

  let _st = null;          // last status
  let _flow = null;        // the open plans / checkout window
  let _timer = null;

  // ── status ────────────────────────────────────────────────────────────────
  async function refresh() {
    let r;
    try { r = await SFM.subscriptionStatus(); } catch (e) { r = { ok: false, error: String(e) }; }
    if (!r || !r.ok) {
      _st = null;
      openFlow({ locked: true, error: (r && r.error) || 'Could not read the subscription' });
      return r;
    }
    _st = r;
    renderPage();
    if (!r.active) openFlow({ locked: true });
    else if (_flow && _flow.locked && _flow.step !== 'done') closeFlow();
    schedule();
    return r;
  }

  // check again when the subscription runs out (and every hour)
  function schedule() {
    clearTimeout(_timer);
    let ms = 3600e3;
    if (_st && _st.active && _st.expires_at) ms = Math.min(ms, Math.max(5e3, (_st.expires_at - _st.now) * 1000 + 1000));
    _timer = setTimeout(refresh, ms);
  }

  const planById = id => ((_st && _st.plans) || []).find(p => p.id === id);

  // ── the lock screen / plans / checkout window ─────────────────────────────
  function closeFlow() {
    if (!_flow) return;
    document.removeEventListener('keydown', _flow.onKey, true);
    _flow.el.remove();
    document.body.classList.remove('sub-locked');
    _flow = null;
  }

  function openFlow(opts = {}) {
    const locked = !!opts.locked;
    if (_flow) {
      _flow.locked = locked;
      _flow.el.classList.toggle('is-locked', locked);
      if (opts.error) _flow.error = opts.error;
      if (_flow.step !== 'checkout' && _flow.step !== 'waiting') render();
      return;
    }
    const el = document.createElement('div');
    el.className = 'sub-overlay' + (locked ? ' is-locked' : '');
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Office Axe subscription');
    document.body.appendChild(el);
    document.body.classList.add('sub-locked');
    _flow = { el, locked, step: 'plans', plan: 'yearly', method: '', session: null, busy: false, error: opts.error || '' };
    // nothing behind the lock screen may react to keys
    _flow.onKey = e => {
      if (!_flow) return;
      if (document.querySelector('.modal-overlay.dlg-top')) return;
      if (!el.contains(e.target)) { e.preventDefault(); e.stopPropagation(); }
      else e.stopPropagation();
      if (e.key === 'Escape' && !_flow.locked && !_flow.busy) closeFlow();
    };
    document.addEventListener('keydown', _flow.onKey, true);
    if (opts.plan) _flow.plan = opts.plan;
    render();
  }

  function render() {
    const f = _flow;
    if (!f) return;
    const step = f.error && !_st ? 'error' : (_st && _st.state === 'device_limit' && f.locked ? 'devices' : f.step);
    f.el.innerHTML = `
      <div class="sub-window sub-step-${step}">
        <div class="sub-top">
          <span class="sub-brand">${Icons.logo(26)}<b>Office Axe</b></span>
          ${f.locked ? '' : `<button class="modal-close" data-act="close" aria-label="Close" title="Close (Esc)">${ic('x')}</button>`}
        </div>
        <div class="sub-body">${
          step === 'error' ? viewError() :
          step === 'devices' ? viewDevices() :
          step === 'checkout' ? viewCheckout() :
          step === 'waiting' ? viewWaiting() :
          step === 'done' ? viewDone() : viewPlans()}</div>
      </div>`;
    wire(step);
  }

  function viewError() {
    return `<div class="sub-center">
      <div class="sub-icon sub-icon-red">${ic('alert-triangle', 26)}</div>
      <h1 class="sub-h1">Can't check your subscription</h1>
      <p class="sub-lead">${esc(_flow.error)}</p>
      <button class="btn btn-primary btn-lg" data-act="retry">${ic('refresh')}Try again</button>
    </div>`;
  }

  function lead() {
    if (!_st || !_flow.locked) return 'Pick the plan that suits you. Both include every tool and up to 5 devices.';
    if (_st.state === 'expired') return `Your subscription ended on <b>${date(_st.expires_at)}</b>. Renew to keep using Office Axe.`;
    return 'Subscribe to start using Office Axe. One subscription works on up to 5 devices.';
  }

  function viewPlans() {
    const plans = (_st && _st.plans) || [];
    const sel = planById(_flow.plan) || plans[0];
    const card = p => {
      const on = sel && p.id === sel.id;
      return `<button class="sub-plan${on ? ' is-selected' : ''}${p.compare_at ? ' is-best' : ''}" data-plan="${esc(p.id)}" role="radio" aria-checked="${on}">
        ${p.compare_at ? `<span class="sub-ribbon">${ic('crown', 12)}Best value</span>` : ''}
        <span class="sub-plan-head"><span class="sub-radio"></span><span class="sub-plan-name">${esc(p.name)}</span>
          ${p.save_pct ? `<span class="pill pill-green">Save ${p.save_pct}%</span>` : ''}</span>
        <span class="sub-price">
          ${p.compare_at ? `<s class="sub-was">${tk(p.compare_at)}</s>` : ''}
          <span class="sub-amount">${tk(p.price)}</span><span class="sub-per">/ ${PERIOD[p.period] || p.period}</span>
        </span>
        <span class="sub-plan-note">${p.per_month ? `Just ${tk(p.per_month)} a month, billed once a year` : 'Billed every month'}</span>
        <span class="sub-plan-term">${p.months === 12 ? '1 year of access' : '1 month of access'}</span>
      </button>`;
    };
    return `
      <div class="sub-hero">
        <h1 class="sub-h1">${_st && _st.state === 'expired' && _flow.locked ? 'Renew your subscription' : 'Choose your plan'}</h1>
        <p class="sub-lead">${lead()}</p>
      </div>
      <div class="sub-plans" role="radiogroup" aria-label="Plans">${plans.map(card).join('')}</div>
      <ul class="sub-features">${FEATURES.map(t => `<li>${ic('check', 14)}<span>${esc(t)}</span></li>`).join('')}</ul>
      <div class="sub-actions">
        <button class="btn btn-primary btn-lg sub-cta" data-act="continue" ${sel ? '' : 'disabled'}>
          Continue with ${esc(sel ? sel.name : '')} · ${tk(sel ? sel.price : 0)}${ic('arrow-right')}
        </button>
        <span class="sub-secure">${ic('shield-check', 14)}Secure checkout · prices in Bangladeshi taka (BDT)</span>
      </div>`;
  }

  function periodText(p) {
    // a renewal adds to the time left
    const from = _st && _st.active && _st.expires_at ? _st.expires_at * 1000 : Date.now();
    const to = new Date(from);
    to.setMonth(to.getMonth() + p.months);
    return `${date(from / 1000)} – ${date(to.getTime() / 1000)}`;
  }

  function viewCheckout() {
    const p = planById(_flow.plan);
    const g = (_st && _st.gateway) || { methods: [] };
    const a = Object.assign({ name: '', email: '', phone: '' }, (_st && _st.account) || {}, _flow.billing || {});
    const m = _flow.method || (g.methods[0] && g.methods[0].id) || '';
    _flow.method = m;
    return `
      <div class="sub-checkout">
        <div class="sub-co-main">
          <button class="sub-back" data-act="back">${ic('arrow-left', 14)}Plans</button>
          <h1 class="sub-h1">Checkout</h1>
          <section class="sub-sec">
            <div class="sub-sec-title">Your details</div>
            <div class="sub-grid2">
              <label class="sub-field"><span class="field-label">Full name</span>
                <input class="input-text" id="sub-name" autocomplete="name" value="${esc(a.name)}" placeholder="Your name"></label>
              <label class="sub-field"><span class="field-label">Email</span>
                <input class="input-text" id="sub-email" type="email" autocomplete="email" value="${esc(a.email)}" placeholder="you@example.com"></label>
              <label class="sub-field sub-span2"><span class="field-label">Mobile number <span class="text-muted">(optional)</span></span>
                <input class="input-text" id="sub-phone" type="tel" autocomplete="tel" value="${esc(a.phone)}" placeholder="+880 1XXX XXXXXX"></label>
            </div>
          </section>
          <section class="sub-sec">
            <div class="sub-sec-title">Payment method</div>
            <div class="sub-methods" role="radiogroup" aria-label="Payment method">
              ${g.methods.map(x => `<button class="sub-method${x.id === m ? ' is-selected' : ''}" data-method="${esc(x.id)}" role="radio" aria-checked="${x.id === m}">
                <span class="sub-method-icon sub-m-${esc(x.id)}">${x.id === 'card' ? ic('credit-card', 18) : esc(x.label.slice(0, 1))}</span>
                <span class="sub-method-text"><b>${esc(x.label)}</b><small>${esc(x.hint || '')}</small></span>
              </button>`).join('')}
            </div>
            <div class="sub-gateway" id="sub-gateway">
              ${g.test_mode
                ? `${ic('info', 16)}<div><b>Online payment is coming soon.</b> The payment gateway isn't connected yet, so this is a test payment: your plan starts and nothing is charged.</div>`
                : `${ic('external-link', 16)}<div>You'll finish paying on <b>${esc(g.label)}</b>'s secure page in your browser.</div>`}
            </div>
          </section>
          <div class="sub-error" id="sub-error" role="alert">${esc(_flow.error || '')}</div>
        </div>
        <aside class="sub-summary">
          <div class="sub-sec-title">Order summary</div>
          <div class="sub-sum-plan">
            <span class="sub-sum-icon">${ic('crown', 18)}</span>
            <div><b>Office Axe ${esc(p.name)}</b><small>${esc(periodText(p))}</small></div>
          </div>
          <div class="sub-sum-row"><span>Plan price</span><span>${p.compare_at ? `<s>${tk(p.compare_at)}</s> ` : ''}${tk(p.price)}</span></div>
          ${p.compare_at ? `<div class="sub-sum-row sub-save"><span>You save</span><span>${tk(p.compare_at - p.price)}</span></div>` : ''}
          <div class="sub-sum-row"><span>Devices</span><span>Up to ${(_st && _st.max_devices) || 5}</span></div>
          <div class="sub-sum-total"><span>Total</span><span>${tk(p.price)}</span></div>
          <button class="btn btn-primary btn-lg btn-block" data-act="pay">${ic('lock', 15)}Pay ${tk(p.price)}${g.test_mode ? ' (test)' : ''}</button>
          <p class="sub-fine">${g.test_mode ? 'Test mode: no money is taken.' : 'Payments are processed by ' + esc(g.label) + '.'} By paying you agree to the Office Axe terms.</p>
        </aside>
      </div>`;
  }

  function viewWaiting() {
    return `<div class="sub-center">
      <div class="sub-icon"><span class="spinner"></span></div>
      <h1 class="sub-h1">Finish paying in your browser</h1>
      <p class="sub-lead">A secure payment page has opened. When you've paid, come back and click <b>I've paid</b>.</p>
      <div class="sub-error" id="sub-error" role="alert">${esc(_flow.error || '')}</div>
      <div class="sub-row-btns">
        <button class="btn btn-secondary" data-act="cancel-pay">Cancel</button>
        <button class="btn btn-primary btn-lg" data-act="confirm">${ic('check')}I've paid</button>
      </div>
    </div>`;
  }

  function viewDone() {
    const p = (_st && _st.plan) || {};
    const pay = (_st && _st.payments && _st.payments[0]) || {};
    return `<div class="sub-center">
      <div class="sub-icon sub-icon-green">${ic('check', 28)}</div>
      <h1 class="sub-h1">You're all set</h1>
      <p class="sub-lead">Office Axe ${esc(p.name || '')} is active until <b>${date(_st && _st.expires_at)}</b>.</p>
      <div class="sub-receipt">
        <div><span>Invoice</span><b>${esc(pay.id || '—')}</b></div>
        <div><span>Amount</span><b>${tk(pay.amount)}${pay.test ? ' <span class="pill pill-yellow">Test</span>' : ''}</b></div>
        <div><span>Devices</span><b>${(_st && _st.devices.length) || 1} of ${(_st && _st.max_devices) || 5}</b></div>
      </div>
      <button class="btn btn-primary btn-lg" data-act="start">Start using Office Axe${ic('arrow-right')}</button>
    </div>`;
  }

  function deviceRows(removable) {
    return `<div class="sub-devices">${(_st.devices || []).map(d => `
      <div class="sub-device">
        <span class="sub-device-icon">${ic('monitor', 18)}</span>
        <div class="sub-device-text"><b>${esc(d.name || 'PC')}</b>${d.current ? ' <span class="pill pill-blue">This device</span>' : ''}
          <small>Added ${date(d.added_at)} · last used ${date(d.last_seen)}</small></div>
        ${removable && !d.current ? `<button class="btn btn-secondary btn-sm" data-remove="${esc(d.id)}" data-name="${esc(d.name)}">Remove</button>` : ''}
      </div>`).join('')}</div>`;
  }

  function viewDevices() {
    return `<div class="sub-center sub-narrow">
      <div class="sub-icon sub-icon-yellow">${ic('monitor', 26)}</div>
      <h1 class="sub-h1">All ${_st.max_devices} devices are in use</h1>
      <p class="sub-lead">This subscription is already used on ${_st.max_devices} devices. Remove one you no longer use to start using Office Axe on this PC.</p>
      ${deviceRows(true)}
    </div>`;
  }

  // ── actions ───────────────────────────────────────────────────────────────
  function wire(step) {
    const el = _flow.el;
    const on = (sel, fn) => el.querySelectorAll(sel).forEach(b => b.addEventListener('click', fn));
    on('[data-act="close"]', () => closeFlow());
    on('[data-act="retry"]', () => { _flow.error = ''; refresh(); });
    on('[data-plan]', e => { _flow.plan = e.currentTarget.dataset.plan; render(); });
    on('[data-act="continue"]', () => { _flow.step = 'checkout'; _flow.error = ''; render(); });
    on('[data-act="back"]', () => { _flow.billing = billing(); _flow.step = 'plans'; _flow.error = ''; render(); });
    on('[data-method]', e => {
      _flow.method = e.currentTarget.dataset.method;
      el.querySelectorAll('[data-method]').forEach(b => {
        const s = b.dataset.method === _flow.method;
        b.classList.toggle('is-selected', s); b.setAttribute('aria-checked', s);
      });
    });
    on('[data-act="pay"]', pay);
    on('[data-act="confirm"]', confirmPaid);
    on('[data-act="cancel-pay"]', async () => {
      if (_flow.session) await SFM.subscriptionCancel(_flow.session).catch(() => null);
      _flow.session = null; _flow.step = 'checkout'; _flow.error = ''; render();
    });
    on('[data-act="start"]', () => { closeFlow(); App.toast('Welcome to Office Axe', 'success', 2500); });
    on('[data-remove]', e => removeDevice(e.currentTarget.dataset.remove, e.currentTarget.dataset.name));
    if (step === 'checkout') {
      const first = ['#sub-name', '#sub-email'].map(s => el.querySelector(s)).find(i => !i.value);
      (first || el.querySelector('[data-act="pay"]')).focus();
      el.querySelectorAll('.sub-field input').forEach(i => {
        i.addEventListener('keydown', e => { if (e.key === 'Enter') pay(); });
        i.addEventListener('input', () => { if (i.classList.contains('is-invalid')) showError(''); });
      });
    } else {
      const b = el.querySelector('.sub-cta, [data-act="start"], [data-act="retry"]');
      if (b) b.focus();
    }
  }

  const billing = () => {
    const v = id => ((_flow.el.querySelector(id) || {}).value || '').trim();
    return { name: v('#sub-name'), email: v('#sub-email'), phone: v('#sub-phone') };
  };

  function showError(msg, field) {
    _flow.error = msg;
    const e = _flow.el.querySelector('#sub-error');
    if (e) e.textContent = msg;
    _flow.el.querySelectorAll('.input-text.is-invalid').forEach(i => i.classList.remove('is-invalid'));
    if (field) { const i = _flow.el.querySelector(field); if (i) { i.classList.add('is-invalid'); i.focus(); } }
  }

  function setBusy(on, label) {
    _flow.busy = on;
    const b = _flow.el.querySelector('[data-act="pay"], [data-act="confirm"]');
    if (!b) return;
    b.disabled = on;
    b.classList.toggle('is-loading', on);
    if (on && label) { b.dataset.label = b.innerHTML; b.innerHTML = label; }
    else if (!on && b.dataset.label) b.innerHTML = b.dataset.label;
  }

  async function pay() {
    if (_flow.busy) return;
    const b = billing();
    _flow.billing = b;
    if (!b.name) return showError('Enter your name', '#sub-name');
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(b.email)) return showError('Enter a valid email address', '#sub-email');
    if (!_flow.method) return showError('Choose a payment method');
    showError('');
    setBusy(true, 'Processing…');
    const co = await SFM.subscriptionCheckout(_flow.plan, b).catch(e => ({ ok: false, error: String(e) }));
    if (!_flow) return;
    if (!co || !co.ok) { setBusy(false); return showError((co && co.error) || 'Could not start the payment'); }
    _flow.session = co.session_id;
    if (co.redirect_url) {          // real gateway: pay in the browser, then confirm
      setBusy(false);
      _flow.step = 'waiting'; _flow.error = ''; render();
      return;
    }
    await confirmPaid();
  }

  async function confirmPaid() {
    if (!_flow || !_flow.session) return;
    setBusy(true, 'Confirming…');
    const r = await SFM.subscriptionPay(_flow.session, { method: _flow.method }).catch(e => ({ ok: false, error: String(e) }));
    if (!_flow) return;
    setBusy(false);
    if (!r || !r.ok) {
      if (r && r.code === 'no_checkout') { _flow.session = null; _flow.step = 'checkout'; }
      _flow.error = (r && r.error) || 'The payment did not go through';
      render();
      return;
    }
    _st = r;
    _flow.session = null; _flow.error = ''; _flow.step = 'done';
    _flow.locked = true;               // stays until "Start using"
    renderPage();
    schedule();
    render();
  }

  async function removeDevice(id, name) {
    const ok = await Dialogs.confirm({
      title: 'Remove device', icon: 'monitor', tone: 'warning', okLabel: 'Remove',
      message: `Remove ${name || 'this device'} from the subscription?`,
      detail: 'It will need a free slot to use Office Axe again.',
    });
    if (!ok) return;
    const r = await SFM.subscriptionRemoveDevice(id).catch(e => ({ ok: false, error: String(e) }));
    if (!r || !r.ok) { App.toast(esc((r && r.error) || 'Could not remove the device'), 'error', 5000); return; }
    App.toast(`Removed ${esc(name || 'the device')}`, 'success', 2500);
    await refresh();
  }

  // ── the Subscription page ─────────────────────────────────────────────────
  function renderPage() {
    const host = document.getElementById('sub-page-body');
    if (!host || !_st) return;
    const s = _st, p = s.plan || {};
    const state = { active: ['Active', 'pill-green'], expired: ['Expired', 'pill-red'], none: ['No plan', 'pill-neutral'],
                    device_limit: ['Device limit reached', 'pill-yellow'] }[s.state] || [s.state, 'pill-neutral'];
    const total = s.started_at && s.expires_at ? s.expires_at - s.started_at : 0;
    const used = total ? Math.min(1, Math.max(0, (s.now - s.started_at) / total)) : 0;
    const soon = s.active && s.days_left <= 7;
    host.innerHTML = `
      <div class="card sub-status-card">
        <div class="sub-status-top">
          <span class="sub-sum-icon">${ic('crown', 20)}</span>
          <div class="flex-1">
            <div class="sub-status-plan">${p.name ? `Office Axe ${esc(p.name)}` : 'No subscription'}
              <span class="pill pill-dot ${state[1]}">${state[0]}</span>
              ${s.gateway && s.gateway.test_mode ? '<span class="pill pill-yellow" title="No real payment gateway yet">Test payments</span>' : ''}</div>
            <div class="card-subtitle">${p.price ? `${tk(p.price)} / ${PERIOD[p.period] || p.period}` : 'Choose a plan to use Office Axe'}</div>
          </div>
          <div class="sub-status-btns">
            ${s.plan ? `<button class="btn btn-secondary" data-page-act="change">Change plan</button>` : ''}
            <button class="btn btn-primary" data-page-act="renew">${ic('refresh', 14)}${s.plan ? 'Renew' : 'Choose a plan'}</button>
          </div>
        </div>
        ${s.expires_at ? `
        <div class="sub-stats">
          <div><span>${s.active ? 'Valid until' : 'Ended on'}</span><b>${date(s.expires_at)}</b></div>
          <div><span>Days left</span><b class="${soon ? 'sub-warn' : ''}">${s.days_left}</b></div>
          <div><span>Devices</span><b>${s.devices.length} of ${s.max_devices}</b></div>
        </div>
        <div class="sub-progress" title="${Math.round(used * 100)}% of this period used"><span style="width:${(used * 100).toFixed(1)}%"></span></div>
        ${soon ? `<div class="callout warning">${ic('alert-triangle', 14)}<span>Your subscription ends in ${s.days_left} day${s.days_left === 1 ? '' : 's'}. Renew now: the new period starts when this one ends.</span></div>` : ''}` : ''}
      </div>

      ${s.account && s.account.email ? `
      <div class="card">
        <div class="sub-card-head"><span class="card-title">Billing details</span></div>
        <div class="sub-stats sub-stats-plain">
          <div><span>Name</span><b>${esc(s.account.name)}</b></div>
          <div><span>Email</span><b>${esc(s.account.email)}</b></div>
          <div><span>Mobile</span><b>${esc(s.account.phone || '—')}</b></div>
        </div>
      </div>` : ''}

      <div class="card">
        <div class="sub-card-head"><span class="card-title">Devices</span><span class="text-muted text-sm">${s.devices.length} of ${s.max_devices} used</span></div>
        ${s.devices.length ? deviceRows(true) : '<p class="text-muted text-sm">Devices appear here once you subscribe.</p>'}
      </div>

      <div class="card card-flush">
        <div class="sub-card-head sub-pad"><span class="card-title">Payment history</span></div>
        ${s.payments.length ? `
        <div class="sub-table-wrap"><table class="sub-table">
          <thead><tr><th>Date</th><th>Invoice</th><th>Plan</th><th>Method</th><th class="num">Amount</th><th>Status</th></tr></thead>
          <tbody>${s.payments.map(x => `<tr>
            <td>${date(x.date)}</td>
            <td><span class="sub-mono" title="${esc(x.transaction_id || '')}">${esc(x.id)}</span></td>
            <td>${esc(x.plan_name || x.plan)}<small class="sub-sub">${date(x.period_start)} – ${date(x.period_end)}</small></td>
            <td>${esc(((s.gateway.methods || []).find(m => m.id === x.method) || {}).label || x.method || '—')}</td>
            <td class="num">${tk(x.amount)}</td>
            <td><span class="pill ${x.status === 'paid' ? 'pill-green' : 'pill-neutral'}">${x.status === 'paid' ? 'Paid' : esc(x.status)}</span>${x.test ? ' <span class="pill pill-yellow">Test</span>' : ''}</td>
          </tr>`).join('')}</tbody>
        </table></div>` : `<div class="empty-state empty-state-sm"><div class="empty-state-icon">${ic('history', 20)}</div><div class="empty-state-text">No payments yet.</div></div>`}
      </div>`;
    host.querySelectorAll('[data-page-act]').forEach(b => b.addEventListener('click', () =>
      openFlow({ plan: b.dataset.pageAct === 'renew' && s.plan ? s.plan.id : 'yearly' })));
    host.querySelectorAll('[data-remove]').forEach(b => b.addEventListener('click', () => removeDevice(b.dataset.remove, b.dataset.name)));
  }

  function init() {
    refresh().then(r => {
      if (r && r.ok && r.active && r.days_left <= 7)
        App.toast(`Your Office Axe subscription ends in ${r.days_left} day${r.days_left === 1 ? '' : 's'}`, 'warning', 6000);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
  return { refresh, openPlans: (plan) => openFlow({ plan }), state: () => _st, _flow: () => _flow && { step: _flow.step, locked: _flow.locked } };
})();
