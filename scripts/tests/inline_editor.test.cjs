const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const filename = require('node:path').resolve('static/scheduling/inline-editor.js');
const source = fs.readFileSync(filename, 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.textContent = '';}
  append(...nodes) {this.children.push(...nodes);}
  prepend(node) {this.children.unshift(node);}
  replaceChildren(...nodes) {this.children = nodes;}
  setAttribute(k, v) {this.attrs[k] = v;}
  getAttribute(k) {return this.attrs[k] || null;}
  removeAttribute(k) {delete this.attrs[k];}
  addEventListener(k, fn) {this.listeners[k] = fn;}
  focus() {this.focused = true;}
  remove() {this.removed = true;}
  after(node) {this.next = node;}
  closest(selector) {return selector === 'tr' ? this.row : selector === 'table' ? this.grid : selector === '.table-wrap, .dash-table-scroll' ? this.table : selector === '.section-heading' ? this.heading : this.actions;}
  querySelectorAll(selector) {
    if (selector === '.actions a') return this.cancel ? [this.cancel] : [];
    return this.children.flatMap(c => [...(c.tag === 'button' ? [c] : []), ...c.querySelectorAll(selector)]);
  }
  querySelector(selector) {return this[selector] || null;}
  fire(name, props = {}) {
    const event = {preventDefault() {this.prevented = true;}, ...props};
    this.listeners[name](event);
    return event;
  }
}

function page({row = false, heading = true, icons = true, narrow = false, add = false, empty = false} = {}) {
  const links = [new Element('a'), new Element('a')];
  const body = new Element('tbody');
  body.grid = new Element('table');
  body.grid['thead tr'] = {children: [1, 2, 3, 4]};
  if (!empty) body.tr = new Element('tr');
  if (narrow) {body.table = new Element('div'); body.table.scrollWidth = 700; body.table.clientWidth = 350;}
  for (const link of links) {
    link.href = '/protected/edit/'; link.textContent = 'Edit';
    link.actions = new Element('div');
    if (add) link.attrs['data-inline-target'] = '#rows';
    if (row) {link.row = new Element('tr'); link.row.children = [1, 2, 3];}
    if (narrow) {link.table = new Element('div'); link.table.scrollWidth = 700; link.table.clientWidth = 350;}
    else if (heading) link.heading = new Element('div');
  }
  const nodes = [], requests = [], responses = [];
  let reloads = 0;
  const window = {location: {reload() {reloads++;}}};
  if (icons) window.lucide = {createIcons() {}};
  vm.runInNewContext(source, {window, document: {
    querySelectorAll() {return links;}, querySelector() {return body;}, createElement(tag) {const n = new Element(tag); nodes.push(n); return n;}
  }, DOMParser: class {parseFromString(doc) {return doc;}},
  FormData: class {constructor(form) {this.form = form; this.values = [];} append(...value) {this.values.push(value);}},
  async fetch(url, options) {requests.push({url, ...options}); const response = responses.shift(); if (response instanceof Error) throw response; return await response;}
  }, {filename});
  function response({ok = true, status = 200, redirected = false, workspace = false, content = true, errors = false, cancel = true} = {}) {
    const form = new Element('form'), section = new Element('section');
    const submit = new Element('button'); submit.type = 'submit'; form.append(submit);
    if (cancel) form.cancel = new Element('a');
    section.form = form; section.h2 = new Element('h2');
    if (errors) section['.errorlist'] = new Element('ul');
    section.append(form);
    const doc = {querySelector(s) {return s === '[data-inline-editor]' ? content && section : workspace;}};
    const value = {ok, status, redirected, async text() {return doc;}};
    return {value, form, section, submit};
  }
  return {links, body, nodes, requests, responses, response, get reloads() {return reloads;},
    get panel() {return nodes.find(n => n.tag === 'section' && !n.removed);},
    get status() {return nodes.filter(n => n.tag === 'p').at(-1);}};
}

test('inline row editor preserves endpoint, preview token and submit action, then refreshes saved record', async () => {
  const p = page({row: true}); const initial = p.response(); p.responses.push(initial.value);
  assert.equal(p.links[0].fire('click').prevented, true); await tick();
  assert.equal(p.links[0].row.next.children[0].colSpan, 3);
  assert.equal(initial.form.action, '/protected/edit/');
  const preview = p.response(); p.responses.push(preview.value);
  initial.form.fire('submit', {submitter: {name: 'action', value: 'preview'}}); await tick();
  assert.deepEqual(p.requests[1].body.values, [['action', 'preview']]);
  assert.equal(p.requests[1].body.form, initial.form);
  p.responses.push(p.response({redirected: true, workspace: true, content: false}).value);
  preview.form.fire('submit', {submitter: {name: 'action', value: 'confirm'}}); await tick();
  assert.equal(p.reloads, 1);
});

