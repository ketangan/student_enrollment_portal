const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync('static/scheduling/booking-document.js', 'utf8');

test('document print uses the browser print action', () => {
  let printed = 0;
  let icons = 0;
  let click;
  vm.runInNewContext(source, {
    window: {print: () => printed++, lucide: {createIcons: () => icons++}},
    document: {querySelector: selector => {
      assert.equal(selector, '[data-document-print]');
      return {addEventListener: (name, handler) => {assert.equal(name, 'click'); click = handler;}};
    }},
  });
  assert.equal(icons, 1);
  assert.equal(printed, 0);
  click();
  assert.equal(printed, 1);
});

test('missing print control or icons is harmless', () => {
  vm.runInNewContext(source, {window: {}, document: {querySelector: () => null}});
});
