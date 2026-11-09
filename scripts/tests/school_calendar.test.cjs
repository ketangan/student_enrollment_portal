const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const filename = require('node:path').resolve('static/scheduling/school-calendar.js');
const source = fs.readFileSync(filename, 'utf8');

function page({root = true, library = true, range = 'week'} = {}) {
  const error = {hidden: true};
  const config = {date: '2030-01-07', range, events: []};
  let options, destination;
  const context = {document: {
    getElementById(id) {return id === 'school-calendar' ? root : id === 'school-calendar-error' ? error : {textContent: JSON.stringify(config)};},
    createElement(tag) {return {tag};}
  }, window: {location: {assign(url) {destination = url;}}}};
  if (library) context.EventCalendar = {create(element, value) {options = value;}};
  vm.runInNewContext(source, context, {filename});
  return {error, get options() {return options;}, get destination() {return destination;}};
}

test('calendar renders only on its page and has a usable library failure fallback', () => {
  assert.equal(page({root: false}).options, undefined);
  assert.equal(page({library: false}).error.hidden, false);
});

test('calendar uses the existing read-only renderer for day, week and month', () => {
  for (const [range, view] of [['day', 'timeGridDay'], ['week', 'timeGridWeek'], ['month', 'dayGridMonth']]) {
    const p = page({range});
    assert.equal(p.options.view, view);
    assert.equal(p.options.firstDay, 1);
    assert.equal(p.options.editable, false);
    assert.equal(p.options.selectable, false);
    assert.equal(p.options.eventDurationEditable, false);
    assert.equal(p.options.slotHeight, 44);
  }
});

test('events use text, distinguish types, and support click and keyboard navigation', () => {
  const p = page();
  for (const kind of ['Lesson', 'Class', 'Program session']) {
    const event = {title: '<img onerror="alert(1)">', extendedProps: {
      kind, url: '/bookings/school/record/', status: kind === 'Class' ? 'Draft' : 'Scheduled',
      person: kind === 'Lesson' ? 'Riley' : '', teacher: kind === 'Lesson' ? 'Alex' : '',
      location: 'Main', zone: 'America/Los_Angeles'
    }};
    const content = p.options.eventContent({event, timeText: '10 AM'}).domNodes;
    assert.equal(content[0].textContent, event.extendedProps.person || event.title);
    assert.equal(content[0].innerHTML, undefined);
    assert.ok(content[1].textContent.includes(kind === 'Lesson' ? 'Alex' : kind));
    assert.equal(content[1].textContent.includes('Draft'), kind === 'Class');
    assert.equal(p.options.eventClassNames({event})[0], kind === 'Lesson' ? 'event-partial' : kind === 'Class' ? 'event-party' : 'event-camp');
    const element = {attrs: {}, setAttribute(name, value) {this.attrs[name] = value;}, addEventListener(name, handler) {this[name] = handler;}};
    p.options.eventDidMount({el: element, event});
    assert.equal(element.tabIndex, 0);
    assert.equal(element.attrs.role, 'link');
    assert.ok(element.attrs['aria-label'].includes(event.title));
    let prevented = 0;
    for (const key of ['Enter', ' ', 'Tab']) element.keydown({key, preventDefault() {prevented++;}});
    assert.equal(prevented, 2);
    assert.equal(p.destination, event.extendedProps.url);
    p.options.eventClick({event});
    assert.equal(p.destination, event.extendedProps.url);
  }
});
