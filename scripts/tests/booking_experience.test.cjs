const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../../static/scheduling/experience.js');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/scheduling/catalog-picker.js'), 'utf8') + '\n' + fs.readFileSync(filename, 'utf8');

function element(select = false) {
  return {
    children: [], listeners: {}, attrs: {}, dataset: {}, value: '', textContent: '',
    classList: {toggle() {}},
    addEventListener(event, callback) { this.listeners[event] = callback; },
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; delete this[name]; },
    replaceChildren(...items) { this.children = items; if (select) this.value = items[0]?.value || ''; },
    add(item) { this.children.push(item); if (this.children.length === 1) this.value = item.value; },
    append(...items) { this.children.push(...items); },
    querySelector(selector) { return this.children.find(node => `.${node.className}` === selector); },
    style: {setProperty(name, value) { this[name] = value; }},
  };
}

const session = (id, location, workshop, name, date, duration = 120, places = 4) => ({
  id, location_id: location, location: location === 1 ? 'Charlotte, NC' : 'Pasadena, CA',
  workshop_id: workshop, workshop: name, date, day: date, time: '10:00 AM', duration_minutes: duration, places,
});
const sessions = [
  session(1, 1, 10, 'Plushie-making workshop', '2030-10-10'),
  session(2, 1, 11, 'Young Horizons', '2030-10-12', 90),
  session(3, 1, 11, 'Young Horizons', '2030-10-30', 120, 2),
  session(4, 2, 12, 'Young Horizons', '2030-10-15', 60),
];

function page(rows = sessions) {
  const nodes = Object.fromEntries(['sessions-data', 'studio', 'workshop', 'count', 'dates', 'times',
    'start-booking', 'availability', 'price-total', 'workshop-name', 'workshop-duration',
    'calendar-month', 'previous-month', 'next-month', 'chosen-day', 'brand-location', 'summary-location', 'summary-session'].map(id => [id,
    element(['studio', 'workshop'].includes(id))]));
  nodes['sessions-data'].textContent = JSON.stringify(rows);
  nodes.count.value = '1';
  nodes['price-total'].dataset.unit = '6500';
  nodes['start-booking'].dataset.url = '/experience/session/0/';
  const document = {getElementById: id => nodes[id], querySelector: selector => nodes[selector.slice(1)],
    createElement: () => element()};
  vm.runInNewContext(source, {document, window: {}, Intl,
    Option: function (text, value) { this.textContent = text; this.value = value; }}, {filename});
  return nodes;
}
function change(node, value) { node.value = value; node.listeners.change(); }
const dates = p => p.dates.children.filter(node => node.dataset.date && !node.disabled);

test('studio and workshop selections isolate dates, including same-named workshops elsewhere', () => {
  const p = page();
  assert.deepEqual(p.workshop.children.map(o => o.textContent), ['Plushie-making workshop', 'Young Horizons']);
  assert.deepEqual(dates(p).map(o => o.dataset.date), ['2030-10-10']);
  change(p.workshop, '11');
  assert.deepEqual(dates(p).map(o => o.dataset.date), ['2030-10-12', '2030-10-30']);
  assert.equal(p['workshop-name'].textContent, 'Young Horizons');
  change(p.studio, '2');
  assert.deepEqual(p.workshop.children.map(o => o.value), ['12']);
  assert.deepEqual(dates(p).map(o => o.dataset.date), ['2030-10-15']);
  assert.equal(p['workshop-duration'].textContent, '1 hour');
});

test('changing workshop, studio or date clears the previous session link', () => {
  const p = page();
  p.times.children[0].listeners.click();
  assert.equal(p['start-booking'].href, '/experience/session/1/?participants=1');
  change(p.workshop, '11');
  assert.equal(p['start-booking'].href, undefined);
  p.times.children[0].listeners.click();
  assert.equal(p['start-booking'].href, '/experience/session/2/?participants=1');
  dates(p)[1].listeners.click();
  assert.equal(p['start-booking'].attrs['aria-disabled'], 'true');
  p.times.children[0].listeners.click();
  assert.equal(p['start-booking'].href, '/experience/session/3/?participants=1');
  change(p.studio, '2');
  assert.equal(p['start-booking'].href, undefined);
});

