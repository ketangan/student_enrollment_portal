(() => {
  const read = id => JSON.parse(document.getElementById(id)?.textContent || 'null');
  let catalog = read('party-catalog');
  let partyPicker;
  let selectedDay = '';
  function setupPartyTimes(slots, direct) {
    const box = document.getElementById('party-time-picker');
    if (!box || !window.flatpickr) return;
    partyPicker?.destroy(); partyPicker = null;
    box.hidden = !direct;
    slots.hidden = direct;
    if (!direct) return;
    slots.required = false;
    const dates = read('party-slot-dates') || {};
    const offered = [...slots.options].filter(option => option.value && !option.disabled && dates[option.value]);
    const days = [...new Set(offered.map(option => dates[option.value].date))].sort();
    const buttons = document.getElementById('party-time-buttons');
    const heading = document.getElementById('party-time-date');
    const renderTimes = day => {
      buttons.replaceChildren();
      heading.textContent = day ? new Intl.DateTimeFormat(undefined, {dateStyle:'long'}).format(new Date(`${day}T12:00:00`)) : (days.length ? 'Choose a date' : 'No party times are currently offered.');
      for (const option of offered.filter(option => dates[option.value].date === day)) {
        const button = document.createElement('button');
        button.type = 'button'; button.textContent = dates[option.value].time;
        button.setAttribute('aria-pressed', String(slots.value === option.value));
        button.addEventListener('click', () => {
          slots.value = option.value;
          slots.dispatchEvent(new Event('change', {bubbles:true}));
          document.getElementById('party-submit').disabled = false;
          for (const other of buttons.children) other.setAttribute('aria-pressed', String(other === button));
        });
        buttons.append(button);
      }
    };
    const selected = dates[slots.value]?.date || (days.includes(selectedDay) ? selectedDay : '');
    selectedDay = selected;
    partyPicker = window.flatpickr(document.getElementById('party-date'), {
      inline:true, dateFormat:'Y-m-d', enable:days, disableMobile:true, monthSelectorType:'static',
      appendTo:document.getElementById('party-date-calendar'), defaultDate:selected,
      onChange:(_, day) => { selectedDay = day; slots.value = ''; slots.dispatchEvent(new Event('change', {bubbles:true})); document.getElementById('party-submit').disabled = true; renderTimes(day); },
      onReady:(_, __, picker) => {
        picker.calendarContainer.classList.add('booking-calendar');
        picker.calendarContainer.setAttribute('aria-label', 'Choose a party date');
        for (const direction of ['prev', 'next']) {
          const nav = picker.calendarContainer.querySelector(`.flatpickr-${direction}-month`);
          nav.setAttribute('aria-label', direction === 'prev' ? 'Previous month' : 'Next month');
          nav.setAttribute('role', 'button'); nav.setAttribute('tabindex', '0');
          nav.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); nav.click(); } });
        }
      },
    });
    if (!selected && days.length) partyPicker.jumpToDate(days[0]);
    renderTimes(selected);
    document.getElementById('party-submit').disabled = !slots.value;
  }
  function updateGuests() {
    const editor = document.querySelector('[data-guest-editor]');
    if (!editor) return;
    const rows = [...editor.querySelectorAll('.guest-row')];
    const limit = Math.min(50, Math.max(0, Number(document.getElementById('id_group_size').value) || 0));
    rows.forEach((row, index) => {
      row.querySelector('[data-guest-number]').textContent = `Guest ${index + 1}`;
      row.querySelector('[data-remove-guest]').setAttribute('aria-label', `Remove guest ${index + 1}`);
      row.querySelectorAll('[name],[id],[for]').forEach(el => {
        for (const attr of ['name', 'id', 'for']) {
          if (el.hasAttribute(attr)) el.setAttribute(attr, el.getAttribute(attr).replace(/materials-(?:\d+|__prefix__)-/g, `materials-${index}-`));
        }
      });
    });
    document.getElementById('id_materials-TOTAL_FORMS').value = rows.length;
    document.getElementById('add-guest').disabled = !limit || rows.length >= limit;
    document.getElementById('guest-count').textContent = rows.length > limit
      ? 'Remove extra guests to match the number of participants.'
      : `${rows.length} of ${limit} guests`;
    window.lucide?.createIcons();
  }
  document.addEventListener('input', event => {
    if (event.target.id === 'id_group_size') updateGuests();
  });
  document.addEventListener('click', event => {
    const remove = event.target.closest('[data-remove-guest]');
    if (remove) { remove.closest('.guest-row').remove(); updateGuests(); return; }
    if (!event.target.closest('#add-guest')) return;
    const button = document.getElementById('add-guest');
    if (button.disabled) return;
    document.getElementById('material-rows').append(document.getElementById('material-template').content.cloneNode(true));
    updateGuests(); setupMaterials();
  });
  function setupMaterials() {
    if (!catalog) return;
    document.querySelectorAll('#material-rows .material-row').forEach(row => {
      if (row.dataset.visualReady) return;
      const design = row.querySelector('select[name$="-design"]'), color = row.querySelector('select[name$="-color"]');
      if (!design || !color || !row.querySelector('.visual-picker')) return;
      row.dataset.visualReady = '1';
      const recipe = value => catalog.variants.find(v => v.options.includes(Number(design.value)) && v.options.includes(Number(value ?? color.value)));
      const update = window.PontoraCatalogPicker.mount(row, design, color, catalog, {
        recipe: () => recipe(), available: value => !design.value || Boolean(recipe(value)),
        change: () => { if (color.value && !recipe()) color.value = ''; update(); },
      });
      for (const select of [design, color]) {
        const field = select.closest('.form-field');
        field.hidden = !field.querySelector('.errorlist');
        select.hidden = true;
        field.querySelector('label').hidden = field.hidden;
      }
      row.querySelector('.visual-picker').hidden = false;
    });
  }
  function setup() {
    const occasion = document.getElementById('id_occasion');
    if (occasion) {
      const updateBirthday = () => {
        for (const name of ['birthday_name', 'birthday_age']) {
          const input = document.getElementById(`id_${name}`);
          if (!input) continue;
          const show = /\bbirthday\b/i.test(occasion.value);
          input.closest('[data-party-field]').hidden = !show;
          input.disabled = !show;
        }
      };
      occasion.addEventListener('input', updateBirthday);
      updateBirthday();
    }
    const select = document.getElementById('id_package'), details = read('party-package-details');
    if (select && details) {
      const update = () => {
        const selected = details[select.value];
        document.getElementById('party-package-summary').textContent = selected ? selected.text : '';
        const packageOnly = document.getElementById('party-request-form')?.dataset.packagesOnly === 'true';
        const staff = document.getElementById('party-request-form')?.dataset.staff === 'true';
        const direct = packageOnly || Boolean(selected?.direct);
        const slots = document.getElementById('id_party_slot');
        if (slots) {
          const mapping = read('party-slot-packages') || {};
          for (const option of slots.options) {
            option.hidden = Boolean(option.value && mapping[option.value] !== select.value);
            option.disabled = option.hidden;
          }
          if (slots.value && mapping[slots.value] !== select.value) slots.value = '';
          slots.disabled = !direct;
          slots.required = direct;
          slots.closest('[data-party-field]').hidden = !direct;
          const preferred = document.getElementById('id_starts_at');
          preferred.disabled = direct;
          preferred.required = !direct;
          preferred.closest('[data-party-field]').hidden = direct;
          document.getElementById('party-submit').textContent = staff ? 'Create booking' : direct ? 'Continue to payment' : 'Send party request';
          document.getElementById('party-next-step').textContent = staff ? 'Payment is recorded separately on the reservation.' : direct ? 'Your party is confirmed after payment.' : 'Subject to availability and staff confirmation.';
          const noTimes = direct && ![...slots.options].some(option => option.value && !option.disabled);
          document.getElementById('party-submit').disabled = noTimes;
          if (noTimes) document.getElementById('party-next-step').textContent = select.value ? 'No party times are currently offered. Choose another package or location.' : 'Choose a package to see available times.';
          setupPartyTimes(slots, direct);
        }
        document.querySelectorAll('[data-custom-party-field]').forEach(field => {
          field.hidden = packageOnly || Boolean(selected);
          field.querySelectorAll('input, select').forEach(input => { input.disabled = packageOnly || Boolean(selected); });
        });
      };
      select.addEventListener('change', update); update();
    }
    setupMaterials();
    updateGuests();
  }
  setup();
  document.addEventListener('party-material-added', setupMaterials);
  const location = document.getElementById('party-location'), form = document.getElementById('party-request-form');
  if (location && (!form || form.dataset.customerSteps === 'true')) location.addEventListener('change', () => location.form.requestSubmit());
  if (form?.dataset.customerSteps === 'true') return;
  if (!location || !form) return;
  let current = location.value, pending;
  location.addEventListener('change', async () => {
    if (!current || !location.value) {
      const url = new URL(location.form.action);
      if (location.value) url.searchParams.set('offering', location.value);
      if (new URL(form.action).searchParams.has('preview')) url.searchParams.set('preview', '1');
      window.location.assign(url.toString());
      return;
    }
    pending?.abort(); pending = new AbortController();
    const controller = pending, chosen = location.value;
    const url = new URL(form.action); url.searchParams.set('offering', chosen);
    const status = document.getElementById('party-loading'), submit = form.querySelector('button[type="submit"]');
    status.hidden = false; status.textContent = 'Updating available packages and designs...'; submit.disabled = true;
    try {
      const response = await fetch(url, {signal: controller.signal, credentials: 'same-origin'});
      if (!response.ok) throw new Error('Unable to load location');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const replacement = page.getElementById('party-request-form');
      if (controller !== pending) return;
      if (!replacement) throw new Error('Location form unavailable');
      for (const name of ['package', 'space', 'occasion', 'birthday_name', 'birthday_age', 'age_range', 'party_slot']) {
        const old = form.querySelector(`[data-party-field="${name}"]`), next = replacement.querySelector(`[data-party-field="${name}"]`);
        if (old && next && ['occasion', 'birthday_name', 'birthday_age', 'age_range'].includes(name)) next.querySelector('input').value = old.querySelector('input').value;
        if (old && next) old.replaceWith(next);
        else if (old) old.remove();
        else if (next) {
          const materialArea = document.getElementById('party-materials');
          materialArea.parentNode.insertBefore(next, name === 'package' ? materialArea.parentNode.firstChild : materialArea);
        }
      }
      document.getElementById('party-materials').replaceWith(page.getElementById('party-materials'));
      form.querySelector('[name="request_token"]').value = replacement.querySelector('[name="request_token"]').value;
      for (const id of ['party-package-details', 'party-slot-packages', 'party-slot-dates', 'party-catalog']) {
        document.getElementById(id)?.remove();
        const data = page.getElementById(id); if (data) document.body.append(data);
      }
      const preferred = document.getElementById('id_starts_at');
      if (preferred) {
        preferred.disabled = false;
        preferred.required = true;
        preferred.closest('[data-party-field]').hidden = false;
        document.getElementById('party-submit').textContent = 'Send party request';
        document.getElementById('party-next-step').textContent = 'Subject to availability and staff confirmation.';
      }
      catalog = read('party-catalog'); form.action = url; current = chosen; setup();
      history.replaceState(null, '', url); status.hidden = true;
    } catch (error) {
      if (error.name === 'AbortError' || controller !== pending) return;
      location.value = current; status.textContent = 'Could not change location. Please try again.';
    } finally {
      if (controller === pending) {
        submit.disabled = false;
        const slots = document.getElementById('id_party_slot');
        if (slots && !slots.disabled && ![...slots.options].some(option => option.value && !option.disabled)) submit.disabled = true;
        if (slots && !slots.disabled && document.getElementById('party-time-picker') && !slots.value) submit.disabled = true;
      }
    }
  });
})();
