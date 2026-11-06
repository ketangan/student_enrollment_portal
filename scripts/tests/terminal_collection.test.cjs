const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/terminal-collection.js', 'utf8');
const tick = () => new Promise(setImmediate);

function setup(kind = 'collect') {
  const listeners = {}, calls = [], timers = new Map();
  let counter = 0;
  const p = {kind, refreshes: 0, refreshOK: true, active: ['poll', 'refund'].includes(kind), message: {textContent: '', hidden: true}, response: {state: 'unknown', status: 'PENDING', attention: '', active: true}};
  const reasonRow = {};
  const button = {};
  const summary = {}, reduction = {}, treatmentFields = {}, error = {}, progress = {};
  const dialog = {close() { p.closed = true; }};
  const form = {dataset: {state: 'unknown', status: 'PENDING', attention: '', active: 'true'},
    elements: {amount: {value: '25', setCustomValidity(value) { this.error = value; }}, expected_balance: {value: '10000'},
      treatment: {value: 'discount'}, settle_balance: {checked: false},
      device: {selectedOptions: [{dataset: {maximum: '999999.99'}}]}, reason: {closest: () => reasonRow}},
    reportValidity() { return !this.elements.amount.error && !p.invalid; },
    getAttribute: () => '/terminal/',
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { delete this[name]; },
    querySelectorAll: () => [button],
    querySelector(selector) { return ({'[data-collection-summary]': summary,
      '[data-collection-reduction]': reduction, '[data-collection-treatment]': treatmentFields,
      '[data-collection-error]': error, '[data-payment-progress]': progress})[selector] || p.message; },
    append() { assert.fail('Messages must use the fixed form slot, not append new elements'); },
    matches(selector) { return selector.includes(kind === 'refund-submit' ? '[data-booking-refund]' : kind === 'refund' ? '[data-refund-poll]' : `[data-terminal-${kind === 'poll' ? 'poll' : kind}]`); },
    closest: () => kind === 'collect' ? dialog : null};
  const document = {hidden: false,
    getElementById(id) { return id === 'money-title' ? {focus() { p.paymentFocused = true; }, scrollIntoView() { p.paymentVisible = true; }} : null; },
    querySelectorAll(selector) { if (selector === '[data-booking-refund]') return kind === 'refund-submit' ? [form] : []; return p.pollForms || (p.active || (kind === 'health' && form.dataset.active === 'true') ? [form] : []); },
    querySelector(selector) {
      if (selector === '[data-reservation-workspace], [data-terminal-health]') return kind !== 'absent' ? {} : null;
      if (selector === '[data-terminal-collect]') return kind === 'collect' ? form : null;
      if (selector === 'dialog[open]') return p.dialogOpen ? dialog : null;
      return p.active || (kind === 'health' && form.dataset.active === 'true') ? form : null;
    },
    addEventListener(name, fn) { listeners[name] = fn; },
    dispatchEvent(event) { p.refreshes++; event.detail.resolve(p.refreshOK); },
    createElement() { assert.fail('Terminal feedback must not create floating messages'); }};
  class FormData { constructor() { this.values = {}; } set(k,v) { this.values[k] = v; } }
  class CustomEvent { constructor(type, {detail}) { this.type = type; this.detail = detail; } }
  p.now = Date.now();
  vm.runInNewContext(source, {document, Date: {now: () => p.now}, window: {addEventListener(name, fn) { listeners[name] = fn; }}, FormData, CustomEvent, Intl,
    fetch: async (url, options) => {
      calls.push({url, options});
      if (p.wait) await p.wait;
      if (p.networkError) throw Error('Network error');
      return {ok: !p.error, json: async () => p.error ? {error: p.error === true ? '' : p.error} : p.response};
    }, setTimeout(fn, delay) { p.delay = delay; timers.set(++counter, fn); return counter; }, clearTimeout(id) { timers.delete(id); }},
    {filename: fs.realpathSync('static/scheduling/terminal-collection.js')});
  return Object.assign(p, {form, reasonRow, button, summary, reduction, treatmentFields, progress, errorNode: error, document, calls, listeners,
    async submit(value = 'collect') { listeners.submit({target: form, submitter: value ? {value} : null, preventDefault() {}}); await tick(); },
    async poll() { const fn = [...timers.values()].at(-1); timers.clear(); fn?.(); await tick(); }});
}