test('participant count preserves capacity protection and updates price and booking link', () => {
  const p = page();
  change(p.workshop, '11');
  dates(p)[1].listeners.click();
  p.times.children[0].listeners.click();
  change(p.count, '2');
  assert.equal(p['start-booking'].href, '/experience/session/3/?participants=2');
  assert.equal(p['price-total'].textContent, '2 participants: $130.00');
  change(p.count, '3');
  assert.equal(p.times.children[0].disabled, true);
  assert.equal(p['start-booking'].href, undefined);
});

test('duration reflects the selected session rather than a hardcoded two hours', () => {
  const p = page();
  assert.equal(p['workshop-duration'].textContent, '2 hours');
  change(p.workshop, '11');
  assert.equal(p['workshop-duration'].textContent, '');
  p.times.children[0].listeners.click();
  assert.equal(p['workshop-duration'].textContent, '90 minutes');
  dates(p)[1].listeners.click();
  p.times.children[0].listeners.click();
  assert.equal(p['workshop-duration'].textContent, '2 hours');
});

test('empty availability has no enabled workshop or booking link', () => {
  const p = page([]);
  assert.equal(p.workshop.disabled, true);
  assert.equal(p['start-booking'].href, undefined);
  assert.equal(p['start-booking'].attrs['aria-disabled'], 'true');
  assert.equal(p['availability'].textContent, 'No upcoming sessions available.');
  assert.equal(p['workshop-duration'].textContent, '');
  assert.equal(p['calendar-month'].textContent, 'No available dates');
  assert.equal(p['previous-month'].disabled, true);
  assert.equal(p['next-month'].disabled, true);
  assert.equal(p.dates.children.length, 0);
});

test('calendar handles leap years, Monday alignment and unavailable dates', () => {
  const p = page([session(1, 1, 10, 'Workshop', '2032-02-29')]);
  assert.equal(p['calendar-month'].textContent, 'February 2032');
  assert.equal(p.dates.children.filter(n => n.dataset.date).length, 29);
  assert.equal(p.dates.children[0].attrs['aria-hidden'], 'true');
  assert.equal(p.dates.children[6].dataset.date, '2032-02-01');
  assert.deepEqual(dates(p).map(n => n.dataset.date), ['2032-02-29']);
  assert.equal(dates(p)[0].attrs['aria-label'], 'Sunday, February 29, 2032');
  assert.equal(dates(p)[0].attrs['aria-pressed'], 'true');
});

test('month navigation stays inside available months and clears the selected time', () => {
  const p = page([session(2, 1, 10, 'Workshop', '2031-02-04'), session(1, 1, 10, 'Workshop', '2030-12-12')]);
  assert.equal(p['calendar-month'].textContent, 'December 2030');
  assert.equal(p['previous-month'].disabled, true);
  assert.equal(p['next-month'].disabled, false);
  p.times.children[0].listeners.click();
  assert.equal(p['start-booking'].href, '/experience/session/1/?participants=1');
  p['next-month'].listeners.click();
  assert.equal(p['calendar-month'].textContent, 'February 2031');
  assert.equal(p['start-booking'].href, undefined);
  assert.deepEqual(dates(p).map(n => n.dataset.date), ['2031-02-04']);
  assert.equal(p['next-month'].disabled, true);
  p['next-month'].listeners.click();
  assert.equal(p['calendar-month'].textContent, 'February 2031');
  p['previous-month'].listeners.click();
  assert.equal(p['calendar-month'].textContent, 'December 2030');
});

test('sidebar and header follow studio and chosen time, including cleared selections', () => {
  const p = page();
  assert.equal(p['brand-location'].textContent, 'Charlotte, NC');
  p.times.children[0].listeners.click();
  assert.equal(p['summary-session'].textContent, 'Thu, Oct 10 at 10:00 AM');
  change(p.studio, '2');
  assert.equal(p['brand-location'].textContent, 'Pasadena, CA');
  assert.equal(p['summary-location'].textContent, 'Pasadena, CA');
  assert.equal(p['summary-session'].textContent, 'Choose your date and time');
});

