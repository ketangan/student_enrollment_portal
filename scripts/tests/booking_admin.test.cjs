const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../../static/scheduling/admin.js');
const source = fs.readFileSync(filename, 'utf8');

function element() {
  const classes = new Set();
  return {
    attrs: {}, listeners: {}, value: '', open: false, focused: false,
    classList: {toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name)},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = value; },
    getAttribute(name) { return this.attrs[name]; },
    contains(target) { return target === this; },
    focus() { this.focused = true; },
  };
}

function page({saved = null, blocked = false, chooser = false, noAccount = false, dashboard = false, icons = false} = {}) {
  const document = element();
  document.body = element();
  const summary = element();
  const nodes = Object.fromEntries(['booking-nav', 'booking-nav-toggle', 'booking-account', 'booking-theme'].map(id => [id, element()]));
  if (dashboard) {
    nodes['dashboard-location-filter'] = element();
    nodes['dashboard-location-filter'].submissions = 0;
    nodes['dashboard-location-filter'].requestSubmit = function () { this.submissions++; };
  }
  nodes['booking-account'].querySelector = () => summary;
  nodes['booking-nav-toggle'].attrs['aria-expanded'] = 'false';
  if (chooser) { delete nodes['booking-nav']; delete nodes['booking-nav-toggle']; }
  if (noAccount) { delete nodes['booking-account']; delete nodes['booking-theme']; }
  document.getElementById = id => nodes[id] || null;
  const media = element();
  const storage = {
    saved,
    getItem(key) { assert.equal(key, 'dash_theme'); if (blocked) throw Error('denied'); return this.saved; },
    setItem(key, value) { assert.equal(key, 'dash_theme'); if (blocked) throw Error('denied'); this.saved = value; },
  };
  const lucide = icons ? {createIcons() { document.iconsRendered = true; }} : undefined;
  vm.runInNewContext(source, {document, window: {matchMedia: () => media, lucide}, localStorage: storage}, {filename});
  return {document, nodes, media, storage, summary};
}

test('mobile navigation toggles, closes outside and on resize, and restores focus on Escape', () => {
  const {nodes, document, media} = page();
  const nav = nodes['booking-nav'];
  const toggle = nodes['booking-nav-toggle'];
  toggle.listeners.click();
  assert.equal(toggle.attrs['aria-expanded'], 'true');
  assert.equal(toggle.attrs['aria-label'], 'Close navigation');
  document.listeners.click({target: nav});
  assert.equal(nav.classList.contains('dash-nav--mobile-open'), true);
  document.listeners.keydown({key: 'Tab'});
  document.listeners.keydown({key: 'Escape'});
  assert.equal(toggle.focused, true);
  assert.equal(toggle.attrs['aria-expanded'], 'false');
  toggle.listeners.click();
  media.listeners.change();
  assert.equal(nav.classList.contains('dash-nav--mobile-open'), false);
  toggle.listeners.click();
  document.listeners.click({target: {}});
  assert.equal(toggle.attrs['aria-label'], 'Open navigation');
  toggle.listeners.click();
  toggle.listeners.click();
  assert.equal(toggle.attrs['aria-expanded'], 'false');
});

test('workspace chooser and missing optional controls are safe', () => {
  for (const options of [{chooser: true}, {noAccount: true}, {chooser: true, noAccount: true}]) {
    const {document, media, nodes} = page(options);
    nodes['booking-nav-toggle']?.listeners.click();
    document.listeners.click({target: {}});
    document.listeners.keydown({key: 'Escape'});
    media.listeners.change();
  }
});

test('Dashboard location change submits the GET form once without changing the account or theme', () => {
  const {nodes, storage} = page({dashboard: true, saved: 'mint'});
  const form = nodes['dashboard-location-filter'];
  assert.equal(form.submissions, 0);
  form.listeners.change();
  assert.equal(form.submissions, 1);
  assert.equal(storage.saved, 'mint');
});

test('available local icons render without requiring a remote library', () => {
  assert.equal(page({icons: true}).document.iconsRendered, true);
});
