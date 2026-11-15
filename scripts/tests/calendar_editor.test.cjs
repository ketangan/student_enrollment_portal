const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const filename = require('node:path').resolve('static/scheduling/calendar-editor.js');
const source = fs.readFileSync(filename, 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.textContent = ''; this.value = '';}
  append(...items) {this.children.push(...items);}
  replaceChildren(...items) {this.children = items;}
  setAttribute(key, value) {this.attrs[key] = value;}
  getAttribute(key) {return this.attrs[key] || null;}
  addEventListener(key, fn) {this.listeners[key] = fn;}
  fire(key, props = {}) {const event = {preventDefault() {this.prevented = true;}, ...props}; this.listeners[key]?.(event); return event;}
  querySelector(selector) {return this[selector] || null;}
  querySelectorAll(selector) {
    if (this[selector]) return this[selector];
    if (selector === '.actions a') return this.cancel ? [this.cancel] : [];
    return this.children.flatMap(child => [...(child.tag === selector ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  focus() {this.focused = true;}
  remove() {this.removed = true;}
  showModal() {this.open = true;}
  close() {this.open = false; this.fire('close');}
}
function page() {
  const ids = ['calendar-editor', 'calendar-editor-body', 'calendar-editor-status', 'calendar-editor-close', 'calendar-editor-title'];
  const nodes = Object.fromEntries(ids.map(id => [id, new Element('div')]));
  const requests = [], replies = [], window = {lucide: {createIcons() {}}};
  let saves = 0;
  vm.runInNewContext(source, {window, document: {getElementById: id => nodes[id], createElement: tag => new Element(tag)},
    DOMParser: class {parseFromString(doc) {return doc;}},
    FormData: class {constructor(form) {this.form = form; this.values = [];} append(...values) {this.values.push(values);}},
    async fetch(url, options) {requests.push({url, ...options}); const reply = replies.shift(); if (reply instanceof Error) throw reply; return await reply;}
  }, {filename});
  function html({ok = true, status = 200, redirected = false, fragment = true} = {}) {
    const section = new Element('section'), form = new Element('form'), submit = new Element('button');
    form.cancel = new Element('a'); form.append(submit); section.append(form); section.h2 = new Element('h2');
    section['[name="starts_at"]'] = new Element('input');
    section['input:not([type=hidden]), select, textarea'] = section['[name="starts_at"]'];
    return {section, form, submit, reply: {ok, status, redirected, headers: {get: () => 'text/html'},
      async text() {return {querySelector: () => fragment && section};}}};
  }
  function json(data, status = 200) {return {ok: status === 200, status, headers: {get: () => 'application/json'}, async json() {return data;}};}
  return {nodes, requests, replies, html, json, open(options = {}) {window.BookingCalendarEditor.open({url: '/edit/', title: 'Edit session', onSaved() {saves++;}, ...options});},
    get saves() {return saves;}, get dialog() {return nodes['calendar-editor'];}, get status() {return nodes['calendar-editor-status'];}};
}

test('calendar editor reuses protected form, prevents double submit, and refreshes only after confirmed save', async () => {
  const p = page(), initial = p.html(); p.replies.push(initial.reply); p.open(); await tick();
  assert.equal(p.requests[0].headers['X-Calendar-Editor'], '1');
  assert.equal(p.requests[0].credentials, 'same-origin');
  assert.equal(initial.section.h2.removed, true);
  let finish; p.replies.push(new Promise(resolve => {finish = resolve;}));
  initial.form.fire('submit', {submitter: {name: 'action', value: 'confirm'}});
  initial.form.fire('submit'); p.open();
  assert.equal(p.requests.length, 2);
  assert.equal(p.dialog.fire('cancel').prevented, true);
  p.nodes['calendar-editor-close'].fire('click'); assert.equal(p.dialog.open, true);
  assert.equal(p.requests[1].body.form, initial.form);
  assert.deepEqual(p.requests[1].body.values, [['action', 'confirm']]);
  finish(p.json({saved: true})); await tick();
  assert.equal(p.saves, 1); assert.equal(p.dialog.open, false);
  assert.equal(p.nodes['calendar-editor-close'].disabled, false);
});

test('validation and publication errors stay in dialog; explicit form action is preserved', async () => {
  const p = page(), initial = p.html({ok: false, status: 400}); initial.form.attrs.action = '/publish/';
  p.replies.push(initial.reply); p.open(); await tick(); assert.match(p.status.textContent, /correct/);
  p.replies.push(p.json({error: 'Room is already booked.'}, 400)); initial.form.fire('submit'); await tick();
  assert.equal(p.requests[1].url, '/publish/'); assert.equal(p.status.textContent, 'Room is already booked.');
  assert.equal(p.saves, 0); assert.equal(initial.submit.disabled, false);
  initial.form.cancel.fire('click'); assert.equal(p.dialog.open, false);
});

test('price review is not reported as a validation error and contextual links stay in the dialog', async () => {
  const p = page(), initial = p.html();
  initial.section.h2.textContent = 'Add booking';
  const link = new Element('a'); link.href = '/new/?new=1';
  initial.section['[data-calendar-load]'] = [link];
  initial.form.cancel = link;
  link.attrs['data-calendar-load'] = '1';
  p.replies.push(initial.reply); p.open(); await tick();
  assert.equal(p.nodes['calendar-editor-title'].textContent, 'Add booking');
  const next = p.html({ok:false,status:400}); next.section['[data-booking-review]'] = new Element('div');
  p.replies.push(next.reply); link.fire('click'); await tick();
  assert.equal(p.requests[1].url, '/new/?new=1');
  assert.equal(p.dialog.open, true);
  assert.match(p.status.textContent, /Review the revised price/);
  assert.equal(p.saves, 0);
});

test('uncertain save cannot be blindly retried; login redirects and missing forms fail closed', async () => {
  for (const failed of [new Error('offline'), {redirected: true}, {ok: false, status: 403}, {fragment: false}]) {
    const p = page(), initial = p.html(); p.replies.push(initial.reply); p.open(); await tick();
    p.replies.push(failed instanceof Error ? failed : p.html(failed).reply);
    initial.form.fire('submit'); await tick();
    assert.match(p.status.textContent, /Refresh the calendar/); assert.equal(initial.submit.disabled, true);
    initial.form.fire('submit'); assert.equal(p.requests.length, 2); assert.equal(p.saves, 0);
    assert.equal(p.nodes['calendar-editor-close'].disabled, false);
  }
});

test('chooser prefills selected date, handles empty list and recovers from failed GET', async () => {
  const p = page(); p.open({url: null, choices: [], initialStart: '2026-12-12T14:30'});
  assert.match(p.status.textContent, /No workshops/); p.nodes['calendar-editor-close'].fire('click');
  p.open({url: null, choices: [{title: 'Workshop', createUrl: '/new/'}], initialStart: '2026-12-12T14:30'});
  assert.match(p.nodes['calendar-editor-body'].children[0].textContent, /2026/);
  const select = p.nodes['calendar-editor-body'].children.find(child => child.tag === 'label').children[0];
  select.fire('change'); assert.equal(p.requests.length, 0);
  const initial = p.html(); p.replies.push(initial.reply); select.value = '/new/'; select.fire('change'); await tick();
  assert.equal(p.requests[0].url, '/new/?starts_at=2026-12-12T14%3A30');
  assert.equal(initial.section['[name="starts_at"]'].value, '2026-12-12T14:30');
  const invalid = p.html({ok:false,status:400}); invalid.section['[name="starts_at"]'].value = '2026-12-13T15:00';
  p.replies.push(invalid.reply); initial.form.fire('submit'); await tick();
  assert.equal(invalid.section['[name="starts_at"]'].value, '2026-12-13T15:00');
  invalid.form.cancel.fire('click'); p.replies.push(new Error('offline')); p.open(); await tick();
  assert.match(p.status.textContent, /Could not open/); assert.equal(p.saves, 0);
});

test('customer booking returns saved reservation and Cancel is a non-submitting button', async () => {
  const p = page(), initial = p.html(), form = initial.form;
  const cancel = new Element('button');
  form['[data-calendar-close]'] = [cancel];
  let result;
  p.replies.push(initial.reply); p.open({onSaved: value => {result = value;}}); await tick();
  cancel.fire('click');
  assert.equal(p.dialog.open, false);
  assert.equal(p.requests.length, 1);
  p.replies.push(initial.reply); p.open({onSaved: value => {result = value;}}); await tick();
  p.replies.push(p.json({saved:true, reservation_url:'/reservation/123/'}));
  form.fire('submit'); await tick();
  assert.equal(result.reservation_url, '/reservation/123/');
});

test('loading party controls preserves domain-disabled submit and session selection loads capacity first', async () => {
  const p = page(), initial = p.html(); initial.submit.disabled = true;
  const field = new Element('div'), select = new Element('select');
  field.dataset = {calendarSessionUrl:'/booking/'}; field.select = select;
  initial.section['[data-calendar-session-url]'] = field;
  p.replies.push(initial.reply); p.open(); await tick();
  assert.equal(initial.submit.disabled, true);
  select.fire('change'); assert.equal(p.requests.length, 1);
  const next = p.html(); p.replies.push(next.reply);
  select.value = '42'; select.fire('change'); await tick();
  assert.equal(p.requests[1].url, '/booking/?session=42');
  assert.equal(p.saves, 0);
});
