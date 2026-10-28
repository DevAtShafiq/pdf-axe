/**
 * subscription.js — plans, checkout, subscription page and the access gate.
 *
 * Office Axe needs an active subscription on this PC. Until it has one, a
 * full-window gate shows the plans and the checkout (the Python side refuses
 * every tool call meanwhile — see subscription_bridge.py). Once active, the
 * "Subscription" page shows status, expiry, devices (max 5) and payment history.
 *
 * Payments go through a gateway seam on the Python side. Until the real
 * gateway is connected it runs in test mode: the checkout says so and no money
 * is charged. A hosted gateway returns a redirect_url; we open it and poll
 * subscription_confirm until the payment is verified.
 */
const Subscription = (() => {
  let st = null;                // last subscription_state()
  let gateOpen = false;
  let gateDismissable = false;  // true when opened from the page to extend
  let view = 'plans';
  let plan = 'yearly';
  let method = 'bkash';
  let checkout = null;          // {checkout_id, ...}
  let pollTimer = null;
  let receipt = null;
  let unlockOnClose = false;    // gate was blocking: reload the app when done

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ic = (n, s = 16) => Icons.svg(n, s);
  const fmtDate = iso => {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };
  const fmtDateTime = iso => {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? '—' : `${fmtDate(iso)}, ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`;
  };
  const planById = id => (st && st.plans || []).find(p => p.id === id) || null;
  const isActive = () => !!(st && (st.status === 'active' || st.status === 'expiring'));

  // ── state ─────────────────────────────────────────────────────────────────
  async function refresh() {
    try {
      const r = await SFM.call('subscription_state');
      if (r && r.ok) apply(r);
      return r;
    } catch (e) {
      console.error('[Subscription] state', e);
      return null;
    }
  }

  function apply(s) {
    st = s;
    if (st.customer && !st.customer.name) st.customer = {};
    renderChip();
    renderPage();
    if (!st.access) {
      // Once the app has been seen locked, finishing the gate reloads it (its
      // first folder listing etc. were refused while locked).
      unlockOnClose = true;
      if (!gateOpen || gateDismissable) openGate({ blocking: true });
      else {
        if (view === 'plans' || view === 'devices') view = defaultView();
        renderGate();
      }
    } else if (gateOpen && !gateDismissable && view !== 'success' && view !== 'processing') {
      // (While paying, the subscription_changed event can arrive before the
      // confirm call returns; the receipt screen closes the gate itself.)
      closeGate();
    } else if (gateOpen) {
      renderGate();
    }
  }

  // ── status-bar chip ───────────────────────────────────────────────────────
  function renderChip() {
    const chip = document.getElementById('sub-chip');
    if (!chip || !st) return;
    if (!isActive()) { chip.style.display = 'none'; return; }
    chip.style.display = '';
    chip.classList.toggle('is-warn', st.status === 'expiring');
    const d = st.days_left;
    chip.querySelector('.sub-chip-text').textContent =
      `${st.plan ? st.plan.name : 'Plan'} · ${d} day${d === 1 ? '' : 's'} left`;
    chip.title = `Subscription valid until ${fmtDate(st.expires_at)}`;
  }

  // ── gate (full window) ────────────────────────────────────────────────────
  function gateEl() {
    let el = document.getElementById('sub-gate');
    if (!el) {
      el = document.createElement('div');
      el.id = 'sub-gate';
      el.className = 'sub-gate acct-overlay';       // .acct-overlay: App.blocked() stops shortcuts
      el.setAttribute('role', 'dialog');
      el.setAttribute('aria-modal', 'true');
      el.setAttribute('aria-label', 'Office Axe subscription');
      document.body.appendChild(el);
      el.addEventListener('click', onGateClick);
      el.addEventListener('input', onGateInput);
      el.addEventListener('keydown', e => {
        if (e.key === 'Escape' && gateDismissable && view !== 'processing') { e.stopPropagation(); closeGate(); }
        if (e.key === 'Enter' && e.target.matches('#sub-key-input')) activateKey();
      });
    }
    return el;
  }

  function openGate({ blocking = false, startView = null, planId = null } = {}) {
    gateDismissable = !blocking;
    if (planId) plan = planId;
    view = startView || defaultView();
    gateOpen = true;
    document.body.classList.toggle('sub-locked', blocking);
    const el = gateEl();
    el.classList.remove('hidden');
    renderGate();
    requestAnimationFrame(() => el.classList.add('is-open'));
  }

  function closeGate() {
    stopPolling();
    const el = document.getElementById('sub-gate');
    if (el) { el.classList.remove('is-open'); el.classList.add('hidden'); el.innerHTML = ''; }
    gateOpen = false;
    document.body.classList.remove('sub-locked');
    if (unlockOnClose && st && st.access) location.reload();
  }

  function defaultView() {
    if (!st) return 'plans';
    if (st.reason === 'device_limit' || st.reason === 'device_not_registered') return 'devices';
    return 'plans';
  }

  function setView(v) { view = v; renderGate(); }

  function renderGate() {
    if (!gateOpen) return;
    const el = gateEl();
    const body = {
      plans: viewPlans, checkout: viewCheckout, processing: viewProcessing,
      success: viewSuccess, key: viewKey, devices: viewDevicesGate,
    }[view] || viewPlans;
    el.innerHTML = `
      <div class="sub-gate-scroll">
        <div class="sub-gate-inner sub-view-${esc(view)}">
          <div class="sub-gate-top">
            <div class="sub-brand">
              <img src="assets/brand/office-axe-mark.png" alt="" class="sub-brand-mark">
              <span class="sub-brand-name">Office Axe</span>
            </div>
            ${gateDismissable && view !== 'processing'
              ? `<button class="icon-btn" data-act="close" title="Close" aria-label="Close">${ic('x', 16)}</button>` : ''}
          </div>
          ${st ? body() : `<div class="sub-loading"><span class="loading-spinner"></span></div>`}
        </div>
      </div>`;
    const first = el.querySelector('[data-autofocus]');
    if (first) setTimeout(() => first.focus(), 30);
  }

  // ── view: plans ───────────────────────────────────────────────────────────
  function viewPlans() {
    const r = st.reason;
    let title = 'Choose your plan';
    let sub = `Subscribe to start using Office Axe. Every plan includes all tools on up to ${st.max_devices} devices.`;
    if (gateDismissable && isActive()) {
      title = 'Extend your subscription';
      sub = `The new period starts when your current plan ends on ${fmtDate(st.expires_at)}. Nothing is lost.`;
    } else if (r === 'expired') {
      title = 'Your subscription has ended';
      sub = `It expired on ${fmtDate(st.expires_at)}. Renew to keep using Office Axe — your files stay where they are.`;
    }
    const sel = planById(plan) || st.plans[0];
    return `
      ${r === 'invalid' ? `<div class="callout warning sub-callout">${ic('alert-triangle', 16)}<span>${esc(st.problem)} Buy a plan or activate your license key again.</span></div>` : ''}
      <div class="sub-hero">
        <h1 class="sub-title">${esc(title)}</h1>
        <p class="sub-subtitle">${esc(sub)}</p>
      </div>
      <div class="sub-plans" role="radiogroup" aria-label="Plans">
        ${st.plans.map(planCard).join('')}
      </div>
      <ul class="sub-features">
        <li>${ic('check', 14)}All PDF, document, image, QR and folder tools</li>
        <li>${ic('check', 14)}Use on up to ${st.max_devices} devices with one subscription</li>
        <li>${ic('check', 14)}All updates included while your plan is active</li>
        <li>${ic('check', 14)}Pay with bKash, Nagad or card</li>
      </ul>
      <div class="sub-cta-row">
        <button class="btn btn-primary btn-lg sub-cta" data-act="to-checkout" data-autofocus>
          Continue with ${esc(sel.name)} · ${esc(sel.price_label)}${ic('arrow-right', 16)}
        </button>
      </div>
      ${!isActive() ? `<div class="sub-alt">
        <button class="btn btn-link" data-act="to-key">${ic('key', 14)}Already subscribed? Activate with your license key</button>
      </div>` : ''}`;
  }

  function planCard(p) {
    const on = p.id === plan;
    return `
      <button class="sub-plan${on ? ' is-selected' : ''}${p.compare_at ? ' is-featured' : ''}"
              role="radio" aria-checked="${on}" data-act="plan" data-plan="${esc(p.id)}">
        <span class="sub-plan-head">
          <span class="sub-plan-radio" aria-hidden="true"></span>
          <span class="sub-plan-name">${esc(p.name)}</span>
          ${p.save_percent ? `<span class="sub-plan-badge">Save ${p.save_percent}%</span>` : ''}
        </span>
        <span class="sub-plan-price">
          <span class="sub-price">${esc(p.price_label)}</span>
          <span class="sub-per">/ ${esc(p.period_label)}</span>
        </span>
        <span class="sub-plan-meta">
          ${p.compare_at
            ? `<s class="sub-compare">${esc(p.compare_at_label)}</s><span class="sub-save">You save ${esc(p.save_label)} · ${esc(p.monthly_equiv_label)}/month</span>`
            : `<span>Pay month by month · no commitment</span>`}
        </span>
        <span class="sub-plan-foot">${ic('calendar', 13)}${esc(p.duration_label)} access · ${st.max_devices} devices</span>
      </button>`;
  }

  // ── view: checkout ────────────────────────────────────────────────────────
  function viewCheckout() {
    const p = planById(plan);
    const g = st.gateway || {};
    const c = Object.assign({ name: '', email: '', phone: '' }, st.customer || {}, checkoutDraft);
    const startsAt = isActive() ? new Date(st.expires_at) : new Date();
    const ends = addMonths(startsAt, p.months);
    const payLabel = g.available
      ? (g.sandbox ? `Complete test payment · ${p.price_label}` : `Pay ${p.price_label}`)
      : 'Online payment coming soon';
    return `
      <div class="sub-back-row">
        <button class="btn btn-ghost btn-sm" data-act="to-plans">${ic('arrow-left', 14)}Plans</button>
      </div>
      <div class="sub-hero sub-hero-left">
        <h1 class="sub-title">Checkout</h1>
        <p class="sub-subtitle">Secure payment for your Office Axe subscription.</p>
      </div>
      <div class="sub-checkout">
        <form class="sub-form" id="sub-form" novalidate onsubmit="return false">
          <section class="sub-section">
            <h3 class="sub-section-title"><span class="sub-step">1</span>Your details</h3>
            <div class="sub-fields">
              ${field('name', 'Full name', c.name, 'text', 'name', 'As it should appear on the receipt')}
              ${field('email', 'Email', c.email, 'email', 'email', 'The receipt and license key are sent here')}
              ${field('phone', 'Mobile number', c.phone, 'tel', 'tel', 'e.g. 01XXXXXXXXX')}
            </div>
          </section>
          <section class="sub-section">
            <h3 class="sub-section-title"><span class="sub-step">2</span>Payment method</h3>
            <div class="sub-methods" role="radiogroup" aria-label="Payment method">
              ${st.methods.map(m => `
                <button type="button" class="sub-method${m.id === method ? ' is-selected' : ''}" role="radio"
                        aria-checked="${m.id === method}" data-act="method" data-method="${esc(m.id)}">
                  <span class="sub-method-logo sub-logo-${esc(m.id)}">${methodLogo(m.id)}</span>
                  <span class="sub-method-name">${esc(m.name)}</span>
                </button>`).join('')}
            </div>
            <div class="sub-gateway-slot" id="sub-gateway-slot" data-gateway="${esc(g.id || '')}">
              <span class="sub-gateway-icon">${ic('credit-card', 20)}</span>
              <div class="sub-gateway-text">
                <div class="sub-gateway-title">${g.live ? esc(g.name) : 'Secure payment gateway — coming soon'}</div>
                <div class="sub-gateway-sub">${g.live
                  ? 'You will finish the payment on the provider’s secure page.'
                  : 'The bKash, Nagad and card payment window will open here once the gateway is connected.'}</div>
              </div>
              ${g.sandbox ? `<span class="pill pill-yellow sub-test-pill">Test mode</span>` : ''}
            </div>
            ${g.sandbox ? `<p class="sub-test-note">${ic('info', 13)}Test mode: no money is charged. The payment is approved instantly so the full flow can be tried.</p>` : ''}
          </section>
          <div class="sub-form-error" id="sub-form-error" role="alert"></div>
        </form>

        <aside class="sub-summary" aria-label="Order summary">
          <h3 class="sub-section-title">Order summary</h3>
          <div class="sub-summary-plan">
            <span class="sub-summary-icon">${ic('crown', 18)}</span>
            <div>
              <div class="sub-summary-name">Office Axe ${esc(p.name)}</div>
              <div class="sub-summary-sub">${esc(p.duration_label)} · up to ${st.max_devices} devices</div>
            </div>
          </div>
          <dl class="sub-lines">
            ${p.compare_at ? `<div><dt>Regular price</dt><dd><s>${esc(p.compare_at_label)}</s></dd></div>
            <div class="is-save"><dt>Yearly discount</dt><dd>− ${esc(p.save_label)}</dd></div>` : ''}
            <div><dt>Access period</dt><dd>${fmtDate(startsAt.toISOString())} – ${fmtDate(ends.toISOString())}</dd></div>
            <div><dt>Auto-renew</dt><dd>Off — renew when you like</dd></div>
            <div class="is-total"><dt>Total</dt><dd>${esc(p.price_label)}</dd></div>
          </dl>
          <button class="btn btn-primary btn-lg btn-block sub-pay" data-act="pay" ${g.available ? '' : 'disabled'}>
            ${ic(g.available ? 'lock' : 'clock', 16)}${esc(payLabel)}
          </button>
          <p class="sub-fineprint">${ic('shield-check', 13)}Prices in Bangladeshi Taka (BDT). Your card or wallet details are entered only on the payment provider’s page — never in Office Axe.</p>
          <button class="btn btn-link btn-sm sub-switch" data-act="switch-plan">
            Switch to ${plan === 'yearly' ? 'Monthly' : 'Yearly'} plan
          </button>
        </aside>
      </div>`;
  }

  let checkoutDraft = {};

  function field(name, label, value, type, autocomplete, hint) {
    return `
      <div class="field">
        <label class="field-label" for="sub-f-${name}">${esc(label)}</label>
        <input id="sub-f-${name}" name="${name}" class="input-text" type="${type}" autocomplete="${autocomplete}"
               value="${esc(value)}" ${name === 'name' ? 'data-autofocus' : ''} spellcheck="false">
        <div class="field-hint">${esc(hint)}</div>
      </div>`;
  }

  function methodLogo(id) {
    if (id === 'card') return ic('credit-card', 18);
    return `<span class="sub-wallet">${id === 'bkash' ? 'b' : 'N'}</span>`;
  }

  function addMonths(d, n) {
    const r = new Date(d.getTime());
    const day = r.getDate();
    r.setDate(1);
    r.setMonth(r.getMonth() + n);
    const last = new Date(r.getFullYear(), r.getMonth() + 1, 0).getDate();
    r.setDate(Math.min(day, last));
    return r;
  }

  function readForm() {
    const f = document.getElementById('sub-form');
    if (!f) return checkoutDraft;
    checkoutDraft = {
      name: f.elements.name.value.trim(),
      email: f.elements.email.value.trim(),
      phone: f.elements.phone.value.trim(),
    };
    return checkoutDraft;
  }

  function validate(c) {
    if (c.name.length < 2) return { field: 'name', error: 'Enter your full name.' };
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(c.email)) return { field: 'email', error: 'Enter a valid email address for the receipt.' };
    if (!/^\+?[0-9][0-9 \-]{6,18}$/.test(c.phone)) return { field: 'phone', error: 'Enter a valid mobile number, e.g. 01XXXXXXXXX.' };
    return null;
  }

  function showFormError(err) {
    document.querySelectorAll('#sub-form .is-invalid').forEach(x => x.classList.remove('is-invalid'));
    const box = document.getElementById('sub-form-error');
    if (box) box.innerHTML = err ? `${ic('alert-circle', 14)}<span>${esc(err.error)}</span>` : '';
    if (err && err.field) {
      const inp = document.getElementById(`sub-f-${err.field}`);
      if (inp) { inp.classList.add('is-invalid'); inp.focus(); }
    }
  }

  async function pay() {
    const c = readForm();
    const bad = validate(c);
    if (bad) { showFormError(bad); return; }
    showFormError(null);
    const btn = document.querySelector('[data-act="pay"]');
    if (btn) btn.classList.add('is-loading');
    let r;
    try {
      r = await SFM.call('subscription_checkout', plan, method, c);
    } catch (e) {
      r = { ok: false, error: String(e && e.message || e) };
    }
    if (!r || !r.ok) {
      if (btn) btn.classList.remove('is-loading');
      showFormError({ field: r && r.field, error: (r && r.error) || 'The payment could not be started.' });
      return;
    }
    checkout = r;
    setView('processing');
    if (r.redirect_url) {
      startPolling();
    } else {
      setTimeout(confirmPayment, r.sandbox ? 1100 : 0);
    }
  }

  async function confirmPayment() {
    if (!checkout) return;
    let r;
    try {
      r = await SFM.call('subscription_confirm', checkout.checkout_id);
    } catch (e) {
      r = { ok: false, error: String(e && e.message || e) };
    }
    if (r && r.ok && r.pending) return;           // still waiting on the provider
    stopPolling();
    if (r && r.ok && r.paid) {
      receipt = r.receipt;
      checkout = null;
      checkoutDraft = {};
      view = 'success';
      apply(r.state);
      renderGate();
      return;
    }
    checkout = null;
    view = 'checkout';
    renderGate();
    showFormError({ error: (r && r.error) || 'The payment did not go through. No money was taken.' });
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(confirmPayment, 3000);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function cancelPayment() {
    stopPolling();
    if (checkout) SFM.call('subscription_cancel_checkout', checkout.checkout_id).catch(() => {});
    checkout = null;
    setView('checkout');
  }

  // ── view: processing ──────────────────────────────────────────────────────
  function viewProcessing() {
    const redirect = checkout && checkout.redirect_url;
    return `
      <div class="sub-center">
        <span class="loading-spinner sub-big-spinner"></span>
        <h1 class="sub-title">${redirect ? 'Finish the payment in your browser' : 'Confirming your payment…'}</h1>
        <p class="sub-subtitle">${redirect
          ? 'The payment page opened in your browser. This window updates by itself when the payment is confirmed.'
          : 'This only takes a moment. Please keep Office Axe open.'}</p>
        ${redirect ? `<button class="btn btn-ghost" data-act="cancel-payment">Cancel payment</button>` : ''}
      </div>`;
  }

  // ── view: success ─────────────────────────────────────────────────────────
  function viewSuccess() {
    const r = receipt || {};
    return `
      <div class="sub-center sub-success">
        <span class="sub-success-icon">${ic('check', 28)}</span>
        <h1 class="sub-title">${r.test ? 'Test payment successful' : 'Payment successful'}</h1>
        <p class="sub-subtitle">Your ${esc(r.plan || '')} plan is active until <strong>${fmtDate(r.expires_at)}</strong>.</p>
        <div class="sub-receipt">
          <dl class="sub-lines">
            <div><dt>Order</dt><dd class="mono">${esc(r.order_id)}</dd></div>
            <div><dt>Payment reference</dt><dd class="mono">${esc(r.reference)}</dd></div>
            <div><dt>Plan</dt><dd>${esc(r.plan)}</dd></div>
            <div><dt>Valid until</dt><dd>${fmtDate(r.expires_at)}</dd></div>
            <div class="is-total"><dt>Paid</dt><dd>${esc(r.amount_label)}${r.test ? ' <span class="pill pill-yellow">Test</span>' : ''}</dd></div>
          </dl>
          <div class="sub-key-box">
            <div>
              <div class="sub-key-label">License key</div>
              <div class="sub-key mono selectable">${esc(r.license_key)}</div>
            </div>
            <button class="btn btn-sm" data-act="copy-key" data-key="${esc(r.license_key)}">${ic('copy', 14)}Copy</button>
          </div>
          <p class="sub-fineprint">${ic('info', 13)}Keep this key. Use it to activate Office Axe on up to ${st.max_devices} devices.</p>
        </div>
        <button class="btn btn-primary btn-lg" data-act="done" data-autofocus>
          ${unlockOnClose ? 'Start using Office Axe' : 'Done'}${ic('arrow-right', 16)}
        </button>
      </div>`;
  }

  // ── view: license key ─────────────────────────────────────────────────────
  function viewKey() {
    return `
      <div class="sub-back-row">
        <button class="btn btn-ghost btn-sm" data-act="to-plans">${ic('arrow-left', 14)}Plans</button>
      </div>
      <div class="sub-narrow">
        <div class="sub-hero sub-hero-left">
          <h1 class="sub-title">Activate with license key</h1>
          <p class="sub-subtitle">Use your subscription on this PC. One subscription works on up to ${st.max_devices} devices.</p>
        </div>
        <div class="field">
          <label class="field-label" for="sub-key-input">License key</label>
          <input id="sub-key-input" class="input-text mono sub-key-input" placeholder="OAXE-XXXX-XXXX-XXXX-XXXX"
                 autocomplete="off" spellcheck="false" data-autofocus>
          <div class="field-hint">You’ll find it on your payment receipt and on the Subscription page of a PC that already uses it.</div>
        </div>
        <div class="sub-form-error" id="sub-key-error" role="alert"></div>
        <button class="btn btn-primary btn-lg btn-block" data-act="activate-key">${ic('key', 16)}Activate on this PC</button>
      </div>`;
  }

  async function activateKey() {
    const inp = document.getElementById('sub-key-input');
    const box = document.getElementById('sub-key-error');
    const btn = document.querySelector('[data-act="activate-key"]');
    const key = (inp && inp.value || '').trim();
    if (btn) btn.classList.add('is-loading');
    const r = await SFM.call('subscription_activate_key', key).catch(e => ({ ok: false, error: String(e) }));
    if (btn) btn.classList.remove('is-loading');
    if (r && r.ok) { apply(r.state); return; }
    if (box) box.innerHTML = `${ic('alert-circle', 14)}<span>${esc((r && r.error) || 'Activation failed.')}</span>`;
    if (inp) inp.classList.add('is-invalid');
  }

  // ── view: devices (gate) ──────────────────────────────────────────────────
  function viewDevicesGate() {
    const full = st.device_count >= st.max_devices;
    return `
      <div class="sub-narrow">
        <div class="sub-hero sub-hero-left">
          <h1 class="sub-title">${full ? 'Device limit reached' : 'Use Office Axe on this PC'}</h1>
          <p class="sub-subtitle">${full
            ? `Your subscription is already used on ${st.max_devices} of ${st.max_devices} devices. Remove a device you no longer use to continue here.`
            : `Your subscription is active until ${fmtDate(st.expires_at)}. ${st.device_count} of ${st.max_devices} devices are in use.`}</p>
        </div>
        ${deviceMeter()}
        <div class="sub-device-list">${st.devices.map(d => deviceRow(d, true)).join('') ||
          `<div class="text-muted text-sm">No devices yet.</div>`}</div>
        <div class="sub-form-error" id="sub-dev-error" role="alert"></div>
        <button class="btn btn-primary btn-lg btn-block" data-act="activate-device" ${full ? 'disabled' : ''} data-autofocus>
          ${ic('monitor', 16)}Use on this PC (${esc(st.this_device.name)})
        </button>
      </div>`;
  }

  function deviceMeter() {
    const pct = Math.min(100, Math.round(st.device_count * 100 / st.max_devices));
    return `
      <div class="sub-meter" aria-label="${st.device_count} of ${st.max_devices} devices used">
        <div class="sub-meter-head"><span>Devices</span><span><strong>${st.device_count}</strong> of ${st.max_devices}</span></div>
        <div class="sub-meter-track">${Array.from({ length: st.max_devices }, (_, i) =>
          `<span class="sub-meter-seg${i < st.device_count ? ' is-used' : ''}"></span>`).join('')}</div>
      </div>`;
  }

  function deviceRow(d, removable) {
    return `
      <div class="sub-device${d.current ? ' is-current' : ''}">
        <span class="sub-device-icon">${ic('monitor', 18)}</span>
        <div class="sub-device-main">
          <div class="sub-device-name">${esc(d.name)}${d.current ? ' <span class="pill pill-blue">This PC</span>' : ''}</div>
          <div class="sub-device-sub">${esc(d.os || '')} · added ${fmtDate(d.added_at)} · last active ${fmtDate(d.last_seen)}</div>
        </div>
        ${removable && !d.current ? `<button class="btn btn-sm btn-ghost sub-device-remove" data-act="remove-device"
            data-id="${esc(d.id)}" data-name="${esc(d.name)}">${ic('trash', 14)}Remove</button>` : ''}
      </div>`;
  }

  async function removeDevice(id, name) {
    const ok = await Dialogs.confirm({
      title: 'Remove device?', danger: true, okLabel: 'Remove', okIcon: 'trash',
      message: `Remove “${name}” from your subscription?`,
      detail: 'Office Axe stops working on that PC until it is activated again. This frees one of your device slots.',
    });
    if (!ok) return;
    const r = await SFM.call('subscription_remove_device', id).catch(e => ({ ok: false, error: String(e) }));
    if (r && r.ok) { apply(r.state); App.toast(`Removed ${name}`, 'success'); }
    else App.toast((r && r.error) || 'Could not remove the device.', 'error');
  }

  async function activateDevice() {
    const r = await SFM.call('subscription_activate_device').catch(e => ({ ok: false, error: String(e) }));
    if (r && r.ok) { apply(r.state); return; }
    const box = document.getElementById('sub-dev-error');
    if (box) box.innerHTML = `${ic('alert-circle', 14)}<span>${esc((r && r.error) || 'Activation failed.')}</span>`;
  }

  // ── gate events ───────────────────────────────────────────────────────────
  function onGateInput(e) {
    if (e.target.closest('#sub-form')) {
      e.target.classList.remove('is-invalid');
      readForm();
    }
    if (e.target.id === 'sub-key-input') e.target.classList.remove('is-invalid');
  }

  function onGateClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    if (view === 'checkout') readForm();
    switch (act) {
      case 'close': closeGate(); break;
      case 'plan': plan = b.dataset.plan; renderGate(); break;
      case 'to-checkout': setView('checkout'); break;
      case 'to-plans': setView('plans'); break;
      case 'to-key': setView('key'); break;
      case 'switch-plan': plan = plan === 'yearly' ? 'monthly' : 'yearly'; renderGate(); break;
      case 'method':
        method = b.dataset.method;
        document.querySelectorAll('.sub-method').forEach(x => {
          const on = x.dataset.method === method;
          x.classList.toggle('is-selected', on);
          x.setAttribute('aria-checked', on);
        });
        break;
      case 'pay': pay(); break;
      case 'cancel-payment': cancelPayment(); break;
      case 'activate-key': activateKey(); break;
      case 'activate-device': activateDevice(); break;
      case 'remove-device': removeDevice(b.dataset.id, b.dataset.name); break;
      case 'copy-key': copyKey(b.dataset.key, b); break;
      case 'done':
        receipt = null;
        closeGate();
        break;
    }
  }

  async function copyKey(key, btn) {
    try {
      await navigator.clipboard.writeText(key);
    } catch (_) {
      await SFM.call('set_clipboard', key).catch(() => {});
    }
    if (btn) {
      // Toasts are hidden behind the gate, so the button itself confirms.
      const html = btn.innerHTML;
      btn.innerHTML = `${ic('check', 14)}Copied`;
      setTimeout(() => { if (btn.isConnected) btn.innerHTML = html; }, 1600);
    } else {
      App.toast('License key copied', 'success', 2000);
    }
  }

  // ── Subscription page (sidebar) ───────────────────────────────────────────
  let showKey = false;

  function renderPage() {
    const host = document.getElementById('sub-page-body');
    if (!host || !st) return;
    const active = isActive();
    const p = st.plan;
    const pill = { active: ['green', 'Active'], expiring: ['yellow', 'Expiring soon'],
                   expired: ['red', 'Expired'], none: ['neutral', 'No plan'] }[st.status] || ['neutral', st.status];
    const used = st.period_days ? Math.max(0, Math.min(100, Math.round(st.days_left * 100 / st.period_days))) : 0;
    const g = st.gateway || {};

    host.innerHTML = `
      ${g.sandbox ? `<div class="callout warning sub-callout">${ic('info', 16)}<span><strong>Test mode.</strong> The payment gateway is not connected yet, so payments are simulated and nothing is charged.</span></div>` : ''}
      <div class="sub-page-grid">
        <section class="card sub-status-card">
          <div class="sub-card-head">
            <div>
              <div class="section-title">Current plan</div>
              <div class="sub-plan-title">${p ? `Office Axe ${esc(p.name)}` : 'No subscription'}
                <span class="pill pill-${pill[0]} pill-dot">${esc(pill[1])}</span></div>
            </div>
            ${p ? `<div class="sub-plan-amount">${esc(planById(p.id).price_label)}<span>/ ${esc(p.period_label)}</span></div>` : ''}
          </div>
          ${p ? `
          <div class="sub-expiry">
            <div class="sub-expiry-row">
              <span>${active ? 'Valid until' : 'Expired on'} <strong>${fmtDate(st.expires_at)}</strong></span>
              <span class="${st.status === 'expiring' ? 'sub-warn-text' : 'text-muted'}">${active ? `${st.days_left} day${st.days_left === 1 ? '' : 's'} left` : ''}</span>
            </div>
            <div class="progress-wrap sub-progress${st.status === 'expiring' ? ' is-warn' : ''}"><div class="progress-bar" style="width:${used}%"></div></div>
            <div class="sub-expiry-foot text-muted text-sm">Current period ${fmtDate(st.period_start)} – ${fmtDate(st.expires_at)} · auto-renew off</div>
          </div>` : `<p class="text-muted">Choose a plan to start using Office Axe.</p>`}
          <div class="sub-actions">
            <button class="btn btn-primary" data-pact="extend">${ic('crown', 14)}${active ? 'Extend plan' : 'Choose a plan'}</button>
            ${active && p && p.id === 'monthly' ? `<button class="btn" data-pact="yearly">Switch to yearly · save ${esc(planById('yearly').save_label)}</button>` : ''}
          </div>
          ${st.license_key ? `
          <div class="sub-key-box sub-key-box-page">
            <div>
              <div class="sub-key-label">License key</div>
              <div class="sub-key mono selectable">${esc(showKey ? st.license_key : st.license_key_masked)}</div>
            </div>
            <div class="row">
              <button class="icon-btn" data-pact="toggle-key" title="${showKey ? 'Hide' : 'Show'} key" aria-label="${showKey ? 'Hide' : 'Show'} key">${ic(showKey ? 'eye-off' : 'eye', 16)}</button>
              <button class="btn btn-sm" data-pact="copy-key">${ic('copy', 14)}Copy</button>
            </div>
          </div>` : ''}
        </section>

        <section class="card sub-devices-card">
          <div class="sub-card-head">
            <div>
              <div class="section-title">Devices</div>
              <div class="sub-card-sub">One subscription works on up to ${st.max_devices} devices.</div>
            </div>
          </div>
          ${deviceMeter()}
          <div class="sub-device-list">${st.devices.map(d => deviceRow(d, true)).join('') ||
            `<div class="text-muted text-sm">No devices activated yet.</div>`}</div>
        </section>
      </div>

      <section class="card sub-history-card">
        <div class="sub-card-head">
          <div>
            <div class="section-title">Payment history</div>
            <div class="sub-card-sub">${st.payments.length ? `${st.payments.length} payment${st.payments.length === 1 ? '' : 's'}` : 'Your receipts will appear here.'}</div>
          </div>
        </div>
        ${st.payments.length ? historyTable() : `
          <div class="empty-state empty-state-sm">
            <div class="empty-state-icon">${ic('history', 22)}</div>
            <div class="empty-state-title">No payments yet</div>
          </div>`}
      </section>`;
  }

  function historyTable() {
    const statusPill = s => ({ paid: ['green', 'Paid'], failed: ['red', 'Failed'], pending: ['yellow', 'Pending'],
                               refunded: ['neutral', 'Refunded'] }[s] || ['neutral', s]);
    return `
      <div class="sub-table-wrap">
        <table class="sub-table">
          <thead><tr><th>Date</th><th>Description</th><th>Method</th><th>Reference</th><th class="num">Amount</th><th>Status</th></tr></thead>
          <tbody>
            ${st.payments.map(x => {
              const sp = statusPill(x.status);
              return `<tr>
                <td data-label="Date">${fmtDateTime(x.created_at)}</td>
                <td data-label="Description">Office Axe ${esc(x.plan_name)}${x.period_end ? `<div class="text-muted text-sm">${fmtDate(x.period_start)} – ${fmtDate(x.period_end)}</div>` : ''}</td>
                <td data-label="Method">${esc(x.method_name || x.method)}</td>
                <td data-label="Reference" class="mono selectable">${esc(x.reference || x.id)}</td>
                <td data-label="Amount" class="num">${esc(x.amount_label)}</td>
                <td data-label="Status"><span class="pill pill-${sp[0]}">${esc(sp[1])}</span>${x.test ? ' <span class="pill pill-neutral">Test</span>' : ''}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`;
  }

  function onPageClick(e) {
    const b = e.target.closest('[data-pact], [data-act="remove-device"]');
    if (!b) return;
    if (b.dataset.act === 'remove-device') { removeDevice(b.dataset.id, b.dataset.name); return; }
    switch (b.dataset.pact) {
      case 'extend': openGate({ blocking: !st.access, startView: 'plans', planId: (st.plan && st.plan.id) || 'yearly' }); break;
      case 'yearly': openGate({ startView: 'checkout', planId: 'yearly' }); break;
      case 'toggle-key': showKey = !showKey; renderPage(); break;
      case 'copy-key': copyKey(st.license_key, b); break;
    }
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  function init() {
    const page = document.getElementById('sub-page-body');
    if (page) page.addEventListener('click', onPageClick);
    const chip = document.getElementById('sub-chip');
    if (chip) chip.addEventListener('click', () => App.switchPanel('subscription'));
    SFM.on('subscription_changed', s => { if (s && s.ok) apply(s); });
    // Gate right away (before the first answer) so the app never flashes unlocked.
    openGate({ blocking: true });
    refresh();
    // Expiry is checked again while the app stays open.
    setInterval(refresh, 10 * 60 * 1000);
    window.addEventListener('focus', () => { if (!gateOpen) refresh(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return { refresh, open: () => App.switchPanel('subscription'), openCheckout: (id) => openGate({ startView: 'checkout', planId: id }) };
})();