test('validation stays inline, close and Escape restore focus, modified clicks remain normal links', async () => {
  const p = page({icons: false}); p.links[0].attrs['aria-label'] = 'Edit teacher';
  assert.equal(p.links[0].fire('click', {ctrlKey: true}).prevented, undefined);
  p.responses.push(p.response({ok: false, status: 400, errors: true}).value);
  p.links[0].fire('click'); await tick();
  assert.match(p.status.textContent, /correct/);
  assert.equal(p.panel.attrs['aria-label'], 'Edit teacher');
  p.links[1].fire('click'); assert.equal(p.panel.focused, true); assert.equal(p.requests.length, 1);
  const panel = p.panel; panel.fire('keydown', {key: 'Tab'}); panel.fire('keydown', {key: 'Escape'});
  assert.equal(panel.removed, true); assert.equal(p.links[0].focused, true);
  p.responses.push(p.response().value); p.links[0].fire('click'); await tick();
  p.links[0].fire('click'); assert.equal(p.links[0].attrs['aria-expanded'], 'false');
});

test('failed load, failed save and login redirects are never treated as success', async () => {
  for (const failed of [new Error('offline'), {ok: true, redirected: true, content: false}, {ok: false, status: 403}]) {
    const p = page({heading: false});
    p.responses.push(failed instanceof Error ? failed : p.response(failed).value);
    p.links[0].fire('click'); await tick();
    assert.match(p.status.textContent, /Could not open/); assert.equal(p.reloads, 0);
    p.panel.children[0].fire('click');
    const initial = p.response({cancel: false}); p.responses.push(initial.value);
    p.links[0].fire('click'); await tick();
    p.responses.push(failed instanceof Error ? failed : p.response(failed).value);
    initial.form.fire('submit'); await tick();
    assert.match(p.status.textContent, /Could not confirm/); assert.equal(p.reloads, 0);
    assert.equal(initial.submit.disabled, true);
    initial.form.fire('submit'); assert.equal(p.requests.length, 3);
  }
});

test('busy editor prevents double submits and navigation; form cancel closes without saving', async () => {
  const p = page(); let release;
  const initial = p.response(); p.responses.push(new Promise(resolve => {release = resolve;}));
  p.links[0].fire('click'); p.links[1].fire('click');
  p.panel.fire('keydown', {key: 'Escape'}); p.panel.children[0].fire('click');
  assert.equal(p.panel.removed, undefined); release(initial.value); await tick();
  p.responses.push(new Promise(resolve => {release = resolve;}));
  initial.form.fire('submit'); initial.form.fire('submit'); initial.form.cancel.fire('click');
  assert.equal(p.requests.length, 2); assert.equal(initial.submit.disabled, true);
  release(p.response().value); await tick();
  const form = p.panel.children[2].children[0].form;
  form.cancel.fire('click'); assert.equal(p.links[0].attrs['aria-expanded'], 'false');
});

test('on narrow screens the editor stays full width below the scrollable table', async () => {
  const p = page({row: true, narrow: true}); p.responses.push(p.response().value);
  p.links[0].fire('click'); await tick();
  assert.equal(p.links[0].table.next, p.panel);
  assert.equal(p.links[0].row.next, undefined);
});

test('Add opens in the target grid, including empty grids, never in the heading actions', async () => {
  for (const empty of [false, true]) {
    const p = page({add: true, empty}); p.responses.push(p.response().value);
    p.links[0].fire('click'); await tick();
    assert.equal(p.body.children[0].children[0].colSpan, 4);
    assert.equal(p.body.children[0].children[0].children[0], p.panel);
    assert.equal(p.links[0].actions.next, undefined);
    assert.equal(p.links[0].heading.next, undefined);
  }
  const p = page({add: true, empty: true, narrow: true}); p.responses.push(p.response().value);
  p.links[0].fire('click'); await tick();
  assert.equal(p.body.table.next, p.panel);
});

test('header editors sit after the whole heading, never beside the title', async () => {
  const p = page(); p.responses.push(p.response().value);
  p.links[0].fire('click'); await tick();
  assert.equal(p.links[0].heading.next, p.panel);
  assert.equal(p.links[0].actions.next, undefined);
});
