const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/team-access.js', 'utf8');

function fixture() {
  const events = {};
  const makePicker = () => {
    const all = {checked: true};
    const locations = [{checked: false, dataset: {name: 'North'}}, {checked: false, dataset: {name: 'South'}}];
    const label = {};
    const menu = {style: {}, offsetHeight: 180};
    const summary = {getBoundingClientRect: () => ({left: 300, bottom: 700}), focus() { this.focused = true; }};
    const handlers = {};
    const picker = {open: false, all, locations, label, menu, summary, handlers,
      contains: target => target === all || locations.includes(target),
      querySelector: selector => ({'[value="all"]': all, summary, '.team-location-menu': menu, '[data-location-label]': label})[selector],
      querySelectorAll: () => locations,
      addEventListener: (name, fn) => { handlers[name] = fn; },
    };
    return picker;
  };
  const pickers = [makePicker(), makePicker()];
  vm.runInNewContext(source, {document: {
    querySelectorAll: () => pickers, addEventListener: (name, fn) => { events[name] = fn; },
  }, window: {innerWidth: 390, innerHeight: 844}});
  return {pickers, events};
}

test('location dropdown selects all or specific locations without changing other members', () => {
  const {pickers: [a, b]} = fixture();
  a.locations[0].checked = true;
  a.handlers.change({target: a.locations[0]});
  assert.equal(a.all.checked, false);
  assert.equal(a.label.textContent, 'North');
  a.locations[1].checked = true;
  a.handlers.change({target: a.locations[1]});
  assert.equal(a.label.textContent, '2 locations');
  a.all.checked = true;
  a.handlers.change({target: a.all});
  assert.equal(a.label.textContent, 'All locations');
  assert.ok(a.locations.every(x => !x.checked));
  assert.ok(b.all.checked && b.locations.every(x => !x.checked));
});

test('dropdown stays inside viewport and closes with outside click or Escape', () => {
  const {pickers: [a, b], events} = fixture();
  a.open = b.open = true;
  a.handlers.toggle();
  assert.equal(b.open, false);
  assert.equal(a.menu.style.left, '152px');
  assert.equal(a.menu.style.top, '656px');
  events.click({target: a.all});
  assert.equal(a.open, true);
  events.keydown({key: 'Escape'});
  assert.equal(a.open, false);
  assert.equal(a.summary.focused, true);
  a.open = true;
  events.click({target: {}});
  assert.equal(a.open, false);
});

test('absent team controls is safe', () => {
  assert.doesNotThrow(() => vm.runInNewContext(source, {
    document: {querySelectorAll: () => [], addEventListener() {}},
  }));
});
