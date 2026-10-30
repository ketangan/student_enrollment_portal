const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/scheduling/email-editor.js', 'utf8');

function element(extra = {}) {
  return {listeners: {}, attributes: {}, value: '', hidden: true, textContent: '', innerHTML: '',
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attributes[name] = value; },
    focus() { this.focused = true; this.listeners.focus?.(); },
    ...extra};
}

function page({empty = false, template = false, saved = false} = {}) {
  const textarea = element({value: saved ? '<p>Draft</p>' : ''});
  const editor = element({contains: node => node === editor});
  const toolbar = element();
  const label = {};
  const subject = element({selectionStart: 0, selectionEnd: 0,
    setRangeText(text) { this.value += text; }});
  const selector = template ? null : element();
  const token = element({dataset: {emailToken: 'first_name'}});
  const linkToken = template ? null : element();
  const linkNote = element(), linkHint = element();
  const wrapper = {querySelector: selector => ({textarea, '[contenteditable]': editor,
    '[role="toolbar"]': toolbar, label}[selector])};
  const form = element({querySelector: name => ({'[data-email-editor]': wrapper, '[name="subject"]': subject,
    '[name="template"]': selector, '[name="link_token"]': linkToken,
    '[data-email-link-note]': linkNote, '[data-email-link-hint]': linkHint}[name]), querySelectorAll: () => [token]});
  const payload = {subject: 'Welcome', body: '<p>Ready</p>', link_token: 'signed-link', link_hint: 'Adds a guest details button.'};
  const selection = {rangeCount: 0, anchorNode: editor, focusNode: editor,
    getRangeAt: () => ({cloneRange: () => ({commonAncestorContainer: editor})}),
    removeAllRanges() { this.removed = true; }, addRange() { this.added = true; }};
  const commands = [], alerts = [];
  const window = {getSelection: () => selection, confirm: () => true, prompt: () => 'https://example.invalid', alert: text => alerts.push(text)};
  const document = element({querySelector: name => ({'[data-email-form]': empty ? null : form}[name]),
    getElementById: id => template ? null : ({textContent: JSON.stringify({'': {subject:'',body:''}, '1': payload,
      '2': {subject: 'Visit update', body: '<p>Please review the changes.</p>'}})}),
    execCommand(command, _, value) { commands.push({command, value}); if (command === 'insertText') editor.innerHTML += value; }});
  vm.runInNewContext(source, {document, window}, {filename: 'static/scheduling/email-editor.js'});
  return {form, textarea, editor, subject, selector, token, toolbar, linkToken, linkNote, linkHint,
    selection, commands, alerts, document, window};
}
const click = (element, button) => element.listeners.click({target: {closest: () => button}});

test('no editor is a no-op; server-sanitized draft is restored', () => {
  page({empty: true});
  const p = page({saved: true});
  assert.equal(p.editor.innerHTML, '<p>Draft</p>');
  assert.equal(p.textarea.hidden, true);
  assert.equal(p.editor.hidden, false);
  assert.equal(p.toolbar.hidden, false);
  p.window.confirm = () => false;
  p.selector.value = '1'; p.selector.listeners.change();
  assert.equal(p.editor.innerHTML, '<p>Draft</p>');
  assert.equal(p.selector.value, '');
});

test('template selection, replacement cancellation and blank reset preserve the right link', () => {
  const p = page();
  p.selector.value = '1'; p.selector.listeners.change();
  assert.equal(p.subject.value, 'Welcome');
  assert.equal(p.textarea.value, '<p>Ready</p>');
  assert.equal(p.linkToken.value, 'signed-link');
  assert.equal(p.linkNote.hidden, false);
  assert.equal(p.linkHint.textContent, 'Adds a guest details button.');
  p.editor.innerHTML = '<p>Edited</p>'; p.editor.listeners.input();
  p.subject.listeners.input();
  p.window.confirm = () => false;
  p.selector.value = '1'; p.selector.listeners.change();
  assert.equal(p.editor.innerHTML, '<p>Edited</p>');
  assert.equal(p.selector.value, '1');
  assert.equal(p.linkToken.value, 'signed-link');
  p.window.confirm = () => true;
  p.selector.value = ''; p.selector.listeners.change();
  assert.equal(p.selector.value, '');
  assert.equal(p.editor.innerHTML, '');
  assert.equal(p.subject.value, '');
  assert.equal(p.linkToken.value, '');
  assert.equal(p.linkNote.hidden, true);
  p.selector.listeners.change();
  p.selector.value = 'missing'; p.selector.listeners.change();
  assert.equal(p.selector.value, '');
});

