const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/email-preview.js', 'utf8');

test('color swatch updates the saved color field and triggers preview', () => {
  let click, event;
  const input = {value:'#2563eb', dispatchEvent:e => {event=e;}};
  const button = {dataset:{emailColor:'#ff8a92'}, addEventListener:(_,fn) => {click=fn;},
    setAttribute:() => {}, closest:() => ({querySelector:() => input, querySelectorAll:() => [button]})};
  vm.runInNewContext(source, {document:{querySelectorAll:s => s === '[data-email-color]' ? [button] : []}, Event});
  click();
  assert.equal(input.value, '#ff8a92');
  assert.equal(event.type, 'input');
  assert.equal(event.bubbles, true);
});

function page() {
  const listeners = {}, timers = [], calls = [], pending = [];
  const form = {action: {tagName: 'BUTTON'}, dataset: {previewAction: 'preview'},
    getAttribute: () => null, addEventListener: (key, fn) => { listeners[key] = fn; }, querySelector: () => null};
  const frame = {srcdoc: 'original'}, status = {}, subject = {};
  const root = {querySelector: selector => ({form, '[data-preview-frame]': frame,
    '[data-preview-status]': status, '[data-preview-subject]': subject}[selector])};
  vm.runInNewContext(source, {document: {querySelectorAll: selector => selector === '[data-email-preview]' ? [root] : []}, location: {href: '/email/templates/1/'},
    FormData: class {set(key, value) { this[key] = value; }}, AbortController,
    setTimeout: fn => {timers.push(fn); return timers.length;}, clearTimeout: () => {},
    fetch: (url, options) => { calls.push({url, options}); return new Promise(resolve => pending.push(resolve)); }});
  return {listeners, timers, calls, pending, frame, status, subject};
}

test('preview posts to the URL, not the action button; does not save', async () => {
  const p = page(); p.listeners.input(); const done = p.timers.pop()();
  assert.equal(p.calls[0].url, '/email/templates/1/');
  assert.equal(p.calls[0].options.body.action, 'preview');
  p.pending.shift()({ok:true,json:async () => ({html:'<p>New</p>',subject:'New subject'})}); await done;
  assert.equal(p.frame.srcdoc, '<p>New</p>'); assert.equal(p.subject.textContent, 'New subject');
  assert.equal(p.status.textContent, '');
});

test('stale responses never overwrite newer edits and invalid input keeps the last preview', async () => {
  const p = page(); p.listeners.input(); const first = p.timers.pop()();
  p.listeners.change(); const second = p.timers.pop()();
  assert.equal(p.calls[0].options.signal.aborted, true);
  p.pending[1]({ok:true,json:async () => ({html:'new',subject:'New'})}); await second;
  p.pending[0]({ok:true,json:async () => ({html:'stale',subject:'Old'})}); await first;
  assert.equal(p.frame.srcdoc, 'new');
  p.listeners.input(); const invalid = p.timers.pop()();
  p.pending[2]({ok:false,json:async () => ({errors:{subject:['Enter a subject.']}})}); await invalid;
  assert.equal(p.frame.srcdoc, 'new'); assert.equal(p.status.textContent, 'Subject: Enter a subject.');
});

for (const succeeds of [true, false]) test(`branding navigation waits for save (${succeeds})`, async () => {
  const handlers = {}, status = {}, calls = [];
  let finish, navigate, prevented = false;
  const toggle = {checked:false, addEventListener:(_,fn) => {handlers.change=fn;}, dispatchEvent:()=>{}};
  const form = {querySelector:s => s.includes('branded_booking_email') ? toggle : status,
    getAttribute:()=>null, addEventListener:()=>{}};
  const link = {href:'/templates', addEventListener:(_,fn)=>{handlers.click=fn;}};
  vm.runInNewContext(source, {document:{querySelectorAll:s => s==='[data-brand-mode-form]' ? [form] : s==='.email-tabs a, .email-template-list a' ? [link] : []},
    FormData:class {set(k,v){this[k]=v;}}, Event,
    location:{href:'/branding',assign:url=>{navigate=url;}},
    fetch:(_,options)=>{calls.push(options);return new Promise(resolve=>{finish=resolve;});}});
  handlers.change();
  const click = handlers.click({preventDefault:()=>{prevented=true;}});
  assert.equal(prevented,true); assert.equal(navigate,undefined);
  assert.equal(calls[0].body.action,'save_brand_mode');
  assert.equal(calls[0].body.branded_booking_email,'false');
  finish({ok:succeeds,json:async()=>({branded:false})}); await click;
  assert.equal(navigate,succeeds ? '/templates' : undefined);
  assert.equal(toggle.disabled,false);
  assert.equal(toggle.checked,!succeeds);
  if (succeeds) assert.equal(status.textContent, '');
  else assert.match(status.textContent, /Could not confirm/);
});
