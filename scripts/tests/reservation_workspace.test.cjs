const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/reservation-workspace.js', 'utf8');

function element(extra = {}) {
  return {listeners: {}, attributes: {}, dataset: {}, hidden: false,
    addEventListener(event, fn) { this.listeners[event] = fn; },
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; },
    classList: {toggle() {}}, querySelector() { return null; }, ...extra};
}

function page(inquiry = false) {
  const panels = ['details', 'preparation'].map(name => element({dataset: {reservationPanel: name}}));
  const tabs = ['http://localhost/detail/', 'http://localhost/detail/prep/'].map(href => element({href}));
  if (inquiry) tabs[1].href = 'http://localhost/detail/?tab=preparation';
  const notes = {value: 'An unsaved note'};
  const replacement = element();
  const section = element({replaceWith(next) { this.replacement = next; }});
  const workspace = element({querySelectorAll: () => panels,
    querySelector: selector => selector.startsWith('section') ? section : panels[1]});
  const menu = element({open:true, contains: target => target === menu, querySelector: () => ({focus(){menu.focused=true;}})});
  const document = element({querySelector: selector => selector === '[data-reservation-workspace]' ? workspace : null,
    querySelectorAll: selector => selector === '.reservation-action-menu[open]' ? (menu.open ? [menu] : []) : tabs, getElementById: () => null});
  const location = {href: tabs[0].href, pathname: '/detail/'};
  const window = element({scrollY: 440, scrollTo({top}) { this.scrollY = top; }});
  const pushes = [];
  const calls = [];
  const history = {pushState(state, _, url) { pushes.push({state, url}); }};
  class FormData { constructor(form) { this.form = form; } }
  class DOMParser { parseFromString() { return {getElementById: () => null, querySelector: selector => selector.startsWith('section') ? replacement : null}; } }
  vm.runInNewContext(source, {document, window, history, location, URL, FormData, DOMParser,
    fetch: async (url, options) => { calls.push({url, options}); return {ok: true, text: async () => '<section />'}; }});
  return {panels, tabs, notes, workspace, window, section, replacement, pushes, calls, document, menu, location};
}

test('inquiry tab history distinguishes panels on the same path and preserves draft notes', () => {
  const p = page(true);
  p.tabs[1].listeners.click({preventDefault() {}});
  p.location.href = p.tabs[1].href;
  p.window.listeners.popstate();
  assert.equal(p.panels[1].hidden, false);
  p.location.href = p.tabs[0].href;
  p.window.listeners.popstate();
  assert.equal(p.panels[0].hidden, false);
  assert.equal(p.notes.value, 'An unsaved note');
});

test('switching tabs hides only panels, preserves notes and supports browser back', () => {
  const p = page();
  let prevented = false;
  p.tabs[1].listeners.click({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(p.panels[0].hidden, true);
  assert.equal(p.panels[1].hidden, false);
  assert.equal(p.notes.value, 'An unsaved note');
  assert.equal(p.calls.length, 0);
  assert.equal(p.pushes.length, 1);
  p.window.listeners.popstate();
  assert.equal(p.panels[0].hidden, false);
  assert.equal(p.panels[1].hidden, true);
  p.tabs[1].listeners.click({ctrlKey: true, preventDefault() { throw Error('Modified click intercepted'); }});
  assert.equal(p.pushes.length, 1);
});

test('reservation action menu closes outside and on Escape but not inside', () => {
  const p=page();
  p.document.listeners.click({target:p.menu});assert.equal(p.menu.open,true);
  p.document.listeners.click({target:{}});assert.equal(p.menu.open,false);
  p.menu.open=true;p.document.listeners.keydown({key:'Escape'});
  assert.equal(p.menu.open,false);assert.equal(p.menu.focused,true);
});

for (const action of ['save_selections', 'save_guest', 'correct_terminal', 'save_notes']) {
test(`${action} ignores the shadowing action field and restores scroll position`, async () => {
  const p = page();
  const form = {elements: {action: {value: action}}, action: {value: action},
    getAttribute: () => null};
  let prevented = false;
  p.workspace.listeners.submit({target: form, preventDefault() { prevented = true; }});
  await new Promise(setImmediate);
  assert.equal(prevented, true);
  assert.equal(p.calls[0].url, 'http://localhost/detail/');
  assert.equal(p.calls[0].options.method, 'POST');
  assert.equal(p.calls[0].options.body.form, form);
  assert.equal(p.section.replacement, p.replacement);
  assert.equal(p.window.scrollY, 440);
  assert.equal(p.notes.value, 'An unsaved note');
});
}
