const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const filename = path.resolve(__dirname, '../../static/scheduling/color-picker.js');
const source = fs.readFileSync(filename, 'utf8');

function setup(value) {
  const field = value => ({value, hidden: true, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    dispatchEvent(event) { this.lastEvent = event; this.listeners[event.type](); },
  });
  const picker = field('#ffffff'), hex = field(value);
  const control = {querySelector: selector => selector.includes('color') ? picker : hex};
  vm.runInNewContext(source, {document: {querySelectorAll: () => [control]}, Event}, {filename});
  return {picker, hex};
}

test('saved and typed valid hex values update the picker without losing text', () => {
  const {picker, hex} = setup('#aAbBcC');
  assert.equal(picker.hidden, false);
  assert.equal(picker.value, '#aAbBcC');
  hex.value = '#112233'; hex.listeners.input();
  assert.equal(picker.value, '#112233');
});

test('picker updates the submitted hex and emits a bubbling input event', () => {
  const {picker, hex} = setup('');
  assert.equal(hex.value, '');
  picker.value = '#abc123'; picker.listeners.input();
  assert.equal(hex.value, '#ABC123');
  assert.equal(hex.lastEvent.bubbles, true);
});

test('invalid or partial hex remains editable and unrelated pages are safe', () => {
  const {picker, hex} = setup('#bad');
  assert.equal(picker.value, '#ffffff');
  assert.equal(hex.value, '#bad');
  hex.value = ''; hex.listeners.input();
  assert.equal(picker.value, '#ffffff');
  vm.runInNewContext(source, {document: {querySelectorAll: () => []}}, {filename});
});
