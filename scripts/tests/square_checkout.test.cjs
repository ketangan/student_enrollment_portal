const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const filename = path.resolve(__dirname, '../../static/scheduling/square_checkout.js');
const source = fs.readFileSync(filename, 'utf8');

async function setup({missing = false, loadError = false, tokenError = false, tokenStatus = 'OK', networkError = false, result = {state: 'paid', return_url: '/return/'}} = {}) {
  const elements = {};
  for (const id of ['square-payment-form', 'square-status', 'square-pay', 'square-options', 'square-check-form', 'square-retry-form']) {
    elements[id] = {hidden: false, disabled: true, textContent: '', addEventListener: (_, fn) => {elements[id].submit = fn;}};
  }
  elements['square-options'].textContent = JSON.stringify({applicationId: 'sandbox-app', locationId: 'LOC', amount: '25.00'});
  const calls = [];
  const card = {attach: async () => {if(loadError) throw Error('SDK failed');}, tokenize: async details => {
    calls.push(details); if(tokenError) throw Error('invalid card'); return {status: tokenStatus, token: 'cnon:test'};
  }};
  const window = {Square: {payments: () => ({card: async () => card})}, location: {href: '/pay/', assign: value => {window.redirect = value;}}};
  class FormData {set(key, value) {this[key] = value;}}
  await vm.runInNewContext(source, {document: {getElementById: id => missing ? null : elements[id]}, window, FormData,
    fetch: async (url, options) => {calls.push(options); if(networkError) throw Error('timeout'); return {json: async () => result};}}, {filename});
  const submit = async () => elements['square-payment-form'].submit({preventDefault() {}});
  return {elements, submit, calls, window};
}

test('SDK card success sends only a token and redirects after verified payment', async () => {
  const ctx = await setup();
  assert.equal(ctx.elements['square-pay'].disabled, false);
  await ctx.submit();
  assert.equal(ctx.calls[1].body.source_id, 'cnon:test');
  assert.equal(ctx.calls[1].credentials, 'same-origin');
  assert.equal(ctx.window.redirect, '/return/');
  const count = ctx.calls.length;
  await ctx.submit();
  assert.equal(ctx.calls.length, count);
});

test('missing form and failed SDK are safe', async () => {
  await setup({missing: true});
  const ctx = await setup({loadError: true});
  assert.equal(ctx.elements['square-pay'].disabled, true);
  assert.match(ctx.elements['square-status'].textContent, /could not load/);
});

test('invalid card details allow another tokenization but no payment request', async () => {
  for (const options of [{tokenError: true}, {tokenStatus: 'INVALID'}]) {
    const ctx = await setup(options);
    await ctx.submit();
    assert.equal(ctx.calls.length, 1);
    assert.equal(ctx.elements['square-pay'].disabled, false);
  }
});

test('decline shows retry; pending and uncertain results require verification', async () => {
  for (const options of [{result: {state: 'expired', error: 'Declined'}}, {result: {state: 'unknown'}}, {networkError: true}]) {
    const ctx = await setup(options);
    await ctx.submit();
    assert.equal(ctx.elements['square-payment-form'].hidden, true);
    assert.equal(ctx.elements['square-pay'].disabled, true);
    assert.equal(ctx.window.redirect, undefined);
    const id = options.result?.state === 'expired' ? 'square-retry-form' : 'square-check-form';
    assert.equal(ctx.elements[id].hidden, false);
  }
});
