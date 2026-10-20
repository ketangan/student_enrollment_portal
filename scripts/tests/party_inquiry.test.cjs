const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').resolve(__dirname, '../../static/scheduling/party-inquiry.js'), 'utf8');

test('guest rows are numbered, capped and renumbered without losing names', () => {
  const listeners = {}, rows = [];
  function makeRow(name = '') {
    const label = {}, remove = {setAttribute(key, value) { this[key] = value; }};
    const input = {name: 'materials-__prefix__-guest_name', value: name,
      hasAttribute(key) { return key === 'name'; }, getAttribute(key) { return this[key]; },
      setAttribute(key, value) { this[key] = value; }};
    const row = {input, label, removeButton: remove, dataset: {},
      querySelector(selector) { return selector === '[data-guest-number]' ? label : selector === '[data-remove-guest]' ? remove : null; },
      querySelectorAll() { return [input]; },
      remove() { rows.splice(rows.indexOf(row), 1); }, closest() { return row; }};
    return row;
  }
  rows.push(makeRow('Alex'));
  const add = {disabled: false}, total = {}, count = {}, size = {value: '2'};
  const editor = {querySelectorAll() { return rows; }};
  const nodes = {'id_group_size': size, 'add-guest': add, 'guest-count': count,
    'id_materials-TOTAL_FORMS': total, 'material-rows': {append(row) { rows.push(row); }},
    'material-template': {content: {cloneNode() { return makeRow(); }}}};
  vm.runInNewContext(source, {window: {}, document: {
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    querySelector(selector) { return selector === '[data-guest-editor]' ? editor : null; },
    getElementById(id) { return nodes[id]; },
  }});
  assert.equal(rows[0].label.textContent, 'Guest 1');
  const click = target => listeners.click.forEach(fn => fn({target}));
  click({closest(selector) { return selector === '#add-guest' ? add : null; }});
  assert.equal(add.disabled, true);
  assert.equal(total.value, 2);
  rows[1].input.value = 'Sam';
  click({closest(selector) { return selector === '[data-remove-guest]' ? rows[0] : null; }});
  assert.equal(rows[0].input.value, 'Sam');
  assert.equal(rows[0].input.name, 'materials-0-guest_name');
  assert.equal(rows[0].label.textContent, 'Guest 1');
  assert.equal(add.disabled, false);
  size.value = '0'; listeners.input.forEach(fn => fn({target: {id: 'id_group_size'}}));
  assert.equal(add.disabled, true);
  assert.match(count.textContent, /Remove extra guests/);
});

test('package choice hides custom fields; switching back restores them', () => {
  const select = {value: '7', addEventListener(type, callback) { this.change = callback; }};
  const summary = {textContent: ''};
  const input = {disabled: false};
  const field = {hidden: false, querySelectorAll() { return [input]; }};
  const nodes = {'id_package': select, 'party-package-summary': summary,
    'party-package-details': {textContent: JSON.stringify({'7': {text: '6 people, $450 total'}})}};
  vm.runInNewContext(source, {document: {addEventListener() {}, querySelector() { return null; }, getElementById(id) { return nodes[id]; }, querySelectorAll() { return [field]; }}});
  assert.equal(summary.textContent, '6 people, $450 total');
  assert.equal(field.hidden, true);
  assert.equal(input.disabled, true);
  select.value = '';
  select.change();
  assert.equal(summary.textContent, '');
  assert.equal(field.hidden, false);
  assert.equal(input.disabled, false);
});

test('legacy inquiry pages do not need package controls', () => {
  vm.runInNewContext(source, {document: {addEventListener() {}, querySelector() { return null; }, getElementById() { return null; }}});
});

test('guest details can be entered now or left for later', () => {
  const wrapper = {hidden: false};
  const checkbox = {checked: false, addEventListener(type, callback) { this.change = callback; }};
  vm.runInNewContext(source, {document: {addEventListener() {}, querySelector(selector) { return selector === '[data-party-selections]' ? wrapper : null; }, getElementById(id) { return id === 'id_selections_now' ? checkbox : null; }}});
  assert.equal(wrapper.hidden, true);
  checkbox.checked = true;
  checkbox.change();
  assert.equal(wrapper.hidden, false);
});

function locationPage(fetch) {
  const location = {value: '1', addEventListener(type, callback) { this.change = callback; }};
  const status = {hidden: true}, submit = {disabled: false}, token = {value: 'original'};
  const contact = {value: 'Keep my name'};
  const form = {action: 'http://localhost:8002/bookings/example/party/?offering=1',
    querySelector(selector) { return selector === 'button[type="submit"]' ? submit : selector === '[name="request_token"]' ? token : null; }};
  const nodes = {'party-location': location, 'party-loading': status, 'party-request-form': form,
    'party-materials': {replaceWith() {}}, 'id_organizer_name': contact};
  const document = {addEventListener() {}, querySelector() { return null; },
    getElementById(id) { return nodes[id]; }, body: {append() {}}};
  class DOMParser {
    parseFromString(text) {
      return {getElementById(id) { return id === 'party-request-form'
        ? {querySelector(selector) { return selector === '[name="request_token"]' ? {value: text} : null; }}
        : null; }};
    }
  }
  vm.runInNewContext(source, {document, fetch, URL, AbortController, DOMParser, history: {replaceState() {}}});
  return {location, status, submit, token, form, contact};
}

test('failed location load restores the previous selection and permits retry', async () => {
  const p = locationPage(async () => ({ok: false}));
  p.location.value = '2';
  await p.location.change();
  assert.equal(p.location.value, '1');
  assert.equal(p.token.value, 'original');
  assert.equal(p.submit.disabled, false);
  assert.match(p.status.textContent, /Please try again/);
});

test('only the latest location response updates the scoped submission token', async () => {
  const pending = [];
  const p = locationPage((url, options) => new Promise(resolve => pending.push({url, options, resolve})));
  p.location.value = '2'; const first = p.location.change();
  assert.equal(p.submit.disabled, true);
  p.location.value = '3'; const second = p.location.change();
  assert.equal(pending[0].options.signal.aborted, true);
  pending[1].resolve({ok: true, text: async () => 'new-location-token'});
  await second;
  pending[0].resolve({ok: true, text: async () => 'stale-token'});
  await first;
  assert.equal(p.token.value, 'new-location-token');
  assert.equal(new URL(p.form.action).searchParams.get('offering'), '3');
  assert.equal(p.contact.value, 'Keep my name');
  assert.equal(p.submit.disabled, false);
  assert.equal(p.status.hidden, true);
});

test('visual controls preserve field labels and server errors when hiding dropdowns', () => {
  const label = {}, field = {querySelector: selector => selector === '.errorlist' ? {} : label};
  const select = {value: '', closest: () => field};
  const picker = {hidden: true};
  const row = {dataset: {}, querySelector: selector => selector === '.visual-picker' ? picker : select};
  const document = {addEventListener() {}, querySelector() { return null; },
    querySelectorAll: () => [row], getElementById: id => id === 'party-catalog' ? {textContent: '{"variants":[]}'} : null};
  vm.runInNewContext(source, {document, window: {PontoraCatalogPicker: {mount: () => () => {}}}});
  assert.equal(select.hidden, true);
  assert.equal(field.hidden, false);
  assert.equal(label.hidden, false);
  assert.equal(picker.hidden, false);
});