test('amount preview distinguishes partial, purchase, discount and credit', () => {
  const p = setup();
  assert.match(p.summary.textContent, /still owe \$75.00/);
  assert.notEqual(p.reasonRow.hidden, true);
  assert.equal(p.treatmentFields.disabled, true);
  p.form.elements.amount.value = '125'; p.listeners.input();
  assert.match(p.summary.textContent, /\$25.00 additional/);
  assert.notEqual(p.form.elements.reason.required, true);
  p.form.elements.amount.value = '75'; p.form.elements.settle_balance.checked = true; p.listeners.change();
  assert.equal(p.treatmentFields.disabled, false);
  assert.match(p.summary.textContent, /\$25.00 discount/);
  p.form.elements.treatment.value = 'credit'; p.listeners.input();
  assert.match(p.summary.textContent, /\$25.00 credit/);
  p.form.elements.settle_balance.checked = false; p.listeners.input();
  assert.equal(p.treatmentFields.disabled, true);
  assert.match(p.summary.textContent, /still owe \$25.00/);
  p.form.elements.amount.value = '100'; p.listeners.input();
  assert.equal(p.reduction.hidden, true);
  assert.equal(p.summary.textContent, '');
  assert.equal(p.summary.hidden, true);
  p.form.elements.amount.value = ''; p.listeners.input();
  assert.equal(p.summary.textContent, '');
  p.form.elements.amount.value = 'NaN'; p.listeners.input();
  assert.equal(p.reduction.hidden, true);
});

test('sandbox limit is visible before submission and clears when device or amount changes', async () => {
  const p = setup();
  p.form.elements.device.selectedOptions[0].dataset.maximum = '25';
  p.form.elements.amount.value = '33.22'; p.listeners.input();
  assert.match(p.errorNode.textContent, /Sandbox supports up to \$25.00/);
  assert.equal(p.errorNode.hidden, false);
  await p.submit(); assert.equal(p.calls.length, 0);
  p.form.elements.amount.value = '25'; p.listeners.input();
  assert.equal(p.errorNode.hidden, true);
  await p.submit(); assert.equal(p.calls.length, 1);
  p.form.elements.amount.value = '33.22';
  p.form.elements.device.selectedOptions[0].dataset.maximum = '999999.99'; p.listeners.change();
  assert.equal(p.form.elements.amount.error, '');
  p.form.elements.amount.value = '1000000'; p.listeners.input();
  assert.match(p.errorNode.textContent, /999,999.99 or less/);
});

test('submission refreshes payment only and guards double clicks', async () => {
  const p = setup();
  let resolve; p.wait = new Promise(r => { resolve = r; });
  await p.submit(); await p.submit();
  assert.equal(p.progress.textContent, 'Sending payment to Terminal...');
  assert.equal(p.progress.hidden, false);
  assert.equal(p.calls.length, 1); assert.equal(p.button.disabled, true);
  resolve(); await tick();
  assert.equal(p.refreshes, 1); assert.equal(p.closed, true); assert.equal(p.button.disabled, false);
  assert.equal(p.paymentFocused, true); assert.equal(p.paymentVisible, true);
  assert.equal(p.calls[0].options.headers.Accept, 'application/json');
  assert.equal(p.progress.hidden, true);
  p.listeners.submit({target: {matches: () => false}});
  assert.equal(p.calls.length, 1);
});

test('one configured Terminal uses its hidden device limit without a selector', async () => {
  const p = setup();
  p.form.elements.device = {value: 'only-terminal', dataset: {maximum: '25'}};
  p.form.elements.amount.value = '26'; p.listeners.input();
  assert.match(p.errorNode.textContent, /Sandbox supports up to \$25.00/);
  await p.submit(); assert.equal(p.calls.length, 0);
  p.form.elements.amount.value = '25'; await p.submit();
  assert.equal(p.calls.length, 1);
});

