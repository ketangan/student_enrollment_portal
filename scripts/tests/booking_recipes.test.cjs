const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const filename = path.resolve(__dirname, '../../static/scheduling/recipes.js');
const source = fs.readFileSync(filename,'utf8');

function row(id = '', hidden = false) {
  const fields = {'[name$="-id"]':{value:id}, '[name$="-DELETE"]':{value:''}, select:{value:'7',focus(){this.focused=true;}}};
  return {hidden, fields, querySelector: key => fields[key],querySelectorAll: () => Object.values(fields)};
}
function setup(initial = [row('1'),row()]) {
  const add = {listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},focus(){this.focused=true;}};
  const rows = {items:initial,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},
    querySelectorAll(selector){return this.items.filter(item => selector.includes(':not') ? !item.hidden : item.hidden);},
    append(content){this.items.push(content.row);this.lastElementChild=content.row;}};
  const total = {value:initial.length};
  const attributes = {id:'id_recipe-__prefix__-material',name:'recipe-__prefix__-material',for:'id_recipe-__prefix__-material'};
  const template = {content:{cloneNode(){
    return {row:row(),querySelectorAll(){
      return [{hasAttribute:k=>k in attributes,getAttribute:k=>attributes[k],setAttribute:(k,v)=>{attributes[k]=v;}}];
    }};
  }}};
  const nodes = {'add-recipe-row':add,'recipe-rows':rows,'id_recipe-TOTAL_FORMS':total,'recipe-empty':template};
  vm.runInNewContext(source,{document:{getElementById:id=>nodes[id]},window:{lucide:{createIcons(){}}}},{filename});
  return {add,rows,total,attributes};
}
test('delete marks saved row for submission and hides it without touching remaining rows', () => {
  const p=setup(); const item=p.rows.items[0];
  p.rows.listeners.click({target:{closest:()=>({closest:()=>item})}});
  assert.equal(item.hidden,true); assert.equal(item.fields['[name$="-DELETE"]'].value,'on');
  assert.equal(p.rows.items[1].hidden,false); assert.equal(p.total.value,2); assert.equal(p.add.focused,true);
  p.rows.listeners.click({target:{closest:()=>null}});
});
test('add row replaces every formset prefix and focuses the new material', () => {
  const p=setup(); p.add.listeners.click();
  assert.equal(p.total.value,3); assert.equal(p.rows.items.length,3);
  assert.equal(p.attributes.name,'recipe-2-material'); assert.equal(p.attributes.for,'id_recipe-2-material');
  assert.equal(p.rows.lastElementChild.fields.select.focused,true);
});
test('reuse unsaved deleted rows while preserving saved deletions', () => {
  const saved=row('1',true), unsaved=row('',true),p=setup([saved,unsaved]);
  p.add.listeners.click(); assert.equal(saved.hidden,true); assert.equal(unsaved.hidden,false);
  assert.equal(unsaved.fields.select.value,''); assert.equal(p.total.value,2);
});
test('30 active rows are the limit but deleting then adding remains possible', () => {
  const p=setup(Array.from({length:30},(_,i)=>row(String(i+1))));
  assert.equal(p.add.disabled,true); p.add.listeners.click(); assert.equal(p.total.value,30);
  p.rows.listeners.click({target:{closest:()=>({closest:()=>p.rows.items[0]})}});
  p.add.listeners.click(); assert.equal(p.total.value,31); assert.equal(p.add.disabled,true);
});
test('absolute form limit and unrelated pages are safe', () => {
  const p=setup();p.total.value=61;p.add.listeners.click();assert.equal(p.rows.items.length,2);
  vm.runInNewContext(source,{document:{getElementById:()=>null}},{filename});
});
