const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/session-preflight.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function page(live = false) {
  const message = {}, submit = {}, listeners = {}, timers = [], requests = [];
  const notification = {};
  const review = {innerHTML:'', querySelector(selector){return selector.includes('highlight') ? this.innerHTML.includes('changed') : this.innerHTML.includes('customers');}, replaceChildren(){this.innerHTML='';}}, token = {value:''}, customers = {};
  let value = 0;
  const form = {dataset:{}, isConnected:true, matches:() => true,
    querySelector:s => s === '.session-notify' ? notification : s.includes('conflict') ? message : s.includes('live-review') ? (live ? review : null) : s.includes('review_token') ? token : s === '.session-customers' ? customers : submit,
    addEventListener:(name, fn) => {listeners[name] = fn;}};
  const window = {};
  vm.runInNewContext(source, {window, document:{querySelectorAll:() => []},
    setTimeout:fn => {timers.push(fn);return timers.length;}, clearTimeout() {},
    FormData:class {constructor(form){this.form=form;} entries(){return [['duration', value], ['review_token',token.value]];}},
    fetch:(url, options) => new Promise((resolve,reject) => requests.push({url,options,resolve,reject}))});
  window.SessionPreflight.mount(form, '/sessions/1/edit/');
  return {window,form,message,submit,review,token,customers,notification,listeners:{...listeners,input(){value++;listeners.input();},change(){value++;listeners.change();}},rawListeners:listeners,timers,requests};
}

test('notification requires both changed values and affected customers', async () => {
  const p = page(true);
  assert.equal(p.notification.hidden,true);
  for (const [html,hidden] of [['changed',true],['customers',true],['changed customers',false],['customers',true]]) {
    p.listeners.input(); assert.equal(p.notification.hidden,true); p.timers.pop()();
    p.requests.at(-1).resolve({ok:true,json:async()=>({valid:true,review_token:'signed',review_html:html})});
    await tick(); assert.equal(p.notification.hidden,hidden);
  }
});

test('live review updates values and authorization without a Save click; edits invalidate it', async () => {
  const p = page(true); p.listeners.input(); p.timers.pop()();
  p.requests[0].resolve({ok:true,json:async()=>({valid:true,review_token:'signed',review_html:'2 bookings affected'})}); await tick();
  assert.equal(p.review.innerHTML, '2 bookings affected');
  assert.equal(p.token.value, 'signed'); assert.equal(p.submit.disabled, false);
  assert.equal(p.customers.hidden, true);
  p.rawListeners.change(); assert.equal(p.timers.length, 0);
  p.listeners.input(); assert.equal(p.token.value, '');
  assert.equal(p.review.innerHTML, ''); assert.equal(p.submit.disabled, true);
  p.timers.pop()();
  p.requests[1].resolve({ok:true,json:async()=>({valid:true})}); await tick();
  assert.equal(p.submit.disabled, true);
});

test('blur with unchanged values does not disable Save or send another check', async () => {
  const p = page(); p.listeners.input(); p.timers.pop()();
  p.requests[0].resolve({ok:true,json:async()=>({valid:true})}); await tick();
  p.rawListeners.change();
  assert.equal(p.submit.disabled, false);
  assert.equal(p.timers.length, 0);
  assert.equal(p.requests.length, 1);
});

test('conflicting previews block save; resolving the conflict enables it without writing', async () => {
  const p = page(); p.listeners.input();
  assert.equal(p.submit.disabled,true);
  let prevented=false; p.listeners.submit({preventDefault(){prevented=true;}}); assert.equal(prevented,true);
  p.timers.pop()();
  assert.equal(p.requests[0].options.headers['X-Session-Preflight'],'1');
  assert.equal(p.requests[0].options.body.form,p.form);
  p.requests[0].resolve({ok:false,json:async()=>({errors:{__all__:[{message:'Room is occupied.'}]}})}); await tick();
  assert.equal(p.message.textContent,'Room is occupied.'); assert.equal(p.submit.disabled,true);
  p.listeners.change();p.timers.pop()();
  p.requests[1].resolve({ok:true,json:async()=>({valid:true})});await tick();
  assert.equal(p.submit.disabled,false);assert.equal(p.message.hidden,true);
  prevented=false;p.listeners.submit({preventDefault(){prevented=true;}});assert.equal(prevented,false);
  p.window.SessionPreflight.mount(p.form,'/other/');assert.equal(p.requests.length,2);
});

test('stale and failed availability responses never enable an unsafe save',async()=>{
  const p=page();p.listeners.input();p.timers.pop()();p.listeners.input();p.timers.pop()();
  p.requests[0].resolve({ok:true,json:async()=>({valid:true})});await tick();assert.equal(p.submit.disabled,true);
  p.requests[1].reject(new Error('offline'));await tick();assert.match(p.message.textContent,/Could not check/);
  p.listeners.input();p.timers.pop()();p.requests[2].resolve({ok:false,json:async()=>({})});await tick();assert.equal(p.submit.disabled,true);
  p.listeners.input();p.timers.pop()();p.form.isConnected=false;
  p.requests[3].resolve({ok:true,json:async()=>({valid:true})});await tick();assert.equal(p.submit.disabled,true);
});
