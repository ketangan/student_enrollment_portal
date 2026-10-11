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
      if (selected) link.href = link.dataset.url.replace('/0/', `/${selected.id}/`) + `?participants=${places}`;
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
    let active = 0, groupMode = !visual;
    const persons = () => $$('.participant', list);
    const field = (person, name) => $$('.person-fields label', person).find(el => el.dataset.field === name);
    const select = (person, name) => field(person, name)?.querySelector('select');
    const optionName = el => el.selectedOptions[0]?.textContent || '';
    const updates = new Map();
    const refresh = () => {
      persons().forEach(person => updates.get(person)?.());
      summarize();
    };
    const recipe = (person, color) => config.inventory.variants.find(row =>
      row.options.includes(Number(select(person, config.design_group)?.value)) &&
      row.options.includes(Number(color ?? select(person, config.color_group)?.value)));
    const enough = selections => materialAvailability.enough(selections, config.inventory.materials);
    const available = (person, colorId) => {
      const others = persons().filter(other => other !== person).map(other => recipe(other)).filter(Boolean);
      return enough([...others, recipe(person, colorId)]);
    };
    const rebuild = () => {
      const all = persons(); active = Math.min(active, all.length - 1);
      $('#id_participants-TOTAL_FORMS').value = all.length;
      all.forEach((person, index) => {
        person.hidden = !groupMode && active !== index;
        $('.person-number', person).textContent = index + 1;
        $$('[name],[id],[for]', person).forEach(el => ['name', 'id', 'for'].forEach(attr => {
          if (el.hasAttribute(attr)) el.setAttribute(attr, el.getAttribute(attr).replace(/participants-(?:\d+|__prefix__)-/g, `participants-${index}-`));
        }));
        if (visual) {
          field(person, config.design_group).hidden = !groupMode;
          field(person, config.color_group).hidden = !groupMode;
        }
        $('.visual-picker', person).hidden = groupMode || !visual;
        $('.remove-person', person).hidden = all.length === 1;
      });
      $('#person-tabs').replaceChildren(...all.map((person, index) => {
        const name = $('input[type=text]', person).value.trim();
        const node = button(name || `Participant ${index+1}`, () => {active = index; groupMode = !visual; rebuild();});
        node.setAttribute('aria-pressed', String(!groupMode && active === index)); return node;
      }));
      $('#guided').setAttribute('aria-pressed', String(!groupMode)); $('#group').setAttribute('aria-pressed', String(groupMode));
      list.classList.toggle('group-mode', groupMode);
      $('#add-person').disabled = all.length >= config.limit;
      $('#selection-total').textContent = `${all.length} participant${all.length === 1 ? '' : 's'} / ${dollars(all.length*config.price)}`;
      refresh();
      icons();
    };
    const photo = (node, design) => {
      let art = $('.photo-art', node);
      if (!art) {art = document.createElement('span'); art.className = 'photo-art'; node.append(art);}
      art.style.backgroundImage = design?.image_url ? `url("${design.image_url}")` : design?.x ? `url("${config.image}")` : 'none';
      art.style.backgroundSize = design?.image_url ? 'contain' : '';
      art.style.backgroundPosition = design?.image_url ? 'center' : `${design?.x || '50%'} ${design?.y || '50%'}`;
      // Each collage row has different headroom; keep the complete toy without neighboring shelf labels.
      art.style.aspectRatio = design?.image_url ? '4 / 5' : design?.y === '50%' ? '1 / 1.08' : design?.y === '100%' ? '1 / 1.15' : '4 / 5';
      node.classList.toggle('no-photo', !design?.image_url && !design?.x);
    };
    const summarize = () => {
      let complete = 0;
      $('#group-summary-list').replaceChildren(...persons().map((person, index) => {
        const name = $('input[type=text]', person).value.trim();
        const design = config.designs.find(d => d.name === optionName(select(person, config.design_group) || {selectedOptions: []}));
        const color = config.options[config.color_group]?.find(c => c.id === select(person, config.color_group)?.value);
        const row = document.createElement('div'); row.className = 'group-person';
        const exactPhoto = recipe(person)?.image_url;
        const picture = document.createElement('div'); picture.className = 'plushie-image group-photo'; photo(picture, exactPhoto ? {...design, image_url: exactPhoto} : design);
        if (color && !exactPhoto) {
          const tint = document.createElement('span'); tint.className = 'tint'; tint.style.setProperty('--color', color.swatch); picture.append(tint);
        }
        picture.setAttribute('aria-hidden', 'true');
        const details = document.createElement('div'); const title = document.createElement('strong'); title.textContent = name || `Participant ${index + 1}`;
        const description = document.createElement('span'); description.className = 'muted tiny';
        const ageSelect = select(person, config.age_group);
        const age = ageSelect?.value ? optionName(ageSelect) : '';
        description.textContent = [age && /^\d+$/.test(age) ? `Age ${age}` : age, design?.name, color?.name].filter(Boolean).join(' / ') || 'Selections pending';
        if (name && $$('input,select', person).filter(el => el.required).every(el => el.value)
            && (!config.tracks_stock || !visual || available(person))) complete++;
        details.append(title, description); row.append(picture, details); return row;
      }));
      const status = $('#group-completeness'); status.textContent = `${complete} of ${persons().length} participants ready`;
      status.classList.toggle('all-ready', complete === persons().length);
    };
    const setup = person => {
      $('input[type=text]', person).addEventListener('change', rebuild);
      $('input[type=text]', person).addEventListener('input', summarize);
      select(person, config.age_group)?.addEventListener('change', summarize);
      $('.remove-person', person).addEventListener('click', () => {updates.delete(person); person.remove(); rebuild();});
      if (!visual) return;
      const designSelect = select(person, config.design_group), colorSelect = select(person, config.color_group);
      const update = () => {
        const design = config.designs.find(d => d.name === optionName(designSelect));
        const color = config.options[config.color_group].find(c => c.name === optionName(colorSelect));
        const exactPhoto = recipe(person)?.image_url;
        photo($('.plushie-image', person), exactPhoto ? {...design, image_url: exactPhoto} : design);
        $('.tint', person).hidden = Boolean(exactPhoto);
        $('.tint', person).style.setProperty('--color', color?.swatch || '#fff');
        $('.selection-label', person).textContent = design && color ? `${color.name} ${design.name}` : 'Choose a design and color';
        $$('.design-option', person).forEach(el => el.setAttribute('aria-pressed', String(el.dataset.value === designSelect.value)));
        $$('.color-option', person).forEach(el => el.setAttribute('aria-pressed', String(el.dataset.value === colorSelect.value)));
        if (config.tracks_stock) {
          $$('.color-option', person).forEach(el => {
            const remaining = available(person, el.dataset.value);
            el.disabled = !remaining;
            el.title = `${config.options[config.color_group].find(o => o.id === el.dataset.value)?.name}: ${remaining ? 'Available' : 'Materials unavailable'}`;
          });
          [...colorSelect.options].forEach(option => { option.disabled = Boolean(option.value) && !available(person, option.value); });
          colorSelect.setCustomValidity(colorSelect.value && !available(person, colorSelect.value) ? 'Not enough materials for these selections. Choose another design or color.' : '');
        }
      };
      $('.designs', person).replaceChildren(...config.options[config.design_group].map(option => {
        const node = button('', () => {designSelect.value = option.id; refresh();}, 'design-option');
        const picture = document.createElement('span'); picture.className = 'design-photo';
        photo(picture, config.designs.find(d => d.name === option.name));
        const label = document.createElement('span'); label.textContent = option.name;
        node.append(picture, label); node.dataset.value = option.id; return node;
      }));
      $('.colors', person).replaceChildren(...config.options[config.color_group].map(option => {
        const node = button(option.name, () => {colorSelect.value = option.id; refresh();}, 'color-option');
        node.style.setProperty('--color', option.swatch); node.title = option.name;
        node.setAttribute('aria-label', option.name); node.dataset.value = option.id; return node;
      }));
      designSelect.addEventListener('change', refresh); colorSelect.addEventListener('change', refresh);
      updates.set(person, update);
      update();
    };
    const add = () => {
      if (persons().length >= config.limit) return;
      list.append($('#empty-person').content.cloneNode(true));
      setup(persons().at(-1)); active = persons().length - 1; rebuild();
    };
    persons().forEach(setup);
    const desired = Math.min(config.limit, Math.max(1, Number(config.initial_count) || 1));
    while (persons().length < desired) add();
    $('#add-person').addEventListener('click', add);
    $('#guided').addEventListener('click', () => {groupMode = false; rebuild();});
    $('#guided').disabled = !visual;
    $('#group').addEventListener('click', () => {groupMode = true; rebuild();});
    $('.group-color').hidden = !visual;
    (config.options[config.color_group] || []).forEach(o => $('#group-color').add(new Option(o.name, o.id)));
    $('#apply-color').addEventListener('click', () => {
      const value = $('#group-color').value; if (!value) return;
      if (config.tracks_stock) {
        if (!enough(persons().map(person => recipe(person, value)))) {
          $('#group-color-status').textContent = 'Choose a design for everyone and a color with enough materials for the whole group. No selections changed.';
          return;
        }
      }
      persons().forEach(person => {select(person, config.color_group).value = value;});
      $('#group-color-status').textContent = 'Color applied to everyone.';
      refresh();
    });
    $('#choices-form').addEventListener('invalid', () => {groupMode = true; rebuild();}, true);
    if ($$('.errorlist', list).length) groupMode = true;
    active = 0; rebuild();
  }
  $('#print-prep')?.addEventListener('click', () => window.print());
  if ($('#notion-payload')) $('#payload-preview').textContent = JSON.stringify(data('notion-payload'), null, 2);
  icons();
})();
