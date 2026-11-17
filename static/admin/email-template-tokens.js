/* Shared token insertion for Enrollment and Bookings template editors. */
window.EmailTemplateTokens = {
  attach({editor, subject, inputs = [], buttons, changed = () => {}}) {
    let focused = editor;
    let savedRange = null;
    [editor, subject, ...inputs].forEach(field => field.addEventListener('focus', () => { focused = field; }));
    document.addEventListener('selectionchange', () => {
      const selection = window.getSelection();
      if (selection && selection.rangeCount && editor.contains(selection.anchorNode) && editor.contains(selection.focusNode)) {
        savedRange = selection.getRangeAt(0).cloneRange();
      }
    });
    buttons.forEach(button => button.addEventListener('click', () => {
      const text = '{{' + button.dataset.token + '}}';
      if (focused !== editor) {
        focused.setRangeText(text, focused.selectionStart, focused.selectionEnd, 'end');
        focused.focus();
        focused.dispatchEvent(new Event('input', {bubbles: true}));
      } else {
        editor.focus();
        const selection = window.getSelection();
        const range = savedRange && editor.contains(savedRange.commonAncestorContainer)
          ? savedRange : document.createRange();
        if (range !== savedRange) {
          range.selectNodeContents(editor);
          range.collapse(false);
        }
        range.deleteContents();
        range.insertNode(document.createTextNode(text));
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
        savedRange = range.cloneRange();
      }
      changed();
    }));
  }
};
