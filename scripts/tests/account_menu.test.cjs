const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const filename = path.resolve(__dirname, '../../static/admin/account-menu.js');
const source = fs.readFileSync(filename, 'utf8');

function element() {
  const classes = new Set();
  return {attrs: {}, listeners: {}, hidden: true, focused: false,
    classList: {toggle(key, on) { on ? classes.add(key) : classes.delete(key); }, contains: key => classes.has(key)},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(key, value) { this.attrs[key] = value; },
    contains(target) { return target === this; },
    focus() { this.focused = true; }};
}
function page(saved = null, blocked = false) {
  const document = element(), button = element(), panel = element();
  document.body = element();
  const themes = ['clean', 'midnight', 'mint'].map(name => {
    const item = element();
    item.dataset = {dashTheme: name};
    item.span = {};
    item.querySelector = () => item.span;
    return item;
  });
  document.querySelectorAll = () => themes;
  document.getElementById = id => id === 'user-menu-btn' ? button : panel;
  const storage = {saved,
    getItem(key) { assert.equal(key, 'dash_theme'); if (blocked) throw Error('blocked'); return this.saved; },
    setItem(key, value) { assert.equal(key, 'dash_theme'); if (blocked) throw Error('blocked'); this.saved = value; }};
  vm.runInNewContext(source, {document, localStorage: storage}, {filename});
  return {document, button, panel, themes, storage};
}
test('shared menu toggles, stays open inside, closes outside and restores focus with Escape', () => {
  const {document, button, panel} = page();
  button.listeners.click();
  assert.equal(panel.hidden, false);
  assert.equal(button.attrs['aria-expanded'], 'true');
  document.listeners.click({target: button});
  document.listeners.click({target: panel});
  document.listeners.keydown({key: 'Tab'});
  assert.equal(panel.hidden, false);
  document.listeners.keydown({key: 'Escape'});
  assert.equal(panel.hidden, true);
  assert.equal(button.focused, true);
  document.listeners.keydown({key: 'Escape'});
  button.listeners.click();
  button.listeners.click();
  assert.equal(panel.hidden, true);
  button.listeners.click();
  document.listeners.click({target: {}});
  assert.equal(panel.hidden, true);
});
test('both portals share theme preference, selected state and safe storage fallback', () => {
  for (const blocked of [false, true]) {
    const {document, themes, storage} = page('midnight', blocked);
    assert.equal(document.body.classList.contains('dash-midnight'), !blocked);
    for (let index = 0; index < themes.length; index++) {
      themes[index].listeners.click();
      assert.equal(themes[index].attrs['aria-pressed'], 'true');
      assert.equal(themes[index].span.textContent, '\u2713');
      assert.equal(document.body.classList.contains('dash-midnight'), index === 1);
      assert.equal(document.body.classList.contains('dash-mint'), index === 2);
      if (!blocked) assert.equal(storage.saved, themes[index].dataset.dashTheme);
    }
  }
  for (const saved of [null, 'invalid']) assert.equal(page(saved).themes[0].attrs['aria-pressed'], 'true');
});