function choicesPage({customPhoto = false, variantPhoto = '', bookingMode = false} = {}) {
  const config = {design_group: 'Design', color_group: 'Color', age_group: 'Age', price: 6500, limit: 8,
    initial_count: 1, image: '/designs.jpg', tracks_stock: true, deferred_groups: ['Design', 'Color'],
    options: {Design: [{id: '1', name: 'Bear'}, {id: '2', name: 'Bunny'}],
      Color: [{id: '3', name: 'Blue', swatch: '#abc'}, {id: '4', name: 'Grey', swatch: '#ccc'}]},
    designs: [{name: 'Bear', x: '50%', y: '50%', image_url: customPhoto ? '/bear.jpg' : ''},
      {name: 'Bunny', x: '74%', y: '0%'}],
    inventory: {materials: {fabric: 1000}, variants: [
      {options: [1, 3], requirements: {fabric: 500}, image_url: variantPhoto}, {options: [2, 3], requirements: {fabric: 500}},
      {options: [1, 4], requirements: {fabric: 1500}}]}};
  const nodes = Object.fromEntries(['catalog-data', 'participant-list', 'id_participants-TOTAL_FORMS', 'person-tabs',
    'guided', 'group', 'add-person', 'selection-total', 'group-summary-list', 'group-completeness', 'group-color',
    'apply-color', 'group-color-status', 'choices-form', 'empty-person'].map(id => [id, element()]));
  nodes['catalog-data'].textContent = JSON.stringify(config);
  const groupColor = element();
  if (bookingMode) for (const id of ['choose-later', 'choose-now', 'later-count', 'later-count-field']) nodes[id] = element();
  function participant() {
    const p = element();
    const name = element(); name.required = true;
    const dropdown = options => {
      const el = element(); el.options = [{value: '', textContent: 'Choose'}, ...options]; el.required = true;
      el.setCustomValidity = message => {el.validationMessage = message;};
      Object.defineProperty(el, 'selectedOptions', {get: () => el.options.filter(option => option.value === el.value)});
      return el;
    };
    const fields = {Design: dropdown(config.options.Design.map(o => ({value:o.id,textContent:o.name}))),
      Color: dropdown(config.options.Color.map(o => ({value:o.id,textContent:o.name}))),
      Age: dropdown([{value:'8',textContent:'8'},{value:'adult',textContent:'Adult'}])};
    const labels = Object.entries(fields).map(([field, node]) => ({dataset:{field},querySelector: () => node, querySelectorAll: () => [node]}));
    const parts = Object.fromEntries(['.remove-person','.person-number','.visual-picker','.plushie-image','.tint',
      '.selection-label','.designs','.colors'].map(selector => [selector, element()]));
    parts['[name$="-selections_deferred"]'] = element();
    p.querySelector = selector => selector === 'input[type=text]' ? name : parts[selector];
    p.querySelectorAll = selector => ({'.person-fields label': labels,
      '.design-option': parts['.designs'].children, '.color-option': parts['.colors'].children,
      'input,select': [name, ...Object.values(fields)]}[selector] || []);
    p.remove = () => {nodes['participant-list'].children = nodes['participant-list'].children.filter(row => row !== p);};
    p.name = name; p.fields = fields; p.parts = parts;
    p.contains = target => target === name || Object.values(fields).includes(target);
    return p;
  }
  nodes['participant-list'].children = [participant()];
  nodes['participant-list'].querySelectorAll = selector => selector === '.participant' ? nodes['participant-list'].children : [];
  nodes['empty-person'].content = {cloneNode: () => participant()};
  const document = {getElementById: id => nodes[id],
    querySelectorAll: selector => selector === '[name="choose_when"]' && bookingMode ? [nodes['choose-now'], nodes['choose-later']] : [],
    querySelector: selector => selector === '.group-color' ? groupColor : nodes[selector.slice(1)],
    createElement: () => element()};
  vm.runInNewContext(source, {document, window: {}, Intl,
    materialAvailability: require('../../static/scheduling/material-availability.js'),
    Option: function(text, value) {this.textContent = text;this.value = value;}}, {filename});
  return nodes;
}

