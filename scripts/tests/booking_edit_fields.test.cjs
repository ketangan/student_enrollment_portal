const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/scheduling/booking-edit-fields.js', 'utf8');

test('participant removal appears only for a reduction, and hidden choices cannot be submitted', () => {
  const choices = [{checked:true}, {checked:false}], hint = {}, quantity = {value:'3',addEventListener(_event, fn) {this.update=fn;}};
  const removals = {dataset:{originalCount:'3'},querySelectorAll:()=>choices,querySelector:()=>hint};
  const root = {querySelector:selector=>selector === '[data-original-count]' ? removals : quantity};
  const window = {};
  vm.runInNewContext(source,{window,document:root});
  assert.equal(removals.hidden,true);
  assert.equal(choices[0].checked,false);
  assert.ok(choices.every(choice=>choice.disabled));
  quantity.value='2'; quantity.update();
  assert.equal(removals.hidden,false); assert.match(hint.textContent,/Select 1 participant to remove/);
  assert.ok(choices.every(choice=>!choice.disabled));
  quantity.value='1'; quantity.update(); assert.match(hint.textContent,/Select 2 participants/);
  quantity.value='4'; quantity.update(); assert.equal(removals.hidden,true);
  window.BookingEditFields.mount({querySelector:()=>null});
});
