(() => {
  const dialog = document.getElementById('reservation-email-dialog');
  if (!dialog) return;
  let opener;
  const open = button => {
    opener = button;
    if (!dialog.open) dialog.showModal();
    document.body.classList.add('email-drawer-open');
    dialog.querySelector('[name="template"]').focus();
  };
  // Reservation inline edits replace the heading, so delegate the open action.
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-email-open]');
    if (button) open(button);
  });
  dialog.querySelectorAll('[data-email-close]').forEach(button => {
    button.addEventListener('click', () => dialog.close());
  });
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const fields = [...dialog.querySelectorAll('button, input:not([type="hidden"]), select, textarea:not([hidden]), [contenteditable="true"]')]
      .filter(field => !field.disabled && field.offsetParent !== null);
    const target = event.shiftKey && document.activeElement === fields[0] ? fields.at(-1)
      : !event.shiftKey && document.activeElement === fields.at(-1) ? fields[0] : null;
    if (target) {
      event.preventDefault();
      target.focus();
    }
  });
  dialog.addEventListener('close', () => {
    document.body.classList.remove('email-drawer-open');
    if (opener?.isConnected) opener.focus();
    else document.querySelector('[data-email-open]')?.focus();
  });
  if (dialog.hasAttribute('data-email-error')) open(document.querySelector('[data-email-open]'));
})();
