const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/session-fields.js','utf8');

test('seat controls respect bounds, emit input for pricing and mount once', () => {
  const nodes = [], handlers = {}, events = [];
  const seats = {value:'2', min:'1', max:'3', disabled:false,
    before(node){this.controls=node;},
    stepDown(){this.value=String(Math.max(Number(this.min),Number(this.value)-1));},
    stepUp(){this.value=String(Math.min(Number(this.max),Number(this.value)+1));},
    dispatchEvent(event){events.push(event);handlers.input();}};
  const form = {dataset:{},hasAttribute:()=>false,querySelector:s=>s.includes('capacity') ? seats : null,
    addEventListener:(name, fn)=>{handlers[name]=fn;}};
  const document = {querySelectorAll:()=>[form],createElement(tag){
    const node={tag,dataset:{},children:[],attrs:{},handlers:{},
      append(...items){this.children.push(...items);},
      setAttribute(name,value){this.attrs[name]=value;},
      addEventListener(name,fn){this.handlers[name]=fn;}};
    nodes.push(node);return node;
  }};
  const window = {};
  vm.runInNewContext(source,{document,window,Event:class {constructor(type,options){this.type=type;this.bubbles=options.bubbles;}}});
  const [,minus,plus]=nodes;
  assert.equal(minus.attrs['aria-label'],'Decrease seats');
  plus.handlers.click();assert.equal(seats.value,'3');assert.equal(plus.disabled,true);
  plus.handlers.click();assert.equal(seats.value,'3');
  seats.max='4';handlers.seatlimit();assert.equal(plus.disabled,false);
  seats.max='3';handlers.seatlimit();assert.equal(plus.disabled,true);
  minus.handlers.click();minus.handlers.click();assert.equal(seats.value,'1');assert.equal(minus.disabled,true);
  assert(events.every(e=>e.type==='input' && e.bubbles));
  seats.disabled=true;handlers.change();assert.equal(plus.disabled,true);
  seats.disabled=false;seats.value='';plus.handlers.click();assert.equal(seats.value,'1');
  window.SessionFields.mount(document);assert.equal(nodes.length,3);
});

test('session picker remains intact; repeat fields and notification choice stay synchronized', () => {
  function node() {return {dataset:{},children:[],handlers:{},append(...items){this.children.push(...items);},
    addEventListener(name,fn){this.handlers[name]=fn;},querySelector(selector){
      return this.children.flatMap(item=>item.children).find(item=>selector.includes(item.dataset.startPart));
    }};}
  const parent={querySelector:()=>({remove(){}}),prepend(pair){this.pair=pair;}};
  const start={type:'datetime-local',value:'2026-11-17T10:15',required:true,closest:()=>parent};
  let pickerDestroyed = false;
  start._flatpickr = {destroy(){pickerDestroyed = true;}};
  const notifications = [];
  const mode={...node(),value:'once'}, notify={...node(),checked:true}, hidden={value:'1',dispatchEvent(event){notifications.push(event);}};
  const interval={parent:{},closest(){return this.parent;}}, through={parent:{},closest(){return this.parent;}};
  const fields={'input[name="starts_at"]':start,'[name="mode"]':mode,
    '[name="interval"]':interval,'[name="last_date"]':through,
    '[data-notify-customers]':notify,'[name="notify_customers"]':hidden};
  const form={dataset:{},querySelector:selector=>fields[selector] || null};
  const document={querySelectorAll:()=>[form],createElement:()=>node()};
  vm.runInNewContext(source,{document,window:{},Event});
  assert.equal(pickerDestroyed,false);
  assert.equal(start.type,'datetime-local');assert.equal(start.required,true);
  assert.equal(parent.pair,undefined);
  assert.equal(interval.parent.hidden,true);assert.equal(through.required,false);
  mode.value='repeat';mode.handlers.change();assert.equal(interval.parent.hidden,false);assert.equal(through.required,true);
  notify.checked=false;notify.handlers.change();assert.equal(hidden.value,'0');
  notify.checked=true;notify.handlers.change();assert.equal(hidden.value,'1');
  assert.equal(notifications.length,2);
  assert(notifications.every(event=>event.type==='input' && event.bubbles));
});

for (const attribute of ['data-session-preflight', 'data-new-session']) test(`${attribute} capacity retains its compact number input without a custom stepper`, () => {
  let wrapped = false;
  const capacity = {before(){wrapped = true;}};
  const form = {dataset:{},hasAttribute:name=>name===attribute,
    querySelector:selector=>selector.includes('capacity') ? capacity : null};
  vm.runInNewContext(source,{window:{},document:{querySelectorAll:()=>[form]}});
  assert.equal(wrapped,false);
});
