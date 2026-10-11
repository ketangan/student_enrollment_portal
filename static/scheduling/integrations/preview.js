(() => {
  'use strict';
  const frame = document.querySelector('[data-booking-frame]');
  const host = frame.closest('.embed-host');
  const detail = host.querySelector('.loading-detail');
  const title = host.querySelector('.loading-panel strong');
  const actions = host.querySelector('.load-actions');
  const dialog = document.querySelector('#booking-dialog');
  const opener = document.querySelector('#open-booking');
  let timeout;
  let started = false;
  const bookingURL = new URL(frame.dataset.url, location.protocol === 'file:' ? 'http://localhost:8002' : location.origin);
  document.querySelectorAll('.direct-link').forEach(link => { link.href = bookingURL.href; });

  function load() {
    clearTimeout(timeout);
    started = true;
    host.dataset.state = 'loading';
    host.setAttribute('aria-busy', 'true');
    title.textContent = 'Loading workshop booking...';
    detail.textContent = 'Connecting to the Pontora demo.';
    actions.hidden = true;
    frame.src = bookingURL.href;
    timeout = setTimeout(() => {
      host.dataset.state = 'slow';
      title.textContent = 'Booking is taking longer to load';
      detail.textContent = 'The demo may be unavailable, or embedding may be blocked. You can retry or open it separately.';
      actions.hidden = false;
    }, 12000);
  }

  frame.addEventListener('load', () => {
    if (!started) return;
    if (frame.contentDocument?.URL === 'about:blank') return;
    clearTimeout(timeout);
    host.removeAttribute('aria-busy');
    // Inspect only the real same-origin document; never replace its UI or responses.
    try {
      const doc = frame.contentDocument;
      if (!doc || !doc.querySelector('.brandbar')) throw new Error('Booking document unavailable');
      host.dataset.state = 'loaded';
      if (dialog) {
        // Escape does not bubble out of iframe documents. Same-origin preview only.
        doc.addEventListener('keydown', event => {
          if (event.key === 'Escape' && !event.defaultPrevented && dialog.open) {
            event.preventDefault();
            dialog.close();
          }
        });
      }
    } catch {
      host.dataset.state = 'error';
      title.textContent = 'Booking could not be embedded';
      detail.textContent = 'The Pontora demo or its approved embedding configuration is unavailable. No replacement form is shown.';
      actions.hidden = false;
    }
  });
  host.querySelector('.retry-button').addEventListener('click', load);
  if (dialog) {
    opener.addEventListener('click', () => {
      dialog.showModal();
      document.body.classList.add('modal-open');
      if (!started) load();
    });
    document.querySelector('#close-booking').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
      document.body.classList.remove('modal-open');
      opener.focus();
    });
  } else {
    load();
  }
  if (window.lucide) window.lucide.createIcons();
})();
