const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/party-package.js', 'utf8');

function page(locationTax = null) {
  const values = {name:'Birthday party', offering:'1', duration_minutes:'90', participant_limit:'8',
    included_observers:'2', additional_participant_limit:'4', additional_participant_price:'25',
    total:'280', deposit:'50', tax_rate:'10', room:'10'};
  const elements = Object.fromEntries(Object.entries(values).map(([name,value]) => [name,{value}]));
  elements.offering.selectedOptions = [{textContent:'Workshop (Pasadena)'}];
  elements.room.options = [{value:''},{value:'10'},{value:'20'}];
  elements.room.selectedOptions = [elements.room.options[1]];
  const output = {};
  const listeners = {};
  const form = {elements, addEventListener:(name,fn) => { listeners[name] = fn; },
    querySelector: selector => output[selector] ||= {textContent:''}};
  const document = {querySelector: () => form, getElementById: id => ({textContent:JSON.stringify(
    id === 'package-offerings' ? {'1':{location:1,tax_rate:locationTax},'2':{location:2}} : {'10':{location:1},'20':{location:2}})})};
  vm.runInNewContext(source, {document, Intl});
  return {elements,listeners,value:name=>output[`[data-package-summary="${name}"]`].textContent};
}

test('package summary updates prices, group capacity and duration from existing fields', () => {
  const p = page();
  assert.equal(p.value('price'), '$280.00 + tax');
  assert.equal(p.value('maximum'), '12 · $380.00 + tax');
  assert.equal(p.value('duration'), '1 hr 30 min');
  assert.equal(p.value('deposit'), '$50.00');
  p.elements.additional_participant_limit.value = '0';
  p.elements.tax_rate.value = '';
  p.elements.deposit.value = '0';
  p.listeners.input();
  assert.equal(p.value('extras'), 'None');
  assert.equal(p.value('maximum'), '8 · $280.00');
  assert.equal(p.value('deposit'), 'None');
  p.elements.total.value = '';
  p.elements.name.value = '<script>text</script>';
  p.listeners.input();
  assert.equal(p.value('price'), 'Price not set');
  assert.equal(p.value('name'), '<script>text</script>');
});

test('changing workshop clears a room from the previous location', () => {
  const p = page();
  assert.equal(p.elements.room.options[1].disabled, false);
  assert.equal(p.elements.room.options[2].disabled, true);
  p.elements.offering.value = '2';
  p.listeners.change({target:p.elements.offering});
  assert.equal(p.elements.room.value, '');
  assert.equal(p.elements.room.options[1].disabled, true);
  assert.equal(p.elements.room.options[2].disabled, false);
});

test('blank package tax inherits location tax while explicit zero overrides it', () => {
  const p = page('8.875');
  p.elements.tax_rate.value = '';
  p.listeners.input();
  assert.equal(p.value('price'), '$280.00 + tax');
  p.elements.tax_rate.value = '0';
  p.listeners.input();
  assert.equal(p.value('price'), '$280.00');
});
