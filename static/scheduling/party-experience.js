(() => {
  const form = document.getElementById('party-request-form');
  if (!form || form.dataset.customerSteps !== 'true') return;
  const packages = JSON.parse(document.getElementById('party-package-details').textContent);
  const slots = JSON.parse(document.getElementById('party-slot-dates').textContent);
  const panels = [...form.querySelectorAll('[data-party-panel]')];
  let current = 1;
  const money = cents => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(cents / 100);
  const text = (id, value) => { document.getElementById(id).textContent = value; };
  const countMode = form.elements.participant_count_mode;
  let acknowledgedPackage = form.elements.package.value;
  const updateCount = () => {
    if (!countMode) return;
    const range = countMode.value === 'estimate';
    const exact = form.elements.group_size, estimate = form.elements.participant_estimate;
    exact.closest('[data-party-field]').hidden = range;
    estimate.closest('[data-party-field]').hidden = !range;
    estimate.disabled = !range;
    estimate.required = range;
    if (range) {
      const match = estimate.value.trim().match(/^(\d{1,5})(?:\s*(?:-|\u2013|to)\s*(\d{1,5}))?(?:\s+guests?)?$/i);
      exact.value = match ? match[2] || match[1] : '';
    }
    // Hidden exact count is derived by the server for range bookings.
    exact.disabled = range;
  };
  const update = () => {
    if (acknowledgedPackage !== form.elements.package.value) {
      for (const name of ['waiver_acknowledged', 'attendance_acknowledged', 'policy_acknowledged']) {
        if (form.elements[name]) form.elements[name].checked = false;
      }
      acknowledgedPackage = form.elements.package.value;
    }
    const p = packages[form.elements.package.value];
    updateCount();
    if (p && !form.elements.group_size.value && countMode?.value !== 'estimate') form.elements.group_size.value = p.included;
    const count = Number(form.elements.group_size.value);
    const slot = slots[form.elements.party_slot.value];
    if (!p) {
      text('party-summary-name', 'Choose a package');
      text('party-summary-time', ''); text('party-summary-guests', '');
      for (const id of ['subtotal','tax','total','due','later']) text(`party-${id}`, '\u2014');
      return;
    }
    form.elements.group_size.max = p.included + p.extra_limit;
    const countHelp = document.getElementById('party-count-help');
    if (countHelp) countHelp.textContent = `Maximum ${p.included + p.extra_limit} participants.`;
    form.querySelectorAll('[data-acknowledgment-text]').forEach(label => {
      label.textContent = label.dataset.template.replace('{observers}', String(p.observers));
    });
    const subtotal = p.price_cents + Math.max(0, count - p.included) * p.extra_cents;
    const taxRateThousandths = Math.round(Number(p.tax_rate) * 1000);
    const tax = Math.floor((subtotal * taxRateThousandths + 50000) / 100000);
    const total = subtotal + tax, due = p.deposit_cents || total;
    text('party-summary-name', p.name);
    const date = slot ? new Date(`${slot.date}T12:00:00`).toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric', year:'numeric'}) : '';
    const duration = [Math.floor(p.duration / 60) ? `${Math.floor(p.duration / 60)} hr` : '', p.duration % 60 ? `${p.duration % 60} min` : ''].filter(Boolean).join(' ');
    text('party-summary-time', slot ? `${date}\n${slot.time.replace(/^0/, '')} · ${duration}` : 'Choose a date and time');
    text('party-summary-guests', [count ? `${count} plushie maker${count === 1 ? '' : 's'}` : '', p.observers ? `${p.observers} observers included` : ''].filter(Boolean).join(' · '));
    for (const [id, value] of Object.entries({subtotal,tax,total,due,later:total-due})) text(`party-${id}`, money(value));
    const review = document.getElementById('party-review-details');
    review.replaceChildren();
    for (const name of ['organizer_name','organizer_email','organizer_phone','group_size','participant_estimate','age_range','occasion','customer_notes']) {
      const input = form.elements[name];
      if (!input.value || input.disabled) continue;
      const row = document.createElement('div'), title = document.createElement('dt'), value = document.createElement('dd');
      title.textContent = (input.labels[0]?.textContent || name).replace(/\s*\*$/, '');
      value.textContent = input.value; row.append(title,value); review.append(row);
    }
  };
  const show = (next, focus = false) => {
    current = next;
    document.querySelector('.party-experience').dataset.step = next;
    panels.forEach(panel => { panel.hidden = Number(panel.dataset.partyPanel) !== next; });
    document.getElementById('party-location-form').hidden = next !== 1;
    document.querySelectorAll('[data-booking-step]').forEach(step => {
      const n = Number(step.dataset.bookingStep);
      if (n === next) step.setAttribute('aria-current','step'); else step.removeAttribute('aria-current');
      step.classList.toggle('done', n < next);
    });
    update();
    if (focus) { panels[next-1].tabIndex = -1; panels[next-1].focus(); }
  };
  const valid = panel => {
    const fields = [...panel.querySelectorAll('input,select,textarea')].filter(input => !input.disabled && input.type !== 'hidden');
    const invalid = fields.find(input => !input.checkValidity());
    if (invalid) { show(Number(panel.dataset.partyPanel)); invalid.reportValidity(); return false; }
    if (panel.dataset.partyPanel === '1' && !form.elements.party_slot.value) {
      document.getElementById('party-time-date').textContent = 'Choose an available party time.';
      return false;
    }
    return true;
  };
  form.addEventListener('input', update); form.addEventListener('change', update);
  const errorSummary = document.getElementById('party-form-errors');
  const clearErrors = () => {
    form.querySelectorAll('.errorlist').forEach(list => list.remove());
    form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    errorSummary.replaceChildren();
  };
  const renderErrors = errors => {
    let firstPanel = null, firstInput = null;
    for (const [name, messages] of Object.entries(errors)) {
      const input = form.elements.namedItem(name);
      const wrapper = input?.closest('[data-party-field]');
      const list = document.createElement('ul');
      list.className = 'errorlist';
      list.setAttribute('role', 'alert');
      messages.forEach(error => {
        const li = document.createElement('li'); li.textContent = error.message; list.append(li);
      });
      (wrapper || errorSummary).append(list);
      if (input) input.setAttribute('aria-invalid', 'true');
      const panel = wrapper?.closest('[data-party-panel]');
      if (panel && (!firstPanel || Number(panel.dataset.partyPanel) < Number(firstPanel.dataset.partyPanel))) {
        firstPanel = panel; firstInput = input;
      }
    }
    show(firstPanel ? Number(firstPanel.dataset.partyPanel) : current, true);
    if (firstInput && !firstInput.hidden) firstInput.focus();
  };
  let validating = false;
  form.querySelectorAll('[data-party-next]').forEach(button => button.addEventListener('click', async () => {
    if (validating) return;
    const next = Number(button.dataset.partyNext);
    if (next < current) { show(next, true); return; }
    if (!valid(panels[current-1])) return;
    if (next !== 3) { show(next, true); return; }
    clearErrors();
    validating = true;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    // Use the same authoritative validators as checkout, without creating a booking.
    const data = new FormData(form);
    data.set('validate_only', '1');
    try {
      const response = await fetch(form.action, {method:'POST', body:data, credentials:'same-origin'});
      if (!response.ok) throw new Error('Validation unavailable');
      const result = await response.json();
      if (Object.keys(result.errors).length) renderErrors(result.errors);
      else show(3, true);
    } catch (_error) {
      errorSummary.textContent = 'We could not check your details. Please try again before continuing.';
    } finally {
      validating = false;
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  }));
  form.addEventListener('submit', event => {
    if (validating) { event.preventDefault(); return; }
    if (current !== 3) {
      event.preventDefault();
      panels[current-1].querySelector('[data-party-next="' + (current+1) + '"]').click();
      return;
    }
    for (const panel of panels) if (!valid(panel)) { event.preventDefault(); show(Number(panel.dataset.partyPanel), true); return; }
  });
  const errors = panels.find(panel => panel.querySelector('.errorlist'));
  show(errors ? Number(errors.dataset.partyPanel) : 1);
})();