test('native refund submission provides immediate feedback, blocks duplicates, and restores on browser back', async () => {
  const p = setup('refund-submit');
  await p.submit();
  assert.equal(p.progress.textContent, 'Submitting refund...');
  assert.equal(p.progress.hidden, false);
  assert.equal(p.button.disabled, true);
  assert.equal(p.form['aria-busy'], 'true');
  let prevented = false;
  p.listeners.submit({target: p.form, preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  p.listeners.pageshow();
  assert.equal(p.button.disabled, false);
  assert.equal(p.progress.hidden, true);
  assert.equal(p.form.dataset.submitting, undefined);
  assert.equal(p.calls.length, 0);
  setup().listeners.pageshow();
});

test('manual refund refresh reports progress and checks the entire request', async () => {
  const p = setup('refund');
  let resolve; p.wait = new Promise(r => { resolve = r; });
  await p.submit('check_all');
  assert.equal(p.progress.textContent, 'Checking status...');
  assert.equal(p.button.disabled, true);
  resolve(); await tick();
  assert.equal(p.calls[0].options.body.values.action, 'check_all');
  assert.equal(p.progress.hidden, true);
  assert.equal(p.refreshes, 1);
});

test('unchanged status does not replace the page; settlement refreshes payment', async () => {
  const p = setup('poll');
  await p.poll(); assert.equal(p.refreshes, 0);
  p.response = {state: 'paid', status: 'COMPLETED', attention: '', active: false};
  await p.poll(); assert.equal(p.refreshes, 1);
  assert.equal(p.message.hidden, true);
  assert.equal(p.message.textContent, '');
  assert.equal(p.calls[0].options.body.values.action, 'check');
  p.active = false; await p.poll(); assert.equal(p.calls.length, 2);
});

for (const state of ['pending', 'succeeded', 'failed', 'requires_action']) {
  test(`refund ${state} refreshes payment history without a floating message`, async () => {
    const p = setup('refund');
    p.response = {state, status: state, attention: ''};
    await p.poll();
    assert.equal(p.refreshes, 1);
    assert.equal(p.calls[0].options.body.values.action, 'check_all');
    assert.equal(p.message.hidden, true);
    await p.submit('retry');
    assert.equal(p.calls[1].options.body.values.action, 'retry');
  });
}

test('split refunds use one batch check without starving a Terminal payment', async () => {
  const p = setup('refund');
  const second = {...p.form, getAttribute: () => '/second-refund/'};
  const terminal = {...p.form, matches: selector => selector.includes('[data-terminal-poll]'), getAttribute: () => '/payment/'};
  p.pollForms = [p.form, second, terminal];
  await p.poll(); await p.poll(); await p.poll();
  assert.deepEqual(p.calls.map(call => call.url), ['/terminal/', '/payment/', '/terminal/']);
  assert.deepEqual(p.calls.map(call => call.options.body.values.action), ['check_all', 'check', 'check_all']);
});

test('batch changes refresh even when the anchor refund is unchanged', async () => {
  const p = setup('refund');
  p.form.id = 'refund-check-one';
  const second = {...p.form, id: 'refund-check-two'};
  p.pollForms = [p.form, second];
  p.response.refunds = [{public_id: 'two', state: 'succeeded', attention: ''}];
  await p.poll();
  assert.equal(p.refreshes, 1);
  p.response.refunds = [{public_id: 'old', state: 'succeeded', attention: ''}, {public_id: 'two', state: 'unknown', attention: ''}];
  await p.poll();
  assert.equal(p.refreshes, 1);
  p.response.refunds[1].attention = 'Check provider';
  await p.poll();
  assert.equal(p.refreshes, 2);
});

test('recent operations poll quickly then slow down without resetting on content refresh', async () => {
  for (const kind of ['poll', 'refund']) {
    const p = setup(kind);
    p.form.dataset.pollStarted = String(p.now / 1000);
    p.listeners['booking:content-updated']();
    assert.equal(p.delay, 2000);
    p.now += 60000; await p.poll();
    assert.equal(p.delay, 5000);
    p.now += 60000; await p.poll();
    assert.equal(p.delay, 15000);
    p.listeners['booking:content-updated']();
    assert.equal(p.delay, 15000);
  }
});

test('split refund network errors share one recovery slot tied to the failing request', async () => {
  const p = setup('refund');
  const feedback = {hidden: true}, check = {setAttribute(name, value) { this[name] = value; }};
  const status = {querySelector(selector) { return ({'[data-terminal-message]': p.message,
    '[data-refund-feedback]': feedback, '[data-refund-check]': check, '[data-payment-progress]': p.progress})[selector]; }};
  p.form.id = 'refund-one';
  p.form.closest = selector => selector === '[data-refund-status]' ? status : null;
  const second = {...p.form, id: 'refund-two', getAttribute: () => '/second-refund/'};
  p.pollForms = [p.form, second];
  p.networkError = true;
  await p.poll();
  assert.equal(feedback.hidden, false);
  assert.equal(check.form, 'refund-one');
  await p.poll();
  assert.equal(check.form, 'refund-one');
  assert.match(p.message.textContent, /Automatic status check unavailable/);
  p.networkError = false;
  await p.submit('check');
  assert.equal(feedback.hidden, true);
  assert.equal(p.message.hidden, true);
});

for (const [state, status] of [['paid', 'COMPLETED'], ['expired', 'CANCELED'], ['unknown', 'PENDING'],
  ['unknown', 'IN_PROGRESS'], ['unknown', 'CANCEL_REQUESTED'], ['unknown', '']]) {
  test(`Terminal outcome ${status || 'unverified'} refreshes without a floating message`, async () => {
    const p = setup('poll');
    p.response = {state, status, attention: '', active: state === 'unknown'};
    await p.submit('check');
    assert.equal(p.refreshes, 1);
    assert.equal(p.message.textContent, '');
    assert.equal(p.message.hidden, true);
  });
}

test('amount selects on entry, but a later click can position the cursor', () => {
  const p = setup();
  let selected = 0;
  const amount = {matches: selector => selector.includes('input[name="amount"]'), select() { selected++; }};
  const other = {matches: () => false};
  p.listeners.pointerdown({target: amount});
  p.document.activeElement = amount;
  p.listeners.focusin({target: amount});
  p.listeners.click({target: amount});
  assert.equal(selected, 2);
  p.listeners.pointerdown({target: amount}); p.listeners.click({target: amount});
  assert.equal(selected, 2);
  p.listeners.pointerdown({target: other}); p.listeners.focusin({target: other}); p.listeners.click({target: other});
  assert.equal(selected, 2);
  p.listeners.focusin({target: amount});
  assert.equal(selected, 3);
});

test('hidden tab, open dialog and paused recovery do not poll', async () => {
  const p = setup('poll');
  p.document.hidden = true; await p.poll();
  p.document.hidden = false; p.dialogOpen = true; await p.poll();
  p.dialogOpen = false; p.form.dataset.paused = 'true'; await p.poll();
  assert.equal(p.calls.length, 0);
  p.form.dataset.paused = 'false'; p.listeners.visibilitychange(); await p.poll();
  assert.equal(p.calls.length, 1);
  p.listeners['booking:content-updated']();
});

test('network failures stop after three tries and keep manual recovery available', async () => {
  const p = setup('poll'); p.networkError = true;
  await p.poll(); await p.poll(); await p.poll(); await p.poll();
  assert.equal(p.calls.length, 3);
  assert.match(p.message.textContent, /Automatic status check unavailable/);
  p.networkError = false; await p.submit('check');
  assert.equal(p.calls.length, 4);
  assert.equal(p.message.hidden, true);
});

test('validation and refresh failures remain visible and release buttons', async () => {
  const p = setup(); p.error = 'Add a description';
  await p.submit(); assert.equal(p.message.textContent, 'Add a description');
  p.error = true; await p.submit(); assert.match(p.message.textContent, /status is unavailable/);
  p.error = false; p.refreshOK = false;
  await p.submit(null); assert.match(p.message.textContent, /Could not refresh/);
  assert.equal(p.closed, undefined);
  assert.equal(p.message.hidden, false);
  assert.equal(p.button.disabled, false);
});

test('a missing form slot cannot create a stray message elsewhere', async () => {
  const p = setup(); p.message = null; p.error = 'Invalid amount';
  await p.submit();
  assert.equal(p.message, null);
  assert.equal(p.button.disabled, false);
});

test('physical connection check polls without refreshing reservation content', async () => {
  const p = setup('health'); p.response = {active: true, message: 'Checking connection'};
  await p.submit('ping'); assert.equal(p.refreshes, 0);
  p.response = {active: false, message: 'Terminal responded'};
  await p.poll(); assert.equal(p.form.dataset.active, 'false');
  assert.equal(p.message.textContent, 'Terminal responded');
  await p.poll(); assert.equal(p.calls.length, 2);
  assert.equal(p.calls[1].options.body.values.automatic, 'yes');
});

test('unrelated pages do not install handlers', () => {
  assert.equal(Object.keys(setup('absent').listeners).length, 0);
});
