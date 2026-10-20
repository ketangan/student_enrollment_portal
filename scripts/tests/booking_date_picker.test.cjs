const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const filename = path.resolve(__dirname, '../../static/scheduling/date-picker.js');
const source = fs.readFileSync(filename, 'utf8');

function node() {
  return {children: [], attrs: {}, dataset: {}, listeners: {}, style: {}, classList: {add() {}},
    append(...items) { this.children.push(...items); },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = value; },
    hasAttribute(name) { return name in this.attrs; }, getAttribute(name) { return this.attrs[name]; },
    getBoundingClientRect() { return {top:500,left:900,height:470,width:320}; },
    querySelectorAll() { return this.periods || []; },
    querySelector(selector) { return this.navigation[selector]; }, click() { this.clicked = true; }};
}
function setup({type = 'datetime-local', disabled = false, readOnly = false, library = true, selected = true, id = 'starts', inDialog = false} = {}) {
  const input = {...node(), type, id, disabled, readOnly, min:'2030-01-01T00:00', max:'2031-01-01T00:00'};
  input.attrs['aria-describedby'] = 'hint';
  input.closest = selector => inDialog && selector === 'dialog' ? {} : null;
  const picker = {calendarContainer:node(), altInput:node(), timeContainer:node(), hourElement:{value:'10'},
    minuteElement:{value:'25'}, amPM:{textContent:'AM'}, isOpen:true,
    selectedDates: selected ? [new Date(2030, 5, 2, 10, 25)] : [],
    isEnabled() { return this.allowed !== false; },
    setDate(date) { this.selectedDates = [date]; this.amPM.textContent = date.getHours() >= 12 ? 'PM' : 'AM'; this.options.onChange([], '', this); },
    clear() { this.cleared = true; this.selectedDates = []; }, close() { this.isOpen = false; this.options.onClose([], '', this); }};
  picker.calendarContainer.navigation = {'.flatpickr-prev-month':node(), '.flatpickr-next-month':node()};
  const window = {innerHeight:720,innerWidth:1000,scrollX:0,scrollY:0,addEventListener(event, fn){ picker.resize = fn; }};
  if (library) window.flatpickr = (element, options) => { picker.options = picker.config = options; options.onReady([], '', picker); };
  vm.runInNewContext(source, {window,document:{querySelectorAll: () => [input], createElement:node},
    requestAnimationFrame:fn => fn(), Date}, {filename});
  if (picker.options) picker.calendarContainer.periods = picker.timeContainer.children[0]?.children || [];
  return {input,picker};
}

test('datetime picker preserves ISO transport and accessible labels', () => {
  const {input,picker:p} = setup();
  assert.equal(p.options.dateFormat, 'Y-m-d\\TH:i');
  assert.equal(p.options.altFormat, 'm/d/Y h:i K');
  assert.equal(p.options.minuteIncrement,1);
  assert.equal(p.options.disableMobile,true);
  assert.equal(p.options.minDate,input.min); assert.equal(p.options.maxDate,input.max);
  assert.equal(input.id,'starts__value'); assert.equal(p.altInput.id,'starts');
  assert.equal(p.altInput.attrs['aria-describedby'],'hint');
  assert.equal(p.calendarContainer.attrs.role,'dialog');
  assert.equal(p.hourElement.tabIndex,0);
});

test('dialog calendar stays inside the modal instead of positioning behind it', () => {
  const {picker:p} = setup({type:'date',inDialog:true});
  assert.equal(p.options.static,true);
  p.options.onOpen([], '', p);
  assert.equal(p.calendarContainer.style.top,undefined);
  assert.equal(p.altInput.attrs['aria-expanded'],'true');
});
test('AM and PM preserve date and minutes, including midnight and noon', () => {
  const {picker:p} = setup(); const [am,pm] = p.calendarContainer.periods;
  pm.listeners.click(); assert.equal(p.selectedDates[0].getHours(),22);
  assert.equal(p.selectedDates[0].getMinutes(),25);
  assert.equal(p.selectedDates[0].getDate(),2);
  assert.equal(pm.attrs['aria-pressed'],'true');
  p.hourElement.value = '12'; am.listeners.click(); assert.equal(p.selectedDates[0].getHours(),0);
  pm.listeners.click(); assert.equal(p.selectedDates[0].getHours(),12);
});
test('empty picker period and Today initialize a date; footer clear and close work', () => {
  const {picker:p} = setup({selected:false}); const [today,clear,close] = p.calendarContainer.children[0].children;
  p.calendarContainer.periods[1].listeners.click(); assert.equal(p.selectedDates.length,1);
  today.listeners.click(); assert.equal(p.selectedDates[0].getDate(),new Date().getDate());
  p.allowed=false; const previous=p.selectedDates[0]; today.listeners.click(); assert.equal(p.selectedDates[0],previous);
  clear.listeners.click(); assert.equal(p.cleared,true);
  p.allowed=true; today.listeners.click(); assert.equal(p.selectedDates.length,1);
  close.listeners.click(); assert.equal(p.isOpen,false); assert.equal(p.altInput.attrs['aria-expanded'],'false');
});
test('popover stays inside laptop viewport and close race does not reposition', () => {
  const {picker:p} = setup(); p.options.onOpen([], '', p);
  assert.equal(p.calendarContainer.style.top,'242px'); assert.equal(p.calendarContainer.style.left,'672px');
  assert.equal(p.altInput.attrs['aria-expanded'],'true');
  p.isOpen=false; p.calendarContainer.style.top='0px'; p.options.onOpen([], '', p);
  assert.equal(p.calendarContainer.style.top,'0px');
  p.isOpen=true; p.resize(); assert.equal(p.calendarContainer.style.top,'242px');
});
test('month arrows are labeled and keyboard operable without form submission', () => {
  const {picker:p} = setup();
  const prev = p.calendarContainer.navigation['.flatpickr-prev-month'];
  assert.equal(prev.attrs['aria-label'], 'Previous month'); assert.equal(prev.tabIndex,0);
  let prevented = 0;
  for (const key of ['Enter',' ','Escape']) prev.listeners.keydown({key,preventDefault(){prevented++;}});
  assert.equal(prev.clicked,true); assert.equal(prevented,2);
});
test('date-only inputs have no time fields and weekends stay selectable', () => {
  const {picker:p} = setup({type:'date',id:''});
  assert.equal(p.options.enableTime,false); assert.equal(p.options.dateFormat,'Y-m-d');
  assert.equal(p.timeContainer.children.length,0); assert.equal(p.altInput.placeholder,'');
  let count=0;
  for (const day of [new Date(2030,5,1),new Date(2030,5,3)]) p.options.onDayCreate([], '', p, {dateObj:day,classList:{add(){count++;}}});
  assert.equal(count,1);
  p.calendarContainer.children[0].children[0].listeners.click();
  p.options.onValueUpdate([], '', p);
});
test('disabled/readonly controls and missing library retain native behavior', () => {
  for (const options of [{disabled:true},{readOnly:true},{library:false}]) assert.equal(setup(options).picker.options,undefined);
});
