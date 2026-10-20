(() => {
  const read = id => JSON.parse(document.getElementById(id)?.textContent || 'null');
  let catalog = read('party-catalog');
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
    const selections = document.querySelector('[data-party-selections]');
    const selectionsNow = document.getElementById('id_selections_now');
    if (selections && selectionsNow) {
      const updateSelections = () => { selections.hidden = !selectionsNow.checked; };
      selectionsNow.addEventListener('change', updateSelections);
      updateSelections();
    }
    const select = document.getElementById('id_package'), details = read('party-package-details');
    if (select && details) {
      const update = () => {
        const selected = details[select.value];
        document.getElementById('party-package-summary').textContent = selected ? selected.text : '';
        document.querySelectorAll('[data-custom-party-field]').forEach(field => {
          field.hidden = Boolean(selected);
          field.querySelectorAll('input, select').forEach(input => { input.disabled = Boolean(selected); });
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
  if (!location || !form) return;
  let current = location.value, pending;
  location.addEventListener('change', async () => {
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
      for (const name of ['package', 'space', 'occasion', 'age_range', 'selections_now']) {
        const old = form.querySelector(`[data-party-field="${name}"]`), next = replacement.querySelector(`[data-party-field="${name}"]`);
        if (old && next && ['occasion', 'age_range'].includes(name)) next.querySelector('input').value = old.querySelector('input').value;
        if (old && next) old.replaceWith(next);
        else if (old) old.remove();
        else if (next) form.insertBefore(next, name === 'package' ? form.firstChild : document.getElementById('party-materials'));
      }
      document.getElementById('party-materials').replaceWith(page.getElementById('party-materials'));
      form.querySelector('[name="request_token"]').value = replacement.querySelector('[name="request_token"]').value;
      for (const id of ['party-package-details', 'party-catalog']) {
        document.getElementById(id)?.remove();
        const data = page.getElementById(id); if (data) document.body.append(data);
      }
      catalog = read('party-catalog'); form.action = url; current = chosen; setup();
      history.replaceState(null, '', url); status.hidden = true;
    } catch (error) {
      if (error.name === 'AbortError' || controller !== pending) return;
      location.value = current; status.textContent = 'Could not change location. Please try again.';
    } finally { if (controller === pending) submit.disabled = false; }
  });
})();
