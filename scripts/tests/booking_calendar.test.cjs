const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const filename = require('node:path').resolve('static/scheduling/calendar.js');
const source = fs.readFileSync(filename, 'utf8');

function element(tag = 'div') {
  return {tag, children: [], listeners: {}, attrs: {}, dataset: {}, textContent: '', clientWidth: 1200,
    addEventListener(name, fn) {this.listeners[name] = fn;},
    append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this.children = children;},
    setAttribute(name, value) {this.attrs[name] = value;},
    querySelector() {return this.children[0] || null;},
    showModal() {this.open = true;}, close() {this.open = false; this.listeners.close?.();},
  };
}

function page({focused=false, form=false, search='?view=calendar'} = {}) {
  const ids = ['booking-calendar', 'calendar-config', 'calendar-error', 'calendar-count',
    'calendar-hide-empty', 'calendar-detail', 'calendar-detail-body', 'calendar-detail-title',
    'calendar-detail-close', 'calendar-range', 'calendar-offerings', 'calendar-retry',
    'calendar-unpaid', 'calendar-balances', 'calendar-empty'];
  const nodes = Object.fromEntries(ids.map(id => [id, element()]));
  nodes['calendar-error'].append(element('span'));
  if (form) {
    nodes['dashboard-location-filter']=element('form');
    nodes['dashboard-location-filter'].querySelector=selector=>nodes['dashboard-location-filter'].children.find(child=>selector===`input[name="${child.name}"]`) || null;
  }
  nodes['calendar-config'].textContent = JSON.stringify({location:focused?'1':'',today:'2026-10-20',
    eventsUrl:'/calendar/events/',resources:[{id:'2',title:'East',zone:'America/New_York'}],offerings:[{id:'1',title:'<script>not markup</script>'},{id:'2',title:'Workshop'}]});
  const ranges = ['day','week','month'].map(value => Object.assign(element('button'),{dataset:{calendarRange:value}}));
  const actions = ['prev','next','today'].map(value => Object.assign(element('button'),{dataset:{calendarAction:value}}));
  const filters = ['all','none'].map(value => Object.assign(element('button'),{dataset:{calendarFilter:value}}));
  const requests = [];
  let options, refetches=0;
  const location = {href:`http://localhost/bookings/test/${search}`,origin:'http://localhost',search,assign(url){this.href=String(url);}};
  const calendar = {setOption(key,value) {options[key]=value;},getOption(key) {return key==='date'?new Date(options.date):options[key];},
    refetchEvents() {refetches++;},next() {this.nextCalled=true;},prev() {this.prevCalled=true;}};
  const document = {getElementById:id=>nodes[id] || null,createElement:element,
    querySelectorAll(selector) {
      if(selector==='[data-calendar-range]')return ranges;
      if(selector==='[data-calendar-action]')return actions;
      if(selector==='[data-calendar-filter]')return filters;
      if(selector==='#calendar-offerings input')return nodes['calendar-offerings'].children.map(label=>label.children[0]);
      return [];
    }};
  vm.runInNewContext(source,{document,location,URL,URLSearchParams,AbortController,Intl,
    history:{replaceState(_a,_b,url){location.href=String(url);}},queueMicrotask,
    fetch:(url,opts)=>new Promise((resolve,reject)=>requests.push({url,opts,resolve,reject})),
    EventCalendar:{create(_root,opts){options=opts;return calendar;}}}, {filename});
  return {nodes,ranges,actions,filters,requests,calendar,location,get options(){return options;},get refetches(){return refetches;}};
}

const interval = {startStr:'2026-10-18T00:00:00',endStr:'2026-10-25T00:00:00'};
const summary = {unpaid:1,balance:2,empty:3};
function resolve(request,events=[]) {request.resolve({ok:true,json:async()=>({events,summary})});}

test('controls change views and navigate through the calendar library',()=>{
  const p=page();
  assert.equal(p.options.view,'timeGridWeek');
  p.ranges[0].listeners.click();assert.equal(p.options.view,'resourceTimelineDay');
  p.ranges[2].listeners.click();assert.equal(p.options.view,'dayGridMonth');
  p.actions[0].listeners.click();assert.equal(p.calendar.prevCalled,true);
  p.actions[1].listeners.click();assert.equal(p.calendar.nextCalled,true);
  p.actions[2].listeners.click();assert.equal(p.options.date,'2026-10-20');
  assert.equal(p.options.editable,false);
  assert.equal(p.options.eventDurationEditable,false);
});

