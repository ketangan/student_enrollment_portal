(() => {
  window.lucide?.createIcons();
  document.getElementById('print-report')?.addEventListener('click', () => window.print());
  document.querySelectorAll('[data-copy-link]').forEach(button => {
    let resetTimer;
    button.addEventListener('click', async () => {
      const status = button.parentElement.querySelector('[data-copy-status]');
      window.clearTimeout(resetTimer);
      try {
        await navigator.clipboard.writeText(button.dataset.copyLink);
        status.textContent = 'Link copied';
        resetTimer = window.setTimeout(() => { status.textContent = ''; }, 3000);
      } catch (_) {
        status.textContent = 'Could not copy. Please try again.';
      }
    });
  });
  document.querySelectorAll('[data-setup-dialog]').forEach(dialog => {
    dialog.addEventListener('input', event => {
      if (event.target.form?.id !== 'package-edit') return;
      dialog.querySelectorAll('[data-party-time-submit]').forEach(button => { button.disabled = true; });
      const notice = dialog.querySelector('[data-save-rules-first]');
      if (notice) notice.hidden = false;
    });
    dialog.querySelectorAll('[data-close-setup-dialog]').forEach(button => {
      button.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('close', () => {
      if (dialog.dataset.returnUrl) {
        window.location.replace(dialog.dataset.returnUrl);
        return;
      }
      const url = new URL(window.location.href);
      const packageId = url.searchParams.get('edit');
      url.searchParams.delete('edit');
      url.searchParams.delete('section');
      url.hash = `package-${packageId}`;
      window.location.replace(url);
    });
    dialog.showModal();
  });
  const initializedPayments = new WeakSet();
  const setupPayments = () => {
  document.querySelectorAll('[data-terminal-payment]').forEach(form => {
    if (initializedPayments.has(form)) return;
    initializedPayments.add(form);
    const amount = form.elements.amount;
    const balance = Number(form.elements.expected_balance.value);
    const total = Number(form.elements.expected_total.value);
    const row = form.querySelector('[data-terminal-extras]');
    const summary = form.querySelector('[data-extras-summary]');
    const update = () => {
      const cents = Math.round(Number(amount.value) * 100);
      const extra = Number.isFinite(cents) ? Math.max(0, cents - balance) : 0;
      row.hidden = extra === 0;
      summary.textContent = extra ? `$${(extra / 100).toFixed(2)} in extra purchases. New total: $${((total + extra) / 100).toFixed(2)}.` : '';
      const after = form.querySelector('[data-payment-after]');
      if (after) after.textContent = extra ? `New total: $${((total + extra) / 100).toFixed(2)}` : `Balance after: $${(Math.max(0, balance - (cents || 0)) / 100).toFixed(2)}`;
      const submit = form.querySelector('[data-payment-submit]');
      if (submit) submit.textContent = Number.isFinite(cents) && cents > 0 ? `${extra ? 'Add charge & record' : 'Record'} $${(cents / 100).toFixed(2)}` : 'Record payment';
      const receipt = form.querySelector('[data-payment-receipt]');
      if (receipt) receipt.hidden = form.elements.method.value === 'cash';
    };
    amount.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
  });
  document.querySelectorAll('[data-payment-dialog]').forEach(button => {
    if (initializedPayments.has(button)) return;
    initializedPayments.add(button);
    const dialog = document.getElementById(button.dataset.paymentDialog);
    if (!dialog) return;
    button.addEventListener('click', () => dialog.showModal());
    dialog.querySelectorAll('[data-close-payment-dialog]').forEach(close => {
      close.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('close', () => button.focus());
    if (dialog.hasAttribute('data-payment-error')) dialog.showModal();
  });
  };
  setupPayments();
  document.addEventListener('booking:content-updated', setupPayments);
  document.querySelectorAll('[name="payment_at_booking"]').forEach(select => {
    const row = select.form?.querySelector('[data-booking-deposit]');
    if (!row) return;
    const update = () => {
      row.hidden = select.value !== 'deposit';
      const input = row.querySelector('input');
      if (input) {
        input.required = !row.hidden;
        input.disabled = row.hidden;
      }
    };
    select.addEventListener('change', update);
    update();
  });
  const nav = document.getElementById('booking-nav');
  const toggle = document.getElementById('booking-nav-toggle');
  const locationFilter = document.getElementById('dashboard-location-filter');
  const reservationFilters = document.getElementById('reservation-filters');
  reservationFilters?.addEventListener('change', event => {
    if (event.target.matches('select, input[type="checkbox"]')) reservationFilters.requestSubmit();
  });
  reservationFilters?.addEventListener('click', event => {
    const option = event.target.closest('[data-filter-field]');
    if (!option) return;
    reservationFilters.elements[option.dataset.filterField].value = option.dataset.filterValue;
    reservationFilters.requestSubmit();
  });
  locationFilter?.addEventListener('change', () => locationFilter.requestSubmit());
  const setNav = open => {
    if (!nav || !toggle) return;
    nav.classList.toggle('dash-nav--mobile-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  };
  toggle?.addEventListener('click', () => {
    setNav(toggle.getAttribute('aria-expanded') !== 'true');
  });
  document.addEventListener('click', event => {
    if (!nav?.contains(event.target) && !toggle?.contains(event.target)) setNav(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (toggle?.getAttribute('aria-expanded') === 'true') {
      setNav(false);
      toggle.focus();
    }
  });
  window.matchMedia('(max-width: 900px)').addEventListener('change', () => setNav(false));
})();
