/* Preview uses the same renderer as delivery, without saving or sending. */
(() => {
  document.querySelectorAll('[data-email-color]').forEach(button => {
    button.addEventListener('click', () => {
      const input = button.closest('form').querySelector('[name="email_brand_color"]');
      input.value = button.dataset.emailColor;
      button.closest('form').querySelectorAll('[data-email-color]').forEach(swatch => {
        swatch.setAttribute('aria-pressed', String(swatch === button));
      });
      input.dispatchEvent(new Event('input', {bubbles:true}));
    });
  });
  document.querySelectorAll('[data-email-preview]').forEach(root => {
    const form = root.querySelector('form');
    const frame = root.querySelector('[data-preview-frame]');
    const status = root.querySelector('[data-preview-status]');
    let timer, revision = 0, controller;
    const schedule = () => {
      const current = ++revision;
      clearTimeout(timer);
      if (controller) controller.abort();
      status.textContent = 'Updating preview...';
      timer = setTimeout(async () => {
        controller = new AbortController();
        const data = new FormData(form);
        data.set('action', form.dataset.previewAction);
        try {
          const response = await fetch(form.getAttribute('action') || location.href, {
            method: 'POST', body: data, credentials: 'same-origin', signal: controller.signal
          });
          const result = await response.json();
          if (current !== revision) return;
          if (!response.ok) {
            const labels = {name:'Name', subject:'Subject', body:'Message', heading:'Heading', booking_kind:'Available for', booking_link:'Included button'};
            status.textContent = Object.entries(result.errors || {}).map(([field, errors]) =>
              `${labels[field] || field}: ${errors.join(' ')}`).join(' ') || 'Preview unavailable.';
            return;
          }
          frame.srcdoc = result.html;
          root.querySelector('[data-preview-subject]').textContent = result.subject;
          status.textContent = '';
        } catch (error) {
          if (error.name !== 'AbortError' && current === revision) status.textContent = 'Could not update preview. Your changes have not been saved.';
        }
      }, 400);
    };
    form.addEventListener('input', schedule);
    form.addEventListener('change', schedule);
    const editor = form.querySelector('[contenteditable]');
    if (editor) new MutationObserver(schedule).observe(editor, {subtree: true, childList: true, characterData: true});
  });
})();