test('none filter does not accidentally fetch all workshops',async()=>{
  const p=page();p.filters[1].listeners.click();let events;
  await p.options.eventSources[0].events(interval,value=>{events=value;});
  assert.equal(events.length,0);assert.equal(p.requests.length,0);
  assert.equal(p.nodes['calendar-count'].textContent,'No workshops selected');
});

test('stale network responses cannot replace a newer date range',async()=>{
  const p=page(),received=[];
  const first=p.options.eventSources[0].events(interval,value=>received.push(value));
  const second=p.options.eventSources[0].events(interval,value=>received.push(value));
  resolve(p.requests[1]);await second;resolve(p.requests[0]);await first;
  assert.equal(received.length,1);assert.equal(p.requests[0].opts.signal.aborted,true);
  assert.equal(p.nodes['calendar-balances'].textContent,2);
});

test('failed loads clear stale events and expose retry',async()=>{
  const p=page();let events;
  const pending=p.options.eventSources[0].events(interval,value=>{events=value;});
  p.requests[0].resolve({ok:false,json:async()=>({error:'Unavailable'})});await pending;
  assert.equal(events.length,0);assert.equal(p.nodes['calendar-error'].hidden,false);
  const before=p.refetches;p.nodes['calendar-retry'].listeners.click();assert.ok(p.refetches>before);
});

test('crowded simultaneous sessions collapse into a day-view link',async()=>{
  const p=page();let events;
  const pending=p.options.eventSources[0].events(interval,value=>{events=value;});
  resolve(p.requests[0],[1,2,3].map(id=>({id:`session-${id}`,title:'Workshop',start:'2026-10-24T10:00:00',end:'2026-10-24T12:00:00',extendedProps:{kind:'session',seats:1,capacity:6}})));
  await pending;assert.equal(events.length,1);assert.equal(events[0].extendedProps.seats,3);
  p.options.eventClick({event:events[0]});assert.equal(p.options.view,'resourceTimelineDay');
});

test('untrusted event titles use text nodes, not HTML',()=>{
  const p=page();
  const event={title:'<img onerror=alert(1)>',extendedProps:{location:'<script>x</script>',seats:1,capacity:6,published:true}};
  const content=p.options.eventContent({event,timeText:'10 AM'});
  assert.equal(content.domNodes[0].textContent,'<script>x</script>');
  assert.equal(content.domNodes[0].innerHTML,undefined);
});

test('session details render verified totals and existing reservation links',async()=>{
  const p=page();
  const pending=p.options.eventClick({event:{title:'Session',extendedProps:{detailUrl:'/details/'}}});
  assert.equal(p.nodes['calendar-detail'].open,true);
  p.requests[0].resolve({ok:true,json:async()=>({title:'Workshop',time:'Saturday',location:'Studio',room:'Room',url:'/workshop/',truncated:true,
    reservations:[{name:'<img>',quantity:2,state:'Confirmed',received:2500,total:6500,currency:'usd',url:'/reservation/'}]})});
  await pending;
  assert.equal(p.nodes['calendar-detail-title'].textContent,'Workshop');
  const table=p.nodes['calendar-detail-body'].children[2].children[0];
  const row=table.children[1].children[0];
  assert.equal(row.children[0].textContent,'<img>');
  assert.equal(row.children[4].textContent,'$40.00');
  const reservationLink = new URL(row.children[5].children[0].href, 'http://localhost');
  assert.equal(reservationLink.pathname, '/reservation/');
  assert.match(reservationLink.searchParams.get('calendar'), /^\/bookings\/test\/\?view=calendar/);
  assert.equal(p.nodes['calendar-detail-body'].children.some(child => child.tag === 'a'), false);
  p.nodes['calendar-detail-close'].listeners.click();assert.equal(p.nodes['calendar-detail'].open,false);
});

test('empty, failed and closed detail dialogs do not show stale results',async()=>{
  const p=page(),event={title:'Session',extendedProps:{detailUrl:'/details/'}};
  const first=p.options.eventClick({event});p.requests[0].resolve({ok:false});await first;
  assert.match(p.nodes['calendar-detail-body'].children[0].textContent,/Could not/);
  const retry=p.nodes['calendar-detail-body'].children[1].listeners.click();
  p.requests[1].resolve({ok:true,json:async()=>({title:'Empty',time:'Now',location:'Studio',reservations:[],url:'/workshop/'})});await retry;
  assert.equal(p.nodes['calendar-detail-body'].children[2].textContent,'No active reservations.');
  assert.equal(p.nodes['calendar-detail-body'].children.length,3);
  const third=p.options.eventClick({event});p.nodes['calendar-detail'].close();
  p.requests[2].resolve({ok:true,json:async()=>({title:'Stale'})});await third;
  assert.notEqual(p.nodes['calendar-detail-title'].textContent,'Stale');
});

