const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/party-inquiry.js', 'utf8');

function mount({packageOnly = true, staff = false, calendar = false, capacity = null, summary = true} = {}) {
  const events = {};
  const wrappers = {};
  const field = id => ({value:'', closest:() => wrappers[id] ||= {hidden:false},
    removeAttribute(name) {delete this[name];}, addEventListener:(event, fn) => { events[`${id}:${event}`] = fn; }});
  const nodes = {
    'party-package-details':{textContent:JSON.stringify({'1':{direct:true,text:'Package price $450'},'2':{direct:false,text:'Review package'}})},
    'party-slot-packages':{textContent:'{"3":"1"}'},
    'party-request-form':{dataset:{packagesOnly:String(packageOnly),staff:String(staff)}},
    id_package:field('package'), id_party_slot:{...field('slots'),options:[{value:''},{value:'3'}]},
    id_starts_at:field('starts'), 'party-package-summary':{}, 'party-next-step':{}, 'party-submit':{},
  };
  const customInput = {};
  if (!summary) delete nodes['party-package-summary'];
  const custom = {querySelectorAll:() => [customInput]};
  let pickerOptions;
  const window = {};
  if (capacity) {
    nodes['party-slot-capacity'] = {textContent:JSON.stringify(capacity)};
    nodes['party-capacity-notice'] = {};
    nodes.id_group_size = field('group');
  }
  if (calendar) {
    nodes['party-time-picker'] = {};
    nodes['party-date'] = {};
    nodes['party-date-calendar'] = {};
    nodes['party-time-date'] = {};
    nodes['party-time-buttons'] = {children:[],replaceChildren() {this.children=[];},append(button) {this.children.push(button);}};
    nodes['party-slot-dates'] = {textContent:JSON.stringify({3:{date:'2030-11-10',time:'4 PM'},4:{date:'2030-11-10',time:'5 PM'}})};
    nodes['party-slot-packages'].textContent = '{"3":"1","4":"2"}';
    nodes.id_party_slot.options.push({value:'4'});
    nodes.id_party_slot.dispatchEvent = () => {};
    window.flatpickr = (_, options) => {pickerOptions = options; return {destroy() {},jumpToDate() {}};};
  }
  vm.runInNewContext(source, {window,Event:class {},document:{
    createElement:() => ({setAttribute() {},addEventListener() {}}),
    getElementById:id => nodes[id] || null,
    querySelector:() => null,
    querySelectorAll:selector => selector === '[data-custom-party-field]' ? [custom] : [],
    addEventListener:() => {},
  }});
  return {nodes,wrappers,custom,customInput,events,choose:value => {nodes.id_package.value=value;events['package:change']();}, date:day => pickerOptions.onChange([],day)};
}

test('calendar party capacity warns before customer submission and resets between packages', () => {
  const ui = mount({staff:true,capacity:{3:2,4:0,5:1}});
  ui.choose('1');
  ui.nodes.id_party_slot.value='3'; ui.events['slots:change']();
  assert.match(ui.nodes['party-capacity-notice'].textContent,/2 places available/);
  ui.nodes.id_group_size.value='3'; ui.events['group:input']();
  assert.equal(ui.nodes['party-submit'].disabled,true);
  assert.match(ui.nodes['party-capacity-notice'].textContent,/reduce the group/);
  ui.nodes.id_group_size.value='1'; ui.events['group:input']();
  assert.equal(ui.nodes['party-submit'].disabled,false);
  ui.nodes.id_party_slot.value='4'; ui.events['slots:change']();
  assert.match(ui.nodes['party-capacity-notice'].textContent,/full/);
  assert.equal(ui.nodes['party-submit'].disabled,true);
  ui.nodes.id_party_slot.value='5'; ui.events['slots:change']();
  assert.match(ui.nodes['party-capacity-notice'].textContent,/1 place available/);
  ui.choose('');
  assert.equal(ui.nodes['party-capacity-notice'].hidden,true);
  assert.equal(ui.nodes.id_group_size.max,undefined);
});

test('package-only intake never offers a custom fallback, including before package selection', () => {
  const ui = mount();
  assert.equal(ui.nodes['party-submit'].disabled,true);
  assert.equal(ui.nodes.id_starts_at.disabled,true);
  assert.equal(ui.custom.hidden,true);
  assert.equal(ui.customInput.disabled,true);
  assert.equal(ui.nodes['party-next-step'].textContent,'Choose a package to see available times.');
  ui.choose('1');
  assert.equal(ui.nodes['party-submit'].textContent,'Continue to payment');
  assert.equal(ui.nodes['party-submit'].disabled,false);
  assert.equal(ui.nodes.id_party_slot.options[1].disabled,false);
  ui.nodes.id_party_slot.value='3';
  ui.choose('');
  assert.equal(ui.nodes.id_party_slot.value,'');
  assert.equal(ui.nodes['party-submit'].disabled,true);
});

test('staff shares package times without implying an online charge', () => {
  const ui = mount({staff:true,summary:false});
  ui.choose('1');
  assert.equal(ui.nodes['party-submit'].textContent,'Create booking');
  assert.equal(ui.nodes['party-next-step'].textContent,'Payment is recorded separately on the reservation.');
  assert.equal(ui.nodes.id_party_slot.disabled,false);
  assert.equal(ui.nodes.id_party_slot.options[1].disabled,false);
  ui.choose('');
  assert.equal(ui.nodes['party-submit'].disabled,true);
});

test('customer package summary remains available', () => {
  const ui = mount();
  ui.choose('1');
  assert.equal(ui.nodes['party-package-summary'].textContent,'Package price $450');
});

test('other schools retain custom review behavior', () => {
  const ui = mount({packageOnly:false});
  assert.equal(ui.nodes['party-submit'].textContent,'Send party request');
  assert.equal(ui.nodes.id_starts_at.disabled,false);
  assert.equal(ui.custom.hidden,false);
});

test('switching packages retains the selected date and immediately refreshes times', () => {
  const ui = mount({calendar:true});
  ui.choose('1');
  ui.date('2030-11-10');
  ui.nodes.id_party_slot.value = '3';
  ui.choose('2');
  assert.equal(ui.nodes.id_party_slot.value, '');
  assert.equal(ui.nodes['party-time-buttons'].children.length, 1);
  assert.equal(ui.nodes['party-time-buttons'].children[0].textContent, '5 PM');
  assert.equal(ui.nodes['party-submit'].disabled, true);
});
