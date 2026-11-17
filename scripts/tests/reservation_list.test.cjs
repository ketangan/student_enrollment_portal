const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/reservation-list.js', 'utf8');

test('reservation toolbar uses the shared editor and refreshes only on saved callback', () => {
  const listeners = {}, opened = [];
  let hidden = 0, refreshed = 0;
  const menu = {style: {}, offsetWidth: 340, offsetHeight: 160,
    addEventListener: (event, fn) => listeners[event] = fn, hidePopover: () => hidden++};
  const nodes = {'reservation-new': {getBoundingClientRect: () => ({right: 700, bottom: 100})},
    'reservation-new-menu': menu,
    'reservation-create-choices': {textContent: '[{"createUrl":"/workshop/book/"}]'},
    'party-create-choices': {textContent: '[{"createUrl":"/party/new/?offering=1"}]'}};
  vm.runInNewContext(source, {document: {getElementById: id => nodes[id]}, innerWidth: 768, innerHeight: 1024,
    window: {addEventListener() {}, location: {reload: () => refreshed++}, BookingCalendarEditor: {open: options => opened.push(options)}}});
  listeners.toggle({newState: 'open'});
  assert.equal(menu.style.left, '360px');
  for (const kind of ['reservation', 'party']) listeners.click({target: {closest: () => ({dataset: {calendarNew: kind}})}});
  assert.equal(opened[0].title, 'Add Reservation');
  assert.equal(opened[1].title, 'Add Party');
  assert.equal(opened[1].choices[0].createUrl, '/party/new/?offering=1');
  assert.equal(hidden, 2);
  assert.equal(refreshed, 0);
  opened[1].onSaved();
  assert.equal(refreshed, 1);
});
