(() => {
  window.lucide?.createIcons();
  document.querySelectorAll('[name="payment_at_booking"]').forEach(select => {
    const row = select.form?.querySelector('[data-booking-deposit]');
    if (!row) return;
    const update = () => {
      row.hidden = select.value !== 'deposit';
      const input = row.querySelector('input');
      if (input) {
        input.required = !row.hidden;
        input.disabled = row.hidden;
      }
    };
    select.addEventListener('change', update);
    update();
  });
  const nav = document.getElementById('booking-nav');
  const toggle = document.getElementById('booking-nav-toggle');
  const locationFilter = document.getElementById('dashboard-location-filter');
  locationFilter?.addEventListener('change', () => locationFilter.requestSubmit());
  const setNav = open => {
    if (!nav || !toggle) return;
    nav.classList.toggle('dash-nav--mobile-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  };
  toggle?.addEventListener('click', () => {
    setNav(toggle.getAttribute('aria-expanded') !== 'true');
  });
  document.addEventListener('click', event => {
    if (!nav?.contains(event.target) && !toggle?.contains(event.target)) setNav(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (toggle?.getAttribute('aria-expanded') === 'true') {
      setNav(false);
      toggle.focus();
    }
  });
  window.matchMedia('(max-width: 900px)').addEventListener('change', () => setNav(false));
})();
