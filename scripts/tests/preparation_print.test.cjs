const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('valid prep reports open print after loading; invalid filters never auto-print', () => {
  for (const ready of ['true', 'false']) {
    const handlers = {};
    let prints = 0;
    vm.runInNewContext(fs.readFileSync('static/scheduling/preparation-print.js', 'utf8'), {
      document: {body:{dataset:{printReady:ready}},getElementById:()=>({addEventListener:(name,fn)=>{handlers[name]=fn;}})},
      window: {print(){prints++;},addEventListener:(name,fn)=>{handlers[name]=fn;}},
    });
    assert.equal(prints,0);
    handlers.load();assert.equal(prints,ready === 'true' ? 1 : 0);
    handlers.click();assert.equal(prints,ready === 'true' ? 2 : 1);
  }
});
