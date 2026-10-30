const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/email-drawer.js', 'utf8');

function page({absent=false, error=false}={}) {
  const listeners = {}, classes = new Set();
  const template = {focus() { this.focused = true; }};
  const opener = {isConnected:true, focus() { this.focused = true; }};
  const close = {offsetParent:{}, focus() { this.focused=true; }, addEventListener(name, fn) { this[name] = fn; }};
  const send = {offsetParent:{}, focus() { this.focused=true; }};
  const dialog = {open:false, shows:0, events:{},
    showModal() { this.open=true; this.shows++; },
    close() { this.open=false; this.events.close(); },
    addEventListener(name, fn) { this.events[name]=fn; },
    querySelector() { return template; }, querySelectorAll(selector) { return selector === '[data-email-close]' ? [close] : [close, {disabled:true}, {offsetParent:null}, send]; },
    hasAttribute() { return error; }};
  const document = {body:{classList:{add: value=>classes.add(value), remove:value=>classes.delete(value)}},
    getElementById:()=>absent?null:dialog, querySelector:()=>opener,
    addEventListener(name, fn) { listeners[name]=fn; }};
  vm.runInNewContext(source,{document});
  return {dialog, template, opener, close, send, classes, listeners, document};
}

test('drawer is closed until requested and closes without clearing form state', () => {
  page({absent:true});
  const p=page();
  assert.equal(p.dialog.open,false);
  p.listeners.click({target:{closest:()=>null}});
  p.listeners.click({target:{closest:()=>p.opener}});
  p.listeners.click({target:{closest:()=>p.opener}});
  assert.equal(p.dialog.shows,1);
  assert.ok(p.template.focused && p.classes.has('email-drawer-open'));
  p.close.click();
  assert.equal(p.dialog.open,false);
  assert.ok(p.opener.focused);
  assert.equal(p.classes.size,0);
});

test('validation errors reopen; focus restores after the reservation heading is replaced', () => {
  const p=page({error:true});
  assert.equal(p.dialog.open,true);
  p.opener.isConnected=false;
  const replacement={focus() { this.focused=true; }};
  p.document.querySelector=()=>replacement;
  p.dialog.close();
  assert.ok(replacement.focused);
  p.document.querySelector=()=>null;
  p.dialog.events.close();
});

test('Tab cycles inside the drawer in both directions', () => {
  const p=page();
  let prevented=0;
  const event={key:'Tab',preventDefault() { prevented++; }};
  p.dialog.events.keydown({...event,key:'Escape'});
  p.document.activeElement=p.send;
  p.dialog.events.keydown(event);
  assert.ok(p.close.focused);
  p.document.activeElement=p.close;
  p.dialog.events.keydown({...event,shiftKey:true});
  assert.ok(p.send.focused);
  p.document.activeElement=p.template;
  p.dialog.events.keydown(event);
  assert.equal(prevented,2);
});
