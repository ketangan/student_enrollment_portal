const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../../static/scheduling/integrations/preview.js'), 'utf8');

function setup(mode='inline', broken=false, file=false, icons=false) {
  function element() {
    const events = {};
    const attrs = {};
    const classes = new Set();
    return {events, attrs, dataset:{}, hidden:false,
      classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c)},
      addEventListener:(type,fn)=>(events[type]??=[]).push(fn),
      emit(type,event={}){for(const fn of events[type]??[]) fn(event);},
      setAttribute:(key,value)=>{attrs[key]=value;}, removeAttribute:key=>{delete attrs[key];},
      focus(){this.focused=true;}};
  }
  const frame=element(), host=element(), detail=element(), title=element(), actions=element(), retry=element();
  const doc=element(), body=element(), opener=element(), close=element(), link=element();
  const dialog=mode==='modal'?element():null;
  if(dialog){dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;dialog.emit('close');};}
  doc.querySelector=()=>broken?null:{};
  frame.contentDocument=doc;
  frame.dataset.url='/bookings/plushumi-demo/experience/';
  frame.closest=()=>host;
  host.querySelector=selector=>({'.loading-detail':detail,'.loading-panel strong':title,'.load-actions':actions,'.retry-button':retry})[selector];
  const selectors={'[data-booking-frame]':frame,'#booking-dialog':dialog,'#open-booking':opener,'#close-booking':close};
  let timer;
  vm.runInNewContext(script, {URL,document:{body,querySelector:s=>selectors[s],querySelectorAll:()=>[link]},
    location:{protocol:file?'file:':'http:',origin:'http://localhost:8002'},
    window:icons?{lucide:{createIcons(){}}}:{},
    setTimeout:fn=>{timer=fn;return 1;},clearTimeout:()=>{timer=null;}},
    {filename:path.join(__dirname, '../../static/scheduling/integrations/preview.js')});
  return {frame,host,detail,title,actions,retry,doc,body,opener,close,dialog,link,timeout:()=>timer?.()};
}
test('inline loads real URL immediately, with honest loading state',()=>{
  const ui=setup();assert.equal(ui.frame.src,'http://localhost:8002/bookings/plushumi-demo/experience/');
  assert.equal(ui.host.dataset.state,'loading');assert.equal(ui.host.attrs['aria-busy'],'true');
  assert.equal(ui.actions.hidden,true);
});
test('modal does not preload the booking request',()=>{
  const ui=setup('modal');assert.equal(ui.frame.src,undefined);
  ui.frame.emit('load');assert.equal(ui.frame.src,undefined);
  ui.opener.emit('click');assert.equal(ui.host.dataset.state,'loading');assert.equal(ui.dialog.open,true);
});
test('file preview and missing content document retain honest fallback behavior',()=>{
  const ui=setup('inline',false,true,true);
  assert.equal(ui.frame.src,'http://localhost:8002/bookings/plushumi-demo/experience/');
  ui.frame.contentDocument=null;ui.frame.emit('load');assert.equal(ui.host.dataset.state,'error');
});
test('load verifies the real app document before dismissing loader',()=>{
  const ui=setup();ui.frame.emit('load');assert.equal(ui.host.dataset.state,'loaded');assert.equal(ui.host.attrs['aria-busy'],undefined);
});
test('initial about:blank load cannot be mistaken for failure or readiness',()=>{
  const ui=setup();ui.doc.URL='about:blank';ui.frame.emit('load');assert.equal(ui.host.dataset.state,'loading');
  ui.timeout();assert.equal(ui.host.dataset.state,'slow');
});
test('failed or blocked frame reports failure without fabricated booking fields',()=>{
  const ui=setup('inline',true);ui.frame.emit('load');assert.equal(ui.host.dataset.state,'error');assert.equal(ui.actions.hidden,false);
});
test('slow load exposes recovery controls, late success still works',()=>{
  const ui=setup();ui.timeout();assert.equal(ui.host.dataset.state,'slow');assert.equal(ui.actions.hidden,false);
  ui.frame.emit('load');assert.equal(ui.host.dataset.state,'loaded');
});
test('retry keeps the same actual destination',()=>{
  const ui=setup();ui.timeout();ui.retry.emit('click');assert.equal(ui.host.dataset.state,'loading');
  assert.equal(ui.frame.src,ui.link.href);assert.equal(ui.actions.hidden,true);
});
test('modal close unlocks scroll, restores focus, and reopening retains frame',()=>{
  const ui=setup('modal');ui.opener.emit('click');ui.frame.emit('load');
  ui.frame.src='http://localhost:8002/bookings/plushumi-demo/experience/session/10/';
  ui.close.emit('click');assert.equal(ui.opener.focused,true);assert.equal(ui.body.classList.contains('modal-open'),false);
  ui.opener.emit('click');assert.match(ui.frame.src,/session\/10\/$/);assert.equal(ui.host.dataset.state,'loaded');
});
test('Escape from embedded content closes modal unless already handled',()=>{
  const ui=setup('modal');ui.opener.emit('click');ui.frame.emit('load');
  ui.doc.emit('keydown',{key:'Escape',defaultPrevented:true});assert.equal(ui.dialog.open,true);
  let prevented=false;ui.doc.emit('keydown',{key:'Escape',defaultPrevented:false,preventDefault:()=>{prevented=true;}});
  assert.equal(ui.dialog.open,false);assert.equal(prevented,true);
});
test('popup contains only a real iframe, never a re-created booking form',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../../templates/scheduling/experience/integrations/modal.html'),'utf8');
  assert.equal((html.match(/<iframe\b/g)||[]).length,1);
  assert.doesNotMatch(html,/<form\b|srcdoc=|id="studio"|id="dates"/);
  assert.match(html,/data-url="\/bookings\/plushumi-demo\/experience\/"/);
});