test('date and location controls preserve the anchor and form context',()=>{
  const p=page({form:true});
  p.options.datesSet({view:{title:'October 2026',currentStart:new Date('2026-10-01T00:00:00Z')}});
  assert.equal(p.nodes['calendar-range'].textContent,'October 2026');
  const header=p.options.dayHeaderFormat(new Date('2026-10-24T00:00:00Z'));
  header.domNodes[0].listeners.click();assert.equal(p.options.date,'2026-10-24');
  assert.equal(p.nodes['dashboard-location-filter'].children.find(item=>item.name==='date').value,'2026-10-24');
  p.ranges[2].listeners.click();assert.equal(typeof p.options.dayHeaderFormat(new Date()),'string');
  const cell=p.options.dayCellContent({date:new Date('2026-10-25T00:00:00Z')});
  cell.domNodes[0].listeners.click({stopPropagation(){}});assert.equal(p.options.date,'2026-10-25');
  assert.equal(p.options.dayCellContent({date:new Date()}),undefined);
  const resource=p.options.resourceLabelContent({resource:{id:'2',title:'East'}});
  resource.domNodes[0].listeners.click();assert.match(p.location.href,/location=2/);
  p.actions[1].listeners.click();p.options.datesSet({view:{title:'Next day',currentStart:new Date('2026-10-26T00:00:00Z')}});
});

test('single location, workshop checkbox and keyboard event controls work',async()=>{
  const p=page({focused:true});p.ranges[0].listeners.click();assert.equal(p.options.view,'timeGridDay');
  const input=p.nodes['calendar-offerings'].children[0].children[0];input.checked=false;input.listeners.change();
  p.nodes['calendar-hide-empty'].checked=true;p.nodes['calendar-hide-empty'].listeners.change();
  const pending=p.options.eventSources[0].events(interval,()=>{});
  assert.equal(p.requests[0].url.searchParams.get('offering'),'2');
  assert.equal(p.requests[0].url.searchParams.get('hide_empty'),'1');
  resolve(p.requests[0],[{id:'session-1',extendedProps:{kind:'session'}}]);await pending;
  input.checked=true;input.listeners.change();
  const el=element(),event={title:'Session',extendedProps:{detailUrl:'/details/',seats:0,capacity:6}};
  p.options.eventDidMount({el,event});assert.equal(el.attrs.role,'button');
  el.listeners.keydown({key:'Enter',preventDefault(){}});assert.equal(p.nodes['calendar-detail'].open,true);
  assert.equal(p.options.eventClassNames({event})[0],'event-empty');
  for(const [props,expected] of [[{kind:'party'},'event-party'],[{camp:true},'event-camp'],[{seats:4,capacity:6},'event-full'],[{seats:1,capacity:6},'event-partial']]) {
    assert.equal(p.options.eventClassNames({event:{extendedProps:props}})[0],expected);
  }
});

test('invalid deep-link dates fall back, valid dates persist, unrelated pages are untouched',()=>{
  vm.runInNewContext(source,{document:{getElementById:()=>null}},{filename});
  const invalid=page({search:'?date=2026-02-31&range=invalid'});assert.equal(invalid.options.date,'2026-10-20');
  const valid=page({search:'?date=2026-12-12&range=day'});assert.equal(valid.options.date,'2026-12-12');
  valid.options.dateClick({date:new Date('2026-12-14T00:00:00Z')});assert.equal(valid.options.date,'2026-12-14');
});

test('connection and aborted requests handle their distinct failure states',async()=>{
  const p=page();
  const failed=p.options.eventSources[0].events(interval,()=>{});
  p.requests[0].reject(new Error('Failed to fetch'));await failed;
  assert.match(p.nodes['calendar-error'].children[0].textContent,/connection/);
  const aborted=p.options.eventSources[0].events(interval,()=>{assert.fail('Aborted request must not publish data');});
  p.requests[1].reject(Object.assign(new Error('Canceled'),{name:'AbortError'}));await aborted;
  assert.equal(p.nodes['calendar-error'].hidden,true);
});
