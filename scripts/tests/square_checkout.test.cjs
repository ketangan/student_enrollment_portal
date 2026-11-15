const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const filename = path.resolve(__dirname, '../../static/scheduling/square_checkout.js');
const source = fs.readFileSync(filename, 'utf8');

async function setup({missing = false, loadError = false, tokenError = false, tokenStatus = 'OK', networkError = false, sdkMissing = false, hang = '', result = {state: 'paid', return_url: '/return/'}} = {}) {
  const elements = {};
  for (const id of ['square-payment-form', 'square-status', 'square-pay', 'square-options', 'square-check-form', 'square-retry-form', 'square-reload', 'square-sdk']) {
    elements[id] = {hidden: false, disabled: true, textContent: '', listeners: {},
      addEventListener: (name, fn) => {elements[id].listeners[name] = fn; elements[id].submit = fn;},
      removeEventListener: name => {delete elements[id].listeners[name];}};
  }
  elements['square-options'].textContent = JSON.stringify({applicationId: 'sandbox-app', locationId: 'LOC', amount: '25.00'});
  const calls = [];
  let finish, timeout;
  const blocked = new Promise(resolve => {finish = resolve;});
  const card = {destroy: async () => {calls.push('destroy');}, attach: async () => {if(loadError) throw Error('SDK failed'); if(hang === 'attach') await blocked;}, tokenize: async details => {
    calls.push(details); if(tokenError) throw Error('invalid card'); return {status: tokenStatus, token: 'cnon:test'};
  }};
  const Square = {payments: () => ({card: async () => {if(hang === 'card') await blocked; return card;}})};
  const window = {Square: sdkMissing ? undefined : Square, location: {href: '/pay/', reload: () => {window.reloaded = true;}, assign: value => {window.redirect = value;}}};
  class FormData {set(key, value) {this[key] = value;}}
  const running = vm.runInNewContext(source, {document: {getElementById: id => missing ? null : elements[id]}, window, FormData,
    setTimeout: fn => {timeout = fn; return 1;}, clearTimeout: () => {},
    fetch: async (url, options) => {calls.push(options); if(networkError) throw Error('timeout'); return {json: async () => result};}}, {filename});
  const submit = async () => elements['square-payment-form'].submit({preventDefault() {}});
  if (!hang && !sdkMissing) await running;
  return {elements, submit, calls, window, running, finish, timeout, Square};
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

test('SDK script failure and unresolved initialization stop safely and reload without posting', async () => {
  for (const options of [{sdkMissing: true}, {hang: 'card'}, {hang: 'attach'}]) {
    const ctx = await setup(options);
    await Promise.resolve();
    ctx.timeout();
    await ctx.running;
    assert.equal(ctx.elements['square-pay'].disabled, true);
    assert.equal(ctx.elements['square-payment-form'].hidden, true);
    assert.equal(ctx.elements['square-reload'].hidden, false);
    assert.match(ctx.elements['square-status'].textContent, /No payment was submitted/);
    ctx.finish();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(ctx.elements['square-pay'].disabled, true, 'late SDK result cannot enable payment');
    ctx.elements['square-reload'].listeners.click();
    assert.equal(ctx.window.reloaded, true);
    assert.equal(ctx.calls.some(call => call?.body), false);
  }
});

test('asynchronous SDK load and error both leave actionable states', async () => {
  const loaded = await setup({sdkMissing: true});
  loaded.window.Square = loaded.Square;
  loaded.elements['square-sdk'].listeners.load();
  await loaded.running;
  assert.equal(loaded.elements['square-pay'].disabled, false);
  const failed = await setup({sdkMissing: true});
  failed.elements['square-sdk'].listeners.error();
  await failed.running;
  assert.equal(failed.elements['square-pay'].disabled, true);
  assert.match(failed.elements['square-status'].textContent, /could not load/);
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
