const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').resolve(__dirname, '../../static/scheduling/party-inquiry.js'), 'utf8');

test('birthday fields appear only for birthdays and retain unsent input when toggled', () => {
  const fields = [{hidden: false}, {hidden: false}];
  const occasion = {value: 'Birthday', addEventListener(type, fn) { this.input = fn; }};
  const name = {value: 'Alex', closest: () => fields[0]}, age = {value: '9', closest: () => fields[1]};
  const nodes = {id_occasion: occasion, id_birthday_name: name, id_birthday_age: age};
  vm.runInNewContext(source, {document: {addEventListener() {}, querySelector() { return null; },
    getElementById(id) { return nodes[id]; }}});
  assert.equal(fields[0].hidden, false);
  occasion.value = 'Team event'; occasion.input();
  assert.equal(fields[0].hidden, true);
  assert.equal(age.disabled, true);
  occasion.value = ' birthday '; occasion.input();
  assert.equal(name.disabled, false);
  assert.equal(name.value, 'Alex');
});

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

test('inline party calendar only offers matching dates and requires an explicit time', () => {
  const field = () => ({hidden:false});
  const slots = {value:'', options:[{value:''}, {value:'1'}, {value:'2'}], closest:field};
  const select = {value:'7', addEventListener(type, fn) { this.change = fn; }};
  const buttons = {children:[], replaceChildren() { this.children=[]; }, append(b) { this.children.push(b); }};
  const nodes = {id_package:select, id_party_slot:slots, id_starts_at:{closest:field},
    'party-package-summary':{}, 'party-submit':{}, 'party-next-step':{},
    'party-time-picker':{}, 'party-time-buttons':buttons, 'party-time-date':{}, 'party-date':{}, 'party-date-calendar':{},
    'party-package-details':{textContent:JSON.stringify({'7':{direct:true,text:'Private room'},'8':{direct:false,text:'Review'}})},
    'party-slot-packages':{textContent:JSON.stringify({'1':'7','2':'9'})},
    'party-slot-dates':{textContent:JSON.stringify({'1':{date:'2026-11-18',time:'12:00 PM PST'},'2':{date:'2026-11-19',time:'01:00 PM PST'}})}};
  let config, jumped;
  vm.runInNewContext(source, {window:{flatpickr(input, options) { config=options; return {destroy(){},jumpToDate(day){jumped=day;}}; }},
    document:{addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];},getElementById(id){return nodes[id];},
      createElement(){return {setAttribute(k,v){this[k]=v;},addEventListener(type,fn){this.click=fn;}};}}});
  assert.deepEqual([...config.enable], ['2026-11-18']);
  assert.equal(jumped,'2026-11-18');
  assert.equal(nodes['party-submit'].disabled,true);
  config.onChange([], '2026-11-18');
  assert.equal(buttons.children.length,1);
  buttons.children[0].click();
  assert.equal(slots.value,'1');
  assert.equal(nodes['party-submit'].disabled,false);
  config.onChange([], '2026-11-19');
  assert.equal(slots.value,'');
  assert.equal(buttons.children.length,0);
  select.value='8'; select.change();
  assert.equal(nodes['party-time-picker'].hidden,true);
});

test('direct parties use only their published times; review restores preferred date', () => {
  const field = () => ({hidden: false});
  const slotField = field(), dateField = field();
  const slots = {value: '2', options: [{value: ''}, {value: '1'}, {value: '2'}], closest: () => slotField};
  const preferred = {closest: () => dateField};
  const select = {value: '7', addEventListener(type, fn) { this.change = fn; }};
  const nodes = {'id_package': select, 'id_party_slot': slots, 'id_starts_at': preferred,
    'party-package-summary': {}, 'party-submit': {}, 'party-next-step': {},
    'party-package-details': {textContent: JSON.stringify({'7': {text: 'Direct', direct: true}, '8': {text: 'Review', direct: false}})},
    'party-slot-packages': {textContent: JSON.stringify({'1': '7', '2': '8'})}};
  vm.runInNewContext(source, {document: {addEventListener() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, getElementById(id) { return nodes[id]; }}});
  assert.equal(slots.value, '');
  assert.equal(slots.options[2].disabled, true);
  assert.equal(slots.options[1].disabled, false);
  assert.equal(preferred.disabled, true);
  assert.equal(slots.required, true);
  assert.equal(nodes['party-submit'].textContent, 'Continue to payment');
  select.value = '8'; select.change();
  assert.equal(slots.disabled, true);
  assert.equal(dateField.hidden, false);
  assert.equal(preferred.required, true);
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
