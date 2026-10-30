const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/party-request.js', 'utf8');

test('party summary follows edits, clears missing values, and renders customer text safely', () => {
  const elements = Object.fromEntries(['occasion', 'offering', 'starts_at', 'group_size', 'space', 'organizer_name']
    .map(name => [name, {value: '', tagName: 'INPUT'}]));
  const nodes = {};
  const events = {};
  const form = {elements, addEventListener: (name, fn) => { events[name] = fn; },
    querySelector: selector => nodes[selector] ||= {textContent: ''}};
  vm.runInNewContext(source, {document: {querySelector: () => form}, Date, Intl});
  assert.equal(nodes['[data-summary="offering"]'].textContent, 'Location not selected');
  elements.offering = {value: '3', tagName: 'SELECT', selectedOptions: [{textContent: 'Workshop (East)'}]};
  elements.organizer_name.value = '<script>unsafe</script>';
  elements.group_size.value = '6';
  events.input();
  assert.equal(nodes['[data-summary="offering"]'].textContent, 'Workshop (East)');
  assert.equal(nodes['[data-summary="organizer_name"]'].textContent, '<script>unsafe</script>');
  assert.equal(nodes['[data-summary="group_size"]'].textContent, '6');
  elements.group_size.value = '';
  events.change();
  assert.equal(nodes['[data-summary="group_size"]'].textContent, 'Not set');
});