test('live group summary shows names, age and choices without counting incomplete participants', () => {
  const p = choicesPage(), person = p['participant-list'].children[0];
  assert.equal(p['group-completeness'].textContent, '0 of 1 participants ready');
  person.name.value = '<Alex>'; person.name.listeners.input();
  assert.equal(p['group-summary-list'].children[0].children[1].children[0].textContent, '<Alex>');
  person.parts['.designs'].children[0].listeners.click();
  person.parts['.colors'].children[0].listeners.click();
  assert.equal(p['group-completeness'].textContent, '0 of 1 participants ready');
  change(person.fields.Age, '8');
  assert.equal(p['group-completeness'].textContent, '1 of 1 participants ready');
  assert.equal(p['group-summary-list'].children[0].children[1].children[1].textContent, 'Age 8 / Bear / Blue');
  change(person.fields.Age, 'adult');
  assert.equal(p['group-summary-list'].children[0].children[1].children[1].textContent, 'Adult / Bear / Blue');
});

test('deferred participants retain their names and age without requiring or claiming a design', () => {
  const p = choicesPage(), person = p['participant-list'].children[0];
  const checkbox = person.parts['[name$="-selections_deferred"]'];
  person.name.value = 'Alex';
  change(person.fields.Age, 'adult');
  person.parts['.designs'].children[0].listeners.click();
  person.parts['.colors'].children[0].listeners.click();
  checkbox.checked = true; checkbox.listeners.change();
  assert.equal(person.parts['.visual-picker'].hidden, true);
  assert.equal(person.fields.Design.disabled, true);
  assert.equal(person.fields.Color.required, false);
  assert.equal(person.name.required, false);
  assert.equal(person.fields.Age.required, false);
  assert.equal(p['group-completeness'].textContent, '1 of 1 participants ready');
  assert.equal(p['group-summary-list'].children[0].children[1].children[1].textContent, 'Choosing at the studio');
  assert.equal(p['group-summary-list'].children[0].children[0].querySelector('.photo-art').style.backgroundImage, 'none');
  assert.equal(p['group-summary-list'].children[0].children[0].querySelector('.tint'), undefined);
  p['add-person'].listeners.click();
  const second = p['participant-list'].children[1];
  second.parts['.designs'].children[0].listeners.click();
  assert.equal(second.parts['.colors'].children[0].disabled, false);
  checkbox.checked = false; checkbox.listeners.change();
  assert.equal(person.fields.Color.required, true);
  assert.equal(person.fields.Design.disabled, false);
  assert.equal(p['group-completeness'].textContent, '1 of 2 participants ready');
});

test('deferred participants can leave personal details blank and still count toward the booking', () => {
  const p = choicesPage(), person = p['participant-list'].children[0];
  const checkbox = person.parts['[name$="-selections_deferred"]'];
  checkbox.checked = true; checkbox.listeners.change();
  assert.equal(p['group-completeness'].textContent, '1 of 1 participants ready');
  assert.equal(p['group-summary-list'].children[0].children[1].children[0].textContent, 'Participant 1');
  checkbox.checked = false; checkbox.listeners.change();
  assert.equal(person.name.required, true);
  assert.equal(person.fields.Age.required, true);
  assert.equal(p['group-completeness'].textContent, '0 of 1 participants ready');
});

test('validation opens the participant containing the invalid field', () => {
  const p = choicesPage(), first = p['participant-list'].children[0];
  p['add-person'].listeners.click();
  const second = p['participant-list'].children[1];
  assert.equal(first.hidden, true);
  p['choices-form'].listeners.invalid({target:first.name});
  assert.equal(first.hidden, false);
  assert.equal(second.hidden, true);
  p['choices-form'].listeners.invalid({target:{}});
  assert.equal(first.hidden, false);
});

