(() => {
  "use strict";
  const element = (tag, text) => {const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el;};
  function mount(root, url) {
    root.querySelectorAll('[data-reservation-fields]').forEach(form => {
      if (form.dataset.reservationMounted) return;
      form.dataset.reservationMounted = 'true';
      const quantity = form.querySelector('[name="quantity"]');
      if (!quantity) return;
      const balance = form.querySelector('[data-balance]');
      const breakdown = form.querySelector('[data-price-breakdown]');
      const unitPrice = form.querySelector('[data-unit-price]');
      function clearPrice() {
        breakdown?.replaceChildren();
        if (unitPrice) unitPrice.hidden = true;
      }
      const submit = form.querySelector('button[type="submit"]');
      const picker = form.querySelector('[data-reservation-picker]');
      const select = form.querySelector('select[name="session"]');
      const selectedSummary = picker?.querySelector('[data-selected-session]');
      let options = [], week, quoteVersion = 0, quoteTimer, quotedQuantity;
      let sessionControls = [];
      let quoteValid = false;
      const seatLimit = option => option.limit ?? option.open;
      function validity() {
        for (const {radio, option} of sessionControls) {
          radio.disabled = seatLimit(option) < Math.max(1, Number(quantity.value) || 1);
        }
        const chosen = options.find(option => option.id === select?.value);
        if (selectedSummary) {
          const tooMany = chosen && Number(quantity.value) > seatLimit(chosen);
          selectedSummary.hidden = !chosen || (!tooMany && sessionControls.some(({option}) => option.id === chosen.id));
          selectedSummary.textContent = chosen ? `Selected session: ${chosen.label}${tooMany ? ` · Fits up to ${seatLimit(chosen)} seats. Choose another session or reduce seats.` : ''}` : '';
        }
        if (select) {
          quantity.max = String(chosen ? seatLimit(chosen) : 0);
          quantity.dispatchEvent(new Event('seatlimit', {bubbles:true}));
        }
        const fits = !select || Boolean(chosen && Number(quantity.value) > 0 && Number(quantity.value) <= seatLimit(chosen));
        if (submit) submit.disabled = !quoteValid || !fits;
        form.dataset.reservationBlocked = String(!quoteValid || !fits);
      }
      async function quote(version) {
        try {
          const target = new URL(url || form.action, location.href);
          target.searchParams.set('quote_quantity', quantity.value);
          const response = await fetch(target, {credentials:'same-origin', headers:{Accept:'application/json'}});
          const result = await response.json();
          if (version !== quoteVersion || !form.isConnected) return;
          quoteValid = response.ok && Number.isInteger(result.balance);
          if (!quoteValid) quotedQuantity = undefined;
          balance.textContent = quoteValid ? new Intl.NumberFormat(undefined, {style:'currency', currency:result.currency}).format(result.balance / 100) : result.error || 'Unable to calculate balance.';
          clearPrice();
          if (quoteValid) {
            const money = cents => new Intl.NumberFormat(undefined, {style:'currency', currency:result.currency}).format(cents / 100);
            if (unitPrice && Number.isInteger(result.unit_price)) {
              unitPrice.textContent = `${result.quantity} × ${money(result.unit_price)} per person + tax`;
              unitPrice.hidden = false;
            }
            for (const row of result.rows || []) {
              const line = element('div');
              line.append(element('dt', row.label), element('dd', money(row.cents)));
              breakdown?.append(line);
            }
          }
        } catch (_) {
          if (version !== quoteVersion || !form.isConnected) return;
          quoteValid = false; quotedQuantity = undefined;
          clearPrice();
          balance.textContent = 'Unable to calculate balance. Change the seat count to retry.';
        }
        validity();
      }
      function changed() {
        // Blur fires change after input. Do not disable Save again for the
        // identical value just as the user clicks it.
        if (quotedQuantity === quantity.value) return;
        quotedQuantity = quantity.value;
        clearTimeout(quoteTimer); quoteValid = false;
        clearPrice();
        const version = ++quoteVersion;
        balance.textContent = 'Calculating...'; validity();
        quoteTimer = setTimeout(() => quote(version), 250);
      }
      if (picker && select) {
        options = JSON.parse(picker.querySelector('script').textContent);
        const selected = options.find(option => option.id === select.value);
        week = new Date(`${selected?.date || picker.dataset.today}T00:00:00Z`);
        week.setUTCDate(week.getUTCDate() - week.getUTCDay());
        form.querySelector('[data-session-select]').hidden = true;
        select.required = false;
        function render() {
          const until = new Date(week); until.setUTCDate(until.getUTCDate() + 7);
          picker.querySelector('[data-week-label]').textContent = new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',timeZone:'UTC'}).format(week);
          const container = picker.querySelector('[data-session-options]'); container.replaceChildren();
          sessionControls = [];
          const rows = options.filter(option => option.date >= week.toISOString().slice(0,10) && option.date < until.toISOString().slice(0,10));
          for (const option of rows) {
            const label = element('label'); label.className = 'reservation-session-option';
            const radio = element('input'); radio.type = 'radio'; radio.name = 'session_choice'; radio.value = option.id;
            radio.checked = select.value === option.id;
            radio.disabled = seatLimit(option) < Math.max(1, Number(quantity.value) || 1);
            sessionControls.push({radio, option});
            const text = element('span'); text.append(element('strong', option.label), element('small', `${option.name} · ${option.duration} min`));
            label.append(radio, text, element('span', option.open ? `${option.open} open` : 'Full'));
            radio.addEventListener('change', () => {
              if (radio.disabled) return;
              select.value = option.id; validity();
            });
            container.append(label);
          }
          if (!rows.length) container.append(element('p', 'No sessions this week.'));
          validity();
        }
        picker.querySelectorAll('[data-week]').forEach(button => button.addEventListener('click', () => {week.setUTCDate(week.getUTCDate() + 7 * Number(button.dataset.week));render();}));
        render();
      }
      quantity.addEventListener('input', changed);
      quantity.addEventListener('change', changed);
      form.addEventListener('submit', event => {if (form.dataset.reservationBlocked === 'true') event.preventDefault();});
      changed();
    });
  }
  window.ReservationFields = {mount};
  mount(document);
})();
