const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('delegated column menus anchor to the header button, not the document', () => {
  const listeners = {};
  const classes = new Set();
  const panel = {style: {}, offsetHeight: 200, offsetWidth: 220, querySelectorAll: () => [], querySelector: () => null,
    classList: {add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value)}};
  const button = {dataset: {columnMenu: 'status'}, attributes: {},
    setAttribute(key, value) { this.attributes[key] = value; },
    getBoundingClientRect: () => ({bottom: 250, left: 400})};
  const document = {addEventListener: (name, fn) => listeners[name] = fn, getElementById: () => panel,
    querySelectorAll: selector => selector.includes('data-column-menu') ? [button] : classes.has('open') ? [panel] : []};
  const window = {innerHeight: 900, innerWidth: 1200, addEventListener() {}};
  vm.runInNewContext(fs.readFileSync('static/school_admin/column-filters.js', 'utf8'), {document, window});
  listeners.click({currentTarget: document, target: {closest: () => button}, stopPropagation() {}});
  assert.equal(panel.style.top, '254px');
  assert.equal(panel.style.left, '400px');
  assert.equal(button.attributes['aria-expanded'], 'true');
  listeners.click({currentTarget: document, target: {closest: () => button}, stopPropagation() {}});
  assert.equal(classes.has('open'), false);
});
