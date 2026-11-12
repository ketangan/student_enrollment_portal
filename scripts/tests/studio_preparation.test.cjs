const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/studio-preparation.js', 'utf8');

function page({error, refreshError, missing, hidden, noButton, noRow, network} = {}) {
  const handlers = {}, calls = [], urls = [];
  const notice = {hidden: true, scrollIntoView() {this.scrolled = true;}};
  const button = {disabled: false, focus() {this.focused = true;}};
  const row = {id: 'prep-1-2', getBoundingClientRect: () => ({top: 80}),
    getClientRects: () => hidden ? [] : [1], querySelector: () => noButton ? null : button};
  const replacement = {querySelector() {}};
  const main = {classList: {toggle(name, value) {main.hidden = value;}}, replaceWith(value) {main.replacement = value;}};
  const inputs = [{checked: false}, {checked: false}];
  const printLink = {href: 'http://localhost/prep/?date=2026-10-26&period=week&location=2&print=1'};
  const document = {addEventListener(name, fn) {handlers[name] = fn;},
    querySelector: () => main, querySelectorAll: selector => selector === '.prep-print' ? [printLink] : inputs,
    getElementById: id => id === 'prep-error' ? notice : noRow ? null : row};
  const form = {matches: () => true, getAttribute: () => '/prep/save/', action: {value: 'ready'},
    closest: selector => selector === '.prep-person' ? null : row, querySelector: () => button};
  class DOMParser { parseFromString() {return {querySelector: () => missing ? null : replacement};} }
  class FormData { constructor(form) {this.form = form;} }
  const window = {scrollBy(x, y) {this.offset = y;}, lucide: {createIcons() {}}};
  vm.runInNewContext(source, {document, window, URL, FormData, DOMParser,
    location: {href: 'http://localhost/prep/?date=2026-10-26&edit=1:2&staff=booking&note=booking:new'},
    history: {replaceState(a, b, url) {urls.push(url);}},
    fetch: async (url, options) => {
      calls.push({url, options});
      if (network) throw Error('Network unavailable');
      return options ? {ok: !error, json: async () => ({error: error === 'blank' ? '' : error})}
        : {ok: !refreshError, text: async () => '<section />'};
    }});
  return {handlers, calls, urls, notice, button, row, form, main, inputs, window, printLink};
}

test('hide prepared synchronizes switches and the URL without navigation', () => {
  const p = page();
  p.handlers.change({target: {matches: () => false}});
  for (const checked of [true, false]) {
    p.handlers.change({target: {matches: () => true, checked}});
    assert.equal(p.main.hidden, checked);
    assert.ok(p.inputs.every(input => input.checked === checked));
    assert.equal(p.urls.at(-1).searchParams.has('hide_prepared'), checked);
    const print = new URL(p.printLink.href);
    assert.equal(print.searchParams.has('hide_prepared'), checked);
    assert.equal(print.searchParams.get('location'), '2');
    assert.equal(print.searchParams.get('date'), '2026-10-26');
    assert.equal(print.searchParams.get('period'), 'week');
    assert.equal(print.searchParams.get('print'), '1');
  }
});

test('prep saves use the HTML action URL, ignore duplicate clicks and preserve position', async () => {
  const p = page();
  await p.handlers.submit({target: {matches: () => false}});
  const event = {target: p.form, submitter: p.button, preventDefault() {}};
  const pending = p.handlers.submit(event);
  await p.handlers.submit(event);
  await pending;
  assert.equal(p.calls.length, 2);
  assert.equal(p.calls[0].url, '/prep/save/');
  assert.equal(p.calls[0].options.body.form, p.form);
  assert.equal(p.urls.at(-1).searchParams.has('edit'), false);
  assert.equal(p.urls.at(-1).searchParams.has('staff'), false);
  assert.equal(p.urls.at(-1).searchParams.has('note'), false);
  assert.equal(p.window.offset, 0);
  assert.equal(p.button.focused, true);
  assert.equal(p.button.disabled, false);
});

for (const options of [{error: 'Stock changed'}, {error: 'blank'}, {refreshError: true}, {missing: true}, {network: true}]) {
  test(`errors remain visible and save becomes usable again: ${JSON.stringify(options)}`, async () => {
    const p = page(options);
    await p.handlers.submit({target: p.form, preventDefault() {}});
    assert.equal(p.notice.hidden, false);
    assert.ok(p.notice.textContent);
    assert.equal(p.button.disabled, false);
    assert.equal(p.main.replacement, undefined);
  });
}

for (const options of [{hidden: true}, {noRow: true}, {noButton: true}]) {
  test(`refresh tolerates filtered or absent row controls: ${JSON.stringify(options)}`, async () => {
    const p = page(options);
    await p.handlers.submit({target: p.form, submitter: p.button, preventDefault() {}});
    assert.equal(p.notice.hidden, true);
    assert.ok(p.main.replacement);
  });
}