test('one booking choice defers six participants and preserves their place count', () => {
  const p = choicesPage({bookingMode:true});
  p['choose-later'].checked = true; p['choose-later'].listeners.change();
  change(p['later-count'], '6');
  assert.equal(p['participant-list'].children.length, 6);
  assert.equal(p['group-completeness'].textContent, '6 participants / Choosing designs later');
  assert.equal(p['group-summary-list'].hidden, true);
  assert.equal(p['selection-total'].textContent, '6 participants / $390.00');
  assert.ok(p['participant-list'].children.every(person => person.hidden && person.parts['[name$="-selections_deferred"]'].checked));
  p['choose-later'].checked = false; p['choose-now'].listeners.change();
  assert.equal(p['participant-list'].children.filter(person => !person.hidden).length, 1);
  assert.ok(p['participant-list'].children.every(person => !person.parts['[name$="-selections_deferred"]'].checked));
  p['choose-later'].checked = true; p['choose-later'].listeners.change();
  change(p['later-count'], '2');
  assert.equal(p['participant-list'].children.length, 2);
});

test('exact design and color photo replaces the tint in preview and group summary', () => {
  const p = choicesPage({variantPhoto:'/blue-bear.jpg'}), person = p['participant-list'].children[0];
  person.parts['.designs'].children[0].listeners.click();
  person.parts['.colors'].children[0].listeners.click();
  assert.equal(person.parts['.plushie-image'].querySelector('.photo-art').style.backgroundImage, 'url("/blue-bear.jpg")');
  assert.equal(person.parts['.tint'].hidden, true);
  const picture = p['group-summary-list'].children[0].children[0];
  assert.equal(picture.querySelector('.photo-art').style.backgroundImage, 'url("/blue-bear.jpg")');
  assert.equal(picture.querySelector('.tint'), undefined);
  person.parts['.designs'].children[1].listeners.click();
  assert.equal(person.parts['.tint'].hidden, false);
  assert.notEqual(person.parts['.plushie-image'].querySelector('.photo-art').style.backgroundImage, 'url("/blue-bear.jpg")');
});

test('photo framing shows complete collage rows and contains custom photos', () => {
  const p = choicesPage(), person = p['participant-list'].children[0];
  person.parts['.designs'].children[0].listeners.click();
  const art = person.parts['.plushie-image'].querySelector('.photo-art');
  assert.equal(art.style.aspectRatio, '1 / 1.08');
  assert.equal(art.style.backgroundPosition, '50% 50%');
  assert.equal(person.parts['.colors'].children[0].textContent, 'Blue');
  const custom = choicesPage({customPhoto:true})['participant-list'].children[0];
  custom.parts['.designs'].children[0].listeners.click();
  const customArt = custom.parts['.plushie-image'].querySelector('.photo-art');
  assert.equal(customArt.style.backgroundImage, 'url("/bear.jpg")');
  assert.equal(customArt.style.backgroundPosition, 'center');
  assert.equal(customArt.style.backgroundSize, 'contain');
  custom.parts['.colors'].children[0].listeners.click();
  assert.equal(custom.parts['.plushie-image'].children.length, 1);
});

test('group summaries, totals and stock guards stay correct when participants are added or removed', () => {
  const p = choicesPage(), first = p['participant-list'].children[0];
  first.parts['.designs'].children[0].listeners.click();
  first.parts['.colors'].children[0].listeners.click();
  assert.equal(first.parts['.colors'].children[1].disabled, true);
  p['add-person'].listeners.click();
  assert.equal(p['group-summary-list'].children.length, 2);
  assert.equal(p['selection-total'].textContent, '2 participants / $130.00');
  p['group-color'].value = '4'; p['apply-color'].listeners.click();
  assert.match(p['group-color-status'].textContent, /No selections changed/);
  assert.equal(first.fields.Color.value, '3');
  const second = p['participant-list'].children[1];
  second.parts['.designs'].children[1].listeners.click();
  second.parts['.colors'].children[0].listeners.click();
  assert.equal(first.hidden, true); assert.equal(second.hidden, false);
  assert.equal(p['group-summary-list'].children.length, 2);
  second.parts['.remove-person'].listeners.click();
  assert.equal(p['group-summary-list'].children.length, 1);
  assert.equal(p['selection-total'].textContent, '1 participant / $65.00');
});
