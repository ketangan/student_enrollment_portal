/* Preview uses the same renderer as delivery, without saving or sending. */
(() => {
  document.querySelectorAll('[data-brand-mode-form]').forEach(form => {
    const toggle = form.querySelector('[name="branded_booking_email"]');
    const status = form.querySelector('[data-brand-mode-status]');
    let pending = null;
    toggle.addEventListener('change', () => {
      const checked = toggle.checked;
      const data = new FormData(form);
      data.set('action', 'save_brand_mode');
      data.set('branded_booking_email', String(checked));
      toggle.disabled = true;
      status.textContent = 'Saving email style...';
      pending = (async () => {
        try {
          const response = await fetch(form.getAttribute('action') || location.href, {
            method:'POST', body:data, credentials:'same-origin', keepalive:true
          });
          const result = await response.json();
          if (!response.ok || result.branded !== checked) throw new Error('Save failed');
          status.textContent = '';
          document.querySelectorAll('[data-email-preview] form').forEach(editorForm => {
            const mode = editorForm.querySelector('[name="branded_booking_email"]');
            if (mode && mode.type === 'hidden') mode.disabled = !checked;
            editorForm.dispatchEvent(new Event('input', {bubbles:true}));
          });
          return true;
        } catch (_) {
          toggle.checked = !checked;
          status.textContent = 'Could not confirm the email style change. Refresh before trying again.';
          toggle.dispatchEvent(new Event('input', {bubbles:true}));
          return false;
        } finally {
          toggle.disabled = false;
          pending = null;
        }
      })();
    });
    document.querySelectorAll('.email-tabs a, .email-template-list a').forEach(link => {
      link.addEventListener('click', async event => {
        if (!pending) return;
        event.preventDefault();
        if (await pending) location.assign(link.href);
      });
    });
    form.addEventListener('submit', event => {
      if (pending) event.preventDefault();
    });
  });
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
        const branding = form.querySelector('[name="branded_booking_email"]');
        if (branding && branding.type === 'checkbox') {
          if (branding.checked) data.set('branded_booking_email', 'on');
          else data.delete('branded_booking_email');
        }
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
