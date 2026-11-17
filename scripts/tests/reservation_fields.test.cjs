const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/reservation-fields.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function page(options) {
  const node = tag => ({tag,children:[],listeners:{},append(...children){this.children.push(...children);},
    replaceChildren(){this.children=[];},addEventListener(name,fn){this.listeners[name]=fn;}});
  const select = {value:'current'}, container = node('div'), label = {}, weekButtons = [node('button'),node('button')];
  weekButtons[0].dataset={week:'-1'};weekButtons[1].dataset={week:'1'};
  const summary = {};
  const picker = {dataset:{today:'2026-11-17'},
    querySelector:s=>s==='script'?{textContent:JSON.stringify(options)}:s.includes('selected-session')?summary:s.includes('week-label')?label:container,
    querySelectorAll:()=>weekButtons};
  const requests = [], timers = [], events = {}, quantityEvents = {};
  const quantity = {value:'2', dispatchEvent(){}, addEventListener:(name, fn) => {quantityEvents[name] = fn;}};
  const balance = {}, submit = {}, unitPrice = {};
  const breakdown = {children:[], replaceChildren(){this.children=[];}, append(row){this.children.push(row);}};
  const form = {dataset:{}, isConnected:true, action:'/reservation/',
    querySelector(selector) {return selector.includes('quantity') ? quantity : selector.includes('data-balance') ? balance : selector.includes('data-price-breakdown') ? breakdown : selector.includes('data-unit-price') ? unitPrice : selector.includes('submit') ? submit : selector.includes('reservation-picker') ? (options ? picker : null) : selector.includes('select[name') ? (options ? select : null) : selector.includes('data-session-select') ? {} : null;},
    addEventListener:(name, fn) => {events[name] = fn;}};
  const window = {};
  vm.runInNewContext(source, {window, URL, Intl, Event, location:{href:'http://localhost/'},
    document:{querySelectorAll:() => [form],createElement:node},
    setTimeout:fn => {timers.push(fn);return timers.length;}, clearTimeout() {},
    fetch:(url, options) => new Promise((resolve, reject) => requests.push({url,options,resolve,reject}))});
  return {requests,timers,events,quantityEvents,quantity,balance,submit,form,window,breakdown,unitPrice,container,select,weekButtons,summary};
}

test('seat limit follows selected session even while browsing another week', () => {
  const p = page([
    {id:'current',open:2,limit:5,date:'2026-11-17',label:'Tue, Nov 17 · 11:15 AM',name:'Workshop',duration:110},
    {id:'next',open:8,limit:8,date:'2026-11-23',label:'Mon, Nov 23 · 12:00 PM',name:'Workshop',duration:120},
  ]);
  assert.equal(p.summary.hidden,true);
  p.weekButtons[1].listeners.click();
  assert.equal(p.summary.hidden,false);
  assert.equal(p.summary.textContent,'Selected session: Tue, Nov 17 · 11:15 AM');
  assert.equal(p.select.value,'current');
  assert.equal(p.quantity.max,'5');
  p.quantity.value='6';p.quantityEvents.input();
  assert.equal(p.submit.disabled,true);
  assert.match(p.summary.textContent,/Fits up to 5 seats/);
  p.container.children[0].children[0].listeners.change();
  assert.equal(p.select.value,'next');
  assert.equal(p.quantity.max,'8');
  assert.equal(p.summary.hidden,true);
  p.weekButtons[1].listeners.click();
  assert.equal(p.summary.hidden,false);
  assert.match(p.summary.textContent,/Mon, Nov 23/);
  assert.equal(p.container.children[0].textContent,'No sessions this week.');
  p.weekButtons[0].listeners.click();
  assert.equal(p.summary.hidden,true);
});

test('session options must fit the whole group and update with seats and week navigation', () => {
  const rows = [0,2,3,5].map((open,i)=>({id:i===3?'current':String(i),open,date:'2026-11-17',label:'Tuesday',name:'Workshop',duration:60}));
  const p = page(rows);
  const radios = () => p.container.children.map(row=>row.children[0]);
  p.quantity.value='3';p.quantityEvents.input();
  assert.deepEqual(radios().map(r=>r.disabled),[true,true,false,false]);
  radios()[1].listeners.change(); assert.equal(p.select.value,'current');
  radios()[2].listeners.change(); assert.equal(p.select.value,'2');
  p.quantity.value='2';p.quantityEvents.input();
  assert.deepEqual(radios().map(r=>r.disabled),[true,false,false,false]);
  p.quantity.value='4';p.quantityEvents.input();
  assert.equal(p.submit.disabled,true);
  p.weekButtons[1].listeners.click();p.weekButtons[0].listeners.click();
  assert.deepEqual(radios().map(r=>r.disabled),[true,true,true,false]);
});

test('current booking seats count toward the edit limit but not the open label', () => {
  const p = page([{id:'current',open:2,limit:5,date:'2026-11-17',label:'Tuesday',name:'Workshop',duration:60}]);
  assert.equal(p.quantity.max,'5');
  assert.equal(p.container.children[0].children[2].textContent,'2 open');
  p.quantity.value='5';p.quantityEvents.input();
  assert.equal(p.container.children[0].children[0].disabled,false);
  p.quantity.value='6';p.quantityEvents.input();
  assert.equal(p.container.children[0].children[0].disabled,true);
  assert.equal(p.submit.disabled,true);
});

test('balance uses the server quote and blocks saving while it is unresolved', async () => {
  const p = page(); assert.equal(p.submit.disabled,true);
  let prevented = false; p.events.submit({preventDefault(){prevented=true;}}); assert.equal(prevented,true);
  p.timers.pop()(); assert.equal(p.requests[0].url.searchParams.get('quote_quantity'),'2');
  assert.equal(p.requests[0].options.credentials,'same-origin');
  p.requests[0].resolve({ok:true,json:async()=>({balance:14430,currency:'usd',unit_price:6500,quantity:2,rows:[{label:'Subtotal',cents:13000},{label:'Tax',cents:1430}]})}); await tick();
  assert.equal(p.unitPrice.textContent, '2 × $65.00 per person + tax');
  assert.deepEqual(p.breakdown.children.map(row=>row.children.map(child=>child.textContent)), [['Subtotal','$130.00'],['Tax','$14.30']]);
  assert.match(p.balance.textContent,/144\.30/); assert.equal(p.submit.disabled,false);
  p.quantityEvents.change();
  assert.equal(p.submit.disabled,false, 'blur must not disable Save for the same seat count');
  assert.equal(p.timers.length,0, 'blur must not start a duplicate quote');
  p.window.ReservationFields.mount({querySelectorAll:() => [p.form]}); assert.equal(p.timers.length,0);
});

test('stale quotes and connection failures cannot enable Save', async () => {
  const p = page(); p.timers.pop()(); p.quantity.value='3';p.quantityEvents.input();p.timers.pop()();
  p.requests[0].resolve({ok:true,json:async()=>({balance:14430,currency:'usd'})}); await tick();
  assert.equal(p.submit.disabled,true);
  p.requests[1].reject(new Error('offline')); await tick();
  assert.equal(p.submit.disabled,true); assert.match(p.balance.textContent,/Unable to calculate/);
  p.quantityEvents.change();p.timers.pop()();
  p.requests[2].resolve({ok:false,json:async()=>({error:'Invalid quantity'})});await tick();
  assert.equal(p.balance.textContent,'Invalid quantity');assert.equal(p.submit.disabled,true);
});
