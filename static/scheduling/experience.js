(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const data = id => JSON.parse(document.getElementById(id).textContent);
  const icons = () => window.lucide?.createIcons();
  const button = (label, fn, className = '') => {
    const node = document.createElement('button'); node.type = 'button'; node.textContent = label;
    node.className = className; node.addEventListener('click', fn); return node;
  };
  const dollars = cents => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(cents / 100);
  if ($('#sessions-data')) {
    const sessions = data('sessions-data'), studio = $('#studio'), count = $('#count'), workshop = $('#workshop');
    let day = '', month = '', selected = null;
    const dateLabel = (date, options) => new Intl.DateTimeFormat('en-US', {...options, timeZone: 'UTC'}).format(new Date(`${date}T12:00:00Z`));
    const locations = new Map(sessions.map(s => [s.location_id, s.location]));
    locations.forEach((name, id) => studio.add(new Option(name, String(id))));
    const workshops = () => {
      const rows = sessions.filter(s => String(s.location_id) === studio.value);
      const options = new Map(rows.map(s => [s.workshop_id, s.workshop]));
      workshop.replaceChildren(...[...options].map(([id, name]) => new Option(name, String(id))));
      workshop.disabled = !options.size;
    };
    const update = () => {
      const places = Number(count.value), rows = sessions.filter(s => String(s.location_id) === studio.value && String(s.workshop_id) === workshop.value);
      const days = [...new Set(rows.map(s => s.date))].sort();
      if (!days.includes(day)) day = days[0];
      const months = [...new Set(days.map(date => date.slice(0, 7)))];
      if (!months.includes(month)) month = day?.slice(0, 7) || '';
      const cells = [];
      if (month) {
        const first = new Date(`${month}-01T12:00:00Z`);
        const offset = (first.getUTCDay() + 6) % 7;
        const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
        for (let i = 0; i < offset; i++) {
          const blank = document.createElement('span'); blank.setAttribute('aria-hidden', 'true'); cells.push(blank);
        }
        for (let n = 1; n <= last; n++) {
          const date = `${month}-${String(n).padStart(2, '0')}`;
          const node = button(String(n), () => {day = date; selected = null; update();});
          node.dataset.date = date; node.disabled = !days.includes(date);
          node.setAttribute('aria-label', dateLabel(date, {weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'}));
          node.setAttribute('aria-pressed', String(day === date)); cells.push(node);
        }
      }
      $('#dates').replaceChildren(...cells);
      $('#calendar-month').textContent = month ? dateLabel(`${month}-01`, {month: 'long', year: 'numeric'}) : 'No available dates';
      $('#previous-month').disabled = months.indexOf(month) <= 0;
      $('#next-month').disabled = !month || months.indexOf(month) >= months.length - 1;
      $('#chosen-day').textContent = day ? dateLabel(day, {weekday: 'short', month: 'short', day: 'numeric'}) : 'Start time';
      const times = rows.filter(s => s.date === day);
      if (!times.some(s => s.id === selected?.id && s.places >= places)) selected = null;
      $('#times').replaceChildren(...times.map(s => {
        const node = button(s.time, () => {selected = s; update();});
        node.disabled = s.places < places; node.title = `${s.places} places available`;
        node.setAttribute('aria-pressed', String(selected?.id === s.id)); return node;
      }));
      const link = $('#start-booking'); link.classList.toggle('disabled', !selected);
      link.setAttribute('aria-disabled', String(!selected));
      if (selected) link.href = link.dataset.editUrl ? `${link.dataset.editUrl}?session=${selected.id}&participants=${places}` : link.dataset.url.replace('/0/', `/${selected.id}/`) + `?participants=${places}`;
      else link.removeAttribute('href');
      $('#availability').textContent = selected ? `${selected.places} places available. Times are local to this studio.` : (days.length ? 'Select a start time.' : 'No upcoming sessions available.');
      const price = Number($('#price-total').dataset.unit);
      $('#price-total').textContent = `${places} participant${places === 1 ? '' : 's'}: ${dollars(places * price)}`;
      $('#workshop-name').textContent = rows[0]?.workshop || 'Plushie-making workshop';
      $('#brand-location').textContent = $('#summary-location').textContent = locations.get(Number(studio.value)) || 'Choose a studio';
      $('#summary-session').textContent = selected ? `${dateLabel(day, {weekday: 'short', month: 'short', day: 'numeric'})} at ${selected.time}` : 'Choose your date and time';
      const minutes = selected?.duration_minutes ?? (new Set(rows.map(s => s.duration_minutes)).size === 1 ? rows[0].duration_minutes : null);
      $('#workshop-duration').textContent = minutes == null ? '' : (minutes % 60 === 0 ? `${minutes / 60} hour${minutes === 60 ? '' : 's'}` : `${minutes} minutes`);
    };
    const moveMonth = direction => {
      const months = [...new Set(sessions.filter(s => String(s.location_id) === studio.value && String(s.workshop_id) === workshop.value).map(s => s.date.slice(0, 7)))].sort();
      const next = months[months.indexOf(month) + direction];
      if (!next) return;
      month = next; day = sessions.filter(s => String(s.location_id) === studio.value && String(s.workshop_id) === workshop.value && s.date.startsWith(month)).map(s => s.date).sort()[0];
      selected = null; update();
    };
    $('#previous-month').addEventListener('click', () => moveMonth(-1));
    $('#next-month').addEventListener('click', () => moveMonth(1));
    studio.addEventListener('change', () => {day = ''; month = ''; selected = null; workshops(); update();});
    workshop.addEventListener('change', () => {day = ''; month = ''; selected = null; update();});
    count.addEventListener('change', update); workshops(); update();
  }
  if ($('#catalog-data')) {
    const config = data('catalog-data'), list = $('#participant-list');
    const visual = Boolean(config.options[config.design_group] && config.options[config.color_group]);
    let active = 0;
    const persons = () => $$('.participant', list);
    const deferred = person => Boolean($('[name$="-selections_deferred"]', person)?.checked);
    const field = (person, name) => $$('.person-fields label', person).find(el => el.dataset.field === name);
    const select = (person, name) => field(person, name)?.querySelector('select');
    const optionName = el => el.selectedOptions[0]?.textContent || '';
    const updates = new Map();
    const remove = person => {
      if (persons().length < 2) return;
      const index = persons().indexOf(person);
      updates.delete(person); person.remove();
      if (index < active) active--;
      rebuild();
    };
    const iconButton = (icon, label, action) => {
      const node = button('', action, 'participant-icon');
      node.setAttribute('aria-label', label); node.title = label;
      const symbol = document.createElement('i'); symbol.setAttribute('data-lucide', icon);
      symbol.setAttribute('aria-hidden', 'true'); node.append(symbol); return node;
    };
    const refresh = () => {
      persons().forEach(person => updates.get(person)?.());
      summarize();
    };
    const recipe = (person, color) => !deferred(person) && config.inventory.variants.find(row =>
      row.options.includes(Number(select(person, config.design_group)?.value)) &&
      row.options.includes(Number(color ?? select(person, config.color_group)?.value)));
    const enough = selections => materialAvailability.enough(selections, config.inventory.materials);
    const available = (person, colorId) => {
      const others = persons().filter(other => other !== person).map(other => recipe(other)).filter(Boolean);
      return enough([...others, recipe(person, colorId)]);
    };
    const rebuild = () => {
      const all = persons(); active = Math.min(active, all.length - 1);
      const later = $('#choose-later')?.checked;
      if ($('#choose-later')) all.forEach(person => {const input = $('[name$="-selections_deferred"]', person); if (input) input.checked = later;});
      $('#person-tabs').hidden = Boolean(later) && !config.age_required;
      if ($('#later-count')) {$('#later-count-field').hidden = !later; $('#later-count').value = all.length;}
      $('#add-person').hidden = Boolean(later);
      $('#id_participants-TOTAL_FORMS').value = all.length;
      all.forEach((person, index) => {
        person.hidden = (Boolean(later) && !config.age_required) || active !== index;
        $('.person-number', person).textContent = index + 1;
        $$('[name],[id],[for]', person).forEach(el => ['name', 'id', 'for'].forEach(attr => {
          if (el.hasAttribute(attr)) el.setAttribute(attr, el.getAttribute(attr).replace(/participants-(?:\d+|__prefix__)-/g, `participants-${index}-`));
        }));
        if (visual) {
          field(person, config.design_group).hidden = true;
          field(person, config.color_group).hidden = true;
        }
        $$('input,select', person).filter(input => input.type !== 'checkbox').forEach(input => {input.required = !deferred(person);});
        const ageInput = select(person, config.age_group);
        if (config.age_required && ageInput) ageInput.required = true;
        (config.deferred_groups || []).forEach(name => {
          const label = field(person, name);
          if (!label) return;
          if (!visual) label.hidden = deferred(person);
          $$('input,select', label).forEach(input => {input.disabled = deferred(person); input.required = !deferred(person);});
        });
        $('.visual-picker', person).hidden = !visual || deferred(person);
        $('.remove-person', person).hidden = all.length === 1;
      });
      $('#person-tabs').replaceChildren(...all.map((person, index) => {
        const name = $('input[type=text]', person).value.trim();
        const node = button(name || `Participant ${index+1}`, () => {active = index; rebuild();}, 'participant-select');
        node.setAttribute('aria-pressed', String(active === index));
        const wrapper = document.createElement('span'); wrapper.className = 'participant-tab';
        wrapper.setAttribute('data-active', String(active === index));
        wrapper.append(node);
        if (all.length > 1) wrapper.append(iconButton('x', `Remove ${name || `participant ${index+1}`}`, () => remove(person)));
        return wrapper;
      }));
      $('#add-person').disabled = all.length >= config.limit;
      $('.group-color').hidden = !visual || all.every(deferred);
      $('#selection-total').textContent = `${all.length} participant${all.length === 1 ? '' : 's'} / ${dollars(all.length*config.price)}`;
      refresh();
      icons();
    };
    const photo = (node, design) => window.PontoraCatalogPicker.photo(node, design, config.image);
    const summarize = () => {
      let complete = 0;
      $('#group-summary-list').replaceChildren(...persons().map((person, index) => {
        const name = $('input[type=text]', person).value.trim();
        const design = deferred(person) ? null : config.designs.find(d => d.name === optionName(select(person, config.design_group) || {selectedOptions: []}));
        const color = deferred(person) ? null : config.options[config.color_group]?.find(c => c.id === select(person, config.color_group)?.value);
        const row = document.createElement('div'); row.className = 'group-person';
        const exactPhoto = config.base_images_only ? null : recipe(person)?.image_url;
        const picture = document.createElement('div'); picture.className = 'plushie-image group-photo'; photo(picture, exactPhoto ? {...design, image_url: exactPhoto} : design);
        if (color && !exactPhoto && !config.base_images_only) {
          const tint = document.createElement('span'); tint.className = 'tint'; tint.style.setProperty('--color', color.swatch); picture.append(tint);
        }
        picture.setAttribute('aria-hidden', 'true');
        const details = document.createElement('div'); const title = document.createElement('strong'); title.textContent = name || `Participant ${index + 1}`;
        const description = document.createElement('span'); description.className = 'muted tiny';
        const ageSelect = select(person, config.age_group);
        const age = ageSelect?.value ? optionName(ageSelect) : '';
        description.textContent = deferred(person) ? 'Choosing at the studio' : [age && /^\d+$/.test(age) ? `Age ${age}` : age, design?.name, color?.name].filter(Boolean).join(' / ') || 'Selections pending';
        const ready = (name || deferred(person)) && $$('input,select', person).filter(el => el.required).every(el => el.value)
            && (deferred(person) || !config.tracks_stock || !visual || available(person));
        if (ready) complete++;
        const tab = $('#person-tabs').children[index]?.querySelector('button');
        if (tab) {
          const status = document.createElement('i');
          status.setAttribute('data-lucide', ready ? 'circle-check' : 'circle');
          status.setAttribute('aria-hidden', 'true'); status.className = 'participant-status';
          const label = document.createElement('span'); label.textContent = name || `Participant ${index+1}`;
          tab.replaceChildren(status, label);
          tab.setAttribute('data-ready', String(Boolean(ready)));
          tab.setAttribute('aria-label', `${label.textContent}${ready ? ', selections complete' : ', selections pending'}`);
        }
        const removeTab = $('#person-tabs').children[index]?.children[1];
        if (removeTab) { removeTab.setAttribute('aria-label', `Remove ${name || `participant ${index+1}`}`); removeTab.title = `Remove ${name || `participant ${index+1}`}`; }
        details.append(title, description); row.append(picture, details);
        if (persons().length > 1) row.append(iconButton('trash-2', `Remove ${name || `participant ${index+1}`}`, () => remove(person)));
        return row;
      }));
      const status = $('#group-completeness'); status.textContent = `${complete} of ${persons().length} participants ready`;
      const later = Boolean($('#choose-later')?.checked);
      $('#group-summary-list').hidden = later;
      if (later) status.textContent = `${persons().length} ${persons().length === 1 ? 'participant' : 'participants'} / Choosing designs later`;
      status.classList.toggle('all-ready', complete === persons().length);
      icons();
    };
    const setup = person => {
      $('[name$="-selections_deferred"]', person)?.addEventListener('change', rebuild);
      $('input[type=text]', person).addEventListener('change', rebuild);
      $('input[type=text]', person).addEventListener('input', summarize);
      select(person, config.age_group)?.addEventListener('change', summarize);
      $('.remove-person', person).addEventListener('click', () => remove(person));
      if (!visual) return;
      const designSelect = select(person, config.design_group), colorSelect = select(person, config.color_group);
      const update = window.PontoraCatalogPicker.mount(person, designSelect, colorSelect, config, {
        recipe: () => recipe(person), available: color => !config.tracks_stock || available(person, color), change: refresh,
      });
      updates.set(person, update);
      update();
    };
    const add = () => {
      if (persons().length >= config.limit) return;
      list.append($('#empty-person').content.cloneNode(true));
      setup(persons().at(-1)); active = persons().length - 1; rebuild();
    };
    persons().forEach(setup);
    $$('[name="choose_when"]').forEach(input => input.addEventListener('change', rebuild));
    const desired = Math.min(config.limit, Math.max(1, Number(config.initial_count) || 1));
    while (persons().length < desired) add();
    $('#add-person').addEventListener('click', add);
    $('#later-count')?.addEventListener('change', () => {
      const target = Math.min(config.limit, Math.max(1, Number($('#later-count').value) || 1));
      while (persons().length < target) add();
      while (persons().length > target) {const person = persons().at(-1); updates.delete(person); person.remove();}
      rebuild();
    });
    (config.options[config.color_group] || []).forEach(o => $('#group-color').add(new Option(o.name, o.id)));
    $('#apply-color').addEventListener('click', () => {
      const value = $('#group-color').value; if (!value) return;
      if (config.tracks_stock) {
        if (!enough(persons().filter(person => !deferred(person)).map(person => recipe(person, value)))) {
          $('#group-color-status').textContent = 'Choose a design for everyone and a color with enough materials for the whole group. No selections changed.';
          return;
        }
      }
      persons().filter(person => !deferred(person)).forEach(person => {select(person, config.color_group).value = value;});
      $('#group-color-status').textContent = 'Color applied to everyone.';
      refresh();
    });
    $('#choices-form').addEventListener('invalid', event => {
      const index = persons().findIndex(person => person.contains(event.target));
      if (index >= 0) {active = index; rebuild();}
    }, true);
    const errorIndex = persons().findIndex(person => $$('.errorlist', person).length);
    active = Math.max(0, errorIndex); rebuild();
  }
  $('#print-prep')?.addEventListener('click', () => window.print());
  if ($('#notion-payload')) $('#payload-preview').textContent = JSON.stringify(data('notion-payload'), null, 2);
  icons();
})();
