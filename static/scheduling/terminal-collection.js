(() => {
  if (!document.querySelector('[data-reservation-workspace], [data-terminal-health]')) return;
  let busy = false, failures = 0, timer, newlyFocusedAmount, pollIndex = 0;
  const isAmount = element => element.matches('[data-terminal-collect] input[name="amount"]');
  document.addEventListener('pointerdown', event => {
    newlyFocusedAmount = isAmount(event.target) && document.activeElement !== event.target ? event.target : null;
  });
  document.addEventListener('focusin', event => {
    if (isAmount(event.target)) event.target.select();
  });
  document.addEventListener('click', event => {
    // A first pointer click can collapse the selection made during focus.
    if (event.target === newlyFocusedAmount) event.target.select();
    newlyFocusedAmount = null;
  });
  const refresh = () => new Promise(resolve => document.dispatchEvent(
    new CustomEvent('booking:refresh-payment', {detail: {resolve}})));
  const summary = () => {
    const form = document.querySelector('[data-terminal-collect]');
    if (!form) return;
    const amount = Math.round(Number(form.elements.amount.value) * 100);
    const balance = Number(form.elements.expected_balance.value);
    const difference = amount - balance;
    const valid = Number.isFinite(amount) && amount > 0;
    const reduction = form.querySelector('[data-collection-reduction]');
    const treatmentFields = form.querySelector('[data-collection-treatment]');
    reduction.hidden = !valid || difference >= 0;
    if (reduction.hidden) form.elements.settle_balance.checked = false;
    const reduce = !reduction.hidden && form.elements.settle_balance.checked;
    treatmentFields.hidden = treatmentFields.disabled = !reduce;
    const treatment = reduce ? form.elements.treatment.value : 'partial';
    const device = form.elements.device;
    const maximum = Number((device.selectedOptions?.[0] || device).dataset.maximum);
    const error = valid && amount > Math.round(maximum * 100)
      ? maximum < 999999.99
        ? `Square Sandbox supports up to $${maximum.toFixed(2)} on this test Terminal. Enter a smaller amount.`
        : 'Enter an amount of $999,999.99 or less.' : '';
    form.elements.amount.max = maximum;
    form.elements.amount.setCustomValidity(error);
    const errorNode = form.querySelector('[data-collection-error]');
    errorNode.textContent = error;
    errorNode.hidden = !error;
    const money = value => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(value / 100);
    form.querySelector('[data-collection-summary]').textContent = !valid ? '' : difference > 0
      ? `${money(difference)} additional purchase (including tax).`
      : treatment !== 'partial' && difference < 0
        ? `${money(-difference)} ${treatment === 'credit' ? 'credit' : 'discount'}. Nothing more to pay after this payment succeeds.`
      : difference < 0 ? `Customer will still owe ${money(-difference)} after this payment.` : '';
    form.querySelector('[data-collection-summary]').hidden = !form.querySelector('[data-collection-summary]').textContent;
  };
  const announce = (form, text) => {
    const refundStatus = form.matches('[data-refund-poll]') ? form.closest('[data-refund-status]') : null;
    const node = (refundStatus || form).querySelector('[data-terminal-message]');
    if (!node) return;
    node.textContent = text;
    node.hidden = !text;
    if (refundStatus) {
      refundStatus.querySelector('[data-refund-feedback]').hidden = !text;
      refundStatus.querySelector('[data-refund-check]').setAttribute('form', form.id);
    }
  };
  const progress = (form, text) => {
    const root = form.matches('[data-refund-poll]') ? form.closest('[data-refund-status]') || form : form;
    const node = root.querySelector('[data-payment-progress]');
    if (node) { node.textContent = text; node.hidden = !text; }
  };
  const refundChanged = result => (result.refunds || []).some(refund => {
    const form = [...document.querySelectorAll('[data-refund-poll]')].find(candidate => candidate.id === `refund-check-${refund.public_id}`);
    return form && (form.dataset.state !== refund.state || form.dataset.attention !== refund.attention);
  });
  const send = async (form, action, automatic = false) => {
    if (busy) return;
    busy = true;
    const buttons = [...form.querySelectorAll('button')];
    if (form.matches('[data-refund-poll]')) {
      const check = form.closest('[data-refund-status]')?.querySelector('[data-refund-check]');
      if (check) buttons.push(check);
    }
    buttons.forEach(button => { button.disabled = true; });
    if (!automatic) progress(form, action === 'collect' ? 'Sending payment to Terminal...' : action === 'cancel' ? 'Canceling payment...' : action === 'retry' ? 'Submitting request...' : 'Checking status...');
    if (!form.matches('[data-terminal-health]')) announce(form, '');
    try {
      const body = new FormData(form); body.set('action', action);
      if (automatic) body.set('automatic', 'yes');
      const response = await fetch(form.getAttribute('action'), {method: 'POST', credentials: 'same-origin',
        headers: {Accept: 'application/json'}, body});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Payment status is unavailable. Check before collecting again.');
      if (form.matches('[data-terminal-health]')) {
        form.dataset.active = String(result.active);
        announce(form, result.message);
      } else if (!automatic || refundChanged(result) || result.state !== form.dataset.state || result.status !== form.dataset.status || result.attention !== form.dataset.attention) {
        const dialog = form.closest('dialog');
        if (!await refresh()) throw new Error('Could not refresh payment details. Check before collecting again.');
        dialog?.close();
        if (action === 'collect') {
          const heading = document.getElementById('money-title');
          heading?.focus({preventScroll: true});
          heading?.scrollIntoView({block: 'start', behavior: 'instant'});
        }
      }
      failures = 0;
    } catch (error) {
      failures++;
      announce(form, automatic ? 'Automatic status check unavailable. Check the status before trying again.' : error.message);
    } finally {
      busy = false;
      if (!automatic) progress(form, '');
      buttons.forEach(button => { button.disabled = false; });
      schedule();
    }
  };
  const pollForms = () => {
    return [...document.querySelectorAll('[data-terminal-poll], [data-refund-poll], [data-terminal-health][data-active="true"]')]
      .filter(form => form.dataset.paused !== 'true');
  };
  const poll = () => {
    let refundIncluded = false;
    const forms = pollForms().filter(form => {
      if (!form.matches('[data-refund-poll]')) return true;
      if (refundIncluded) return false;
      refundIncluded = true;
      return true;
    });
    if (!forms.length || failures >= 3) return;
    const form = forms[pollIndex++ % forms.length];
    if (document.hidden || busy || document.querySelector('dialog[open]')) { schedule(); return; }
    send(form, form.matches('[data-terminal-health]') ? 'ping' : form.matches('[data-refund-poll]') ? 'check_all' : 'check', true);
  };
  const schedule = () => {
    clearTimeout(timer);
    const delays = pollForms().map(form => {
      const started = Number(form.dataset.pollStarted);
      if (!started) return 5000;
      const age = Date.now() - started * 1000;
      return age < 60000 ? 2000 : age < 120000 ? 5000 : 15000;
    });
    if (delays.length) timer = setTimeout(poll, Math.min(...delays));
  };
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form.matches('[data-booking-refund]')) {
      if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
      form.dataset.submitting = 'true';
      form.setAttribute('aria-busy', 'true');
      form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
      progress(form, 'Submitting refund...');
      return;
    }
    if (!form.matches('[data-terminal-collect], [data-terminal-poll], [data-refund-poll], [data-terminal-health]')) return;
    event.preventDefault();
    if (form.matches('[data-terminal-collect]')) {
      summary();
      if (!form.reportValidity()) return;
    }
    send(form, event.submitter?.value || 'collect');
  });
  document.addEventListener('input', summary);
  document.addEventListener('change', summary);
  document.addEventListener('booking:content-updated', () => { summary(); schedule(); });
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('[data-booking-refund]').forEach(form => {
      delete form.dataset.submitting;
      form.removeAttribute('aria-busy');
      form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = false; });
      progress(form, '');
    });
  });
  summary(); schedule();
})();
