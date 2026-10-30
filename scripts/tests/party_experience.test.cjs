const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/party-experience.js', 'utf8');

function mount(response, {countModes = false} = {}) {
  const node = () => ({dataset:{}, listeners:{}, children:[], value:'', labels:[],
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { delete this[name]; },
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; }, focus() { this.focused = true; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    checkValidity() { return true; }, classList:{toggle() {}},
  });
  const panels = [1,2,3].map(n => Object.assign(node(), {dataset:{partyPanel:String(n)}}));
  const next = Object.assign(node(), {dataset:{partyNext:'3'}});
  const form = Object.assign(node(), {dataset:{customerSteps:'true'}, action:'/party/', elements:{}});
  const elements = form.elements;
  for (const name of ['package','group_size','party_slot','organizer_name','organizer_email','organizer_phone','participant_estimate','age_range','occasion','birthday_name','birthday_age','customer_notes']) elements[name] = node();
  elements.package.value = '1'; elements.group_size.value = '6'; elements.party_slot.value = '2';
  elements.namedItem = name => elements[name];
  if (countModes) {
    elements.participant_count_mode = {value:'exact'};
    for (const name of ['group_size','participant_estimate']) {
      const field = node(); elements[name].closest = () => field;
    }
  }
  const wrapper = node(); wrapper.closest = () => panels[1];
  elements.organizer_phone.closest = () => wrapper;
  form.querySelectorAll = selector => selector === '[data-party-panel]' ? panels : selector === '[data-party-next]' ? [next] : [];
  const nodes = {'party-request-form':form, 'party-package-details':{textContent:JSON.stringify({1:{name:'Party',included:6,extra_limit:2,price_cents:45000,extra_cents:6500,tax_rate:'10',deposit_cents:15000}})},'party-slot-dates':{textContent:'{}'}};
  const root = node();
  let calls = 0;
  vm.runInNewContext(source, {
    document:{getElementById:id => nodes[id] ||= node(), querySelector:() => root, querySelectorAll:() => [], createElement:node},
    FormData:class { set() {} },
    fetch:async () => { calls++; return response(); },
  });
  return {panels,next,root,wrapper,nodes,calls:() => calls,click:() => next.listeners.click()};
}

test('server field errors prevent review and keep the relevant step editable', async () => {
  const ui = mount(async () => ({ok:true,json:async () => ({errors:{organizer_phone:[{message:'Enter a valid phone.'}]}})}));
  await ui.click();
  assert.equal(ui.root.dataset.step, 2);
  assert.equal(ui.panels[2].hidden, true);
  assert.equal(ui.wrapper.children[0].children[0].textContent, 'Enter a valid phone.');
  assert.equal(ui.next.disabled, false);
});

test('valid details advance only after the server responds and prevent duplicate requests', async () => {
  let resolve;
  const ui = mount(() => new Promise(done => { resolve = done; }));
  const waiting = ui.click();
  await ui.click();
  assert.equal(ui.calls(), 1);
  assert.equal(ui.next.disabled, true);
  assert.equal(ui.panels[2].hidden, true);
  resolve({ok:true,json:async () => ({errors:{}})});
  await waiting;
  assert.equal(ui.root.dataset.step, 3);
  assert.equal(ui.next.disabled, false);
});

test('network failure does not advance or discard entered fields', async () => {
  const ui = mount(async () => { throw new Error('offline'); });
  ui.nodes['party-request-form'].elements.organizer_phone.value = '6265550123';
  await ui.click();
  assert.equal(ui.panels[2].hidden, true);
  assert.match(ui.nodes['party-form-errors'].textContent, /could not check/);
  assert.equal(ui.nodes['party-request-form'].elements.organizer_phone.value, '6265550123');
  assert.equal(ui.next.disabled, false);
});

test('range mode derives the upper count and never enables both inputs', () => {
  const ui = mount(() => {}, {countModes:true});
  const form = ui.nodes['party-request-form'], fields = form.elements;
  assert.equal(fields.participant_estimate.disabled, true);
  fields.participant_count_mode.value = 'estimate';
  fields.participant_estimate.value = '7-9 guests';
  form.listeners.change();
  assert.equal(fields.group_size.value, '9');
  assert.equal(fields.group_size.disabled, true);
  assert.equal(fields.participant_estimate.disabled, false);
  assert.equal(ui.nodes['party-total'].textContent, '$709.50');
  fields.participant_count_mode.value = 'exact';
  form.listeners.change();
  assert.equal(fields.group_size.disabled, false);
  assert.equal(fields.participant_estimate.disabled, true);
});

test('changing packages clears acknowledgments of the previous package terms', () => {
  const ui = mount(() => {});
  const form = ui.nodes['party-request-form'];
  for (const name of ['waiver_acknowledged','attendance_acknowledged','policy_acknowledged']) form.elements[name] = {checked:true};
  form.elements.package.value = '';
  form.listeners.change();
  assert.equal(form.elements.waiver_acknowledged.checked, false);
  assert.equal(form.elements.attendance_acknowledged.checked, false);
  assert.equal(form.elements.policy_acknowledged.checked, false);
});
