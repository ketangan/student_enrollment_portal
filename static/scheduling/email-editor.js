/* Both template editing and reservation composition use the same editor. */
(() => {
  const form = document.querySelector('[data-email-form]');
  if (!form) return;
  const wrapper = form.querySelector('[data-email-editor]');
  const textarea = wrapper.querySelector('textarea');
  const editor = wrapper.querySelector('[contenteditable]');
  const toolbar = wrapper.querySelector('[role="toolbar"]');
  // The initial value and template options are sanitized on the server.
  editor.innerHTML = textarea.value;
  editor.hidden = toolbar.hidden = false;
  textarea.hidden = true;
  textarea.required = false;
  editor.setAttribute('aria-required', 'true');
  wrapper.querySelector('label').htmlFor = 'email-rich-message';
  editor.id = 'email-rich-message';
  const subject = form.querySelector('[name="subject"]');
  const selector = form.querySelector('[name="template"]');
  let dirty = Boolean(subject.value || textarea.value);
  let selectedTemplate = selector?.value || '';
  let focusedField = editor;
  let savedRange = null;
  const sync = () => { textarea.value = editor.innerHTML; };
  editor.addEventListener('input', () => { dirty = true; sync(); });
  subject.addEventListener('input', () => { dirty = true; });
  [editor, subject].forEach(field => field.addEventListener('focus', () => { focusedField = field; }));
  document.addEventListener('selectionchange', () => {
    const selection = window.getSelection();
    if (selection.rangeCount && editor.contains(selection.anchorNode) && editor.contains(selection.focusNode)) {
      savedRange = selection.getRangeAt(0).cloneRange();
    }
  });
  const restoreSelection = () => {
    editor.focus();
    if (savedRange && editor.contains(savedRange.commonAncestorContainer)) {
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(savedRange);
    }
  };
  const insertText = text => {
    restoreSelection();
    document.execCommand('insertText', false, text);
    dirty = true;
    sync();
  };
  // Paste and drag/drop never introduce executable or tracking markup.
  editor.addEventListener('paste', event => {
    event.preventDefault();
    insertText(event.clipboardData.getData('text/plain'));
  });
  editor.addEventListener('drop', event => { event.preventDefault(); });
  toolbar.addEventListener('click', event => {
    const button = event.target.closest('[data-command]');
    if (!button) return;
    restoreSelection();
    let value = null;
    if (button.dataset.command === 'createLink') {
      value = window.prompt('Link URL (https:// or mailto:)');
      if (!value) return;
      if (!/^(https?:\/\/|mailto:)/i.test(value.trim())) {
        window.alert('Enter an https://, http:// or mailto: link.');
        return;
      }
    }
    document.execCommand(button.dataset.command, false, value);
    dirty = true;
    sync();
  });
  form.querySelectorAll('[data-email-token]').forEach(button => button.addEventListener('click', () => {
    const text = '{{' + button.dataset.emailToken + '}}';
    if (focusedField === subject) {
      subject.setRangeText(text, subject.selectionStart, subject.selectionEnd, 'end');
      subject.focus();
      dirty = true;
    } else insertText(text);
  }));
  const read = id => JSON.parse(document.getElementById(id)?.textContent || '{}');
  const options = read('email-options');
  const updateLink = value => {
    const token = form.querySelector('[name="link_token"]');
    if (!token) return;
    token.value = value?.link_token || '';
    const note = form.querySelector('[data-email-link-note]');
    note.hidden = !value?.link_hint;
    form.querySelector('[data-email-link-hint]').textContent = value?.link_hint || '';
  };
  updateLink(options[selector?.value || '']);
  const apply = value => {
    if (!value || (dirty && !window.confirm('Replace the current subject and message?'))) return false;
    subject.value = value.subject;
    editor.innerHTML = value.body;
    savedRange = null;
    dirty = false;
    sync();
    updateLink(value);
    return true;
  };
  selector?.addEventListener('change', () => {
    if (apply(options[selector.value])) selectedTemplate = selector.value;
    else selector.value = selectedTemplate;
  });
  form.addEventListener('submit', event => {
    sync();
    if (!editor.textContent.trim() || textarea.value.length > 20000) {
      event.preventDefault();
      window.alert('Enter a message under 20,000 characters.');
      editor.focus();
    }
  });
})();