test('switching unedited templates updates subject and body without confirmation', () => {
  const p = page();
  p.window.confirm = () => { throw new Error('Unedited templates must switch immediately'); };
  for (const [id, subject, body] of [['1', 'Welcome', '<p>Ready</p>'],
    ['2', 'Visit update', '<p>Please review the changes.</p>'], ['1', 'Welcome', '<p>Ready</p>']]) {
    p.selector.value = id; p.selector.listeners.change();
    assert.equal(p.subject.value, subject);
    assert.equal(p.editor.innerHTML, body);
    assert.equal(p.textarea.value, body);
  }
  p.subject.value = 'My edit'; p.subject.listeners.input();
  p.window.confirm = () => false;
  p.selector.value = '2'; p.selector.listeners.change();
  assert.equal(p.selector.value, '1');
  assert.equal(p.subject.value, 'My edit');
  assert.equal(p.textarea.value, '<p>Ready</p>');
});

test('formatting restores selection; link prompts reject unsafe schemes and allow cancellation', () => {
  const p = page();
  p.document.listeners.selectionchange();
  p.selection.rangeCount = 1;
  p.selection.focusNode = {};
  p.document.listeners.selectionchange();
  p.selection.focusNode = p.editor;
  p.document.listeners.selectionchange();
  click(p.toolbar, null);
  click(p.toolbar, {dataset: {command: 'bold'}});
  assert.ok(p.selection.removed && p.selection.added);
  click(p.toolbar, {dataset: {command: 'createLink'}});
  assert.equal(p.commands[1].value, 'https://example.invalid');
  p.window.prompt = () => null;
  click(p.toolbar, {dataset: {command: 'createLink'}});
  p.window.prompt = () => 'javascript:bad()';
  click(p.toolbar, {dataset: {command: 'createLink'}});
  assert.equal(p.commands.length, 2);
  assert.equal(p.alerts.length, 1);
});

test('paste uses plain text and drop is blocked', () => {
  const p = page();
  let prevented = 0;
  p.editor.listeners.paste({preventDefault() { prevented++; }, clipboardData: {getData(type) { assert.equal(type, 'text/plain'); return 'Pasted'; }}});
  p.editor.listeners.drop({preventDefault() { prevented++; }});
  assert.equal(prevented, 2);
  assert.equal(p.commands[0].command, 'insertText');
  assert.equal(p.textarea.value, 'Pasted');
});

test('reservation tokens insert into focused subject or message', () => {
  const p = page({template: true});
  p.token.listeners.click();
  assert.equal(p.editor.innerHTML, '{{first_name}}');
  p.subject.focus(); p.token.listeners.click();
  assert.equal(p.subject.value, '{{first_name}}');
  p.editor.focus(); p.token.listeners.click();
  assert.equal(p.editor.innerHTML, '{{first_name}}{{first_name}}');
});

test('submission syncs rich text; blank and oversized messages stay in the form', () => {
  const p = page();
  let prevented = 0;
  const event = {preventDefault() { prevented++; }};
  p.form.listeners.submit(event);
  assert.equal(prevented, 1);
  p.editor.textContent = 'Hello'; p.editor.innerHTML = '<b>Hello</b>';
  p.form.listeners.submit(event);
  assert.equal(p.textarea.value, '<b>Hello</b>');
  assert.equal(prevented, 1);
  p.editor.innerHTML = 'x'.repeat(20001);
  p.form.listeners.submit(event);
  assert.equal(prevented, 2);
});
