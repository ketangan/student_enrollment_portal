const {test} = require('node:test');
const assert = require('node:assert/strict');
const {enough} = require('../../static/scheduling/material-availability.js');
const bear = {requirements: {blue: 500, stuffing: 150000, eyes: 2000}};
const bunny = {requirements: {white: 500, stuffing: 150000, eyes: 2000}};
const stock = {blue: 1000, white: 500, stuffing: 300000, eyes: 4000};

test('different designs and colors share raw material supplies', () => {
  assert.equal(enough([bear, bunny], stock), true);
  assert.equal(enough([bear, bunny], {...stock, stuffing: 299999}), false);
  assert.equal(enough([bear, bunny], {...stock, eyes: 3000}), false);
});
test('same-color group uses combined fabric, not separate variant counts', () => {
  const blueBunny = {requirements: {...bunny.requirements, white: undefined}};
  delete blueBunny.requirements.white;
  blueBunny.requirements.blue = 500;
  assert.equal(enough([bear, blueBunny], stock), true);
  assert.equal(enough([bear, blueBunny], {...stock, blue: 999}), false);
});
test('missing recipes and missing stock fail closed', () => {
  assert.equal(enough([bear, undefined], stock), false);
  assert.equal(enough([{requirements: {}}], stock), false);
  assert.equal(enough([bear], {...stock, blue: undefined}), false);
});
test('integer thousandths keep small measured amounts exact', () => {
  assert.equal(enough([{requirements: {fabric: 1}}, {requirements: {fabric: 9}}], {fabric: 10}), true);
  for (const amount of [NaN, Infinity, -1, 0, 0.5, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(enough([{requirements: {fabric: amount}}], {fabric: 100}), false);
  }
});
